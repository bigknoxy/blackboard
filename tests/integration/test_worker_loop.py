import json
import pytest
from app.worker import StatelessWorkerDaemon


@pytest.mark.asyncio
async def test_worker_loop_claim_hydrate_and_commit(fake_redis, monkeypatch):
    daemon = StatelessWorkerDaemon(
        worker_id="worker-test-1", redis_client=fake_redis
    )

    executed_tasks = []

    async def mock_execute_task(task):
        executed_tasks.append(task)
        return {
            "status": "COMPLETED",
            "task_id": task["task_id"],
            "agent_id": task["agent_id"],
            "reflection_memory_id": "mem-123",
        }

    monkeypatch.setattr(daemon.runner, "execute_task", mock_execute_task)

    await daemon.ensure_consumer_group()
    task_payload = {
        "task_id": "task-e2e-101",
        "workflow_id": "code_review_pipeline",
        "run_id": "run-001",
        "node_id": "stage_code",
        "node_name": "Implement Candidate Patch",
        "agent_id": "coder",
        "entry_type": "code",
        "prompt": "Implement responsive mobile navigation bar.",
    }
    msg_id = await fake_redis.xadd(
        "stream:tasks", {"payload": json.dumps(task_payload)}
    )

    streams = await fake_redis.xreadgroup(
        groupname="agent_worker_pool",
        consumername=daemon.worker_id,
        streams={"stream:tasks": ">"},
        count=1,
    )
    assert len(streams) == 1
    _sname, messages = streams[0]
    read_msg_id, fields = messages[0]
    assert read_msg_id == msg_id

    await daemon._handle_stream_message(read_msg_id, fields)

    # Verify task was executed once and lease was cleanly released afterward
    assert len(executed_tasks) == 1
    assert executed_tasks[0]["task_id"] == "task-e2e-101"
    assert await fake_redis.get("task:task-e2e-101:lease") is None
