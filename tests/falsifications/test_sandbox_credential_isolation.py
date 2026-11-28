"""
Popperian Falsification: Sandbox Credential Isolation.

Shell subprocesses exposed through the MCP gateway must receive an explicit,
non-secret environment allowlist. They must never inherit control-plane
credentials from the gateway process.
"""

import os
import signal

import pytest

from app.mcp_gateway import MCPToolboxGateway, _build_sanitized_subprocess_env
from app.models import MCPToolModel


SECRET_ENV_VARS = {
    "DATABASE_URL": "postgresql://control-plane:secret@db/blackboard",
    "REDIS_URL": "redis://:secret@redis:6379/0",
    "GEMINI_API_KEY": "gemini-secret-value",
    "NVIDIA_NIM_API_KEY": "nim-secret-value",
    "TELEGRAM_BOT_TOKEN": "telegram-secret-value",
    "POSTGRES_PASSWORD": "postgres-secret-value",
    "SERVICE_API_KEY": "service-key-secret-value",
    "OAUTH_TOKEN": "oauth-token-secret-value",
    "CLIENT_SECRET": "client-secret-value",
}


def _parse_env(stdout: str) -> dict[str, str]:
    environment = {}
    for line in stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            environment[name] = value
    return environment


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["env", "printenv"])
async def test_bash_executor_hides_control_plane_secrets(monkeypatch, command):
    for name, value in SECRET_ENV_VARS.items():
        monkeypatch.setenv(name, value)

    gateway = MCPToolboxGateway()
    result = await gateway._handle_bash_executor({"command": command, "timeout": 5})

    assert result["status"] == "SUCCESS"
    assert result["exit_code"] == 0
    stdout = result["stdout"]
    assert "PATH=" in stdout
    assert "HOME=/tmp" in stdout
    assert "LANG=C.UTF-8" in stdout
    assert "LC_ALL=C.UTF-8" in stdout
    assert "TERM=xterm" in stdout
    for name, value in SECRET_ENV_VARS.items():
        assert f"{name}=" not in stdout
        assert value not in stdout


@pytest.mark.asyncio
async def test_dynamic_shell_tool_hides_control_plane_secrets(monkeypatch):
    for name, value in SECRET_ENV_VARS.items():
        monkeypatch.setenv(name, value)

    tool = MCPToolModel(
        id="dynamic_environment_probe",
        name="dynamic_environment_probe",
        description="Exercise a dynamically configured shell tool.",
        server_name="builtin",
        transport="builtin",
        endpoint_config={"bash_command": "env"},
        input_schema={},
        requires_approval=False,
    )
    gateway = MCPToolboxGateway()

    result = await gateway._dispatch_tool_execution(tool, {})

    assert result["status"] == "EXECUTED"
    assert result["exit_code"] == 0
    environment = _parse_env(result["stdout"])
    assert environment["PATH"]
    assert environment["HOME"] == "/tmp"
    assert environment["LANG"] == "C.UTF-8"
    assert environment["LC_ALL"] == "C.UTF-8"
    assert environment["TERM"] == "xterm"
    for name, value in SECRET_ENV_VARS.items():
        assert name not in environment
        assert value not in result["stdout"]


@pytest.mark.asyncio
async def test_bash_executor_timeout_still_reaps_process_group(monkeypatch):
    gateway = MCPToolboxGateway()
    killpg_calls = []
    real_killpg = os.killpg

    def recording_killpg(process_group_id, sig):
        killpg_calls.append((process_group_id, sig))
        real_killpg(process_group_id, sig)

    monkeypatch.setattr("app.mcp_gateway.os.killpg", recording_killpg)

    result = await gateway._handle_bash_executor(
        {"command": "sleep 30", "timeout": 1}
    )

    assert result["status"] == "TIMEOUT"
    assert result["exit_code"] == 124
    assert len(killpg_calls) == 1
    assert killpg_calls[0][1] == signal.SIGKILL


def test_sanitized_subprocess_environment_contains_only_safe_allowlist():
    environment = _build_sanitized_subprocess_env()

    assert set(environment) == {"PATH", "HOME", "LANG", "LC_ALL", "TERM"}
    assert environment["PATH"] == os.environ.get(
        "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    assert environment["HOME"] == "/tmp"
    assert environment["LANG"] == "C.UTF-8"
    assert environment["LC_ALL"] == "C.UTF-8"
    assert environment["TERM"] == "xterm"
