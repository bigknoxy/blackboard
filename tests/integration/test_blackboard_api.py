import json
import pytest
from app.main import ws_manager
from app.schemas import BlackboardEntryCreate, SteeringCreate
from app.workflow_engine import REDIS_EVENTS_CHANNEL, workflow_engine


class DummyWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent_messages = []

    async def accept(self):
        self.accepted = True

    async def send_text(self, text: str):
        self.sent_messages.append(text)


@pytest.mark.asyncio
async def test_websocket_broadcast_and_steering_event(fake_redis, monkeypatch):
    monkeypatch.setattr("app.workflow_engine.get_redis", lambda: fake_redis)

    ws1 = DummyWebSocket()
    ws2 = DummyWebSocket()
    await ws_manager.connect(ws1)
    await ws_manager.connect(ws2)

    steering_payload = SteeringCreate(
        workflow_id="code_review_pipeline",
        task_id="steer-task-1",
        agent_id="operator",
        content="Enforce mobile-first 44x44px touch targets across all cockpit controls.",
        target_agent_id="coder",
    )

    event_payload = {
        "event": "blackboard.steering_injected",
        "workflow_id": steering_payload.workflow_id,
        "agent_id": steering_payload.agent_id,
        "target_agent_id": steering_payload.target_agent_id,
        "entry_type": "steering",
        "content": steering_payload.content,
    }
    await workflow_engine._publish_event(event_payload)
    await ws_manager.broadcast(json.dumps(event_payload))

    assert len(fake_redis.published_events) == 1
    channel, raw_msg = fake_redis.published_events[0]
    assert channel == REDIS_EVENTS_CHANNEL
    parsed = json.loads(raw_msg)
    assert parsed["entry_type"] == "steering"
    assert "44x44px" in parsed["content"]

    assert len(ws1.sent_messages) == 1
    assert len(ws2.sent_messages) == 1

    ws_manager.disconnect(ws1)
    ws_manager.disconnect(ws2)


@pytest.mark.asyncio
async def test_dag_api_endpoints_and_tool_chain(db_session):
    import uuid
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.database import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        wf_id = f"test-dag-api-{uuid.uuid4().hex[:8]}"

        # 1. Create root node
        res1 = await client.post(
            "/api/v1/blackboard/nodes",
            json={
                "workflow_id": wf_id,
                "author_agent_id": "architect",
                "node_type": "spec",
                "payload": {"title": "API Gateway Spec"},
            },
        )
        assert res1.status_code == 201
        node1_id = res1.json()["id"]

        # 2. Create child node
        res2 = await client.post(
            "/api/v1/blackboard/nodes",
            json={
                "workflow_id": wf_id,
                "author_agent_id": "coder",
                "node_type": "code",
                "payload": {"code": "def route(): pass"},
                "parent_node_id": node1_id,
                "relation_type": "implements",
            },
        )
        assert res2.status_code == 201
        node2_id = res2.json()["id"]

        # 3. Test cycle detection on edge creation: node2 -> node1 creates cycle
        res_cycle = await client.post(
            "/api/v1/blackboard/edges",
            json={
                "source_id": node2_id,
                "target_id": node1_id,
                "relation_type": "revises",
            },
        )
        assert res_cycle.status_code == 400
        assert "cycle" in res_cycle.json()["detail"].lower()

        # 4. Fetch full graph
        graph_res = await client.get(f"/api/v1/blackboard/graph/{wf_id}")
        assert graph_res.status_code == 200
        graph_data = graph_res.json()
        assert len(graph_data["nodes"]) == 2
        assert len(graph_data["edges"]) == 1
        assert len(graph_data["active_lineage"]) == 2

        # 5. Test tool chain pathfinding endpoint
        tc_res = await client.get(
            "/api/v1/mcp/tool-chain?source_type=query_keyword&target_type=security_audit_report"
        )
        assert tc_res.status_code == 200
        tc_data = tc_res.json()
        assert tc_data["resolved"] is True
        assert tc_data["tool_chain"] == [
            "repo_search",
            "ast_complexity_analyzer",
            "static_security_scanner",
        ]
    app.dependency_overrides.clear()

