"""
Archon-inspired Orchestrator Test Harness (`tests/orchestrator_harness.py`).
Provides deterministic workflow seeding, classifier/LLM stubs, and assertion helpers
for testing Concierge & ChannelGateway routing without external LLM dependencies.
"""

import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.concierge import concierge_agent
from app.models import HITLTicketModel, WorkflowModel
from app.schemas import ConciergeChatRequest, ConciergeChatResponse


class OrchestratorTestHarness:
    """Reusable test harness mirroring Archon's `orchestrator-agent-harness.ts`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed_default_workflows(self) -> None:
        """Ensure standard workflows exist in the test DB."""
        defaults = [
            ("code_review_pipeline", "End-to-End Code Review Pipeline"),
            ("research_plan_verify_pipeline", "Generic Research, Plan & Verify Pipeline"),
            ("intake_pipeline", "Autonomous Backlog Intake Pipeline"),
        ]
        for wf_id, name in defaults:
            if not await self.session.get(WorkflowModel, wf_id):
                self.session.add(
                    WorkflowModel(
                        id=wf_id,
                        name=name,
                        state_graph={
                            "nodes": [
                                {"id": "start", "type": "StartEvent", "name": "Start"},
                                {"id": "end", "type": "EndEvent", "name": "End"},
                            ],
                            "edges": [{"id": "f1", "source": "start", "target": "end"}],
                        },
                        yaml_source=f"id: {wf_id}\nname: {name}\nnodes:\n  - id: start\n    type: StartEvent\n  - id: end\n    type: EndEvent\nedges:\n  - id: f1\n    source: start\n    target: end\n",
                        bpmn_xml="<definitions/>",
                    )
                )
        await self.session.commit()

    async def create_pending_ticket(
        self,
        tool_id: str = "bash_executor",
        agent_id: str = "qa-tester",
        workflow_id: str = "code_review_pipeline",
        command: str = "pytest -q",
    ) -> HITLTicketModel:
        """Create a PENDING HITLTicketModel for approval-gate routing tests."""
        ticket = HITLTicketModel(
            workflow_id=workflow_id,
            task_id=f"task-{uuid.uuid4().hex[:8]}",
            agent_id=agent_id,
            tool_id=tool_id,
            tool_arguments={"command": command},
            gate_type="tool_approval",
            status="PENDING",
        )
        self.session.add(ticket)
        await self.session.commit()
        await self.session.refresh(ticket)
        return ticket

    async def send(
        self,
        message: str,
        workflow_id: Optional[str] = None,
        classifier_decision: Optional[Dict[str, Any]] = None,
        llm_reply: Optional[str] = None,
        llm_native_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> ConciergeChatResponse:
        """Send a message through `concierge_agent.handle_chat` with optional LLM/classifier stubs."""
        clf_patch = (
            patch.object(
                concierge_agent,
                "_classify_intent",
                new=AsyncMock(return_value=classifier_decision),
            )
            if classifier_decision is not None
            else patch.object(
                concierge_agent,
                "_classify_intent",
                new=AsyncMock(return_value={"intent": "chat", "workflow_id": None, "confidence": 0.0}),
            )
        )
        llm_patch = patch.object(
            concierge_agent,
            "_try_ollama_chat_with_tools",
            new=AsyncMock(return_value=(llm_reply, llm_native_calls or [])),
        )
        with clf_patch, llm_patch:
            return await concierge_agent.handle_chat(
                self.session,
                ConciergeChatRequest(message=message, workflow_id=workflow_id),
            )

    @staticmethod
    def assert_workflow_triggered(
        resp: ConciergeChatResponse, expected_workflow_id: str
    ) -> Dict[str, Any]:
        triggers = [
            t for t in resp.tool_calls_executed if t.get("tool") == "trigger_workflow"
        ]
        assert triggers, f"Expected trigger_workflow in {resp.tool_calls_executed}"
        matched = [t for t in triggers if t.get("workflow_id") == expected_workflow_id]
        assert matched, f"Expected {expected_workflow_id}, got {triggers}"
        assert matched[0].get("run_id")
        return matched[0]

    @staticmethod
    def assert_clarification_asked(resp: ConciergeChatResponse) -> None:
        clar = [
            t for t in resp.tool_calls_executed if t.get("tool") == "intent_classifier"
        ]
        assert clar and clar[0].get("status") == "NEEDS_CLARIFICATION"
        assert "workflow" in resp.reply.lower()

    @staticmethod
    def assert_direct_chat_fallback(
        resp: ConciergeChatResponse, expected_substring: str
    ) -> None:
        triggers = [
            t for t in resp.tool_calls_executed if t.get("tool") == "trigger_workflow"
        ]
        assert not triggers, f"Did not expect workflow trigger, got {triggers}"
        assert expected_substring in resp.reply
