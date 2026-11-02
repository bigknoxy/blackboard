import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from defusedxml import ElementTree as DefusedET
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from app.database import get_redis
from app.models import (
    BlackboardEntryModel,
    HITLTicketModel,
    WorkflowModel,
    WorkflowRunModel,
)

logger = logging.getLogger("blackboard.workflow_engine")

REDIS_TASK_STREAM = os.getenv("REDIS_TASK_STREAM", "stream:tasks")
REDIS_EVENTS_CHANNEL = os.getenv(
    "REDIS_EVENTS_CHANNEL", "channel:blackboard:events"
)
BLACKBOARD_PRUNE_THRESHOLD = int(
    os.getenv("BLACKBOARD_PRUNE_THRESHOLD", "15")
)
BLACKBOARD_SUMMARY_KEEP_RECENT = int(
    os.getenv("BLACKBOARD_SUMMARY_KEEP_RECENT", "5")
)

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
BB_NS = "http://blackboard.local/schema/bpmn"


class WorkflowEngine:
    """
    Bi-directional YAML/JSON <-> BPMN 2.0 XML State Machine & Orchestrator.
    Supports:
    - ServiceTask (Autonomous Agent Task)
    - ParallelGateway (Concurrent Fan-out / Fan-in)
    - ExclusiveGateway (Conditional Branching)
    - UserTask (Human-in-the-Loop Gate)
    - Automated Blackboard Debate Pruning / Bounded Context Builder (Falsifier 3)
    - Zombie Task Lease Recovery (Falsifier 5)
    """

    @staticmethod
    def parse_yaml_to_graph(yaml_text: str) -> Dict[str, Any]:
        data = yaml.safe_load(yaml_text) or {}
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        return {
            "id": data.get("id", "custom_workflow"),
            "name": data.get("name", "Custom Workflow"),
            "description": data.get("description", ""),
            "nodes": nodes,
            "edges": edges,
        }

    @staticmethod
    def graph_to_yaml(
        workflow_id: str,
        name: str,
        description: str,
        state_graph: Dict[str, Any],
    ) -> str:
        payload = {
            "id": workflow_id,
            "name": name,
            "description": description,
            "nodes": state_graph.get("nodes", []),
            "edges": state_graph.get("edges", []),
        }
        return yaml.safe_dump(payload, sort_keys=False)

    @staticmethod
    def graph_to_bpmn_xml(
        workflow_id: str, name: str, state_graph: Dict[str, Any]
    ) -> str:
        """
        Convert a JSON state graph into standard BPMN 2.0 XML with full
        BPMNDI diagram coordinates so `bpmn-js` renders it cleanly.
        """
        nodes = state_graph.get("nodes", [])
        edges = state_graph.get("edges", [])

        tag_map = {
            "StartEvent": "bpmn:startEvent",
            "EndEvent": "bpmn:endEvent",
            "ServiceTask": "bpmn:serviceTask",
            "UserTask": "bpmn:userTask",
            "ParallelGateway": "bpmn:parallelGateway",
            "ExclusiveGateway": "bpmn:exclusiveGateway",
        }

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
            f'  xmlns:bpmn="{BPMN_NS}"',
            f'  xmlns:bpmndi="{BPMNDI_NS}"',
            f'  xmlns:dc="{DC_NS}"',
            f'  xmlns:di="{DI_NS}"',
            f'  xmlns:bb="{BB_NS}"',
            f'  id="Definitions_{workflow_id}"',
            f'  targetNamespace="http://blackboard.local/bpmn">',
            f'  <bpmn:process id="Process_{workflow_id}" name="{_xml_escape(name)}" isExecutable="true">',
        ]

        for node in nodes:
            nid = node["id"]
            nname = _xml_escape(node.get("name", nid))
            ntype = node.get("type", "ServiceTask")
            bpmn_tag = tag_map.get(ntype, "bpmn:serviceTask")
            agent_attr = (
                f' bb:agentId="{_xml_escape(node["agent_id"])}"'
                if node.get("agent_id")
                else ""
            )
            entry_attr = (
                f' bb:entryType="{_xml_escape(node["entry_type"])}"'
                if node.get("entry_type")
                else ""
            )
            prompt_attr = (
                f' bb:promptTemplate="{_xml_escape(node["prompt_template"])}"'
                if node.get("prompt_template")
                else ""
            )

            incoming = [e["id"] for e in edges if e.get("target") == nid]
            outgoing = [e["id"] for e in edges if e.get("source") == nid]

            lines.append(
                f'    <{bpmn_tag} id="{nid}" name="{nname}"{agent_attr}{entry_attr}{prompt_attr}>'
            )
            for inc in incoming:
                lines.append(f"      <bpmn:incoming>{inc}</bpmn:incoming>")
            for out in outgoing:
                lines.append(f"      <bpmn:outgoing>{out}</bpmn:outgoing>")
            lines.append(f"    </{bpmn_tag}>")

        for edge in edges:
            eid = edge["id"]
            src = edge["source"]
            tgt = edge["target"]
            cond = edge.get("condition")
            if cond:
                lines.append(
                    f'    <bpmn:sequenceFlow id="{eid}" name="{_xml_escape(cond)}" sourceRef="{src}" targetRef="{tgt}">'
                )
                lines.append(
                    f'      <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{_xml_escape(cond)}</bpmn:conditionExpression>'
                )
                lines.append("    </bpmn:sequenceFlow>")
            else:
                lines.append(
                    f'    <bpmn:sequenceFlow id="{eid}" sourceRef="{src}" targetRef="{tgt}" />'
                )

        lines.append("  </bpmn:process>")

        # Generate visual diagram coordinates for bpmn-js
        lines.append(f'  <bpmndi:BPMNDiagram id="BPMNDiagram_{workflow_id}">')
        lines.append(
            f'    <bpmndi:BPMNPlane id="BPMNPlane_{workflow_id}" bpmnElement="Process_{workflow_id}">'
        )

        coords: Dict[str, Tuple[int, int, int, int]] = {}
        x_cursor = 140
        y_center = 200
        for node in nodes:
            nid = node["id"]
            ntype = node.get("type", "ServiceTask")
            if ntype in ("StartEvent", "EndEvent"):
                w, h = 36, 36
                y = y_center + 22
            elif ntype in ("ParallelGateway", "ExclusiveGateway"):
                w, h = 50, 50
                y = y_center + 15
            else:
                w, h = 130, 80
                y = y_center
            coords[nid] = (x_cursor, y, w, h)
            lines.append(
                f'      <bpmndi:BPMNShape id="{nid}_di" bpmnElement="{nid}">'
            )
            lines.append(
                f'        <dc:Bounds x="{x_cursor}" y="{y}" width="{w}" height="{h}" />'
            )
            lines.append("      </bpmndi:BPMNShape>")
            x_cursor += w + 90

        for edge in edges:
            eid = edge["id"]
            src_box = coords.get(edge["source"], (140, 200, 100, 80))
            tgt_box = coords.get(edge["target"], (300, 200, 100, 80))
            sx = src_box[0] + src_box[2]
            sy = src_box[1] + src_box[3] // 2
            tx = tgt_box[0]
            ty = tgt_box[1] + tgt_box[3] // 2
            lines.append(
                f'      <bpmndi:BPMNEdge id="{eid}_di" bpmnElement="{eid}">'
            )
            lines.append(f'        <di:waypoint x="{sx}" y="{sy}" />')
            lines.append(f'        <di:waypoint x="{tx}" y="{ty}" />')
            lines.append("      </bpmndi:BPMNEdge>")

        lines.append("    </bpmndi:BPMNPlane>")
        lines.append("  </bpmndi:BPMNDiagram>")
        lines.append("</bpmn:definitions>")
        return "\n".join(lines)

    @staticmethod
    def bpmn_xml_to_graph(bpmn_xml: str) -> Dict[str, Any]:
        """
        Parse BPMN 2.0 XML back into a canonical state_graph JSON structure.
        Preserves custom `bb:agentId`, `bb:entryType`, and `bb:promptTemplate` attributes.
        """
        root = DefusedET.fromstring(bpmn_xml)
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []

        tag_lookup = {
            f"{{{BPMN_NS}}}startEvent": "StartEvent",
            f"{{{BPMN_NS}}}endEvent": "EndEvent",
            f"{{{BPMN_NS}}}serviceTask": "ServiceTask",
            f"{{{BPMN_NS}}}task": "ServiceTask",
            f"{{{BPMN_NS}}}userTask": "UserTask",
            f"{{{BPMN_NS}}}parallelGateway": "ParallelGateway",
            f"{{{BPMN_NS}}}exclusiveGateway": "ExclusiveGateway",
        }

        for elem in root.iter():
            if elem.tag in tag_lookup:
                ntype = tag_lookup[elem.tag]
                nid = elem.attrib.get("id", f"node_{len(nodes) + 1}")
                nname = elem.attrib.get("name", nid)
                agent_id = elem.attrib.get(
                    f"{{{BB_NS}}}agentId"
                ) or elem.attrib.get("bb:agentId")
                entry_type = (
                    elem.attrib.get(f"{{{BB_NS}}}entryType")
                    or elem.attrib.get("bb:entryType")
                    or "spec"
                )
                prompt_template = elem.attrib.get(
                    f"{{{BB_NS}}}promptTemplate"
                ) or elem.attrib.get("bb:promptTemplate")

                if ntype == "ServiceTask" and not agent_id:
                    agent_id = _infer_agent_from_name(nname)

                node_obj: Dict[str, Any] = {
                    "id": nid,
                    "name": nname,
                    "type": ntype,
                }
                if agent_id:
                    node_obj["agent_id"] = agent_id
                if ntype == "ServiceTask":
                    node_obj["entry_type"] = entry_type
                if prompt_template:
                    node_obj["prompt_template"] = prompt_template
                nodes.append(node_obj)

            elif elem.tag == f"{{{BPMN_NS}}}sequenceFlow":
                eid = elem.attrib.get("id", f"flow_{len(edges) + 1}")
                src = elem.attrib.get("sourceRef", "")
                tgt = elem.attrib.get("targetRef", "")
                cond_text = elem.attrib.get("name")
                for child in elem:
                    if child.tag == f"{{{BPMN_NS}}}conditionExpression" and (
                        child.text
                    ):
                        cond_text = child.text.strip()
                edge_obj: Dict[str, Any] = {
                    "id": eid,
                    "source": src,
                    "target": tgt,
                }
                if cond_text:
                    edge_obj["condition"] = cond_text
                edges.append(edge_obj)

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def build_bounded_context(
        entries: List[Dict[str, Any]], max_token_budget: int = 4000
    ) -> Dict[str, Any]:
        """
        Falsifier 3 Janitor Routine:
        Guarantees multi-turn agent debates never exceed `max_token_budget` tokens.
        Preserves:
        1. The original problem statement (initial `spec` / `StartEvent` entry)
        2. A condensed digest of intermediate debate turns
        3. The latest `critique` and recent steering entries
        """
        if not entries:
            return {"pruned": False, "token_count": 0, "context_text": ""}

        raw_blocks = [
            f"[{e.get('entry_type', 'artifact').upper()}] ({e.get('agent_id')}): {e.get('content', '')}"
            for e in entries
        ]
        raw_text = "\n\n".join(raw_blocks)
        raw_tokens = len(raw_text.split())
        if raw_tokens < max_token_budget:
            return {
                "pruned": False,
                "token_count": raw_tokens,
                "context_text": raw_text,
            }

        # 1. Preserve original problem statement (first entry / StartEvent spec)
        origin_entry = entries[0]
        origin_words = origin_entry.get("content", "").split()[:350]
        origin_block = (
            f"### Original Problem Statement (`{origin_entry.get('entry_type', 'spec')}`)\n"
            + " ".join(origin_words)
        )

        # 2. Preserve latest critique (and very last entry)
        critiques = [e for e in entries if e.get("entry_type") == "critique"]
        latest_critique = critiques[-1] if critiques else entries[-1]
        latest_words = latest_critique.get("content", "").split()[:600]
        latest_block = (
            f"### Latest Critique / Active Turn (`{latest_critique.get('id')}` by `{latest_critique.get('agent_id')}`)\n"
            + " ".join(latest_words)
        )

        # 3. Summarize intermediate turns within remaining token budget
        middle_entries = entries[1:-1]
        summary_lines = [
            f"### Janitor Pruned Debate Digest ({len(middle_entries)} intermediate turns condensed)"
        ]
        remaining_budget = max(
            200,
            max_token_budget
            - len(origin_block.split())
            - len(latest_block.split())
            - 100,
        )
        per_entry_words = max(8, min(25, remaining_budget // max(1, len(middle_entries))))

        used_summary_words = 0
        for item in middle_entries:
            snippet_words = item.get("content", "").split()[:per_entry_words]
            line = f"- [{item.get('entry_type')}] @{item.get('agent_id')}: {' '.join(snippet_words)}..."
            line_len = len(line.split())
            if used_summary_words + line_len > remaining_budget:
                break
            summary_lines.append(line)
            used_summary_words += line_len

        final_context = "\n\n".join(
            [origin_block, "\n".join(summary_lines), latest_block]
        )
        final_tokens = len(final_context.split())
        return {
            "pruned": True,
            "token_count": final_tokens,
            "context_text": final_context,
            "condensed_entries_count": len(middle_entries),
        }

    async def recover_abandoned_task(
        self,
        task_payload: Dict[str, Any],
        redis_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Falsifier 5 Zombie Lock Recovery:
        Inspects `task:{task_id}:lease`. If the worker crashed and the TTL expired,
        transitions the task to `RETRY_QUEUED` and re-queues it onto `stream:tasks`.
        """
        r = redis_client or get_redis()
        task_id = task_payload["task_id"]
        lease_owner = await r.get(f"task:{task_id}:lease")
        if lease_owner is not None:
            return {
                "status": "LEASE_ACTIVE",
                "task_id": task_id,
                "owner": lease_owner,
            }

        retry_payload = dict(task_payload)
        retry_payload["status"] = "RETRY_QUEUED"
        retry_payload["retry_count"] = int(task_payload.get("retry_count", 0)) + 1

        await r.xadd(
            REDIS_TASK_STREAM,
            {"payload": json.dumps(retry_payload)},
        )
        await r.publish(
            REDIS_EVENTS_CHANNEL,
            json.dumps(
                {
                    "event": "task.retry_queued",
                    "task_id": task_id,
                    "workflow_id": task_payload.get("workflow_id"),
                    "status": "RETRY_QUEUED",
                    "retry_count": retry_payload["retry_count"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
        return {
            "status": "RETRY_QUEUED",
            "task_id": task_id,
            "retry_count": retry_payload["retry_count"],
        }

    async def start_workflow_run(
        self,
        session: AsyncSession,
        workflow: WorkflowModel,
        initial_prompt: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRunModel:
        """
        Create a WorkflowRun, post the initial specification/request to the Blackboard,
        and advance from StartEvent into the first executable node(s).
        """
        run_context = dict(context or {})
        run_context["initial_prompt"] = initial_prompt

        run = WorkflowRunModel(
            workflow_id=workflow.id,
            status="RUNNING",
            current_nodes=[],
            completed_nodes=[],
            context=run_context,
        )
        session.add(run)
        await session.flush()

        # Seed initial user problem statement on the Blackboard
        seed_entry = BlackboardEntryModel(
            workflow_id=workflow.id,
            task_id=f"run-{run.id}-init",
            agent_id="operator",
            entry_type="spec",
            content=initial_prompt,
            metadata_={"run_id": str(run.id), "stage": "StartEvent"},
        )
        session.add(seed_entry)
        await session.commit()
        await session.refresh(run)

        nodes = workflow.state_graph.get("nodes", [])
        start_nodes = [n for n in nodes if n.get("type") == "StartEvent"]
        if not start_nodes and nodes:
            start_nodes = [nodes[0]]

        for snode in start_nodes:
            await self.advance_from_node(
                session, workflow, run, snode["id"], branch_outcome="PASS"
            )

        await session.refresh(run)
        return run

    async def advance_from_node(
        self,
        session: AsyncSession,
        workflow: WorkflowModel,
        run: WorkflowRunModel,
        completed_node_id: str,
        branch_outcome: str = "PASS",
    ) -> None:
        """
        Transition state machine out of `completed_node_id` across outgoing edges.
        Handles `ServiceTask`, `ParallelGateway`, `ExclusiveGateway`, `UserTask`, and `EndEvent`.
        """
        graph = workflow.state_graph or {}
        nodes_by_id = {n["id"]: n for n in graph.get("nodes", [])}
        edges = graph.get("edges", [])

        completed = list(run.completed_nodes or [])
        if completed_node_id not in completed:
            completed.append(completed_node_id)
        current = [
            nid for nid in (run.current_nodes or []) if nid != completed_node_id
        ]
        run.completed_nodes = completed
        run.current_nodes = current

        source_node = nodes_by_id.get(completed_node_id, {})
        outgoing = [e for e in edges if e.get("source") == completed_node_id]

        if source_node.get("type") == "ExclusiveGateway" and len(outgoing) > 1:
            matched = [
                e
                for e in outgoing
                if (e.get("condition") or "").upper() == branch_outcome.upper()
            ]
            outgoing = matched if matched else [outgoing[0]]

        for edge in outgoing:
            target_id = edge.get("target")
            target_node = nodes_by_id.get(target_id)
            if not target_node:
                continue

            await self._activate_node(session, workflow, run, target_node)

        await session.commit()

    async def _activate_node(
        self,
        session: AsyncSession,
        workflow: WorkflowModel,
        run: WorkflowRunModel,
        node: Dict[str, Any],
    ) -> None:
        nid = node["id"]
        ntype = node.get("type", "ServiceTask")
        graph = workflow.state_graph or {}
        edges = graph.get("edges", [])

        # ParallelGateway join check: ensure all incoming branches have completed
        if ntype == "ParallelGateway":
            incoming_sources = [
                e["source"] for e in edges if e.get("target") == nid
            ]
            if len(incoming_sources) > 1:
                completed_set = set(run.completed_nodes or [])
                if not all(src in completed_set for src in incoming_sources):
                    return
            await self.advance_from_node(session, workflow, run, nid)
            return

        if ntype == "ExclusiveGateway":
            await self.advance_from_node(
                session,
                workflow,
                run,
                nid,
                branch_outcome=run.context.get("last_verdict", "PASS"),
            )
            return

        if ntype == "EndEvent":
            completed = list(run.completed_nodes or [])
            if nid not in completed:
                completed.append(nid)
            run.completed_nodes = completed
            run.current_nodes = []
            run.status = "COMPLETED"
            await self._publish_event(
                {
                    "event": "workflow.completed",
                    "workflow_id": workflow.id,
                    "run_id": str(run.id),
                    "node_id": nid,
                    "status": "COMPLETED",
                }
            )
            return

        if ntype == "UserTask":
            current = list(run.current_nodes or [])
            if nid not in current:
                current.append(nid)
            run.current_nodes = current
            run.status = "AWAITING_APPROVAL"
            task_id = f"run-{run.id}-{nid}"

            ticket = HITLTicketModel(
                workflow_id=workflow.id,
                task_id=task_id,
                agent_id=node.get("agent_id") or "human-operator",
                gate_type="user_task",
                tool_id=nid,
                tool_arguments={
                    "node_name": node.get("name", nid),
                    "run_id": str(run.id),
                    "instructions": node.get(
                        "prompt_template",
                        "Review blackboard artifacts and approve or reject.",
                    ),
                },
                status="PENDING",
            )
            session.add(ticket)
            await session.flush()

            await self._publish_event(
                {
                    "event": "hitl.user_task_created",
                    "ticket_id": str(ticket.id),
                    "workflow_id": workflow.id,
                    "run_id": str(run.id),
                    "task_id": task_id,
                    "node_id": nid,
                    "node_name": node.get("name", nid),
                    "status": "AWAITING_APPROVAL",
                }
            )
            # Notify external channels (Discord/Telegram) of pending HITL gate
            try:
                from app.main import _notify_external_channels
                import asyncio
                alert_text = (
                    f"⚠️ **[Blackboard HITL Gate]** Approval requested for workflow `{workflow.id}`!\n"
                    f"- Node: **{node.get('name', nid)}** (`{nid}`)\n"
                    f"- Run ID: `{run.id}`\n"
                    f"Review artifacts and approve in Web Cockpit: http://192.168.1.100:5173"
                )
                asyncio.create_task(_notify_external_channels(alert_text))
            except Exception:
                pass
            return

        # Default: ServiceTask -> Enqueue task to Redis Stream `stream:tasks`
        current = list(run.current_nodes or [])
        if nid not in current:
            current.append(nid)
        run.current_nodes = current
        run.status = "RUNNING"

        task_id = f"task-{uuid.uuid4().hex[:10]}"
        agent_id = node.get("agent_id") or "coder"
        entry_type = node.get("entry_type") or "spec"
        prompt_template = node.get("prompt_template") or (
            f"Execute workflow stage '{node.get('name', nid)}' for goal: "
            f"{run.context.get('initial_prompt', '')}"
        )

        task_payload = {
            "task_id": task_id,
            "workflow_id": workflow.id,
            "run_id": str(run.id),
            "node_id": nid,
            "node_name": node.get("name", nid),
            "agent_id": agent_id,
            "entry_type": entry_type,
            "prompt": prompt_template,
            "initial_prompt": str(run.context.get("initial_prompt", "")),
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }

        redis_client = get_redis()
        await redis_client.xadd(
            REDIS_TASK_STREAM,
            {"payload": json.dumps(task_payload)},
        )
        await self._publish_event(
            {
                "event": "task.enqueued",
                **task_payload,
            }
        )

    async def prune_and_summarize_blackboard(
        self,
        session: AsyncSession,
        workflow_id: str,
        threshold: int = BLACKBOARD_PRUNE_THRESHOLD,
        keep_recent: int = BLACKBOARD_SUMMARY_KEEP_RECENT,
    ) -> Optional[BlackboardEntryModel]:
        """
        State Pruning Utility:
        Condenses older blackboard entries into a single `summary` entry when a workflow's
        debate thread exceeds `threshold`, preventing context window overflow.
        """
        stmt = (
            select(BlackboardEntryModel)
            .where(
                BlackboardEntryModel.workflow_id == workflow_id,
                BlackboardEntryModel.entry_type != "steering",
            )
            .order_by(BlackboardEntryModel.created_at.asc())
        )
        result = await session.execute(stmt)
        entries = list(result.scalars().all())

        if len(entries) <= threshold:
            return None

        to_prune = entries[:-keep_recent]
        digest_lines = [
            f"### Condensed Blackboard Digest ({len(to_prune)} prior artifacts)"
        ]
        for item in to_prune:
            snippet = item.content.strip().replace("\n", " ")[:240]
            digest_lines.append(
                f"- **[{item.entry_type.upper()}]** by `{item.agent_id}` (task `{item.task_id}`): {snippet}"
            )

        summary_entry = BlackboardEntryModel(
            workflow_id=workflow_id,
            task_id=f"prune-{uuid.uuid4().hex[:8]}",
            agent_id="state-pruner",
            entry_type="summary",
            content="\n".join(digest_lines),
            metadata_={
                "pruned_count": len(to_prune),
                "pruned_entry_ids": [str(e.id) for e in to_prune],
            },
        )
        session.add(summary_entry)

        prune_ids = [e.id for e in to_prune]
        await session.execute(
            delete(BlackboardEntryModel).where(
                BlackboardEntryModel.id.in_(prune_ids)
            )
        )
        await session.commit()
        await session.refresh(summary_entry)

        await self._publish_event(
            {
                "event": "blackboard.pruned",
                "workflow_id": workflow_id,
                "pruned_count": len(to_prune),
                "summary_entry_id": str(summary_entry.id),
            }
        )
        return summary_entry

    async def _publish_event(self, payload: Dict[str, Any]) -> None:
        payload.setdefault(
            "timestamp", datetime.now(timezone.utc).isoformat()
        )
        redis_client = get_redis()
        await redis_client.publish(REDIS_EVENTS_CHANNEL, json.dumps(payload))


def _xml_escape(val: str) -> str:
    return (
        str(val)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _infer_agent_from_name(name: str) -> str:
    lowered = name.lower()
    if "research" in lowered or "arch" in lowered or "spec" in lowered:
        return "architect"
    if "sec" in lowered or "audit" in lowered or "review" in lowered:
        return "sec-auditor"
    if "test" in lowered or "qa" in lowered:
        return "qa-tester"
    return "coder"


workflow_engine = WorkflowEngine()
