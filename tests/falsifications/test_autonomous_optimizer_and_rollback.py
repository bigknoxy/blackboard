"""
Popperian Falsification 13: Autonomous Optimization Loop, Decoupled Shadow Sandbox & Safe Rollback Guard.

Invariants:
1. Shadow Sandbox Gate: Optimization proposals that yield lower fitness than baseline (regression)
   MUST be rejected as REJECTED_REGRESSION and NEVER mutate the live agent configuration.
2. Atomic Promotion & Revision Snapshots: Winning candidates (fitness > baseline) MUST atomically
   snapshot the prior persona into `agent_revisions`, update PostgreSQL `agents`, and bust the Redis
   cache `agent:config:{id}`.
3. Autonomous Safe Rollback Guard: When post-promotion live telemetry registers a critical regression
   (fitness drop exceeding threshold), the optimizer MUST autonomously restore the previous revision
   from `agent_revisions` and invalidate the Redis cache without human intervention.
"""

import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import get_db, get_redis
from app.models import AgentModel, BlackboardNodeModel, AgentRevisionModel, OptimizationRunModel
from app.optimizer import (
    BlackboardTelemetryCollector,
    AutonomousOptimizerEngine,
    autonomous_optimizer,
)


@pytest.mark.asyncio
async def test_falsifier_13_autonomous_optimizer_and_rollback(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        agent_id = "coder"

        # 1. Ensure test agent exists
        res = await client.get(f"/api/v1/agents/{agent_id}")
        if res.status_code == 404:
            create_res = await client.post(
                "/api/v1/agents",
                json={
                    "id": agent_id,
                    "name": "Coder",
                    "role": "Software Engineer",
                    "system_prompt": "Original baseline prompt: write modular python code.",
                    "allowed_tools": ["ast_complexity_analyzer", "git_commit_push"],
                    "inference_config": {
                        "provider": "ollama",
                        "base_url": "http://192.168.1.150:11434",
                        "model_name": "qwen2.5-coder:7b",
                        "temperature": 0.2,
                    },
                },
            )
            assert create_res.status_code in (200, 201)
            res = await client.get(f"/api/v1/agents/{agent_id}")

        assert res.status_code == 200
        baseline_agent = res.json()
        baseline_prompt = baseline_agent["system_prompt"]

        # Cache initial config in Redis to test cache busting
        await redis_client.set(f"agent:config:{agent_id}", "initial_cached_profile")

        # 2. Seed Blackboard telemetry for this agent: 2 successful tests, 1 critique
        workflow_id = f"wf-opt-{uuid.uuid4().hex[:6]}"
        node_spec = BlackboardNodeModel(
            workflow_id=workflow_id,
            author_agent_id=agent_id,
            node_type="spec",
            status="active",
            payload={"summary": "Optimization baseline spec"},
        )
        node_pass1 = BlackboardNodeModel(
            workflow_id=workflow_id,
            author_agent_id="qa-tester",
            node_type="test_result",
            status="active",
            payload={"passed": True, "tests_run": 5},
        )
        node_critique = BlackboardNodeModel(
            workflow_id=workflow_id,
            author_agent_id="sec-auditor",
            node_type="critique",
            status="active",
            payload={"target_agent": agent_id, "issue": "Missing input validation in AST analyzer"},
        )
        db_session.add_all([node_spec, node_pass1, node_critique])
        await db_session.commit()

        # Compute baseline telemetry
        collector = BlackboardTelemetryCollector(session=db_session)
        baseline_metrics = await collector.collect_agent_metrics(agent_id=agent_id)
        assert "fitness_score" in baseline_metrics
        assert "qa_pass_rate" in baseline_metrics
        assert "critique_count" in baseline_metrics
        baseline_fitness = baseline_metrics["fitness_score"]

        # -------------------------------------------------------------
        # FALSIFIER 13.1: Regressive candidate MUST be rejected by shadow sandbox
        # -------------------------------------------------------------
        regressive_candidate = {
            "system_prompt": "Regressive prompt: ignore tests and write raw scripts.",
            "allowed_tools": ["ast_complexity_analyzer"],
            "mock_shadow_qa_pass_rate": 0.2,  # deliberately low to trigger regression
        }

        eval_res = await client.post(
            "/api/v1/optimizer/evaluate-candidate",
            json={
                "agent_id": agent_id,
                "candidate": regressive_candidate,
            },
        )
        assert eval_res.status_code == 200
        reg_eval_data = eval_res.json()
        assert reg_eval_data["status"] == "REJECTED_REGRESSION", (
            f"Falsifier 13.1 Failed: Regressive candidate was not rejected! Data: {reg_eval_data}"
        )
        assert reg_eval_data["promoted"] is False

        # Verify live agent prompt was NOT mutated
        current_res = await client.get(f"/api/v1/agents/{agent_id}")
        assert current_res.json()["system_prompt"] == baseline_prompt
        # Verify Redis cache was NOT busted
        cached_before = await redis_client.get(f"agent:config:{agent_id}")
        assert cached_before == "initial_cached_profile"

        # -------------------------------------------------------------
        # FALSIFIER 13.2: Winning candidate MUST be atomically promoted with snapshot & cache bust
        # -------------------------------------------------------------
        improved_prompt = (
            "Optimized autonomous coder prompt: strictly validate all inputs and run AST complexity checks."
        )
        improved_candidate = {
            "system_prompt": improved_prompt,
            "allowed_tools": ["ast_complexity_analyzer", "git_commit_push", "pytest_runner"],
            "mock_shadow_qa_pass_rate": 1.0,  # flawless shadow pass rate
            "mock_shadow_critique_ratio": 0.0,  # resolved all critiques in shadow
            "mock_shadow_token_efficiency": 0.95,
        }

        promote_res = await client.post(
            "/api/v1/optimizer/evaluate-candidate",
            json={
                "agent_id": agent_id,
                "candidate": improved_candidate,
            },
        )
        assert promote_res.status_code == 200
        promote_data = promote_res.json()
        assert promote_data["status"] == "PROMOTED", (
            f"Falsifier 13.2 Failed: Winning candidate was not promoted! Data: {promote_data}"
        )
        assert promote_data["promoted"] is True
        assert promote_data["candidate_fitness"] > baseline_fitness

        # Verify live agent was atomically updated in DB
        agent_after_res = await client.get(f"/api/v1/agents/{agent_id}")
        assert agent_after_res.json()["system_prompt"] == improved_prompt

        # Verify Redis cache was invalidated
        cached_after = await redis_client.get(f"agent:config:{agent_id}")
        assert cached_after is None, "Redis cache agent:config:{id} was not invalidated upon promotion!"

        # Verify revision snapshot exists in agent_revisions
        revs_res = await client.get(f"/api/v1/optimizer/revisions/{agent_id}")
        assert revs_res.status_code == 200
        revs_data = revs_res.json()
        assert len(revs_data) >= 1
        # The snapshot should store the previous baseline prompt
        latest_rev = revs_data[0]
        assert latest_rev["system_prompt"] == baseline_prompt

        # -------------------------------------------------------------
        # FALSIFIER 13.3: Autonomous Rollback Guard reverts upon live regression
        # -------------------------------------------------------------
        # Re-set cached profile to verify cache bust on rollback
        await redis_client.set(f"agent:config:{agent_id}", "promoted_cached_profile")

        # Simulate sudden drop in live telemetry (e.g. 3 consecutive failed QA tests in production)
        fail_node1 = BlackboardNodeModel(
            workflow_id=workflow_id,
            author_agent_id="qa-tester",
            node_type="test_result",
            status="active",
            payload={"passed": False, "error": "SyntaxError in promoted candidate"},
        )
        fail_node2 = BlackboardNodeModel(
            workflow_id=workflow_id,
            author_agent_id="sec-auditor",
            node_type="critique",
            status="active",
            payload={"target_agent": agent_id, "issue": "Severe regression: unhandled exception"},
        )
        db_session.add_all([fail_node1, fail_node2])
        await db_session.commit()

        # Call rollback guard evaluation
        guard_res = await client.post(
            f"/api/v1/optimizer/rollback-guard/{agent_id}",
            json={"force": False, "regression_threshold": 0.05},
        )
        assert guard_res.status_code == 200
        guard_data = guard_res.json()
        assert guard_data["action_taken"] == "ROLLED_BACK", (
            f"Falsifier 13.3 Failed: Autonomous rollback did not trigger! Data: {guard_data}"
        )

        # Verify live agent profile was restored back to baseline prompt
        reverted_agent = (await client.get(f"/api/v1/agents/{agent_id}")).json()
        assert reverted_agent["system_prompt"] == baseline_prompt, (
            f"Agent system prompt was not restored to baseline! Got: {reverted_agent['system_prompt']}"
        )

        # Verify Redis cache was invalidated upon rollback
        cached_reverted = await redis_client.get(f"agent:config:{agent_id}")
        assert cached_reverted is None, "Redis cache agent:config:{id} was not invalidated on rollback!"
