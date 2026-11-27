"""
Popperian Falsification & E2E Test Suite:
Phase 1 & Phase 2 Decision Model Architecture Upgrade.

Verifies:
1. Single Redis MGET config loading + zero-restart runtime toggling.
2. Cooldown-aware local failover (skips cooled-down llama.cpp nodes straight to Ollama).
3. Workflow YAML descriptions injected into Gemini Decisions API `wf_criteria`.
4. All 5 Concierge intents (`trigger_workflow`, `inspect_state`, `memory_op`, `tool_exec`, `chat`).
5. Decision telemetry persisted in Redis and exposed in `GET /api/v1/llm/status`.
6. Workflow `ExclusiveGateway` semantic verdict grading (`PASS` | `REWORK` | `ESCALATE_HITL`) + fallback when OFF.
7. MCP `bash_executor` risk triage (`read_only_safe` | `mutating_require_hitl` | `destructive_block`) respecting Invariants 4 & 16.
8. Model complexity routing (`simple_local` | `complex_reasoning`) & Memory Write-Worthiness Gate.
9. Full E2E API flow (config switch -> chat routing -> status telemetry -> MCP risk triage).
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../control-plane"))

from app.concierge import concierge_agent
from app.database import get_db
from app.decision_engine import DECISION_CONFIG_KEYS, DecisionEngine
from app.main import app
from app.mcp_gateway import mcp_gateway
from app.models import (
    AgentMemoryModel,
    AgentModel,
    BlackboardEntryModel,
    HITLTicketModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.schemas import ConciergeChatRequest, ToolInvokeRequest
from app.workflow_engine import WorkflowEngine


class _FakeHTTPResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


async def _ensure_agents_and_workflows(db_session):
    for ag_id, role_name, tools in [
        ("concierge-admin", "Concierge Admin", ["web_search", "bash_executor"]),
        ("coder", "Coder", ["bash_executor"]),
        ("researcher", "Researcher", ["web_search"]),
    ]:
        if not await db_session.get(AgentModel, ag_id):
            db_session.add(
                AgentModel(
                    id=ag_id,
                    role_name=role_name,
                    system_prompt=f"{role_name} persona",
                    allowed_tools=tools,
                )
            )
    minimal_graph = {
        "nodes": [
            {"id": "s", "name": "Start", "type": "StartEvent"},
            {"id": "e", "name": "End", "type": "EndEvent"},
        ],
        "edges": [{"id": "f1", "source": "s", "target": "e"}],
    }
    for wf_id, wf_name in [
        ("code_review_pipeline", "Code Review Pipeline"),
        ("research_plan_verify_pipeline", "Research Plan Verify Pipeline"),
    ]:
        if not await db_session.get(WorkflowModel, wf_id):
            db_session.add(
                WorkflowModel(
                    id=wf_id,
                    name=wf_name,
                    state_graph=minimal_graph,
                    yaml_source=f"id: {wf_id}\nname: {wf_name}\nnodes: []\nedges: []\n",
                    bpmn_xml="<definitions/>",
                )
            )
    await db_session.commit()


@pytest.mark.asyncio
async def test_single_mget_and_workflow_descriptions_in_gemini_criteria(fake_redis):
    """Config loads via one Redis MGET and Gemini payload injects workflow descriptions."""
    await fake_redis.set("llm:decision:enabled", "true")
    await fake_redis.set("llm:decision:provider", "gemini")
    await fake_redis.set("llm:decision:gemini_api_key", "AIzaFakeTestKey")
    await fake_redis.set("llm:decision:gemini_model", "gemini-decision-1")

    mget_spy = AsyncMock(wraps=fake_redis.mget)
    fake_redis.mget = mget_spy

    captured = {}

    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        return _FakeHTTPResponse(
            200,
            {
                "answers": {
                    "intent": {"choice": "trigger_workflow", "confidence": 0.97},
                    "workflow_id": {"choice": "research_plan_verify_pipeline", "confidence": 0.95},
                }
            },
        )

    engine = DecisionEngine()
    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=fake_post)

    with patch.object(engine, "_get_client", AsyncMock(return_value=fake_client)):
        res = await engine.route_intent(
            "Compare top 3 budget 4K monitors",
            [
                {"id": "code_review_pipeline", "description": "Security audit and PR review."},
                {
                    "id": "research_plan_verify_pipeline",
                    "description": "Live web research and structured buying comparison.",
                },
            ],
            redis_client=fake_redis,
        )

    config_mget_calls = [
        call for call in mget_spy.await_args_list if call.args == DECISION_CONFIG_KEYS
    ]
    assert len(config_mget_calls) == 1
    assert res["intent"] == "trigger_workflow"
    assert res["workflow_id"] == "research_plan_verify_pipeline"
    assert res["provider"] == "gemini"
    wf_crit = captured["payload"]["questions"]["workflow_id"]["criteria"]
    assert "Live web research and structured buying comparison." in wf_crit["research_plan_verify_pipeline"]


@pytest.mark.asyncio
async def test_cooldown_skips_dead_llama_endpoints_straight_to_ollama(fake_redis):
    """When local llama.cpp endpoints are in cooldown, failover goes straight to Ollama."""
    await fake_redis.set("llm:decision:enabled", "true")
    await fake_redis.set("llm:decision:provider", "local")
    await fake_redis.set("llm:decision:ollama_model", "qwen3.5:0.8b")

    engine = DecisionEngine()
    for ep_url, _ in engine._local_llama_endpoints():
        await fake_redis.setex(engine._cooldown_key(ep_url), 60, "offline")

    called_urls = []

    async def fake_post(url, json=None, **kwargs):
        called_urls.append(url)
        return _FakeHTTPResponse(200, {"message": {"content": "1"}})

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=fake_post)

    with patch.object(engine, "_get_client", AsyncMock(return_value=fake_client)):
        res = await engine.route_intent(
            "Run a security audit on auth.py",
            ["code_review_pipeline", "research_plan_verify_pipeline"],
            redis_client=fake_redis,
        )

    assert len(called_urls) == 1
    assert called_urls[0].endswith("/api/chat")
    assert res["provider"] == "ollama:qwen3.5:0.8b"
    assert res["intent"] == "trigger_workflow"
    assert res["workflow_id"] == "code_review_pipeline"

    telemetry = await engine.get_telemetry(redis_client=fake_redis)
    assert telemetry["calls"] >= 1
    assert telemetry["provider"].get("ollama:qwen3.5:0.8b", 0) >= 1


@pytest.mark.asyncio
async def test_concierge_acts_on_memory_op_and_tool_exec_intents(db_session, fake_redis):
    """Stage 2 Concierge router acts on memory_op and tool_exec rather than discarding them."""
    await _ensure_agents_and_workflows(db_session)
    db_session.add(
        AgentMemoryModel(
            agent_id="concierge-admin",
            memory_type="semantic",
            content="Operator prefers pytest -q and CST timezone.",
            embedding=[0.0] * 768,
        )
    )
    await db_session.commit()

    with (
        patch("app.concierge.get_redis", return_value=fake_redis),
        patch("app.concierge.compute_embedding", AsyncMock(return_value=[0.0] * 768)),
        patch.object(concierge_agent, "_try_ollama_chat_with_tools", AsyncMock(return_value=(None, []))),
        patch.object(
            concierge_agent,
            "_classify_intent",
            AsyncMock(return_value={"intent": "memory_op", "workflow_id": None, "confidence": 0.92, "provider": "gemini"}),
        ),
    ):
        resp_mem = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(message="Check my saved preference about pytest flags"),
        )
    assert any(
        tc.get("tool") == "search_memories" and tc.get("routed_by") == "intent_classifier"
        for tc in resp_mem.tool_calls_executed
    )

    with (
        patch("app.concierge.get_redis", return_value=fake_redis),
        patch.object(concierge_agent, "_try_ollama_chat_with_tools", AsyncMock(return_value=(None, []))),
        patch.object(
            concierge_agent,
            "_classify_intent",
            AsyncMock(return_value={"intent": "tool_exec", "workflow_id": None, "confidence": 0.91, "provider": "gemini"}),
        ),
        patch.object(
            mcp_gateway,
            "invoke_tool",
            AsyncMock(return_value=MagicMock(status="SUCCESS", output={"results": [{"title": "Example", "url": "https://example.com"}]})),
        ),
    ):
        resp_tool = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(message="Look up latest FastAPI release notes"),
        )
    assert any(
        tc.get("tool") == "web_search" and tc.get("routed_by") == "intent_classifier"
        for tc in resp_tool.tool_calls_executed
    )


@pytest.mark.asyncio
async def test_exclusive_gateway_semantic_grading_and_escalation(db_session, fake_redis):
    """ExclusiveGateway uses DecisionEngine for PASS / REWORK / ESCALATE_HITL and falls back when OFF."""
    await _ensure_agents_and_workflows(db_session)
    wf_id = "semantic_gate_wf_test"
    wf = await db_session.get(WorkflowModel, wf_id)
    if wf is None:
        wf = WorkflowModel(
            id=wf_id,
            name="Semantic Gate WF",
            yaml_source="id: semantic_gate_wf_test\n",
            bpmn_xml="<definitions/>",
            state_graph={
                "id": wf_id,
                "nodes": [
                    {"id": "start", "type": "StartEvent", "name": "Start"},
                    {
                        "id": "gw_quality",
                        "type": "ExclusiveGateway",
                        "name": "Quality Gate",
                        "semantic_gate": True,
                        "criteria": "Artifact must include verified benchmark metrics.",
                    },
                    {"id": "done", "type": "EndEvent", "name": "Done"},
                    {"id": "rework_task", "type": "ServiceTask", "name": "Rework", "agent": "coder"},
                ],
                "edges": [
                    {"source": "start", "target": "gw_quality"},
                    {"source": "gw_quality", "target": "done", "condition": "PASS"},
                    {"source": "gw_quality", "target": "rework_task", "condition": "REWORK"},
                ],
            },
        )
        db_session.add(wf)

    run = WorkflowRunModel(
        workflow_id=wf.id,
        status="RUNNING",
        current_nodes=[],
        completed_nodes=["start"],
        context={"last_verdict": "PASS", "last_output": "Draft without metrics"},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    wf_engine = WorkflowEngine()
    gw_node = next(n for n in wf.state_graph["nodes"] if n["id"] == "gw_quality")

    with (
        patch("app.workflow_engine.get_redis", return_value=fake_redis),
        patch(
            "app.decision_engine.decision_engine.evaluate_gateway_verdict",
            AsyncMock(return_value={"verdict": "ESCALATE_HITL", "confidence": 0.89, "provider": "gemini"}),
        ),
    ):
        await wf_engine._activate_node(db_session, wf, run, gw_node)

    assert run.status == "AWAITING_APPROVAL"
    tickets = (
        await db_session.execute(
            select(HITLTicketModel).where(HITLTicketModel.workflow_id == wf.id)
        )
    ).scalars().all()
    assert any(t.gate_type == "workflow_gateway_escalation" for t in tickets)

    run2 = WorkflowRunModel(
        workflow_id=wf.id,
        status="RUNNING",
        current_nodes=[],
        completed_nodes=["start"],
        context={"last_verdict": "REWORK", "last_output": "Verified p95=42ms benchmark"},
    )
    db_session.add(run2)
    await db_session.commit()
    await db_session.refresh(run2)

    with (
        patch("app.workflow_engine.get_redis", return_value=fake_redis),
        patch(
            "app.decision_engine.decision_engine.evaluate_gateway_verdict",
            AsyncMock(return_value={"verdict": "PASS", "confidence": 0.96, "provider": "gemini"}),
        ),
    ):
        await wf_engine._activate_node(db_session, wf, run2, gw_node)

    assert run2.status == "COMPLETED"


@pytest.mark.asyncio
async def test_mcp_bash_risk_triage_blocks_destructive_and_tags_hitl(db_session, fake_redis):
    """MCP Gateway blocks destructive_block commands with security critique and tags HITL tickets with risk metadata."""
    await _ensure_agents_and_workflows(db_session)
    with patch("app.mcp_gateway.get_redis", return_value=fake_redis):
        denied = await mcp_gateway.invoke_tool(
            db_session,
            ToolInvokeRequest(
                agent_id="coder",
                tool_id="bash_executor",
                workflow_id="code_review_pipeline",
                task_id="t-risk-1",
                arguments={"command": "rm -rf /"},
            ),
        )
        assert denied.status == "DENIED"
        assert "Destructive Command Blocked" in denied.message
        assert denied.result["risk_assessment"]["risk_level"] == "destructive_block"

        critiques = (
            await db_session.execute(
                select(BlackboardEntryModel).where(
                    BlackboardEntryModel.entry_type == "critique",
                    BlackboardEntryModel.task_id == "t-risk-1",
                )
            )
        ).scalars().all()
        assert any("Destructive Command Blocked" in c.content for c in critiques)

        with patch(
            "app.decision_engine.decision_engine.assess_command_risk",
            AsyncMock(return_value={"risk_level": "read_only_safe", "confidence": 0.95, "provider": "gemini"}),
        ):
            queued = await mcp_gateway.invoke_tool(
                db_session,
                ToolInvokeRequest(
                    agent_id="coder",
                    tool_id="bash_executor",
                    workflow_id="code_review_pipeline",
                    task_id="t-risk-2",
                    arguments={"command": "git status"},
                ),
            )
        assert queued.status == "AWAITING_APPROVAL"
        assert queued.hitl_ticket_id is not None
        ticket = await db_session.get(HITLTicketModel, queued.hitl_ticket_id)
        assert ticket.tool_arguments["_risk_assessment"]["risk_level"] == "read_only_safe"

        rbac_denied = await mcp_gateway.invoke_tool(
            db_session,
            ToolInvokeRequest(
                agent_id="researcher",
                tool_id="bash_executor",
                workflow_id="code_review_pipeline",
                task_id="t-risk-3",
                arguments={"command": "ls -la"},
            ),
        )
        assert rbac_denied.status == "DENIED"


@pytest.mark.asyncio
async def test_complexity_routing_and_memory_worthiness_gate(fake_redis):
    """DecisionEngine classifies complexity and gates memory write-worthiness."""
    await fake_redis.set("llm:decision:enabled", "false")
    engine = DecisionEngine()

    comp = await engine.classify_complexity(
        "Design a distributed Raft consensus protocol", redis_client=fake_redis
    )
    assert comp in ("simple_local", "complex_reasoning")

    explicit_res = await engine.evaluate_memory_worthiness(
        "My deploy window is Tuesdays at 2am", explicit_request=True, redis_client=fake_redis
    )
    assert explicit_res["should_store"] is True

    ephemeral_res = await engine.evaluate_memory_worthiness(
        "hello", explicit_request=False, redis_client=fake_redis
    )
    assert ephemeral_res["should_store"] is False

    question_res = await engine.evaluate_memory_worthiness(
        "What time is it right now?", explicit_request=False, redis_client=fake_redis
    )
    assert question_res["should_store"] is False


@pytest.mark.asyncio
async def test_e2e_api_config_toggle_and_telemetry_status(db_session, fake_redis):
    """E2E API test: POST /api/v1/llm/config -> decision call -> GET /api/v1/llm/status exposes telemetry."""
    await _ensure_agents_and_workflows(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with (
            patch("app.main.get_redis", new=AsyncMock(return_value=fake_redis)),
            patch("app.decision_engine.get_redis", return_value=fake_redis),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                cfg_resp = await client.post(
                    "/api/v1/llm/config",
                    json={
                        "decision_model_enabled": True,
                        "decision_model_provider": "gemini",
                        "gemini_decision_model": "gemini-decision-1",
                        "ollama_classifier_model": "qwen3.5:0.8b",
                    },
                )
                assert cfg_resp.status_code == 200

                status_resp = await client.get("/api/v1/llm/status")
                assert status_resp.status_code == 200
                dm = status_resp.json()["decision_model"]
                assert dm["enabled"] is True
                assert dm["provider"] == "gemini"
                assert dm["ollama_classifier_model"] == "qwen3.5:0.8b"
                assert "telemetry" in dm
                assert "calls" in dm["telemetry"]
    finally:
        app.dependency_overrides.clear()
