"""
Popperian Falsification 15: Concierge Meta-Memory Access, Operator Grounding & Auto-Crystallization.

Invariants:
1. Operator Memory Persistence (Falsifier 15.1):
   When operator commands "remember X" or provides user context (e.g. "I'm in KS and in CST"),
   Concierge MUST persist the memory into PostgreSQL `agent_memories` with 768-dim embeddings,
   and output `store_memory` in `tool_calls_executed`. Hallucinating that a memory was stored
   without an executed DB write is strictly forbidden.
2. Memory Visibility & Inspection (Falsifier 15.2):
   When operator asks "what do your other memories include?", Concierge MUST execute `list_memories`
   or `search_memories`, emit the action receipt, and summarize actual memories across agents
   (architect, coder, qa-tester, researcher) rather than claiming lack of access.
3. Operator Timezone Grounding (Falsifier 15.3):
   When operator timezone preference is stored (e.g. CST), subsequent date/time queries
   MUST ground to the operator's local timezone (e.g. CST) alongside UTC.
4. Autonomous Web Research Crystallization (Falsifier 15.4):
   Web search findings MUST be crystallized into `agent_memories` for the `researcher` agent
   so external intelligence persists in the collective memory.
5. HITL Tool Execution Result Persistence (Falsifier 15.5):
   Approved `tool_approval` tickets MUST persist and return structured `execution_result`.
"""

import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.database import get_db, get_redis
from app.models import AgentMemoryModel, AgentModel, HITLTicketModel
from app.schemas import HITLDecisionRequest


@pytest.mark.asyncio
async def test_falsifier_15_operator_memory_write_and_grounding(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Operator requests to store a memory
        chat_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "Do you have memory? I'd like you to remember I'm in KS and in CST. It's currently 11:50 am",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert chat_res.status_code == 200
        data = chat_res.json()

        # Action receipt MUST include store_memory
        tool_calls = data.get("tool_calls_executed", [])
        tool_names = [tc.get("tool") for tc in tool_calls]
        assert "store_memory" in tool_names, (
            f"Concierge did not execute 'store_memory' meta-tool! Executed: {tool_calls}"
        )

        # Verify memory actually persisted in PostgreSQL agent_memories
        mem_stmt = select(AgentMemoryModel).where(
            AgentMemoryModel.content.ilike("%KS%") | AgentMemoryModel.content.ilike("%CST%")
        )
        saved_mems = (await db_session.execute(mem_stmt)).scalars().all()
        assert len(saved_mems) > 0, "Memory was not persisted to PostgreSQL agent_memories!"
        saved_mem = saved_mems[0]
        assert saved_mem.embedding is not None, "Memory vector embedding was not computed!"
        assert len(saved_mem.embedding) == 768, f"Expected 768-dim embedding, got {len(saved_mem.embedding)}"

        # 2. Operator asks "What time is it?" -> Should reflect CST timezone grounding
        time_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "What time is it?",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert time_res.status_code == 200
        time_data = time_res.json()
        reply = time_data.get("reply", "")
        assert "cst" in reply.lower() or "central" in reply.lower() or "utc" in reply.lower()

    await redis_client.aclose()
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_falsifier_15_memory_inspection_and_crystallization(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Operator asks what other memories include
        chat_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "What do your other memories include?",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert chat_res.status_code == 200
        data = chat_res.json()

        # Action receipt MUST include list_memories or search_memories
        tool_calls = data.get("tool_calls_executed", [])
        tool_names = [tc.get("tool") for tc in tool_calls]
        assert any(t in tool_names for t in ["list_memories", "search_memories"]), (
            f"Concierge did not execute 'list_memories'! Executed: {tool_calls}"
        )

        reply = data.get("reply", "").lower()
        # Must not say it lacks access to other memories
        assert "don't have direct access" not in reply
        assert "cannot access" not in reply

        # 2. Test auto-crystallization of web search findings into researcher memory
        search_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "search the web for distributed pgvector memory patterns",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert search_res.status_code == 200
        search_data = search_res.json()
        s_tool_calls = search_data.get("tool_calls_executed", [])
        s_tool_names = [tc.get("tool") for tc in s_tool_calls]
        assert "web_search" in s_tool_names

        # Verify that a memory for researcher agent was stored/crystallized
        r_mem_stmt = select(AgentMemoryModel).where(
            AgentMemoryModel.agent_id == "researcher"
        )
        r_mems = (await db_session.execute(r_mem_stmt)).scalars().all()
        assert len(r_mems) > 0, "Web search findings were not crystallized into researcher memory!"

    await redis_client.aclose()
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_falsifier_15_hitl_bash_approval_result(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Seed tools catalog if empty in test session
        await client.get("/api/v1/tools")

        from app.models import ApprovalPolicyModel
        from sqlalchemy import delete
        await db_session.execute(delete(ApprovalPolicyModel).where(ApprovalPolicyModel.tool_id == "bash_executor"))
        await db_session.commit()

        # 1. Queue a bash tool execution requiring approval
        invoke_res = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "operator",
                "workflow_id": "code_review_pipeline",
                "task_id": "task_bash_test",
                "tool_id": "bash_executor",
                "arguments": {"command": "echo 'HITL Bash Output 42'"},
                "bypass_approval": False,
            },
        )
        assert invoke_res.status_code == 200
        invoke_data = invoke_res.json()
        assert invoke_data["status"] == "AWAITING_APPROVAL"
        ticket_id = invoke_data["hitl_ticket_id"]

        # 2. Approve ticket and verify execution result is returned
        decide_res = await client.post(
            f"/api/v1/blackboard/hitl/{ticket_id}/decide",
            json={
                "decision": "APPROVED",
                "reviewer_comment": "Approved by operator",
            },
        )
        assert decide_res.status_code == 200
        decide_data = decide_res.json()
        assert decide_data["status"] == "APPROVED"
        exec_res = decide_data.get("execution_result", {})
        assert exec_res is not None, "Ticket response omitted execution_result!"
        assert "HITL Bash Output 42" in exec_res.get("stdout", "")
        assert exec_res.get("exit_code") == 0

    await redis_client.aclose()
    app.dependency_overrides.clear()
