import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from defusedxml import ElementTree as DefusedET
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
import yaml

from app.database import get_redis
from app.web_grounding import sanitize_and_ground_links, verify_deliverable_links
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
    def ensure_lifecycle_events(state_graph: Dict[str, Any]) -> Dict[str, Any]:
        """
        Guarantee every non-empty workflow graph has StartEvent and EndEvent
        nodes and no dangling terminal nodes, so WorkflowEngine always emits
        Blackboard summaries and channel-agnostic `workflow.completed` pings.
        """
        nodes = [dict(n) for n in (state_graph.get("nodes") or [])]
        edges = [dict(e) for e in (state_graph.get("edges") or [])]
        if not nodes:
            return {"nodes": nodes, "edges": edges}

        incoming = {n["id"]: 0 for n in nodes if "id" in n}
        outgoing = {n["id"]: 0 for n in nodes if "id" in n}
        for idx, e in enumerate(edges):
            src, tgt = e.get("source"), e.get("target")
            if not e.get("id"):
                e["id"] = f"flow_{src}_{tgt}_{idx}"
            if src in outgoing:
                outgoing[src] += 1
            if tgt in incoming:
                incoming[tgt] += 1

        start_nodes = [n for n in nodes if n.get("type") == "StartEvent"]
        if not start_nodes:
            start_id = "start_event"
            nodes.insert(0, {"id": start_id, "type": "StartEvent", "name": "Start"})
            entry_targets = [nid for nid, deg in incoming.items() if deg == 0]
            if not entry_targets and len(nodes) > 1:
                entry_targets = [nodes[1]["id"]]
            for tgt in entry_targets:
                edges.append({"id": f"flow_{start_id}_{tgt}", "source": start_id, "target": tgt})
                outgoing[start_id] = outgoing.get(start_id, 0) + 1
                incoming[tgt] = incoming.get(tgt, 0) + 1

        end_nodes = [n for n in nodes if n.get("type") == "EndEvent"]
        if not end_nodes:
            end_id = "end_event"
            nodes.append({"id": end_id, "type": "EndEvent", "name": "End"})
        else:
            end_id = end_nodes[0]["id"]

        # Wire any dangling non-EndEvent terminal nodes to EndEvent
        for n in nodes:
            nid = n.get("id")
            if not nid or n.get("type") == "EndEvent":
                continue
            if outgoing.get(nid, 0) == 0:
                edges.append({"id": f"flow_{nid}_{end_id}", "source": nid, "target": end_id})
                outgoing[nid] = 1

        return {"nodes": nodes, "edges": edges}

    async def upsert_workflow_from_yaml(
        self,
        session: AsyncSession,
        workflow_id: str,
        name: str,
        description: str,
        yaml_content: str,
        source: str = "workflow_activator",
    ) -> WorkflowModel:
        """
        Normalize lifecycle events, regenerate BPMN/YAML, persist WorkflowModel,
        and publish a `workflow.created` / `workflow.updated` Blackboard event.
        """
        parsed_g = self.parse_yaml_to_graph(yaml_content)
        normalized_g = self.ensure_lifecycle_events(
            {"nodes": parsed_g.get("nodes", []), "edges": parsed_g.get("edges", [])}
        )
        canonical_yaml = self.graph_to_yaml(workflow_id, name, description, normalized_g)
        bpmn_x = self.graph_to_bpmn_xml(workflow_id, name, normalized_g)

        existing_wf = await session.get(WorkflowModel, workflow_id)
        is_update = existing_wf is not None
        if existing_wf:
            existing_wf.name = name or existing_wf.name
            existing_wf.description = description or existing_wf.description
            existing_wf.state_graph = normalized_g
            existing_wf.yaml_source = canonical_yaml
            existing_wf.bpmn_xml = bpmn_x
            wf = existing_wf
        else:
            wf = WorkflowModel(
                id=workflow_id,
                name=name or workflow_id,
                description=description or "Architect-generated workflow",
                state_graph=normalized_g,
                yaml_source=canonical_yaml,
                bpmn_xml=bpmn_x,
            )
            session.add(wf)

        await session.commit()
        await session.refresh(wf)
        await self._publish_event(
            {
                "event": "workflow.updated" if is_update else "workflow.created",
                "workflow_id": wf.id,
                "name": wf.name,
                "source": source,
            }
        )
        return wf

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
            "VerificationTask": "bpmn:serviceTask",
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
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRunModel:
        """
        Create a WorkflowRun, post the initial specification/request to the Blackboard,
        and advance from StartEvent into the first executable node(s).
        """
        run_context = dict(context or {})
        if initial_context:
            run_context.update(initial_context)
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
            verdict = run.context.get("last_verdict", "PASS")
            if node.get("semantic_gate") or node.get("criteria"):
                from app.decision_engine import decision_engine

                latest_stmt = (
                    select(BlackboardEntryModel)
                    .where(BlackboardEntryModel.workflow_id == workflow.id)
                    .order_by(BlackboardEntryModel.created_at.desc())
                    .limit(1)
                )
                latest_res = await session.execute(latest_stmt)
                latest_entry = latest_res.scalar_one_or_none()
                artifact_text = (
                    latest_entry.content
                    if latest_entry is not None
                    else str(run.context.get("last_output") or "")
                )
                gate_res = await decision_engine.evaluate_gateway_verdict(
                    criteria=str(
                        node.get("criteria")
                        or f"Verify deliverable for gateway '{node.get('name', nid)}'."
                    ),
                    artifact=artifact_text,
                    fallback_verdict=str(verdict),
                    gate_name=f"workflow_gateway:{nid}",
                )
                verdict = gate_res.get("verdict") or verdict
                ctx = dict(run.context or {})
                ctx["last_verdict"] = verdict
                ctx["last_gateway_decision"] = gate_res
                run.context = ctx
                session.add(
                    BlackboardEntryModel(
                        workflow_id=workflow.id,
                        task_id=f"run-{run.id}-{nid}",
                        agent_id="decision-engine",
                        entry_type="test_result" if verdict == "PASS" else "critique",
                        content=(
                            f"⚖️ Semantic Gateway '{node.get('name', nid)}' verdict: "
                            f"**{verdict}** (conf={gate_res.get('confidence', 0.0):.2f}, "
                            f"provider={gate_res.get('provider', 'fallback')})"
                        ),
                        metadata_={
                            "gateway_node_id": nid,
                            "verdict": verdict,
                            "decision": gate_res,
                        },
                    )
                )
                await session.flush()

                if verdict == "ESCALATE_HITL":
                    current = list(run.current_nodes or [])
                    if nid not in current:
                        current.append(nid)
                    run.current_nodes = current
                    run.status = "AWAITING_APPROVAL"
                    ticket = HITLTicketModel(
                        workflow_id=workflow.id,
                        task_id=f"run-{run.id}-{nid}",
                        agent_id="decision-engine",
                        gate_type="workflow_gateway_escalation",
                        tool_id=nid,
                        tool_arguments={"verdict": verdict, "decision": gate_res},
                        status="PENDING",
                    )
                    session.add(ticket)
                    await session.commit()
                    return

            await self.advance_from_node(
                session,
                workflow,
                run,
                nid,
                branch_outcome=verdict,
            )
            return

        if ntype == "EndEvent":
            await self._complete_workflow_run(session, workflow, run, nid)
            return

        if ntype == "UserTask":
            # Check active workflow node auto-approval policy (Invariant 16)
            from app.models import ApprovalPolicyModel
            pol_stmt = select(ApprovalPolicyModel).where(
                ApprovalPolicyModel.enabled == True,
                ApprovalPolicyModel.policy_type == "workflow_node",
                ApprovalPolicyModel.workflow_id == workflow.id,
                ApprovalPolicyModel.node_id == nid,
            )
            pol_res = await session.execute(pol_stmt)
            node_policy = pol_res.scalar_one_or_none()

            if node_policy:
                auto_entry = BlackboardEntryModel(
                    workflow_id=workflow.id,
                    task_id=f"run-{run.id}-{nid}",
                    agent_id="workflow-engine",
                    entry_type="test_result",
                    content=(
                        f"🛡️ Auto-Approved Workflow Step: Step '{node.get('name', nid)}' (`{nid}`) "
                        f"automatically approved via active policy (ID: {node_policy.id})."
                    ),
                    metadata_={
                        "auto_approved": True,
                        "policy_id": str(node_policy.id),
                        "node_id": nid,
                    },
                )
                session.add(auto_entry)
                await session.flush()
                await self.advance_from_node(
                    session, workflow, run, nid, branch_outcome="PASS"
                )
                return

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
                from app.channel_gateway import channel_gateway
                import asyncio
                asyncio.create_task(channel_gateway.notify_hitl_ticket(ticket))
            except Exception:
                pass
            return

        if ntype == "VerificationTask" or (ntype == "ServiceTask" and node.get("verify_command")):
            await self._run_deterministic_verification(session, workflow, run, node)
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

    async def _complete_workflow_run(
        self,
        session: AsyncSession,
        workflow: WorkflowModel,
        run: WorkflowRunModel,
        nid: str,
    ) -> None:
        """Finalize run state, synthesize Blackboard summary, emit events, and notify channels."""
        import asyncio
        from sqlalchemy import desc

        completed = list(run.completed_nodes or [])
        if nid not in completed:
            completed.append(nid)
        run.completed_nodes = completed
        run.current_nodes = []
        run.status = "COMPLETED"

        ctx = run.context or {}
        origin_channel = ctx.get("origin_channel")
        origin_chat_id = ctx.get("origin_chat_id")

        # Collect verified_urls from run context and entry metadata
        verified_urls: List[str] = list(ctx.get("verified_urls") or [])
        # Also pull verified_urls from blackboard entries' metadata
        try:
            from sqlalchemy import select as sa_select
            from app.models import BlackboardEntryModel

            stmt = sa_select(BlackboardEntryModel).where(
                BlackboardEntryModel.workflow_id == workflow.id,
                BlackboardEntryModel.entry_type != "summary",
            )
            entry_res = await session.execute(stmt)
            for ent in entry_res.scalars().all():
                emeta = ent.metadata_ or {}
                entry_verified = emeta.get("verified_urls", []) or []
                verified_urls.extend(entry_verified)
        except Exception:
            pass

        def _clean_artifact(raw: str) -> str:
            cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", raw or "", flags=re.DOTALL)
            cleaned = re.sub(r"<tool_name>.*?</tool_name>", "", cleaned, flags=re.DOTALL)
            cleaned = re.sub(r"<arg_key>.*?</arg_key>", "", cleaned, flags=re.DOTALL)
            cleaned = re.sub(r"<arg_value>.*?</arg_value>", "", cleaned, flags=re.DOTALL)
            cleaned = re.sub(r"corund[a-zA-Z]*>", "", cleaned)
            cleaned = re.sub(r"<[^>]+>", "", cleaned)
            cleaned = re.sub(r"\n?---\n?\*\*MCP Tool Invoked.*?```json.*?```", "", cleaned, flags=re.DOTALL)
            cleaned = re.sub(r"\*\*MCP Tool Invoked.*?```json.*?```", "", cleaned, flags=re.DOTALL)
            return cleaned.strip()

        summary_lines: List[str] = []
        primary_deliverable: Optional[str] = None
        research_links_section: Optional[str] = None
        fallback_entries: List[Tuple[str, str, str]] = []

        try:
            filters = [
                BlackboardEntryModel.workflow_id == workflow.id,
                BlackboardEntryModel.entry_type != "summary",
            ]
            if getattr(run, "created_at", None) is not None:
                filters.append(BlackboardEntryModel.created_at >= run.created_at)
            entry_stmt = (
                select(BlackboardEntryModel)
                .where(*filters)
                .order_by(desc(BlackboardEntryModel.created_at), desc(BlackboardEntryModel.id))
                .limit(10)
            )
            entry_res = await session.execute(entry_stmt)
            fetched = list(reversed(entry_res.scalars().all()))
            for ent in fetched:
                clean = _clean_artifact(ent.content or "")
                if not clean:
                    continue
                if ent.agent_id == "operator":
                    continue
                if ent.agent_id == "architect" or (ent.entry_type == "spec" and ent.agent_id != "operator"):
                    primary_deliverable = clean
                elif ent.agent_id == "researcher" or ent.entry_type in ("research", "researcher"):
                    if "http://" in clean or "https://" in clean or "**Web Search Results:**" in clean:
                        research_links_section = clean
                    if not primary_deliverable:
                        primary_deliverable = clean
                fallback_entries.append((ent.agent_id, ent.entry_type, clean))
        except Exception as exc:
            logger.debug("Failed to fetch deliverable entries: %s", exc)

        problem = (ctx.get("initial_prompt") or "").strip()
        if primary_deliverable:
            summary_lines.append(primary_deliverable)
            if research_links_section and research_links_section != primary_deliverable:
                if "http://" not in primary_deliverable and "https://" not in primary_deliverable:
                    summary_lines.append(f"\n**Research & Links:**\n{research_links_section}")
        else:
            if problem:
                summary_lines.append(f"**Goal:** {problem[:280]}")
            for agent_id, entry_type, clean in fallback_entries[-3:]:
                summary_lines.append(f"**{agent_id}** ({entry_type}):\n{clean}")

        if not summary_lines:
            summary_lines.append(f"Completed workflow `{workflow.id}`.")

        summary_text = "\n\n".join(summary_lines)

        # Sanitize and ground links in summary text to prevent hallucinated URLs leaking
        sanitized_text, grounding_report = sanitize_and_ground_links(
            summary_text, verified_urls=verified_urls
        )

        try:
            session.add(
                BlackboardEntryModel(
                    workflow_id=workflow.id,
                    task_id=str(run.id),
                    agent_id="workflow-engine",
                    entry_type="summary",
                    content=sanitized_text,
                    metadata_={
                        "run_id": str(run.id),
                        "status": "COMPLETED",
                        "origin_channel": origin_channel,
                        "verified_urls": verified_urls,
                        "grounding_report": grounding_report,
                    },
                )
            )
        except Exception as exc:
            logger.debug("Failed to persist EndEvent summary entry: %s", exc)

        await self._publish_event(
            {
                "event": "workflow.completed",
                "workflow_id": workflow.id,
                "run_id": str(run.id),
                "node_id": nid,
                "status": "COMPLETED",
                "summary": sanitized_text,
                "origin_channel": origin_channel,
            }
        )
        try:
            from app.channel_gateway import channel_gateway

            meta = {"node_id": nid}
            if origin_chat_id:
                meta["chat_id"] = origin_chat_id
            asyncio.create_task(
                channel_gateway.notify_workflow_completed(
                    workflow_id=workflow.id,
                    run_id=str(run.id),
                    summary=sanitized_text,
                    status="COMPLETED",
                    origin_channel=origin_channel,
                    metadata=meta,
                )
            )
        except Exception:
            pass

    async def _run_deterministic_verification(
        self,
        session: AsyncSession,
        workflow: WorkflowModel,
        run: WorkflowRunModel,
        node: Dict[str, Any],
    ) -> None:
        """
        Execute a deterministic VerificationTask / verify_command without an LLM turn,
        feeding failures back to the Blackboard as `critique` with `last_verdict = 'REWORK'`
        and enforcing `max_iterations` to prevent infinite loops (Archon harness pattern).
        """
        import asyncio
        from app.mcp_gateway import mcp_gateway
        from app.schemas import ToolInvokeRequest
        from app.web_grounding import verify_deliverable_links

        nid = node["id"]
        verify_mode = node.get("verify_mode", "") or ""

        # Built-in deterministic web-link verification mode
        if verify_mode == "web_links":
            ctx = dict(run.context or {})
            initial_prompt = ctx.get("initial_prompt", "") or ""

            # Collect deliverable text from blackboard entries
            # The primary deliverable is typically the first non-operator entry
            primary_deliverable = ""
            research_section = ""

            try:
                stmt = select(BlackboardEntryModel).where(
                    BlackboardEntryModel.workflow_id == workflow.id,
                    BlackboardEntryModel.entry_type != "summary",
                    BlackboardEntryModel.agent_id != "operator",
                ).order_by(
                    BlackboardEntryModel.created_at.asc(),
                )
                entry_res = await session.execute(stmt)
                entries = list(entry_res.scalars().all())

                for ent in entries:
                    clean = (ent.content or "").strip()
                    if not clean:
                        continue
                    if "http://" in clean or "https://" in clean or "**Web Search Results:**" in clean:
                        research_section = clean
                    if not primary_deliverable:
                        primary_deliverable = clean
            except Exception as exc:
                logger.debug("Failed to fetch entries for web_links verification: %s", exc)

            # If we have a primary deliverable, verify its links
            if primary_deliverable:
                # Pull verified_urls from context
                verified_urls = ctx.get("verified_urls") or []
                # Also check metadata from entries for verified_urls
                try:
                    stmt2 = select(BlackboardEntryModel).where(
                        BlackboardEntryModel.workflow_id == workflow.id,
                        BlackboardEntryModel.entry_type != "summary",
                    )
                    entry_res2 = await session.execute(stmt2)
                    for ent in entry_res2.scalars().all():
                        emeta = ent.metadata_ or {}
                        entry_verified = emeta.get("verified_urls", []) or []
                        verified_urls.extend(entry_verified)
                except Exception:
                    pass

                result = verify_deliverable_links(
                    primary_deliverable, verified_urls=verified_urls, require_links=False
                )

                # Persist the outcome to the Blackboard
                session.add(
                    BlackboardEntryModel(
                        workflow_id=workflow.id,
                        task_id=str(run.id),
                        agent_id=node.get("agent_id") or "qa-tester",
                        entry_type="test_result" if result["passed"] else "critique",
                        content=(
                            f"Web link verification (verify_mode='web_links') "
                            f"{'PASSED' if result['passed'] else 'FAILED'}:\n"
                            f"Issues: {', '.join(result['issues']) if result['issues'] else 'none'}\n"
                            f"Report: {result['report']}"
                        ),
                        metadata_={"node_id": nid, "verify_mode": "web_links", "result": str(result["passed"])},
                    )
                )

                if result["passed"]:
                    ctx["last_verdict"] = "PASS"
                    run.context = ctx
                    await self.advance_from_node(session, workflow, run, nid, branch_outcome="PASS")
                    return
                else:
                    ctx["last_verdict"] = "FAIL"
                    run.context = ctx
                    run.status = "FAILED"
                    run.current_nodes = []
                    fail_summary = (
                        f"**Goal:** {initial_prompt}\n"
                        f"**Failed Gate:** `{nid}` (web_links verification) after 1 attempt.\n"
                        f"Issues: {'; '.join(result['issues'])}"
                    )
                    # Persist failure summary
                    session.add(
                        BlackboardEntryModel(
                            workflow_id=workflow.id,
                            task_id=str(run.id),
                            agent_id="workflow-engine",
                            entry_type="summary",
                            content=fail_summary,
                            metadata_={"run_id": str(run.id), "stage": "VerificationFailure"},
                        )
                    )
                    await self._publish_event(
                        {
                            "event": "workflow.verification_failed",
                            "workflow_id": workflow.id,
                            "run_id": str(run.id),
                            "node_id": nid,
                            "verification_mode": "web_links",
                            "issues": result["issues"],
                        }
                    )
                    return
            else:
                # No deliverable text found, treat as PASS (no links to verify)
                ctx["last_verdict"] = "PASS"
                run.context = ctx
                await self.advance_from_node(session, workflow, run, nid, branch_outcome="PASS")
                return

        # Original: bash_executor-based verification
        cmd = (node.get("verify_command") or node.get("command") or "").strip()
        if not cmd:
            await self.advance_from_node(session, workflow, run, nid, branch_outcome="PASS")
            return

        ctx = dict(run.context or {})
        retry_counts = dict(ctx.get("retry_counts") or {})
        attempt = int(retry_counts.get(nid, 0)) + 1
        max_iter = max(1, int(node.get("max_iterations") or 3))
        agent_id = node.get("agent_id") or "qa-tester"

        invoke_res = await mcp_gateway.invoke_tool(
            session,
            ToolInvokeRequest(
                agent_id=agent_id,
                workflow_id=workflow.id,
                task_id=str(run.id),
                tool_id="bash_executor",
                arguments={"command": cmd, "timeout": min(int(node.get("timeout") or 60), 60)},
                bypass_approval=bool(node.get("trusted_command", False)),
            ),
        )

        if invoke_res.status == "PENDING_APPROVAL":
            run.status = "AWAITING_APPROVAL"
            run.current_nodes = [nid]
            await self._publish_event(
                {
                    "event": "workflow.awaiting_approval",
                    "workflow_id": workflow.id,
                    "run_id": str(run.id),
                    "node_id": nid,
                    "ticket_id": invoke_res.ticket_id,
                }
            )
            return

        res_dict = invoke_res.result if isinstance(invoke_res.result, dict) else {}
        exit_code = int(res_dict.get("exit_code", 0 if invoke_res.status == "EXECUTED" else 1))
        output_text = (
            res_dict.get("stdout")
            or res_dict.get("stderr")
            or res_dict.get("output")
            or res_dict.get("error")
            or getattr(invoke_res, "error", None)
            or f"Tool status: {invoke_res.status}"
        ).strip()
        passed = exit_code == 0 and invoke_res.status == "EXECUTED"

        # Persist deterministic check outcome to the Blackboard for downstream context
        session.add(
            BlackboardEntryModel(
                workflow_id=workflow.id,
                task_id=str(run.id),
                agent_id=agent_id,
                entry_type="test_result" if passed else "critique",
                content=(
                    f"Deterministic check `{cmd}` (attempt {attempt}/{max_iter}) "
                    f"{'PASSED' if passed else f'FAILED (exit {exit_code})'}:\n{output_text[:1200]}"
                ),
                metadata_={"node_id": nid, "exit_code": exit_code, "attempt": attempt},
            )
        )

        if passed:
            retry_counts[nid] = 0
            ctx["retry_counts"] = retry_counts
            ctx["last_verdict"] = "PASS"
            run.context = ctx
            await self.advance_from_node(session, workflow, run, nid, branch_outcome="PASS")
            return

        retry_counts[nid] = attempt
        ctx["retry_counts"] = retry_counts

        if invoke_res.status == "BLOCKED_RBAC" or attempt >= max_iter:
            ctx["last_verdict"] = "FAIL_LIMIT"
            run.context = ctx
            run.status = "FAILED"
            run.current_nodes = []
            fail_summary = (
                f"**Goal:** {ctx.get('initial_prompt', workflow.id)}\n"
                f"**Failed Gate:** `{nid}` (`{cmd}`) after {attempt}/{max_iter} attempts.\n"
                f"**Last Error:** {output_text[:400]}"
            )
            await self._publish_event(
                {
                    "event": "workflow.failed",
                    "workflow_id": workflow.id,
                    "run_id": str(run.id),
                    "node_id": nid,
                    "status": "FAILED",
                    "summary": fail_summary,
                }
            )
            try:
                from app.channel_gateway import channel_gateway

                asyncio.create_task(
                    channel_gateway.notify_workflow_completed(
                        workflow_id=workflow.id,
                        run_id=str(run.id),
                        summary=fail_summary,
                        status="FAILED",
                        origin_channel=ctx.get("origin_channel"),
                        metadata={"node_id": nid},
                    )
                )
            except Exception:
                pass
            return

        ctx["last_verdict"] = "REWORK"
        run.context = ctx
        await self.advance_from_node(session, workflow, run, nid, branch_outcome="REWORK")

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
