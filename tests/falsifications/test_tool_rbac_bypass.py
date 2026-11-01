import pytest
from app.mcp_gateway import MCPToolboxGateway, UnauthorizedToolException
from app.schemas import ToolInvokeRequest


@pytest.mark.asyncio
async def test_falsifier_4_unauthorized_mcp_tool_invocation_blocked(fake_redis, monkeypatch):
    """
    Falsifier 4: Unauthorized MCP Tool Invocation (RBAC Bypass)
    Hypothesis: "An agent cannot execute an MCP tool that is not explicitly declared
    in its `allowed_tools` manifest, even if prompted by an adversarial LLM response."
    Test: Mock an LLM response from `researcher` that emits a tool call for
    `mcp:execute_shell` (which is omitted from `researcher`'s config). Route the call
    through `mcp_gateway.py`.
    Assertion: The gateway raises an `UnauthorizedToolException`, drops execution,
    logs a security alert on the blackboard, and does NOT forward the call to the
    underlying MCP server.
    """
    monkeypatch.setattr("app.mcp_gateway.get_redis", lambda: fake_redis)

    gateway = MCPToolboxGateway()
    forwarded_calls = []

    async def _spy_dispatch(tool, arguments):
        forwarded_calls.append((tool, arguments))
        return {"status": "SHOULD_NEVER_RUN"}

    monkeypatch.setattr(gateway, "_dispatch_tool_execution", _spy_dispatch)

    agent_profile = {
        "id": "researcher",
        "name": "Researcher Agent",
        "allowed_tools": ["repo_search"],
    }
    adversarial_req = ToolInvokeRequest(
        agent_id="researcher",
        workflow_id="code_review_pipeline",
        task_id="task-adv-01",
        tool_id="mcp:execute_shell",
        arguments={"cmd": "cat /etc/shadow"},
    )

    security_alerts = []

    with pytest.raises(UnauthorizedToolException) as exc_info:
        await gateway.enforce_rbac_and_invoke(
            agent_profile=agent_profile,
            req=adversarial_req,
            security_audit_log=security_alerts,
        )

    assert "researcher" in str(exc_info.value)
    assert "mcp:execute_shell" in str(exc_info.value)
    # Verify underlying MCP server was NEVER called
    assert len(forwarded_calls) == 0
    # Verify security alert was logged to the Blackboard audit list & Redis event stream
    assert len(security_alerts) == 1
    assert security_alerts[0]["entry_type"] == "critique"
    assert security_alerts[0]["metadata"]["security_alert"] is True
    assert "mcp:execute_shell" in security_alerts[0]["content"]
