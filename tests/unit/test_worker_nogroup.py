import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from app.worker import StatelessWorkerDaemon


async def test_ensure_consumer_group_no_group_re_raises():
    """ensure_consumer_group re-raises NOGROUP (it only silently ignores BUSYGROUP)."""
    from redis.exceptions import ResponseError as RealResponseError

    daemon = StatelessWorkerDaemon()
    daemon.redis.xgroup_create = AsyncMock(
        side_effect=RealResponseError("NOGROUP 0 1")
    )

    with pytest.raises(RealResponseError, match="NOGROUP"):
        await daemon.ensure_consumer_group()


async def test_run_forever_no_group_calls_ensure_and_continues():
    """run_forever must self-heal on real redis ResponseError('NOGROUP') and read the task immediately."""
    from redis.exceptions import ResponseError

    daemon = StatelessWorkerDaemon()

    call_order = []
    handled = []
    xread_calls = 0

    async def mock_xreadgroup(groupname, consumername, streams, count, block):
        nonlocal xread_calls
        xread_calls += 1
        call_order.append("xreadgroup")
        if xread_calls == 1:
            raise ResponseError("NOGROUP No such key 'stream:tasks' or consumer group 'agent_worker_pool'")
        daemon._running = False
        return [
            (
                "stream:tasks",
                [("1526587456000-1", {"payload": '{"task_id": "task-1", "agent_id": "researcher"}'})],
            )
        ]

    async def tracking_ensure():
        call_order.append("ensure_consumer_group")

    async def mock_handle(msg_id, fields):
        handled.append((msg_id, fields))
        return {"lease_acquired": True}

    daemon.redis.xreadgroup = mock_xreadgroup
    daemon.redis.aclose = AsyncMock(return_value=None)
    daemon.ensure_consumer_group = tracking_ensure
    daemon._handle_stream_message = mock_handle

    await asyncio.wait_for(daemon.run_forever(), timeout=1.0)

    assert call_order == [
        "ensure_consumer_group",  # initial startup
        "xreadgroup",             # hits NOGROUP
        "ensure_consumer_group",  # self-heals consumer group
        "xreadgroup",             # retries immediately and reads task
    ]
    assert len(handled) == 1
    assert handled[0][0] == "1526587456000-1"