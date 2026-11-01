import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from typing import Any, Dict, Optional
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.runner import StatelessAgentRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent_worker.daemon")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
REDIS_TASK_STREAM = os.getenv("REDIS_TASK_STREAM", "stream:tasks")
REDIS_CONSUMER_GROUP = os.getenv("REDIS_CONSUMER_GROUP", "agent_worker_pool")
TASK_LEASE_TTL_SECONDS = int(os.getenv("TASK_LEASE_TTL_SECONDS", "60"))


class StatelessWorkerDaemon:
    """
    Redis Streams Consumer & Distributed Lease Manager.
    - Atomically pops pending tasks from `stream:tasks` via consumer group `agent_worker_pool`
    - Acquires distributed task lease: `SET task:{id}:lease {worker_id} NX EX 60`
    - Hydrates and executes the assigned agent via `StatelessAgentRunner`
    - Releases the lease and XACKs the stream message on completion
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        redis_client: Optional[Any] = None,
        lease_ttl_seconds: int = TASK_LEASE_TTL_SECONDS,
        release_on_finish: bool = True,
    ) -> None:
        hostname = socket.gethostname()
        self.worker_id = (
            worker_id or f"worker-{hostname}-{uuid.uuid4().hex[:6]}"
        )
        self.redis = redis_client or aioredis.from_url(
            REDIS_URL, decode_responses=True
        )
        self.lease_ttl_seconds = lease_ttl_seconds
        self.release_on_finish = release_on_finish
        self.lease_conflict_count = 0
        self.runner = StatelessAgentRunner(
            worker_id=self.worker_id, redis_client=self.redis
        )
        self._running = True

    async def ensure_consumer_group(self) -> None:
        try:
            await self.redis.xgroup_create(
                name=REDIS_TASK_STREAM,
                groupname=REDIS_CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
            logger.info(
                "Created Redis consumer group '%s' on stream '%s'",
                REDIS_CONSUMER_GROUP,
                REDIS_TASK_STREAM,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def acquire_task_lease(
        self, task_id: str, ttl_override: Optional[float] = None
    ) -> bool:
        """
        Distributed task lease via Redis:
        `SET task:{id}:lease {worker_id} NX EX 60`
        """
        lease_key = f"task:{task_id}:lease"
        ttl = (
            ttl_override
            if ttl_override is not None
            else self.lease_ttl_seconds
        )
        acquired = await self.redis.set(
            lease_key,
            self.worker_id,
            nx=True,
            ex=ttl,
        )
        if not acquired:
            self.lease_conflict_count += 1
        return bool(acquired)

    async def release_task_lease(self, task_id: str) -> None:
        """Release lease only if still owned by this worker instance."""
        lease_key = f"task:{task_id}:lease"
        owner = await self.redis.get(lease_key)
        if owner == self.worker_id:
            await self.redis.delete(lease_key)

    async def run_forever(self) -> None:
        await self.ensure_consumer_group()
        logger.info(
            "Stateless Agent Worker '%s' online and listening on '%s'",
            self.worker_id,
            REDIS_TASK_STREAM,
        )

        while self._running:
            try:
                streams = await self.redis.xreadgroup(
                    groupname=REDIS_CONSUMER_GROUP,
                    consumername=self.worker_id,
                    streams={REDIS_TASK_STREAM: ">"},
                    count=1,
                    block=3000,
                )
                if not streams:
                    continue

                for _stream_name, messages in streams:
                    for msg_id, fields in messages:
                        await self._handle_stream_message(msg_id, fields)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Worker loop error: %s", exc)
                await asyncio.sleep(2.0)

        await self.redis.aclose()

    async def _handle_stream_message(
        self, msg_id: str, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        raw_payload = fields.get("payload", "{}")
        try:
            task: Dict[str, Any] = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.error("Invalid JSON task payload in message %s", msg_id)
            await self.redis.xack(
                REDIS_TASK_STREAM, REDIS_CONSUMER_GROUP, msg_id
            )
            return {"lease_acquired": False, "error": "invalid_json"}

        task_id = task.get("task_id", f"msg-{msg_id}")
        leased = await self.acquire_task_lease(task_id)
        if not leased:
            logger.info(
                "Task '%s' already leased by another worker; skipping.", task_id
            )
            return {
                "lease_acquired": False,
                "task_id": task_id,
                "worker_id": self.worker_id,
            }

        try:
            logger.info(
                "Worker '%s' acquired lease for task '%s' (agent='%s')",
                self.worker_id,
                task_id,
                task.get("agent_id"),
            )
            res = await self.runner.execute_task(task)
            await self.redis.xack(
                REDIS_TASK_STREAM, REDIS_CONSUMER_GROUP, msg_id
            )
            return {
                "lease_acquired": True,
                "task_id": task_id,
                "worker_id": self.worker_id,
                **(res if isinstance(res, dict) else {}),
            }
        except Exception as exc:
            logger.exception("Error executing task '%s': %s", task_id, exc)
            return {
                "lease_acquired": True,
                "task_id": task_id,
                "status": "ERROR",
                "error": str(exc),
            }
        finally:
            if self.release_on_finish:
                await self.release_task_lease(task_id)

    def stop(self) -> None:
        self._running = False


async def main() -> None:
    daemon = StatelessWorkerDaemon()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, daemon.stop)
        except NotImplementedError:
            pass
    await daemon.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
