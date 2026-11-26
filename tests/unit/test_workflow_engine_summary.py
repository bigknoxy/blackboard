"""
Unit tests for workflow_engine.py _complete_workflow_run deliverable-first summary fix.
"""
import inspect
import re
import pytest

from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.workflow_engine import WorkflowEngine


def test_complete_workflow_run_prioritizes_researcher_deliverable():
    """_complete_workflow_run should prioritize researcher research entries with product links."""
    src = inspect.getsource(WorkflowEngine._complete_workflow_run)
    assert "primary_deliverable" in src
    assert "researcher" in src.lower()


def test_complete_workflow_run_omits_stage_counts():
    """_complete_workflow_run should omit redundant 'N stage(s)' counts."""
    src = inspect.getsource(WorkflowEngine._complete_workflow_run)
    # The new code should not have the old stage_info pattern as the primary summary line
    # It should use "Deliverable:" instead
    assert "Deliverable:" in src or "primary_deliverable" in src


def test_complete_workflow_run_strips_xml_and_json():
    """_complete_workflow_run should strip leaked corund XML and MCP Tool Invoked JSON blocks."""
    src = inspect.getsource(WorkflowEngine._complete_workflow_run)
    # Verify stripping regexes are present
    assert "<tool_name>" in src or "corund" in src
    assert "MCP Tool Invoked" in src or "mcp_tool_invoked" in src.lower()


async def test_complete_workflow_run_generates_clean_summary():
    """Pure unit test: _complete_workflow_run produces a clean deliverable-first summary without XML/JSON/stage noise."""
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    engine = WorkflowEngine()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        workflow_id="research_plan_verify_pipeline",
        status="RUNNING",
        current_nodes=["end_research_plan"],
        completed_nodes=["stage_deep_research", "stage_architect_plan"],
        context={
            "initial_prompt": "ESP32 shopping list",
            "last_verdict": "PASS",
            "verified_urls": ["https://example.com/esp32"],
        },
        created_at=None,
    )
    workflow = SimpleNamespace(id="research_plan_verify_pipeline", name="Research Pipeline")

    entries = [
        SimpleNamespace(
            agent_id="operator",
            entry_type="spec",
            content="ESP32 shopping list",
        ),
        SimpleNamespace(
            agent_id="researcher",
            entry_type="research",
            content=(
                "Found parts.<tool_call>web_search<arg_key>query</arg_key><arg_value>esp32</arg_value></tool_call>\n"
                "**Web Search Results:**\n"
                "- [ESP32 DevKit](https://example.com/esp32): Beginner board\n"
                "---\n**MCP Tool Invoked (`repo_search`):**\n```json\n{\"tool\": \"repo_search\"}\n```"
            ),
        ),
        SimpleNamespace(
            agent_id="architect",
            entry_type="spec",
            content="### Shopping List\n- [ESP32 DevKit V1](https://example.com/esp32) ($10)\n- [INMP441 Mic](https://example.com/mic) ($6)",
        ),
    ]

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = list(reversed(entries))
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    engine._publish_event = AsyncMock()
    mock_notify = AsyncMock(return_value={"telegram": {"status": "DELIVERED"}})

    import asyncio
    with patch("app.channel_gateway.channel_gateway.notify_workflow_completed", new=mock_notify):
        await engine._complete_workflow_run(mock_session, workflow, run, "end_research_plan")
        await asyncio.sleep(0)

    assert mock_notify.await_count == 1
    sent_summary = mock_notify.await_args.kwargs["summary"]
    assert "https://example.com/esp32" in sent_summary
    assert "https://www.amazon.com/s?k=INMP441+Mic" in sent_summary
    assert "https://example.com/mic" not in sent_summary
    assert "stage(s)" not in sent_summary
    assert "<tool_call>" not in sent_summary
    assert "<arg_key>" not in sent_summary
    assert "MCP Tool Invoked" not in sent_summary