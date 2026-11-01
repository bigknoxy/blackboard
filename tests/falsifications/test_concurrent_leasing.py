import asyncio
import json
import pytest
from app.worker import StatelessWorkerDaemon


@pytest.mark.asyncio
async def test_falsifier_1_concurrent_lease_theft_prevented(fake_redis):
    """
    Falsifier 1: Concurrent Lease Theft (Blackboard Race Condition)
    Hypothesis: "Two workers listening to the same Redis stream will never claim
    and execute the same subtask concurrently."
    Test: Spawn 10 concurrent async worker tasks attempting to acquire the lease
    for the exact same `task_id` simultaneously using Redis `SET NX EX`.
    Assertion: Exactly ONE worker acquires the lease; the remaining 9 fail gracefully,
    register a lease conflict, and do not trigger downstream LLM hydration or tool execution.
    """
    task_id = "task-race-condition-999"
    task_payload = {
        "task_id": task_id,
        "workflow_id": "code_review_pipeline",
        "agent_id": "coder",
        "entry_type": "code",
        "prompt": "Concurrent subtask execution test",
    }

    downstream_hydrations = []
    workers = [
        StatelessWorkerDaemon(
            worker_id=f"worker-node-{i}",
            redis_client=fake_redis,
            release_on_finish=False,
        )
        for i in range(10)
    ]

    for w in workers:
        async def _mock_exec(t, wid=w.worker_id):
            downstream_hydrations.append(wid)
            await asyncio.sleep(0.02)
            return {"status": "COMPLETED", "worker_id": wid}

        w.runner.execute_task = _mock_exec

    # Fire all 10 workers simultaneously against the exact same task_id
    results = await asyncio.gather(
        *[
            w._handle_stream_message(
                f"1700000000-{idx}", {"payload": json.dumps(task_payload)}
            )
            for idx, w in enumerate(workers)
        ]
    )

    winners = [r for r in results if r and r.get("lease_acquired") is True]
    losers = [r for r in results if r and r.get("lease_acquired") is False]

    # Exactly 1 worker acquired the lease and triggered LLM hydration
    assert len(winners) == 1
    assert len(losers) == 9
    assert len(downstream_hydrations) == 1
    assert len(fake_redis.lease_conflicts) == 9
    assert sum(w.lease_conflict_count for w in workers) == 9
