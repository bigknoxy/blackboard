import asyncio
import json
import pytest
from app.worker import StatelessWorkerDaemon
from app.workflow_engine import workflow_engine


@pytest.mark.asyncio
async def test_falsifier_5_zombie_lock_recovery_after_worker_crash(fake_redis, monkeypatch):
    """
    Falsifier 5: Zombie Lock Recovery (Worker Crash)
    Hypothesis: "If a worker process crashes mid-task, the blackboard task will not
    hang indefinitely in an unresolvable state."
    Test: Claim a task lease with a short TTL (e.g., 2 seconds / 0.2s in test) and
    abruptly terminate the worker without sending completion or release. Wait for TTL expiration.
    Assertion: The task lease automatically expires, the workflow engine detects the
    abandoned task, transitions it to `RETRY_QUEUED`, and a second worker successfully
    re-claims and completes it.
    """
    monkeypatch.setattr("app.workflow_engine.get_redis", lambda: fake_redis)

    task_id = "task-zombie-777"
    task_payload = {
        "task_id": task_id,
        "workflow_id": "code_review_pipeline",
        "agent_id": "coder",
        "entry_type": "code",
        "prompt": "Recover from crashed worker mid-flight",
    }

    # Worker 1 claims the task lease with a short TTL and crashes mid-task without releasing
    worker_1 = StatelessWorkerDaemon(
        worker_id="worker-crashing-1",
        redis_client=fake_redis,
        lease_ttl_seconds=1,
    )
    leased_1 = await worker_1.acquire_task_lease(task_id, ttl_override=0.15)
    assert leased_1 is True
    assert await fake_redis.get(f"task:{task_id}:lease") == "worker-crashing-1"

    # Simulate abrupt crash of worker_1 (no release_task_lease called) and wait for TTL expiry
    await asyncio.sleep(0.2)
    assert await fake_redis.get(f"task:{task_id}:lease") is None

    # Workflow engine detects abandoned task, transitions it to RETRY_QUEUED, and re-queues it
    recovery_state = await workflow_engine.recover_abandoned_task(
        task_payload, redis_client=fake_redis
    )
    assert recovery_state["status"] == "RETRY_QUEUED"
    assert recovery_state["task_id"] == task_id

    # Worker 2 now claims the recovered task and completes it
    worker_2 = StatelessWorkerDaemon(
        worker_id="worker-healthy-2",
        redis_client=fake_redis,
        lease_ttl_seconds=60,
    )
    completed_by = []

    async def _worker2_exec(t):
        completed_by.append(worker_2.worker_id)
        return {"status": "COMPLETED", "task_id": t["task_id"]}

    worker_2.runner.execute_task = _worker2_exec
    result_2 = await worker_2._handle_stream_message(
        "1700000001-1", {"payload": json.dumps(task_payload)}
    )

    assert result_2["lease_acquired"] is True
    assert result_2["status"] == "COMPLETED"
    assert completed_by == ["worker-healthy-2"]
