"""
Popperian Falsification 12: Tool Capability Registration, Dynamic Web Search & Action Receipts.

Invariant:
1. Builtin MCP catalog MUST include `web_search` with typed contracts (`consumes_type="search_query"`, `produces_type="search_results"`).
2. Dynamic tool invocation of `web_search` MUST execute deterministically and return structured results.
3. Concierge Admin Agent MUST recognize tool registration/addition intents, execute the addition in DB,
   and output structured `tool_calls_executed` action receipts rather than unexecuted promises.
4. Agent tool RBAC MUST enforce `web_search` access control strictly.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import get_db, get_redis
from app.models import AgentModel, MCPToolModel
from app.schemas import ToolInvokeRequest, ConciergeChatRequest
from app.mcp_gateway import mcp_gateway, UnauthorizedToolException
from app.concierge import concierge_agent


@pytest.mark.asyncio
async def test_falsifier_12_web_search_and_concierge_tool_registration(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Verify web_search exists in catalog or API
        tools_res = await client.get("/api/v1/tools")
        assert tools_res.status_code == 200
        tools_list = tools_res.json()
        tool_ids = [t["id"] for t in tools_list]
        assert "web_search" in tool_ids, f"'web_search' not found in tools catalog: {tool_ids}"

        web_search_tool = next(t for t in tools_list if t["id"] == "web_search")
        assert web_search_tool["consumes_type"] == "search_query"
        assert web_search_tool["produces_type"] == "search_results"

        # 2. Test direct tool invocation of web_search
        invoke_res = await client.post(
            "/api/v1/tools/invoke",
            json={
                "agent_id": "operator",
                "workflow_id": "code_review_pipeline",
                "task_id": "test_search",
                "tool_id": "web_search",
                "arguments": {"query": "distributed multi-agent memory patterns", "num_results": 3},
                "bypass_approval": True,
            },
        )
        assert invoke_res.status_code == 200
        res_data = invoke_res.json()
        assert res_data["status"] == "EXECUTED"
        assert res_data["result"]["tool"] == "web_search"
        assert len(res_data["result"]["results"]) == 3
        assert "distributed multi-agent memory patterns" in res_data["result"]["results"][0]["title"]

        # 3. Test RBAC: agent without web_search is unauthorized
        unauth_agent = {
            "id": "unauth-agent",
            "allowed_tools": ["ast_complexity_analyzer"],
        }
        with pytest.raises(UnauthorizedToolException):
            await mcp_gateway.enforce_rbac_and_invoke(
                agent_profile=unauth_agent,
                req=ToolInvokeRequest(
                    agent_id="unauth-agent",
                    workflow_id="wf_test",
                    task_id="task_1",
                    tool_id="web_search",
                    arguments={"query": "test query"},
                ),
                session=db_session,
            )

        # 4. Test Concierge Admin Agent intent handling:
        # When user asks "Add web search tool" or "Add web search to researcher agent",
        # Concierge executes the action and produces an action receipt card.
        chat_res = await client.post(
            "/api/v1/chat",
            json={
                "message": "Add web_search tool to researcher agent",
                "workflow_id": "code_review_pipeline",
            },
        )
        assert chat_res.status_code == 200
        chat_data = chat_res.json()
        tool_calls = chat_data.get("tool_calls_executed", [])
        tool_names = [tc.get("tool") for tc in tool_calls]
        assert any(
            t in tool_names for t in ["upsert_tool", "assign_tool_to_agent"]
        ), f"Concierge did not execute tool registration/assignment! Executed: {tool_calls}"

        # Clean up Redis connection
        await redis_client.aclose()

    app.dependency_overrides.clear()
