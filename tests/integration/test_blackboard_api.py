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
