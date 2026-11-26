"""
Deterministic E2E Human QA Regression Test Suite

Covers all 4 real-human chat journeys discovered during live testing.
All tests are isolated with db_session and fake_redis so they never mutate live queues.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.database import get_db, get_redis
from app.models import AgentMemoryModel, AgentModel, WorkflowModel, HITLTicketModel, ApprovalPolicyModel
from sqlalchemy import delete


@pytest.fixture
async def client(db_session, fake_redis):
    """Per-test client isolated with db_session and fake_redis (never mutates live Redis queues)."""
    from unittest.mock import AsyncMock, patch

    app.dependency_overrides[get_db] = lambda: db_session
    with patch("app.database.get_redis", return_value=fake_redis), \
         patch("app.main.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch("app.concierge.get_redis", return_value=fake_redis), \
         patch("app.workflow_engine.get_redis", return_value=fake_redis), \
         patch("app.mcp_gateway.get_redis", return_value=fake_redis):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await c.get("/api/v1/tools")
            yield c

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_scenario_1_research_shopping_list(db_session, client, fake_redis):
    """
    Scenario 1: Research & Shopping List

    Explicit research_plan_verify_pipeline trigger returns crisp ACK with exactly
    1 trigger_workflow call, and end-to-end summary grounding rewrites hallucinated
    amazon.com/dp/ ASINs into distinct amazon.com/s?k=... links while preserving
    verified URLs.
    """
    from app.models import WorkflowModel

    chat_res = await client.post(
        "/api/v1/chat",
        json={
            "message": "Run research_plan_verify_pipeline",
            "workflow_id": "research_plan_verify_pipeline",
        },
    )
    assert chat_res.status_code == 200
    data = chat_res.json()

    # Action receipt MUST include exactly 1 trigger_workflow call and crisp ACK
    tool_calls = data.get("tool_calls_executed", [])
    trigger_calls = [tc for tc in tool_calls if tc.get("tool") == "trigger_workflow"]
    assert len(trigger_calls) == 1, (
        f"Expected exactly 1 trigger_workflow call, got {len(trigger_calls)}: {tool_calls}"
    )
    reply = data.get("reply", "")
    assert "research_plan_verify_pipeline" in reply

    # Verify centralized web grounding rewrites hallucinated amazon.com/dp/ ASINs cleanly
    from app.web_grounding import sanitize_and_ground_links

    sample_deliverable = (
        "- [NodeMCU-32S on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)\n"
        "- [ESP32 Official Guide](https://docs.espressif.com/esp32)"
    )
    grounded, report = sanitize_and_ground_links(
        sample_deliverable,
        verified_urls=["https://docs.espressif.com/esp32"],
    )
    assert "amazon.com/dp/" not in grounded
    assert "amazon.com/s?k=NodeMCU-32S" in grounded
    assert "https://docs.espressif.com/esp32" in grounded
    assert report["rewritten_count"] == 1
    assert report["verified_count"] == 1


@pytest.mark.asyncio
async def test_scenario_2_code_review_pipeline(db_session, client, fake_redis):
    """
    Scenario 2: Code Review & Verification

    Explicit code_review_pipeline trigger returns crisp ACK with exactly 1
    trigger_workflow call (no duplicate run).
    """
    chat_res = await client.post(
        "/api/v1/chat",
        json={
            "message": "Run code_review_pipeline",
            "workflow_id": "code_review_pipeline",
        },
    )
    assert chat_res.status_code == 200
    data = chat_res.json()

    # Should have exactly 1 trigger_workflow call (no duplicate run)
    tool_calls = data.get("tool_calls_executed", [])
    trigger_calls = [tc for tc in tool_calls if tc.get("tool") == "trigger_workflow"]
    assert len(trigger_calls) == 1, (
        f"Expected exactly 1 trigger_workflow call, got {len(trigger_calls)}: {tool_calls}"
    )

    reply = data.get("reply", "")
    assert "code_review_pipeline" in reply


@pytest.mark.asyncio
async def test_scenario_3_memory_store_recall(db_session, client, fake_redis):
    """
    Scenario 3: Operator Memory Store & Recall

    'Remember that my preferred ESP32 supplier is Adafruit and my homelab subnet
    is 192.168.8.x' stores 1 memory; follow-up 'What do you remember about my
    ESP32 supplier and homelab subnet?' does NOT store a duplicate question memory
    and accurately retrieves 'Adafruit' and '192.168.8.x'.
    """
    import uuid
    from app.models import AgentMemoryModel

    tag = uuid.uuid4().hex[:6]
    supplier = f"Adafruit-{tag}"
    subnet = f"192.168.8.{tag[:2]}"

    # Store memory
    chat_res = await client.post(
        "/api/v1/chat",
        json={
            "message": f"Remember that my preferred ESP32 supplier is {supplier} and my homelab subnet is {subnet}",
            "workflow_id": "code_review_pipeline",
        },
    )
    assert chat_res.status_code == 200
    data = chat_res.json()

    # Action receipt MUST include store_memory
    tool_calls = data.get("tool_calls_executed", [])
    tool_names = [tc.get("tool") for tc in tool_calls]
    assert "store_memory" in tool_names, (
        f"Expected store_memory in tool calls, got: {tool_calls}"
    )

    # Verify memory actually persisted in PostgreSQL
    mem_stmt = select(AgentMemoryModel).where(
        AgentMemoryModel.content.ilike(f"%{supplier}%")
    )
    saved_mems = (await db_session.execute(mem_stmt)).scalars().all()
    assert len(saved_mems) == 1, f"Expected exactly 1 memory, got {len(saved_mems)}"
    saved_mem = saved_mems[0]
    assert supplier in saved_mem.content
    assert subnet in saved_mem.content
    assert saved_mem.embedding is not None
    assert len(saved_mem.embedding) == 768

    # Follow-up recall question MUST NOT trigger store_memory and MUST retrieve stored facts
    chat_res2 = await client.post(
        "/api/v1/chat",
        json={
            "message": f"What do you remember about my ESP32 supplier {supplier} and homelab subnet?",
            "workflow_id": "code_review_pipeline",
        },
    )
    assert chat_res2.status_code == 200
    data2 = chat_res2.json()

    tool_calls2 = data2.get("tool_calls_executed", [])
    tool_names2 = [tc.get("tool") for tc in tool_calls2]
    assert "store_memory" not in tool_names2, (
        f"store_memory should not be triggered for recall question, got: {tool_calls2}"
    )
    assert any(t in tool_names2 for t in ("list_memories", "search_memories")), (
        f"Expected list_memories or search_memories, got: {tool_calls2}"
    )

    # Verify stored fact appears in either reply or retrieved memory breakdown
    reply = data2.get("reply", "")
    retrieved_blob = str(tool_calls2)
    assert supplier in reply or supplier in retrieved_blob
    assert subnet in reply or subnet in retrieved_blob


@pytest.mark.asyncio
async def test_scenario_4_hitl_shell_toggle(db_session, client, fake_redis):
    """
    Scenario 4: HITL Gated Shell Command & Decision Model Toggle

    'Run uptime on the server' returns HTTP 200 (not 500!) with a PENDING HITL
    ticket reference, and POST /api/v1/llm/config toggles decision_model_enabled
    False -> True cleanly.
    """
    from app.models import ApprovalPolicyModel

    # Seed tools catalog if empty in test session
    await client.get("/api/v1/tools")

    # Test 4a: 'Run uptime on the server' returns HTTP 200 with PENDING HITL ticket
    invoke_res = await client.post(
        "/api/v1/tools/invoke",
        json={
            "agent_id": "operator",
            "workflow_id": "code_review_pipeline",
            "task_id": "task_uptime_test",
            "tool_id": "bash_executor",
            "arguments": {"command": "uptime"},
            "bypass_approval": False,
        },
    )
    assert invoke_res.status_code == 200
    invoke_data = invoke_res.json()
    assert invoke_data["status"] == "AWAITING_APPROVAL", (
        f"Expected AWAITING_APPROVAL, got {invoke_data['status']}"
    )
    ticket_id = invoke_data["hitl_ticket_id"]
    assert ticket_id is not None, "hitl_ticket_id should not be None"

    # Verify the ticket exists in the database
    ticket_stmt = select(HITLTicketModel).where(HITLTicketModel.id == ticket_id)
    ticket_res = await db_session.execute(ticket_stmt)
    ticket = ticket_res.scalar_one_or_none()
    assert ticket is not None, "HITL ticket should exist in database"
    assert ticket.status == "PENDING", "Ticket status should be PENDING"

    # Test 4b: Chat request 'Run uptime on the server' MUST return HTTP 200 (never 500!)
    chat_shell = await client.post(
        "/api/v1/chat",
        json={"message": "Run uptime on the server"},
    )
    assert chat_shell.status_code == 200

    # Test 4c: POST /api/v1/llm/config toggles decision_model_enabled False -> True
    config_res = await client.post(
        "/api/v1/llm/config",
        json={"decision_model_enabled": False},
    )
    assert config_res.status_code == 200
    config_data = config_res.json()
    assert config_data.get("decision_model", {}).get("enabled") is False, (
        f"Expected decision_model.enabled=False, got {config_data}"
    )

    config_res2 = await client.post(
        "/api/v1/llm/config",
        json={"decision_model_enabled": True},
    )
    assert config_res2.status_code == 200
    config_data2 = config_res2.json()
    assert config_data2.get("decision_model", {}).get("enabled") is True, (
        f"Expected decision_model.enabled=True, got {config_data2}"
    )

    # Also verify that after toggling True, bash_executor calls return hitl_ticket_id
    # (the hitl_ticket_id fix from Fix 1)
    invoke_res2 = await client.post(
        "/api/v1/tools/invoke",
        json={
            "agent_id": "operator",
            "workflow_id": "code_review_pipeline",
            "task_id": "task_uptime_test2",
            "tool_id": "bash_executor",
            "arguments": {"command": "echo 'test'"},
            "bypass_approval": False,
        },
    )
    assert invoke_res2.status_code == 200
    invoke_data2 = invoke_res2.json()
    assert invoke_data2["status"] == "AWAITING_APPROVAL"
    # The response should use hitl_ticket_id (not ticket_id)
    assert "hitl_ticket_id" in invoke_data2, (
        "Response should contain hitl_ticket_id key"
    )