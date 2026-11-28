"""
Popperian Falsification 16: Dynamic Approval Policies, Workflow Step Automation & Dynamic Meta-Tooling.

Invariants:
1. Tool-Level Auto-Approval Policy (Falsifier 16.1):
   When an active tool-level auto-approval policy exists for a protected tool (e.g., `production_release_gate`),
   invocations MUST execute immediately (`status="EXECUTED"`) without generating a pending HITL ticket.
2. Command/Arg Pattern Auto-Approval (Falsifier 16.2):
   When a pattern policy exists (e.g., `bash_executor` with pattern `echo *` or `git *`),
   commands matching the pattern MUST bypass approval and execute, while commands outside
   the pattern MUST strictly halt with `status="AWAITING_APPROVAL"`.
3. Workflow UserTask Step Automation (Falsifier 16.3):
   When an active workflow node policy exists for a `UserTask` (e.g., `stage_hitl_signoff`),
   the workflow state machine MUST auto-advance the step and complete the workflow run
   without stalling in `AWAITING_APPROVAL`.
4. Concierge Dynamic Tool Synthesis (Falsifier 16.4):
   When operator instructs Concierge to create/synthesize a new tool or connection,
   Concierge MUST register the tool in PostgreSQL `mcp_tools`, configure its capability
   contracts, assign it to the target agent, and output `create_dynamic_tool` in `tool_calls_executed`.
5. Policy Revocation & HITL Re-engagement (Falsifier 16.5):
   Deleting or disabling an auto-approval policy MUST immediately re-engage strict HITL gating
   without requiring server restarts.
"""

import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select
from app.main import app
from app.database import get_db, get_redis
from app.models import (
    ApprovalPolicyModel,
    MCPToolModel,
    AgentModel,
    HITLTicketModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.schemas import HITLDecisionRequest


@pytest.mark.asyncio
async def test_falsifier_16_tool_auto_approval_policy(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Seed tools
        await client.get("/api/v1/tools")

        # Clean up any leftover test policies
        await db_session.execute(
            delete(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tool_id == "production_release_gate"
            )
        )
        await db_session.commit()

        # Ensure coder has production_release_gate
        coder = await db_session.get(AgentModel, "coder")
        if not coder:
            coder = AgentModel(
                id="coder",
                name="Coder Agent",
                role="Implementation Specialist",
                system_prompt="Implement code",
                allowed_tools=["production_release_gate"],
            )
            db_session.add(coder)
        elif "production_release_gate" not in (coder.allowed_tools or []):
            coder.allowed_tools = list(coder.allowed_tools or []) + ["production_release_gate"]
        await db_session.commit()

        # Step 1: Without policy, invoking production_release_gate halts with AWAITING_APPROVAL
        res1 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-test-gate-1",
                "tool_id": "production_release_gate",
                "arguments": {"release_tag": "v1.0.0", "changelog": "Initial release"},
            },
        )
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "AWAITING_APPROVAL"
        assert data1.get("hitl_ticket_id") is not None

        # Step 2: Create tool-level auto-approval policy
        policy_res = await client.post(
            "/api/v1/approvals/policies",
            json={
                "policy_type": "tool",
                "tool_id": "production_release_gate",
                "agent_id": "coder",
                "enabled": True,
            },
        )
        assert policy_res.status_code == 201
        policy_id = policy_res.json()["id"]

        # Step 3: Invoking production_release_gate now executes directly!
        res2 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-test-gate-2",
                "tool_id": "production_release_gate",
                "arguments": {"release_tag": "v1.0.1", "changelog": "Auto-approved release"},
            },
        )
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["status"] == "EXECUTED"
        assert data2.get("result", {}).get("status") == "DEPLOYED_TO_HOMELAB_CLUSTER"

        # Step 4: Revoke policy and verify HITL re-engages
        del_res = await client.delete(f"/api/v1/approvals/policies/{policy_id}")
        assert del_res.status_code == 200

        res3 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-test-gate-3",
                "tool_id": "production_release_gate",
                "arguments": {"release_tag": "v1.0.2", "changelog": "Re-gated release"},
            },
        )
        assert res3.status_code == 200
        assert res3.json()["status"] == "AWAITING_APPROVAL"


@pytest.mark.asyncio
async def test_falsifier_16_command_pattern_auto_approval(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Seed tools
        await client.get("/api/v1/tools")

        # Clean up any leftover test policies
        await db_session.execute(
            delete(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tool_id == "bash_executor"
            )
        )
        await db_session.commit()

        # Ensure coder has bash_executor
        coder = await db_session.get(AgentModel, "coder")
        if not coder:
            coder = AgentModel(
                id="coder",
                name="Coder Agent",
                role="Implementation Specialist",
                system_prompt="Implement code",
                allowed_tools=["bash_executor"],
            )
            db_session.add(coder)
        elif "bash_executor" not in (coder.allowed_tools or []):
            coder.allowed_tools = list(coder.allowed_tools or []) + ["bash_executor"]
        await db_session.commit()

        # Step 1: Create pattern policy for "echo *"
        policy_res = await client.post(
            "/api/v1/approvals/policies",
            json={
                "policy_type": "command_pattern",
                "tool_id": "bash_executor",
                "pattern": "echo *",
                "enabled": True,
            },
        )
        assert policy_res.status_code == 201
        policy_id = policy_res.json()["id"]

        # Step 2: "echo hello world" matches pattern -> auto-approved and executes
        res_match = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-bash-echo",
                "tool_id": "bash_executor",
                "arguments": {"command": "echo 'hello world'"},
            },
        )
        assert res_match.status_code == 200
        match_data = res_match.json()
        assert match_data["status"] == "EXECUTED"
        assert "hello world" in match_data.get("result", {}).get("stdout", "")

        # Step 3: "whoami" does NOT match pattern -> halts with AWAITING_APPROVAL
        res_unmatch = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-bash-whoami",
                "tool_id": "bash_executor",
                "arguments": {"command": "whoami"},
            },
        )
        assert res_unmatch.status_code == 200
        assert res_unmatch.json()["status"] == "AWAITING_APPROVAL"

        # Step 4: Clean up policy
        await client.delete(f"/api/v1/approvals/policies/{policy_id}")


@pytest.mark.asyncio
async def test_falsifier_16_workflow_usertask_automation(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    from app.workflow_engine import workflow_engine

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Clean up any leftover test policies
        await db_session.execute(
            delete(ApprovalPolicyModel).where(
                ApprovalPolicyModel.workflow_id == "test_approval_wf"
            )
        )
        await db_session.commit()

        # Load or create test workflow with a UserTask node
        wf = await db_session.get(WorkflowModel, "test_approval_wf")
        if not wf:
            wf_graph = {
                "nodes": [
                    {"id": "n_start", "name": "Start", "type": "StartEvent"},
                    {"id": "n_hitl", "name": "Manual Review", "type": "UserTask"},
                    {"id": "n_end", "name": "End", "type": "EndEvent"},
                ],
                "edges": [
                    {"id": "e1", "source": "n_start", "target": "n_hitl"},
                    {"id": "e2", "source": "n_hitl", "target": "n_end"},
                ],
            }
            wf = WorkflowModel(
                id="test_approval_wf",
                name="Test Approval Workflow",
                description="Workflow with UserTask",
                state_graph=wf_graph,
                yaml_source="test: true",
                bpmn_xml="<bpmn/>",
            )
            db_session.add(wf)
            await db_session.commit()

        # Step 1: Without policy, advancing to UserTask sets status to AWAITING_APPROVAL
        run1 = await workflow_engine.start_workflow_run(
            db_session, wf, initial_prompt="Run 1"
        )
        assert run1.status == "AWAITING_APPROVAL"
        assert "n_hitl" in run1.current_nodes

        # Step 2: Create workflow node auto-approval policy
        policy_res = await client.post(
            "/api/v1/approvals/policies",
            json={
                "policy_type": "workflow_node",
                "workflow_id": "test_approval_wf",
                "node_id": "n_hitl",
                "enabled": True,
            },
        )
        assert policy_res.status_code == 201
        policy_id = policy_res.json()["id"]

        # Step 3: Run 2 with policy auto-advances through n_hitl to EndEvent (COMPLETED)!
        run2 = await workflow_engine.start_workflow_run(
            db_session, wf, initial_prompt="Run 2"
        )
        assert run2.status == "COMPLETED"
        assert "n_end" in run2.completed_nodes

        # Clean up policy
        await client.delete(f"/api/v1/approvals/policies/{policy_id}")


@pytest.mark.asyncio
async def test_falsifier_16_concierge_dynamic_tool_synthesis_and_policy(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Clean up any leftover test tool and policies
        await db_session.execute(
            delete(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tool_id == "disk_free_inspector"
            )
        )
        await db_session.execute(
            delete(MCPToolModel).where(
                MCPToolModel.id == "disk_free_inspector"
            )
        )
        # Ensure architect agent exists (seeds if fresh CI environment)
        arch = await db_session.get(AgentModel, "architect")
        if not arch:
            arch = AgentModel(
                id="architect",
                name="Architect Agent",
                role="System Architect",
                system_prompt="Design architectures",
                allowed_tools=["ast_complexity_analyzer"],
            )
            db_session.add(arch)
        await db_session.commit()

        # Step 1: Operator asks Concierge to create a dynamic tool
        chat_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "Create a new tool named 'disk_free_inspector' with description 'Inspect free disk space' using command 'df -h' and assign it to architect.",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert chat_res.status_code == 200
        chat_data = chat_res.json()
        executed_tools = [t["tool"] for t in chat_data.get("tool_calls_executed", [])]
        assert "create_dynamic_tool" in executed_tools

        # Verify tool was stored in database
        tool = await db_session.get(MCPToolModel, "disk_free_inspector")
        assert tool is not None
        assert tool.name == "disk_free_inspector"

        # Verify architect has tool assigned
        arch = await db_session.get(AgentModel, "architect")
        assert "disk_free_inspector" in (arch.allowed_tools or [])

        # Step 2: Operator asks Concierge to permanently auto-approve a tool
        chat_res2 = await client.post(
            "/api/v1/chat",
            json={
                "message": "Permanently approve tool 'disk_free_inspector' so it never halts for approval.",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert chat_res2.status_code == 200
        chat_data2 = chat_res2.json()
        executed_tools2 = [t["tool"] for t in chat_data2.get("tool_calls_executed", [])]
        assert "manage_approval_policy" in executed_tools2

        # Verify policy exists in DB
        stmt = select(ApprovalPolicyModel).where(
            ApprovalPolicyModel.tool_id == "disk_free_inspector"
        )
        res = await db_session.execute(stmt)
        policy = res.scalar_one_or_none()
        assert policy is not None
        assert policy.policy_type == "tool"
        assert policy.enabled is True


@pytest.mark.asyncio
async def test_hitl_decide_always_allow_pattern_and_global_tool_dedup_retroactive(db_session):
    """
    Verifies:
    1. POST /api/v1/blackboard/hitl/{id}/decide with auto_approve_policy='command_pattern'
       works for single-word commands ('whoami' AND 'whoami *') and auto-approves subsequent calls.
    2. auto_approve_policy='tool' creates a global (agent_id=None), deduplicated policy that
       auto-approves subsequent calls from DIFFERENT authorized agents AND retroactively resolves
       other already-PENDING tickets for the same tool.
    """
    app.dependency_overrides[get_db] = lambda: db_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Ensure coder and architect both have bash_executor in allowed_tools
        for ag_id in ("coder", "architect"):
            ag = await db_session.get(AgentModel, ag_id)
            if ag and "bash_executor" not in (ag.allowed_tools or []):
                ag.allowed_tools = list(ag.allowed_tools or []) + ["bash_executor"]
        # Clean up any leftover bash_executor policies & pending tickets
        await db_session.execute(
            delete(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tool_id == "bash_executor"
            )
        )
        await db_session.execute(
            delete(HITLTicketModel).where(
                HITLTicketModel.tool_id == "bash_executor",
                HITLTicketModel.status == "PENDING",
            )
        )
        await db_session.commit()

        # 1. Queue a 'whoami' ticket and approve with command_pattern='whoami *' (legacy trailing-space glob)
        inv1 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-whoami-1",
                "tool_id": "bash_executor",
                "arguments": {"command": "whoami"},
            },
        )
        assert inv1.status_code == 200
        assert inv1.json()["status"] == "AWAITING_APPROVAL"
        ticket1_id = inv1.json()["hitl_ticket_id"]

        dec1 = await client.post(
            f"/api/v1/blackboard/hitl/{ticket1_id}/decide",
            json={
                "decision": "APPROVED",
                "reviewer_comment": "Allow whoami pattern",
                "auto_approve_policy": "command_pattern",
                "pattern": "whoami *",
            },
        )
        assert dec1.status_code == 200
        assert dec1.json()["status"] == "APPROVED"

        # Subsequent 'whoami' call MUST auto-execute (not stall in AWAITING_APPROVAL)
        inv2 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-whoami-2",
                "tool_id": "bash_executor",
                "arguments": {"command": "whoami"},
            },
        )
        assert inv2.status_code == 200
        assert inv2.json()["status"] == "EXECUTED"

        # Clean up command_pattern policy before testing global tool policy
        await db_session.execute(
            delete(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tool_id == "bash_executor"
            )
        )
        await db_session.commit()

        # 2. Queue TWO pending bash_executor tickets (one from coder, one from architect)
        q1 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "coder",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-tool-1",
                "tool_id": "bash_executor",
                "arguments": {"command": "echo first"},
            },
        )
        q2 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "architect",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-tool-2",
                "tool_id": "bash_executor",
                "arguments": {"command": "echo second"},
            },
        )
        assert q1.json()["status"] == "AWAITING_APPROVAL"
        assert q2.json()["status"] == "AWAITING_APPROVAL"
        t1_id = q1.json()["hitl_ticket_id"]
        t2_id = q2.json()["hitl_ticket_id"]

        # Approve ticket 1 with auto_approve_policy='tool' -> creates global policy (agent_id=None)
        # AND retroactively auto-approves ticket 2!
        dec_tool = await client.post(
            f"/api/v1/blackboard/hitl/{t1_id}/decide",
            json={
                "decision": "APPROVED",
                "reviewer_comment": "Always allow bash_executor",
                "auto_approve_policy": "tool",
            },
        )
        assert dec_tool.status_code == 200

        # Verify global policy (agent_id is None) and no duplicates
        pols = (
            await db_session.execute(
                select(ApprovalPolicyModel).where(
                    ApprovalPolicyModel.tool_id == "bash_executor",
                    ApprovalPolicyModel.policy_type == "tool",
                )
            )
        ).scalars().all()
        assert len(pols) == 1
        assert pols[0].agent_id is None

        # Verify ticket 2 was retroactively auto-approved!
        await db_session.refresh(await db_session.get(HITLTicketModel, t2_id))
        t2_obj = await db_session.get(HITLTicketModel, t2_id)
        assert t2_obj.status == "APPROVED"

        # Verify a DIFFERENT authorized agent (architect) now auto-executes bash_executor
        inv3 = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "architect",
                "workflow_id": "code_review_pipeline",
                "task_id": "task-tool-3",
                "tool_id": "bash_executor",
                "arguments": {"command": "echo third"},
            },
        )
        assert inv3.status_code == 200
        assert inv3.json()["status"] == "EXECUTED"

        # Clean up
        await db_session.execute(
            delete(ApprovalPolicyModel).where(
                ApprovalPolicyModel.tool_id == "bash_executor"
            )
        )
        await db_session.commit()
    app.dependency_overrides.clear()
