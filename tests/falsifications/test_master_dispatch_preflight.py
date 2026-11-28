"""Falsification tests for structured workflow contracts and deterministic dispatch."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.decision_engine import DecisionEngine
from app.main import app
from app.models import AgentModel, MCPToolModel, WorkflowModel
from app.workflow_engine import WORKFLOW_CONTRACT_FIELDS, WorkflowEngine


WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows"
WORKFLOW_FILES = (
    "code_review_pipeline.yaml",
    "intake_pipeline.yaml",
    "meta_workflow_architect.yaml",
    "research_plan_verify_pipeline.yaml",
)


def test_legacy_workflow_without_contracts_remains_supported():
    parsed = WorkflowEngine.parse_yaml_to_graph(
        "id: legacy\nname: Legacy\nnodes: []\nedges: []\n"
    )
    assert parsed["capability_tags"] == []
    assert parsed["required_tools"] == []
    assert parsed["input_contract"] == {}
    assert parsed["output_contract"] == {}


@pytest.mark.parametrize("filename", WORKFLOW_FILES)
def test_all_registry_workflows_declare_round_trippable_contracts(filename):
    raw_yaml = (WORKFLOWS_DIR / filename).read_text(encoding="utf-8")
    parsed = WorkflowEngine.parse_yaml_to_graph(raw_yaml)

    assert parsed["capability_tags"]
    assert parsed["required_tools"]
    assert parsed["input_contract"]
    assert parsed["output_contract"]
    for field in WORKFLOW_CONTRACT_FIELDS:
        assert field in parsed

    state_graph = WorkflowEngine.normalize_state_graph(parsed)
    canonical_yaml = WorkflowEngine.graph_to_yaml(
        parsed["id"],
        parsed["name"],
        parsed["description"],
        state_graph,
    )
    yaml_roundtrip = WorkflowEngine.parse_yaml_to_graph(canonical_yaml)
    assert yaml_roundtrip["capability_tags"] == parsed["capability_tags"]
    assert yaml_roundtrip["required_tools"] == parsed["required_tools"]
    assert yaml_roundtrip["input_contract"] == parsed["input_contract"]
    assert yaml_roundtrip["output_contract"] == parsed["output_contract"]

    bpmn = WorkflowEngine.graph_to_bpmn_xml(
        parsed["id"], parsed["name"], state_graph
    )
    bpmn_roundtrip = WorkflowEngine.bpmn_xml_to_graph(bpmn)
    assert bpmn_roundtrip["capability_tags"] == parsed["capability_tags"]
    assert bpmn_roundtrip["required_tools"] == parsed["required_tools"]
    assert bpmn_roundtrip["input_contract"] == parsed["input_contract"]
    assert bpmn_roundtrip["output_contract"] == parsed["output_contract"]
    bpmn_nodes = {node["id"]: node for node in bpmn_roundtrip["nodes"]}
    for node in parsed["nodes"]:
        assert bpmn_nodes[node["id"]].get("required_tools") == node.get("required_tools")

    autonomous_nodes = [
        node
        for node in parsed["nodes"]
        if node.get("type") in ("ServiceTask", "VerificationTask")
    ]
    assert autonomous_nodes
    assert all(node.get("required_tools") for node in autonomous_nodes)


async def _seed_preflight_workflow(session, suffix: str):
    repo_tool = MCPToolModel(
        id=f"repo_search_{suffix}",
        name="repo_search",
        description="Search repository source.",
        consumes_type=f"query_keyword_{suffix}",
        produces_type=f"code_snippet_{suffix}",
    )
    agent = AgentModel(
        id=f"worker_{suffix}",
        name="Contract Worker",
        role="Implementation Worker",
        system_prompt="Execute the assigned workflow contract.",
        allowed_tools=[repo_tool.id],
    )
    workflow = WorkflowModel(
        id=f"contract_workflow_{suffix}",
        name="Contract Workflow",
        description="Workflow used to falsify deterministic preflight.",
        state_graph={
            "nodes": [
                {
                    "id": "analyze",
                    "name": "Analyze",
                    "type": "ServiceTask",
                    "agent_id": agent.id,
                    "required_tools": [repo_tool.id],
                }
            ],
            "edges": [],
            "capability_tags": ["repository_analysis"],
            "required_tools": [repo_tool.id],
            "input_contract": {
                "type": "request",
                "source_type": f"query_keyword_{suffix}",
            },
            "output_contract": {
                "type": "analysis",
                "target_type": f"code_snippet_{suffix}",
            },
        },
    )
    session.add_all([repo_tool, agent, workflow])
    await session.commit()
    return workflow, agent, repo_tool


@pytest.mark.asyncio
async def test_backfilled_workflows_are_preflight_ready_with_registered_agents(db_session):
    tool_contracts = {
        "repo_search": ("query_keyword", "code_snippet"),
        "ast_complexity_analyzer": ("code_snippet", "ast_report"),
        "pytest_sandbox_runner": ("test_suite", "test_results"),
        "static_security_scanner": ("ast_report", "security_audit_report"),
        "workflow_architect_validator": (
            "yaml_workflow_spec",
            "validated_workflow_ast",
        ),
        "workflow_activator": (
            "validated_workflow_ast",
            "active_workflow_runtime",
        ),
        "web_search": ("search_query", "search_results"),
    }
    agent_contracts = {
        "architect": {
            "repo_search",
            "ast_complexity_analyzer",
            "workflow_architect_validator",
        },
        "researcher": {"web_search", "repo_search"},
        "coder": {"repo_search", "ast_complexity_analyzer"},
        "qa-tester": {"pytest_sandbox_runner", "repo_search"},
        "sec-auditor": {
            "ast_complexity_analyzer",
            "static_security_scanner",
        },
    }

    for tool_id, (consumes_type, produces_type) in tool_contracts.items():
        tool = await db_session.get(MCPToolModel, tool_id)
        if tool is None:
            tool = MCPToolModel(
                id=tool_id,
                name=tool_id,
                description=f"Test registry contract for {tool_id}",
                consumes_type=consumes_type,
                produces_type=produces_type,
            )
            db_session.add(tool)
        else:
            tool.consumes_type = consumes_type
            tool.produces_type = produces_type
    for agent_id, required_tools in agent_contracts.items():
        agent = await db_session.get(AgentModel, agent_id)
        if agent is None:
            agent = AgentModel(
                id=agent_id,
                name=agent_id,
                role=agent_id,
                system_prompt=f"Test persona for {agent_id}",
                allowed_tools=sorted(required_tools),
            )
            db_session.add(agent)
        else:
            agent.allowed_tools = sorted(
                set(agent.allowed_tools or []) | required_tools
            )
    await db_session.commit()

    engine = WorkflowEngine()
    suffix = uuid.uuid4().hex[:8]
    for filename in WORKFLOW_FILES:
        parsed = engine.parse_yaml_to_graph(
            (WORKFLOWS_DIR / filename).read_text(encoding="utf-8")
        )
        workflow = WorkflowModel(
            id=f"backfill_{parsed['id']}_{suffix}",
            name=parsed["name"],
            description=parsed["description"],
            state_graph=engine.normalize_state_graph(parsed),
        )
        db_session.add(workflow)
        await db_session.commit()

        result = await engine.preflight_check_workflow(
            db_session,
            workflow.id,
            required_capabilities=parsed["capability_tags"],
        )
        assert result["ready"] is True, (filename, result)
        assert result["missing_tools"] == []
        assert result["rbac_gaps"] == []
        assert result["missing_agents"] == []
        assert result["tool_chain_valid"] is True


@pytest.mark.asyncio
async def test_preflight_reports_exact_registry_and_rbac_gaps(db_session):
    suffix = uuid.uuid4().hex[:10]
    workflow, agent, repo_tool = await _seed_preflight_workflow(db_session, suffix)
    engine = WorkflowEngine()

    ready = await engine.preflight_check_workflow(
        db_session,
        workflow.id,
        required_capabilities=["repository_analysis"],
    )
    assert ready == {
        "workflow_id": workflow.id,
        "workflow_exists": True,
        "ready": True,
        "missing_tools": [],
        "rbac_gaps": [],
        "missing_agents": [],
        "tool_chain_valid": True,
        "missing_capabilities": [],
        "tool_chain": [repo_tool.id],
    }

    missing_capability = await engine.preflight_check_workflow(
        db_session,
        workflow.id,
        required_capabilities=["release_deployment"],
    )
    assert missing_capability["ready"] is False
    assert missing_capability["missing_capabilities"] == ["release_deployment"]

    graph = dict(workflow.state_graph)
    graph["required_tools"] = [*graph["required_tools"], "missing_tool"]
    graph["nodes"] = [
        {
            **graph["nodes"][0],
            "required_tools": [*graph["nodes"][0]["required_tools"], "missing_tool"],
        },
        {
            "id": "ghost_step",
            "name": "Ghost Step",
            "type": "ServiceTask",
            "agent_id": "missing_agent",
            "required_tools": [repo_tool.id],
        },
    ]
    workflow.state_graph = graph
    await db_session.commit()

    failed = await engine.preflight_check_workflow(db_session, workflow.id)
    assert failed["ready"] is False
    assert failed["missing_tools"] == ["missing_tool"]
    assert failed["missing_agents"] == ["missing_agent"]
    assert failed["rbac_gaps"] == [
        {
            "agent_id": agent.id,
            "node_id": "analyze",
            "tool_id": "missing_tool",
        }
    ]
    assert failed["tool_chain_valid"] is True


@pytest.mark.asyncio
async def test_preflight_api_is_zero_llm_and_handles_missing_workflow(db_session):
    suffix = uuid.uuid4().hex[:10]
    workflow, _, repo_tool = await _seed_preflight_workflow(db_session, suffix)
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with patch.object(
            DecisionEngine,
            "_get_client",
            side_effect=AssertionError("preflight must not call an LLM"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/api/v1/workflows/preflight",
                    json={"workflow_id": workflow.id},
                )
                assert response.status_code == 200
                assert response.json()["ready"] is True
                assert response.json()["workflow_exists"] is True

                workflow_response = await client.get(
                    f"/api/v1/workflows/{workflow.id}"
                )
                assert workflow_response.status_code == 200
                assert workflow_response.json()["capability_tags"] == [
                    "repository_analysis"
                ]
                assert workflow_response.json()["required_tools"] == [repo_tool.id]

                yaml_source = (WORKFLOWS_DIR / "intake_pipeline.yaml").read_text(
                    encoding="utf-8"
                )
                created = await client.post(
                    "/api/v1/workflows",
                    json={
                        "id": f"api_contract_{suffix}",
                        "name": "API Contract Workflow",
                        "description": "Created from registry YAML.",
                        "yaml_source": yaml_source,
                    },
                )
                assert created.status_code == 201
                assert created.json()["capability_tags"] == [
                    "idea_intake",
                    "architecture",
                    "security_review",
                    "backlog",
                    "rbac",
                ]
                assert created.json()["state_graph"]["required_tools"] == [
                    "repo_search",
                    "ast_complexity_analyzer",
                    "static_security_scanner",
                ]

                missing = await client.post(
                    "/api/v1/workflows/preflight",
                    json={"workflow_id": f"absent_{suffix}"},
                )
                assert missing.status_code == 200
                assert missing.json()["ready"] is False
                assert missing.json()["workflow_exists"] is False
    finally:
        app.dependency_overrides.clear()


def _match_workflows():
    return [
        {
            "id": "review_workflow",
            "name": "Review Workflow",
            "description": "Review code and security findings.",
            "capability_tags": ["security review"],
            "required_tools": ["repo_search"],
        },
        {
            "id": "planning_workflow",
            "name": "Planning Workflow",
            "description": "Plan implementation work.",
            "capability_tags": ["architecture"],
            "required_tools": ["repo_search"],
        },
    ]


def _match_tools():
    return [
        {
            "id": "repo_search",
            "consumes_type": "query_keyword",
            "produces_type": "code_snippet",
        }
    ]


@pytest.mark.asyncio
async def test_deterministic_fallback_returns_closed_match_states(fake_redis):
    await fake_redis.set("llm:decision:enabled", "false")
    engine = DecisionEngine()

    matched = await engine.match_workflow_or_gap(
        "perform a security review",
        _match_workflows(),
        _match_tools(),
        redis_client=fake_redis,
    )
    assert matched["status"] == "MATCH"
    assert matched["workflow_id"] == "review_workflow"
    assert matched["provider"] == "disabled"

    no_match = await engine.match_workflow_or_gap(
        "deploy the kubernetes cluster externally",
        _match_workflows(),
        _match_tools(),
        redis_client=fake_redis,
    )
    assert no_match["status"] == "NO_MATCH"
    assert no_match["workflow_id"] is None

    ambiguous_workflows = [
        {"id": "one", "name": "One", "capability_tags": ["architecture"]},
        {"id": "two", "name": "Two", "capability_tags": ["architecture"]},
    ]
    clarification = await engine.match_workflow_or_gap(
        "architecture work",
        ambiguous_workflows,
        _match_tools(),
        redis_client=fake_redis,
    )
    assert clarification["status"] == "NEEDS_CLARIFICATION"
    assert clarification["workflow_id"] is None

    telemetry = await engine.get_telemetry(redis_client=fake_redis)
    assert telemetry["gate_name"].get("workflow_match") == 3
    assert telemetry["provider"].get("disabled") == 3


@pytest.mark.asyncio
async def test_match_falls_back_when_decision_telemetry_store_is_offline():
    class OfflineRedis:
        async def mget(self, *keys):
            raise ConnectionError("redis offline")

    engine = DecisionEngine()
    result = await engine.match_workflow_or_gap(
        "perform a security review",
        _match_workflows(),
        _match_tools(),
        redis_client=OfflineRedis(),
    )
    assert result["status"] == "MATCH"
    assert result["workflow_id"] == "review_workflow"
    assert result["provider"] == "fallback"
    assert result["reason"] == "deterministic_fallback"


@pytest.mark.asyncio
async def test_model_match_is_validated_and_offline_uses_deterministic_fallback(fake_redis):
    await fake_redis.set("llm:decision:enabled", "true")
    engine = DecisionEngine()

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "choice": "MATCH:review_workflow",
                                    "confidence": 0.93,
                                }
                            )
                        }
                    }
                ]
            },
        )
    )
    with patch.object(engine, "_get_client", AsyncMock(return_value=fake_client)):
        matched = await engine.match_workflow_or_gap(
            "review this patch",
            _match_workflows(),
            _match_tools(),
            redis_client=fake_redis,
        )
    assert matched["status"] == "MATCH"
    assert matched["workflow_id"] == "review_workflow"
    assert matched["confidence"] == pytest.approx(0.93)
    assert matched["provider"].startswith("local:")

    offline_client = MagicMock()
    offline_client.post = AsyncMock(side_effect=RuntimeError("offline"))
    with patch.object(engine, "_get_client", AsyncMock(return_value=offline_client)):
        fallback = await engine.match_workflow_or_gap(
            "perform a security review",
            _match_workflows(),
            _match_tools(),
            redis_client=fake_redis,
        )
    assert fallback["status"] == "MATCH"
    assert fallback["workflow_id"] == "review_workflow"
    assert fallback["provider"] == "fallback"
    assert fallback["reason"] == "deterministic_fallback"

    telemetry = await engine.get_telemetry(redis_client=fake_redis)
    assert telemetry["gate_name"].get("workflow_match") == 2
    assert telemetry["provider"].get("fallback") == 1


@pytest.mark.asyncio
async def test_match_rejects_model_choice_when_required_tool_is_unavailable(fake_redis):
    await fake_redis.set("llm:decision:enabled", "true")
    engine = DecisionEngine()
    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "choice": "MATCH:review_workflow",
                                    "confidence": 0.99,
                                }
                            )
                        }
                    }
                ]
            },
        )
    )

    with patch.object(engine, "_get_client", AsyncMock(return_value=fake_client)):
        result = await engine.match_workflow_or_gap(
            "perform a security review",
            _match_workflows(),
            [],
            redis_client=fake_redis,
        )
    assert result["status"] == "NO_MATCH"
    assert result["missing_tools"] == ["repo_search"]
    assert result["reason"] == "invalid_model_match"
