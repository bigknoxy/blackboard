"""
Popperian Falsification 11: Agent Profile Hot-Swapping & Dynamic RBAC Invalidation.

Invariant:
Every persona mutation via PUT /api/v1/agents/{id} MUST:
1. Update PostgreSQL state atomically (models, prompt, allowed_tools).
2. Invalidate the Redis cached configuration (agent:config:{id}).
3. Immediately enforce the updated tool RBAC permissions in MCPToolboxGateway
   without restarting any services or dropping other agents' state.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import get_db, get_redis
from app.models import AgentModel
from app.schemas import ToolInvokeRequest
from app.mcp_gateway import mcp_gateway, UnauthorizedToolException


@pytest.mark.asyncio
async def test_falsifier_11_agent_profile_hot_swap_and_rbac_invalidation(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    redis_client = get_redis()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        agent_id = "coder"

        # 1. Inspect initial agent state
        res = await client.get(f"/api/v1/agents/{agent_id}")
        assert res.status_code == 200
        initial_data = res.json()
        initial_tools = list(initial_data["allowed_tools"])

        # Cache key check
        await redis_client.set(f"agent:config:{agent_id}", "stale_cached_config")

        # 2. Mutate agent profile live: change model to mistral-24b and restrict tools
        restricted_tools = ["ast_complexity_analyzer"]
        updated_prompt = "You are a hardened minimal coder strictly adhering to homelab resource limits."

        put_res = await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "system_prompt": updated_prompt,
                "allowed_tools": restricted_tools,
                "inference_config": {
                    "provider": "openai",
                    "base_url": "http://192.168.1.149:8080/v1",
                    "model_name": "mistral-24b",
                    "temperature": 0.1,
                },
            },
        )
        assert put_res.status_code == 200
        updated_data = put_res.json()
        assert updated_data["system_prompt"] == updated_prompt
        assert updated_data["allowed_tools"] == restricted_tools
        assert updated_data["inference_config"]["model_name"] == "mistral-24b"

        # 3. Invariant: Redis cache MUST have been invalidated (cache busting)
        cached_val = await redis_client.get(f"agent:config:{agent_id}")
        assert cached_val is None, "Redis cache agent:config:{id} was not invalidated on agent update!"

        # 4. Invariant: Tool RBAC is immediately enforced with new allowed_tools
        # Re-fetch agent profile from client (simulating worker hydration)
        hydrated_res = await client.get(f"/api/v1/agents/{agent_id}")
        hydrated_profile = hydrated_res.json()

        # Unauthorized tool call should now fail
        with pytest.raises(UnauthorizedToolException):
            await mcp_gateway.enforce_rbac_and_invoke(
                agent_profile=hydrated_profile,
                req=ToolInvokeRequest(
                    agent_id=agent_id,
                    workflow_id="test_wf",
                    task_id="task-1",
                    tool_id="git_commit_push",
                    arguments={"commit_message": "test"},
                ),
                session=db_session,
            )

        # Authorized tool call should proceed past RBAC check
        # (ast_complexity_analyzer is in updated allowed_tools)
        auth_result = await mcp_gateway.enforce_rbac_and_invoke(
            agent_profile=hydrated_profile,
            req=ToolInvokeRequest(
                agent_id=agent_id,
                workflow_id="test_wf",
                task_id="task-1",
                tool_id="ast_complexity_analyzer",
                arguments={"code": "def hello(): pass"},
            ),
            session=db_session,
        )
        assert auth_result["tool"] == "ast_complexity_analyzer"
        assert "verdict" in auth_result

        # 5. Restore original tools for idempotency
        await client.put(
            f"/api/v1/agents/{agent_id}",
            json={
                "system_prompt": initial_data["system_prompt"],
                "allowed_tools": initial_tools,
                "inference_config": initial_data["inference_config"],
            },
        )

    app.dependency_overrides.clear()
