"""
Archon-style Orchestrator Routing & Eval Suite (`test_orchestrator_routing_and_eval.py`).
Covers:
  1. Command parsing (explicit `run <workflow_id>`, `steer:`, `prune blackboard`)
  2. Intent routing via structured classifier (`trigger_workflow`, `inspect_state`, `NEEDS_CLARIFICATION`)
  3. Approval gate replies (1-tap callback resolution + policy graduation)
  4. Direct-answer fallbacks & deterministic workflow ACK
"""

from unittest.mock import AsyncMock, patch
import pytest

from app.channel_gateway import ChannelGateway
from app.models import ApprovalPolicyModel, HITLTicketModel
from tests.orchestrator_harness import OrchestratorTestHarness


@pytest.mark.asyncio
async def test_orchestrator_eval_command_parsing_and_deterministic_ack(db_session):
    """Command parsing: explicit `Run <workflow_id>`, `steer:`, and `prune` execute deterministically with crisp ACK."""
    harness = OrchestratorTestHarness(db_session)
    await harness.seed_default_workflows()

    # Explicit workflow command even if main LLM tries to return stale prose
    resp = await harness.send(
        "Run code_review_pipeline to review and test the channel gateway changes",
        llm_reply="I see code_review_pipeline was run earlier...",
    )
    trig = harness.assert_workflow_triggered(resp, "code_review_pipeline")
    assert f"Started **code_review_pipeline**" in resp.reply
    assert str(trig["run_id"])[:8] in resp.reply

    # Steering command
    resp_steer = await harness.send("steer: Prioritize pytest coverage on channel_gateway")
    assert any(t.get("tool") == "inject_steering" for t in resp_steer.tool_calls_executed)

    # Prune command
    resp_prune = await harness.send("prune blackboard", workflow_id="code_review_pipeline")
    assert any(t.get("tool") == "prune_blackboard" for t in resp_prune.tool_calls_executed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,classifier_out,expected_wf",
    [
        (
            "Please have the team audit the authentication changes and run tests",
            {"intent": "trigger_workflow", "workflow_id": "code_review_pipeline", "confidence": 0.91},
            "code_review_pipeline",
        ),
        (
            "Investigate best practices for distributed caching and plan next steps",
            {"intent": "trigger_workflow", "workflow_id": "research_plan_verify_pipeline", "confidence": 0.86},
            "research_plan_verify_pipeline",
        ),
    ],
)
async def test_orchestrator_eval_intent_routing(db_session, prompt, classifier_out, expected_wf):
    """Intent routing eval: natural-language requests route to the classified workflow."""
    harness = OrchestratorTestHarness(db_session)
    await harness.seed_default_workflows()

    resp = await harness.send(prompt, classifier_decision=classifier_out, llm_reply="Done.")
    trig = harness.assert_workflow_triggered(resp, expected_wf)
    assert trig["routed_by"] == "intent_classifier"


@pytest.mark.asyncio
async def test_orchestrator_eval_ambiguous_clarification_and_direct_fallback(db_session):
    """Ambiguous mid-confidence asks for clarification; low-confidence chat falls back cleanly."""
    harness = OrchestratorTestHarness(db_session)
    await harness.seed_default_workflows()

    resp_ambig = await harness.send(
        "Maybe check on the auth module later",
        classifier_decision={"intent": "trigger_workflow", "workflow_id": "code_review_pipeline", "confidence": 0.55},
    )
    harness.assert_clarification_asked(resp_ambig)

    resp_chat = await harness.send(
        "Hello! Explain what the Blackboard DAG does.",
        classifier_decision={"intent": "chat", "workflow_id": None, "confidence": 0.95},
        llm_reply="The Blackboard DAG tracks causal lineage across agent turns.",
    )
    harness.assert_direct_chat_fallback(resp_chat, "causal lineage")


@pytest.mark.asyncio
async def test_orchestrator_eval_approval_gate_policy_graduation(db_session, fake_redis):
    """Approval gate routing: `Always Allow` resolves ticket, executes tool, and persists ApprovalPolicyModel."""
    from sqlalchemy import select
    from app.schemas import ToolInvokeResponse

    harness = OrchestratorTestHarness(db_session)
    await harness.seed_default_workflows()
    ticket = await harness.create_pending_ticket(command="pytest -q")

    await fake_redis.set("channel:telegram:token", "123:ABC")
    await fake_redis.set("channel:telegram:chat_id", "99999")
    await fake_redis.set("channel:telegram:enabled", "true")

    gw = ChannelGateway()
    callback = {
        "id": "cb-eval-1",
        "from": {"username": "josh"},
        "data": f"hitl:{ticket.id}:APPROVED:tool",
        "message": {"message_id": 42, "chat": {"id": 99999}},
    }

    with patch("app.channel_gateway.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch.object(gw.telegram, "_answer_callback", new=AsyncMock()), \
         patch.object(gw.telegram, "_edit_message_text", new=AsyncMock()) as edit_mock, \
         patch(
             "app.mcp_gateway.mcp_gateway.invoke_tool",
             new=AsyncMock(return_value=ToolInvokeResponse(status="EXECUTED", result={"exit_code": 0, "stdout": "19 passed"})),
         ):
        res = await gw.telegram.handle_callback_query(callback, db_session)

    assert res["status"] == "RESOLVED"
    assert res["policy_mode"] == "tool"
    refreshed = await db_session.get(HITLTicketModel, ticket.id)
    assert refreshed.status == "APPROVED"

    pol_res = await db_session.execute(
        select(ApprovalPolicyModel).where(ApprovalPolicyModel.tool_id == "bash_executor")
    )
    assert pol_res.scalars().first() is not None
    edited_text = edit_mock.call_args.args[2]
    assert "Auto-Approval Policy Active" in edited_text
    assert "19 passed" in edited_text


@pytest.mark.asyncio
async def test_search_memories_float32_json_serialization(db_session):
    """Fix 1: search_memories must cast numpy.float32 embeddings & score to Python float so json.dumps succeeds."""
    import json
    import numpy as np
    from app.concierge import concierge_agent
    from app.models import AgentMemoryModel, AgentModel

    if not await db_session.get(AgentModel, "researcher"):
        db_session.add(
            AgentModel(
                id="researcher",
                name="Researcher",
                role="Research",
                system_prompt="Research",
                allowed_tools=["web_search"],
            )
        )
        await db_session.commit()

    vec = np.array([0.05] * 768, dtype=np.float32)
    mem = AgentMemoryModel(
        agent_id="researcher",
        memory_type="semantic",
        content="pgvector float32 serialization regression test memory",
        embedding=vec.tolist(),
    )
    db_session.add(mem)
    await db_session.commit()

    try:
        res = await concierge_agent._execute_concierge_tool(
            db_session,
            "search_memories",
            {"query": "pgvector float32 serialization", "agent_id": "researcher"},
        )
        assert res["status"] == "RETRIEVED"
        assert res["count"] >= 1
        assert isinstance(res["results"][0]["score"], float)
        serialized = json.dumps(res)
        assert "pgvector float32 serialization" in serialized
    finally:
        await db_session.delete(mem)
        await db_session.commit()


@pytest.mark.asyncio
async def test_decision_model_toggle_and_gemini_fallback(fake_redis):
    """Fix 3: Decision model toggle ON/OFF, Gemini Decisions API parsing, 429 local fallback, and /llm/config persistence."""
    from unittest.mock import MagicMock
    from httpx import AsyncClient, ASGITransport
    from app.concierge import concierge_agent
    from app.main import app

    # 1. Toggle OFF bypasses classifier without making any HTTP calls
    await fake_redis.set("llm:decision:enabled", "false")

    class NoNetworkClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("Classifier must not make network calls when disabled")

    with patch("app.concierge.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch("app.concierge.httpx.AsyncClient", NoNetworkClient):
        disabled_res = await concierge_agent._classify_intent(
            "Run code_review_pipeline", ["code_review_pipeline"]
        )
    assert disabled_res["intent"] == "chat"
    assert disabled_res["confidence"] == 0.0
    assert disabled_res["reason"] == "disabled"

    # 2. Toggle ON + Gemini Decisions API success
    await fake_redis.set("llm:decision:enabled", "true")
    await fake_redis.set("llm:decision:provider", "gemini")
    await fake_redis.set("llm:decision:gemini_api_key", "test-gem-key")
    await fake_redis.set("llm:decision:gemini_model", "gemini-decision-1")

    gemini_calls = []

    class GeminiSuccessClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, **kwargs):
            gemini_calls.append((url, json))
            return MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={
                        "decisions": [
                            {"questionId": "intent", "choiceId": "trigger_workflow", "confidence": 0.93},
                            {"questionId": "workflow_id", "choiceId": "research_plan_verify_pipeline"},
                        ]
                    }
                ),
            )

    with patch("app.concierge.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch("app.concierge.httpx.AsyncClient", GeminiSuccessClient):
        gem_res = await concierge_agent._classify_intent(
            "Research caching strategies",
            ["code_review_pipeline", "research_plan_verify_pipeline"],
        )
    assert len(gemini_calls) == 1
    assert "generativelanguage.googleapis.com/v1beta/decisions?key=test-gem-key" in gemini_calls[0][0]
    assert gem_res["intent"] == "trigger_workflow"
    assert gem_res["workflow_id"] == "research_plan_verify_pipeline"
    assert gem_res["confidence"] == 0.93

    # 3. Gemini 429 sets cooldown and falls back to local LAN classifier endpoint
    fallback_urls = []

    class Gemini429FallbackClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, **kwargs):
            fallback_urls.append(url)
            if "generativelanguage.googleapis.com" in url:
                return MagicMock(status_code=429, text="rate limited")
            return MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"intent": "inspect_state", "workflow_id": null, "confidence": 0.88}'
                                }
                            }
                        ]
                    }
                ),
            )

    with patch("app.concierge.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch("app.concierge.httpx.AsyncClient", Gemini429FallbackClient):
        fb_res = await concierge_agent._classify_intent(
            "What is on the blackboard right now?", ["code_review_pipeline"]
        )
    assert any("generativelanguage.googleapis.com" in u for u in fallback_urls)
    assert any("/chat/completions" in u for u in fallback_urls)
    assert await fake_redis.get("llm:cooldown:gemini_decision") == "429"
    assert fb_res["intent"] == "inspect_state"
    assert fb_res["confidence"] == 0.88

    # 4. /api/v1/llm/config and /api/v1/llm/status persist the decision model toggle
    with patch("app.main.get_redis", new=AsyncMock(return_value=fake_redis)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            post_res = await client.post(
                "/api/v1/llm/config",
                json={
                    "decision_model_enabled": False,
                    "decision_model_provider": "gemini",
                    "gemini_api_key": "gk-test-key-9999",
                    "gemini_decision_model": "gemini-decision-1",
                },
            )
            assert post_res.status_code == 200
            post_data = post_res.json()
            assert post_data["decision_model"]["enabled"] is False
            assert post_data["decision_model"]["provider"] == "gemini"
            assert post_data["decision_model"]["gemini_api_key_configured"] is True

            get_res = await client.get("/api/v1/llm/status")
            assert get_res.status_code == 200
            get_data = get_res.json()
            assert get_data["decision_model"]["enabled"] is False
            assert get_data["decision_model"]["provider"] == "gemini"
            assert isinstance(get_data["decision_model"]["local_endpoints"], list)
            assert len(get_data["decision_model"]["local_endpoints"]) >= 1
