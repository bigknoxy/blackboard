import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pytest

# Ensure control-plane and agent-worker are both importable under `app.*`
ROOT_DIR = Path(__file__).resolve().parent.parent
for candidate_root in [
    ROOT_DIR,
    ROOT_DIR / "control-plane",
    ROOT_DIR / "agent-worker",
]:
    if candidate_root.exists():
        sys.path.insert(0, str(candidate_root))

import app as _shared_app_pkg  # noqa: E402

for sub_app in [
    ROOT_DIR / "control-plane" / "app",
    ROOT_DIR / "agent-worker" / "app",
    ROOT_DIR / "app",
]:
    if sub_app.exists() and str(sub_app) not in _shared_app_pkg.__path__:
        _shared_app_pkg.__path__.append(str(sub_app))


class InMemoryAsyncRedis:
    """
    Deterministic async Redis & Redis Streams / Lease / PubSub emulator for
    fast, isolated unit, integration, and Popperian falsification tests.
    Supports atomic `SET NX EX` with monotonic TTL expiration, Streams (`XADD`,
    `XREADGROUP`, `XACK`), and Pub/Sub (`PUBLISH`, `pubsub()`).
    """

    def __init__(self) -> None:
        self._kv: Dict[str, Tuple[str, Optional[float]]] = {}
        self._lock = asyncio.Lock()
        self._streams: Dict[str, List[Tuple[str, Dict[str, str]]]] = {}
        self._groups: Dict[str, Dict[str, int]] = {}
        self._acked: Dict[str, set] = {}
        self.published_events: List[ Tuple[str, str] ] = []
        self.lease_conflicts: List[Dict[str, str]] = []

    def _purge_expired(self, key: str) -> None:
        if key in self._kv:
            _val, exp = self._kv[key]
            if exp is not None and time.monotonic() >= exp:
                del self._kv[key]

    async def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: Optional[float] = None,
    ) -> bool:
        async with self._lock:
            self._purge_expired(key)
            if nx and key in self._kv:
                self.lease_conflicts.append(
                    {"key": key, "attempted_by": value}
                )
                return False
            expires_at = (
                time.monotonic() + float(ex) if ex is not None else None
            )
            self._kv[key] = (str(value), expires_at)
            return True

    async def setex(self, key: str, ttl: float, value: str) -> bool:
        return await self.set(key, value, nx=False, ex=ttl)

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            self._purge_expired(key)
            entry = self._kv.get(key)
            return entry[0] if entry else None

    async def delete(self, key: str) -> int:
        async with self._lock:
            self._purge_expired(key)
            if key in self._kv:
                del self._kv[key]
                return 1
            return 0

    async def publish(self, channel: str, message: str) -> int:
        self.published_events.append((channel, message))
        return 1

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = True,
    ) -> bool:
        if mkstream and name not in self._streams:
            self._streams[name] = []
        self._groups.setdefault(name, {})[groupname] = 0
        self._acked.setdefault(f"{name}:{groupname}", set())
        return True

    async def xadd(self, name: str, fields: Dict[str, str]) -> str:
        stream = self._streams.setdefault(name, [])
        msg_id = f"{int(time.time() * 1000)}-{len(stream) + 1}"
        stream.append((msg_id, dict(fields)))
        return msg_id

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Dict[str, str],
        count: int = 1,
        block: int = 100,
    ) -> List[Tuple[str, List[Tuple[str, Dict[str, str]]]]]:
        results = []
        for sname in streams:
            stream_items = self._streams.get(sname, [])
            cursor = self._groups.setdefault(sname, {}).get(groupname, 0)
            batch = stream_items[cursor : cursor + count]
            if batch:
                self._groups[sname][groupname] = cursor + len(batch)
                results.append((sname, batch))
        return results

    async def xack(self, name: str, groupname: str, msg_id: str) -> int:
        self._acked.setdefault(f"{name}:{groupname}", set()).add(msg_id)
        return 1

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis() -> InMemoryAsyncRedis:
    return InMemoryAsyncRedis()


@pytest.fixture
async def db_session():
    import os
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from app.models import Base

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://blackboard:blackboard_secret@postgres:5432/blackboard_db",
    )
    test_engine = create_async_engine(db_url, poolclass=NullPool)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
    await test_engine.dispose()

