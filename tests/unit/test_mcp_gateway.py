import pytest
from app.mcp_gateway import MCPToolboxGateway, UnauthorizedToolException
from app.models import MCPToolModel


def test_default_tool_catalog_includes_hitl_gated_tools():
    catalog = MCPToolboxGateway.default_tool_catalog()
    by_id = {t["id"]: t for t in catalog}
    assert "repo_search" in by_id
    assert by_id["repo_search"]["requires_approval"] is False
    assert "git_branch_commit" in by_id
    assert by_id["git_branch_commit"]["requires_approval"] is True
    assert "production_release_gate" in by_id
    assert by_id["production_release_gate"]["requires_approval"] is True


def test_openai_tool_schema_formatting():
    tool = MCPToolModel(
        id="static_security_scanner",
        name="static_security_scanner",
        description="Run static security checks",
        server_name="secops-mcp",
        transport="builtin",
        endpoint_config={},
        input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
        requires_approval=True,
    )
    schema = MCPToolboxGateway.to_openai_tool_schema(tool)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "static_security_scanner"
    assert "[REQUIRES HUMAN APPROVAL]" in schema["function"]["description"]


def test_unauthorized_tool_exception_is_defined():
    exc = UnauthorizedToolException(
        agent_id="researcher", tool_id="mcp:execute_shell"
    )
    assert "researcher" in str(exc)
    assert "mcp:execute_shell" in str(exc)
