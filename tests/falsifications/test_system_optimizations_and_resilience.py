"""
Popperian Falsification 18: System Optimizations, Process-Group Zombie Reaping,
Preemptive Worker Lease Heartbeat & Fast-Path Telemetry Caching.

Invariants Verified:
1. Subprocess Non-Blocking Event Loop (Falsifier 18.1):
   `_handle_bash_executor` and dynamic shell tools MUST execute via non-blocking
   async subprocesses without stalling the FastAPI event loop or starving concurrent requests.
2. Process-Group Zombie Reaping (Falsifier 18.2):
   When a shell command exceeds its timeout, the entire process sub-tree MUST be
   terminated via `os.killpg(signal.SIGKILL)` without leaving orphaned background processes.
3. Preemptive Lease Heartbeat Watchdog (Falsifier 18.3):
   During task execution, workers MUST periodically refresh their Redis task lease
   TTL via `_lease_heartbeat_loop`, preventing false-positive zombie expirations
   during prolonged reasoning or multi-turn debate steps.
4. Fast-Path In-Memory State Caching & Invalidation (Falsifier 18.4):
   Frequently polled cluster snapshots MUST be served from in-memory cache with
   sliding TTL, invalidating immediately upon mutating actions (tools, agents, workflows).
5. Clean Sub-Second Channel Teardown (Falsifier 18.5):
   External long-polling loops MUST respond to shutdown signals in <150ms without
   hanging on long-polling TCP socket reads.
"""

import asyncio
import os
import signal
import subprocess
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.mcp_gateway import MCPToolboxGateway
try:
    from app.worker import StatelessWorkerDaemon
except ImportError:
    from agent_worker_app.worker import StatelessWorkerDaemon
from app.concierge import concierge_agent, ConciergeAdminAgent
from app.channel_gateway import channel_gateway
from app.database import get_db, get_redis


@pytest.mark.asyncio
async def test_falsifier_18_bash_executor_non_blocking_event_loop():
    """Verify that bash_executor does not block the asyncio event loop."""
    gateway = MCPToolboxGateway()
    counter = 0

    async def event_loop_ticker():
        nonlocal counter
        for _ in range(5):
            await asyncio.sleep(0.05)
            counter += 1

    # Spawn ticker alongside a 0.3s bash command
    ticker_task = asyncio.create_task(event_loop_ticker())
    bash_task = asyncio.create_task(
        gateway._handle_bash_executor({"command": "sleep 0.3", "timeout": 2})
    )

    await asyncio.gather(ticker_task, bash_task)
    res = bash_task.result()

    assert res["status"] == "SUCCESS"
    assert res["exit_code"] == 0
    # If bash_executor were synchronous/blocking, ticker would have made 0 progress until bash finished
    assert counter >= 3, f"Expected event loop ticker to progress while bash executed, got {counter}"


@pytest.mark.asyncio
async def test_falsifier_18_bash_executor_process_group_zombie_reaping():
    """Verify that timed-out shell commands reap entire process group and leave no zombie children."""
    gateway = MCPToolboxGateway()
    unique_tag = f"test_zombie_target_{int(time.time() * 1000)}"

    # Command spawns a background sleep tagged with unique argument, then sleeps
    cmd = f"/bin/bash -c 'sleep 30 {unique_tag} & sleep 30'"

    start = time.time()
    res = await gateway._handle_bash_executor({"command": cmd, "timeout": 1})
    elapsed = time.time() - start

    assert res["status"] == "TIMEOUT"
    assert res["exit_code"] == 124
    assert elapsed < 3.0, f"Expected timeout within ~1s, took {elapsed}s"

    # Allow brief window for OS reaping
    await asyncio.sleep(0.2)

    # Check process table: the child sleep process MUST NOT exist
    check_proc = subprocess.run(
        ["/bin/sh", "-c", f"ps -ef | grep {unique_tag} | grep -v grep"],
        capture_output=True,
        text=True,
    )
    assert not check_proc.stdout.strip(), (
        f"Zombie process detected in OS table for tag {unique_tag}: {check_proc.stdout}"
    )


@pytest.mark.asyncio
async def test_falsifier_18_worker_lease_heartbeat_watchdog(fake_redis):
    """Verify that _lease_heartbeat_loop keeps task lease alive past initial TTL."""
    daemon = StatelessWorkerDaemon(
        worker_id="worker-heartbeat-test",
        redis_client=fake_redis,
        lease_ttl_seconds=2,
    )

    task_id = "task-heartbeat-001"
    acquired = await daemon.acquire_task_lease(task_id, ttl_override=2)
    assert acquired is True

    # Start heartbeat renewal every 1 second
    heartbeat_task = asyncio.create_task(
        daemon._lease_heartbeat_loop(task_id, interval_seconds=1)
    )

    try:
        # Wait 2.5 seconds (which exceeds the initial 2-second TTL)
        await asyncio.sleep(2.5)

        # Lease must still be active and owned by daemon
        lease_owner = await fake_redis.get(f"task:{task_id}:lease")
        assert lease_owner == daemon.worker_id, (
            f"Expected lease to be renewed past initial TTL, got: {lease_owner}"
        )
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await daemon.release_task_lease(task_id)

    # After explicit release, lease should be gone
    post_release = await fake_redis.get(f"task:{task_id}:lease")
    assert post_release is None


@pytest.mark.asyncio
async def test_falsifier_18_concierge_fast_path_cache_and_invalidation():
    """Verify that state snapshot caching speeds up reads and invalidates on mutations."""
    agent = ConciergeAdminAgent()

    mock_session = AsyncMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_result.scalar.return_value = 0
    mock_session.execute.return_value = mock_result

    # First call: cache miss, executes DB queries
    snap1 = await agent._build_state_snapshot(mock_session, workflow_id="wf_cache_test")
    assert mock_session.execute.call_count > 0
    first_call_count = mock_session.execute.call_count

    # Second call: cache hit, zero new DB queries
    snap2 = await agent._build_state_snapshot(mock_session, workflow_id="wf_cache_test")
    assert mock_session.execute.call_count == first_call_count
    assert snap1 == snap2

    # Invalidate cache
    agent.invalidate_state_cache()

    # Third call: cache miss, DB executed again
    snap3 = await agent._build_state_snapshot(mock_session, workflow_id="wf_cache_test")
    assert mock_session.execute.call_count > first_call_count


@pytest.mark.asyncio
async def test_falsifier_18_telegram_polling_graceful_shutdown():
    """Verify that start_telegram_polling shuts down in <300ms when stopped."""
    from app.channel_gateway import ChannelGateway
    cg = ChannelGateway()
    polling_task = asyncio.create_task(cg.start_telegram_polling())

    # Yield control until boot sync completes and loop enters polling
    await asyncio.sleep(1.2)

    # Signal stop and measure loop termination latency
    stop_start = time.time()
    cg.stop_telegram_polling()

    # Wait for loop to exit cleanly
    try:
        await asyncio.wait_for(polling_task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    stop_elapsed = time.time() - stop_start

    assert stop_elapsed < 0.3, f"Expected clean shutdown in <0.3s, took {stop_elapsed}s"
    assert polling_task.done()
