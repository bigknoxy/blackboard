"""
Popperian Falsification 14: Concierge Temporal Grounding, Autonomous Web Search & HITL-Gated Bash Tool.

Invariants:
1. Temporal Grounding: Concierge prompts MUST inject current UTC date/time. Queries asking for today's date
   MUST be grounded and never claim ignorance of current date/time.
2. Autonomous Web Search: Web queries (e.g., "search the web", "NFL games today", "lookup online") MUST autonomously
   trigger `web_search` execution via MCP Gateway and output `web_search` in `tool_calls_executed` action receipts.
3. Bash Tool with HITL Gate: MCP catalog MUST provide `bash_executor` (`consumes_type="shell_command"`,
   `produces_type="command_output"`, `requires_approval=True`). Unapproved agent execution MUST queue an HITL ticket;
   approved execution MUST return exit code, stdout, and stderr.
4. Proactive Tool Suggestion / Auto-Provisioning: Concierge MUST proactively suggest or auto-provision tools
   (e.g., `bash_executor`, `web_search`) when operator asks for system/shell/web capabilities.
"""

from datetime import datetime, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import get_db, get_redis
from app.models import MCPToolModel, HITLTicketModel
from app.schemas import ToolInvokeRequest
from app.mcp_gateway import mcp_gateway


@pytest.mark.asyncio
async def test_falsifier_14_temporal_grounding_and_web_search(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # --- Invariant 1: Temporal Grounding & Autonomous Web Search ---
        # User asks: "Can you search the Web? Tell me what today's date is and if there are any NFL games today"
        now_utc = datetime.now(timezone.utc)
        current_year_str = str(now_utc.year)

        chat_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "Can you search the Web? Tell me what today's date is and if there are any NFL games today",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert chat_res.status_code == 200
        data = chat_res.json()

        # Invariant 1 check: Must contain date grounding (e.g. current year or date string)
        # and MUST NOT contain "I don't have access to external web resources or current date/time"
        reply = data.get("reply", "")
        assert "don't have access to external web" not in reply.lower()
        assert "cannot search the web" not in reply.lower()
        assert current_year_str in reply or now_utc.strftime("%B") in reply, (
            f"Expected current date/year grounding in reply, got: {reply}"
        )

        # Invariant 2 check: Action receipts MUST record autonomous web_search execution
        tool_calls = data.get("tool_calls_executed", [])
        tool_names = [tc.get("tool") for tc in tool_calls]
        assert "web_search" in tool_names, (
            f"Concierge did not autonomously execute 'web_search'! Executed: {tool_calls}"
        )

        await redis_client.aclose()
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_falsifier_14_bash_tool_registration_and_hitl(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # --- Invariant 3: Bash Executor Tool in MCP Catalog ---
        tools_res = await client.get("/api/v1/tools")
        assert tools_res.status_code == 200
        tools_list = tools_res.json()
        tool_ids = [t["id"] for t in tools_list]
        assert "bash_executor" in tool_ids, f"'bash_executor' missing from MCP tools catalog: {tool_ids}"

        bash_tool = next(t for t in tools_list if t["id"] == "bash_executor")
        assert bash_tool["consumes_type"] == "shell_command"
        assert bash_tool["produces_type"] == "command_output"
        assert bash_tool["requires_approval"] is True

        # Unapproved invocation by an agent MUST produce AWAITING_APPROVAL and queue an HITL ticket
        invoke_res = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "operator",
                "workflow_id": "code_review_pipeline",
                "task_id": "test_bash_hitl",
                "tool_id": "bash_executor",
                "arguments": {"command": "echo 'Hello from Sandboxed Bash'"},
                "bypass_approval": False,
            },
        )
        assert invoke_res.status_code == 200
        res_data = invoke_res.json()
        assert res_data["status"] == "AWAITING_APPROVAL"
        assert res_data.get("hitl_ticket_id") is not None

        # Approved / Bypassed invocation MUST execute and return command output
        exec_res = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "operator",
                "workflow_id": "code_review_pipeline",
                "task_id": "test_bash_exec",
                "tool_id": "bash_executor",
                "arguments": {"command": "echo 'Hello from Sandboxed Bash'"},
                "bypass_approval": True,
            },
        )
        assert exec_res.status_code == 200
        exec_data = exec_res.json()
        assert exec_data["status"] == "EXECUTED"
        res_result = exec_data.get("result", {})
        assert res_result.get("exit_code") == 0
        assert "Hello from Sandboxed Bash" in res_result.get("stdout", "")

        await redis_client.aclose()
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_falsifier_14_proactive_tool_suggestion_and_provisioning(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # --- Invariant 4: Proactive Tool Suggestion / Auto-Provisioning ---
        # Operator asks: "We need a bash tool and some web tools. What tools do you suggest or have?"
        chat_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "It seems like we need to add a bash tool and some web tools. Can you suggest or add them?",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert chat_res.status_code == 200
        data = chat_res.json()
        tool_calls = data.get("tool_calls_executed", [])
        tool_names = [tc.get("tool") for tc in tool_calls]

        # Concierge should either provision (upsert_tool) or execute proactive tool suggestions
        assert any(
            t in tool_names for t in ["upsert_tool", "suggest_tools", "assign_tool_to_agent"]
        ), f"Expected tool upsert or suggestion receipt, got: {tool_calls}"

        reply = data.get("reply", "").lower()
        assert "bash" in reply or "web" in reply

    await redis_client.aclose()
    app.dependency_overrides.clear()
