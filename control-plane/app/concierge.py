import hashlib
import json
import logging
import math
import os
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentMemoryModel,
    AgentModel,
    BacklogItemModel,
    BlackboardEntryModel,
    HITLTicketModel,
    MCPToolModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.schemas import ConciergeChatRequest, ConciergeChatResponse
from app.workflow_engine import workflow_engine

logger = logging.getLogger("blackboard.concierge")

LOCAL_OLLAMA_URL = os.getenv("LOCAL_OLLAMA_URL", "http://192.168.8.136:11434")
CONCIERGE_OPENAI_URL = os.getenv(
    "CONCIERGE_OPENAI_URL", "http://192.168.8.180:8081/v1"
)
CONCIERGE_MODEL = os.getenv("CONCIERGE_MODEL", "qwen3-4b")
DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "qwen2.5-coder:0.5b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "768"))


async def compute_embedding(text_input: str) -> List[float]:
    """
    Generate a 768-dim embedding via local/LAN Ollama (`nomic-embed-text`),
    falling back to a deterministic unit-normalized semantic hash vector when
    the LAN Ollama server is offline.
    """
    try:
        async with httpx.AsyncClient(timeout=3.5) as client:
            resp = await client.post(
                f"{LOCAL_OLLAMA_URL.rstrip('/')}/api/embeddings",
                json={"model": EMBEDDING_MODEL, "prompt": text_input},
            )
            if resp.status_code == 200:
                data = resp.json()
                emb = data.get("embedding")
                if isinstance(emb, list) and len(emb) == EMBEDDING_DIMENSION:
                    return [float(x) for x in emb]
    except Exception:
        pass

    # Deterministic 768-dimensional unit-vector fallback for offline/testing
    vec = [0.0] * EMBEDDING_DIMENSION
    tokens = [
        tok.strip(".,:;!?()[]{}\"'").lower()
        for tok in text_input.split()
        if tok.strip()
    ]
    if not tokens:
        tokens = ["empty"]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, len(digest), 2):
            idx = ((digest[i] << 8) | digest[i + 1]) % EMBEDDING_DIMENSION
            sign = 1.0 if (digest[i] & 1) == 0 else -1.0
            vec[idx] += sign * (1.0 + (digest[i + 1] / 255.0))
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


class ConciergeAdminAgent:
    """
    Administrative Concierge Agent equipped with internal MCP meta-tools
    to inspect and mutate agents, workflows, memories, HITL gates, and blackboard state.
    """

    META_TOOLS = [
        {
            "name": "list_agents",
            "description": "List all registered agents and their inference/tool configurations.",
        },
        {
            "name": "upsert_agent",
            "description": "Create or update an agent persona in PostgreSQL.",
        },
        {
            "name": "inject_steering",
            "description": "Write an operator steering instruction onto the active Blackboard.",
        },
        {
            "name": "trigger_workflow",
            "description": "Start a new execution run of a registered workflow.",
        },
        {
            "name": "prune_blackboard",
            "description": "Run the automated summarizer to condense older blackboard entries.",
        },
        {
            "name": "capture_idea",
            "description": "Capture an idea, feature request, or future task into the persistent Backlog table.",
        },
        {
            "name": "list_backlog",
            "description": "List active backlog items, ideas, and their statuses.",
        },
        {
            "name": "inspect_platform_state",
            "description": "Inspect counts and recent entries across agents, tools, workflows, memories, and HITL tickets.",
        },
    ]

    async def handle_chat(
        self, session: AsyncSession, req: ConciergeChatRequest
    ) -> ConciergeChatResponse:
        msg = req.message.strip()
        lowered = msg.lower()
        tool_calls_executed: List[Dict[str, Any]] = []

        # 1. Execute administrative meta-tools triggered by intent or explicit command
        if any(
            k in lowered
            for k in ["steer:", "steer ", "instruct ", "override "]
        ):
            wf_id = req.workflow_id or "code_review_pipeline"
            steering_entry = BlackboardEntryModel(
                workflow_id=wf_id,
                task_id="concierge-steering",
                agent_id="concierge-admin",
                entry_type="steering",
                content=msg,
                metadata_={"source": "concierge_meta_tool"},
            )
            session.add(steering_entry)
            await session.commit()
            await session.refresh(steering_entry)
            await workflow_engine._publish_event(
                {
                    "event": "blackboard.entry_created",
                    "id": str(steering_entry.id),
                    "workflow_id": wf_id,
                    "task_id": steering_entry.task_id,
                    "agent_id": steering_entry.agent_id,
                    "entry_type": "steering",
                    "content": steering_entry.content,
                }
            )
            tool_calls_executed.append(
                {
                    "tool": "inject_steering",
                    "workflow_id": wf_id,
                    "entry_id": str(steering_entry.id),
                }
            )

        if any(
            k in lowered
            for k in ["run workflow", "start workflow", "trigger pipeline"]
        ):
            wf_id = req.workflow_id or "code_review_pipeline"
            wf = await session.get(WorkflowModel, wf_id)
            if wf:
                run = await workflow_engine.start_workflow_run(
                    session, wf, initial_prompt=msg
                )
                tool_calls_executed.append(
                    {
                        "tool": "trigger_workflow",
                        "workflow_id": wf_id,
                        "run_id": str(run.id),
                        "status": run.status,
                    }
                )

        if any(
            k in lowered for k in ["prune", "summarize blackboard", "condense"]
        ):
            wf_id = req.workflow_id or "code_review_pipeline"
            summary = await workflow_engine.prune_and_summarize_blackboard(
                session, wf_id, threshold=2, keep_recent=1
            )
            tool_calls_executed.append(
                {
                    "tool": "prune_blackboard",
                    "workflow_id": wf_id,
                    "summary_created": bool(summary),
                    "summary_id": str(summary.id) if summary else None,
                }
            )

        # Meta-Workflow Trigger: create workflow / architect workflow intent
        if any(
            k in lowered for k in [
                "create workflow", "new workflow", "build workflow",
                "create a workflow", "architect workflow", "design workflow",
                "create agent", "new agent"
            ]
        ):
            meta_wf = await session.get(WorkflowModel, "meta_workflow_architect")
            if meta_wf:
                run = await workflow_engine.start_workflow_run(
                    session, meta_wf, initial_prompt=msg
                )
                tool_calls_executed.append(
                    {
                        "tool": "trigger_meta_workflow",
                        "workflow_id": "meta_workflow_architect",
                        "run_id": str(run.id),
                        "status": run.status,
                        "description": "Triggered Meta-Workflow & Agent Architect Pipeline for Architect synthesis and Security Review.",
                    }
                )

        # Backlog & Idea Capture
        if any(
            k in lowered for k in [
                "capture this as a future feature", "table this for now",
                "capture this", "add to backlog", "save idea", "log idea",
                "capture idea", "future feature", "table this", "add idea", "new idea"
            ]
        ):
            clean_title = msg
            for prefix in [
                "capture this as a future feature", "table this for now but",
                "table this for now", "capture this", "add to backlog", "save idea",
                "log idea", "capture idea", "add idea", "new idea", ":"
            ]:
                if prefix in clean_title.lower():
                    idx = clean_title.lower().find(prefix) + len(prefix)
                    clean_title = clean_title[idx:].strip(" :,.-")
            if not clean_title or len(clean_title) < 5:
                clean_title = "Feature / Backlog Request: " + msg[:50]
            else:
                clean_title = clean_title[:100]

            count_res = await session.execute(select(func.count(BacklogItemModel.id)))
            next_num = (count_res.scalar_one() or 0) + 1
            key = f"IDEA-{next_num}"

            status_val = "tabled" if ("table this" in lowered or "for now" in lowered) else "idea"
            b_item = BacklogItemModel(
                key=key,
                title=clean_title,
                description=msg,
                category="feature",
                priority="P1",
                status=status_val,
                tags=["chat_capture", "operator_idea"],
                metadata_={"source": "concierge_chat", "raw_message": msg},
            )
            session.add(b_item)
            await session.commit()
            await session.refresh(b_item)
            await workflow_engine._publish_event(
                {
                    "event": "backlog.created",
                    "id": str(b_item.id),
                    "key": b_item.key,
                    "title": b_item.title,
                    "status": b_item.status,
                }
            )
            tool_calls_executed.append(
                {
                    "tool": "capture_idea",
                    "key": b_item.key,
                    "title": b_item.title,
                    "status": b_item.status,
                }
            )

        # List Backlog Intent
        if any(
            k in lowered for k in [
                "show backlog", "list backlog", "what ideas", "view ideas",
                "show ideas", "backlog items", "what is on the backlog"
            ]
        ):
            b_res = await session.execute(
                select(BacklogItemModel).order_by(BacklogItemModel.created_at.desc()).limit(10)
            )
            b_items = b_res.scalars().all()
            tool_calls_executed.append(
                {
                    "tool": "list_backlog",
                    "count": len(b_items),
                    "items": [
                        {
                            "key": i.key,
                            "title": i.title,
                            "status": i.status,
                            "priority": i.priority,
                        }
                        for i in b_items
                    ],
                }
            )

        state_snapshot = await self._build_state_snapshot(
            session, req.workflow_id
        )
        tool_calls_executed.append(
            {
                "tool": "inspect_platform_state",
                "result": {
                    "agents_count": len(state_snapshot["agents"]),
                    "tools_count": len(state_snapshot["tools"]),
                    "workflows_count": len(state_snapshot["workflows"]),
                    "pending_hitl_count": len(state_snapshot["pending_hitl"]),
                },
            }
        )

        # Attempt LLM synthesis via LAN Ollama if reachable; otherwise synthesize rich admin report
        llm_reply = await self._try_ollama_chat(
            msg, state_snapshot, tool_calls_executed
        )
        if not llm_reply:
            llm_reply = self._format_deterministic_reply(
                msg, state_snapshot, tool_calls_executed
            )

        return ConciergeChatResponse(
            reply=llm_reply,
            tool_calls_executed=tool_calls_executed,
            state_snapshot=state_snapshot,
        )

    async def _build_state_snapshot(
        self, session: AsyncSession, workflow_id: Optional[str] = None
    ) -> Dict[str, Any]:
        agents_res = await session.execute(select(AgentModel))
        agents = [
            {
                "id": a.id,
                "name": a.name,
                "role": a.role,
                "provider": (a.inference_config or {}).get(
                    "provider", "ollama"
                ),
                "model": (a.inference_config or {}).get("model_name", ""),
                "allowed_tools": a.allowed_tools,
            }
            for a in agents_res.scalars().all()
        ]

        tools_res = await session.execute(select(MCPToolModel))
        tools = [
            {
                "id": t.id,
                "server_name": t.server_name,
                "requires_approval": t.requires_approval,
            }
            for t in tools_res.scalars().all()
        ]

        wf_res = await session.execute(select(WorkflowModel))
        workflows = [
            {"id": w.id, "name": w.name} for w in wf_res.scalars().all()
        ]

        hitl_res = await session.execute(
            select(HITLTicketModel).where(HITLTicketModel.status == "PENDING")
        )
        pending_hitl = [
            {
                "id": str(h.id),
                "workflow_id": h.workflow_id,
                "agent_id": h.agent_id,
                "gate_type": h.gate_type,
                "tool_id": h.tool_id,
            }
            for h in hitl_res.scalars().all()
        ]

        bb_stmt = select(BlackboardEntryModel).order_by(
            BlackboardEntryModel.created_at.desc()
        )
        if workflow_id:
            bb_stmt = bb_stmt.where(
                BlackboardEntryModel.workflow_id == workflow_id
            )
        bb_res = await session.execute(bb_stmt.limit(6))
        recent_entries = [
            {
                "id": str(e.id),
                "workflow_id": e.workflow_id,
                "agent_id": e.agent_id,
                "entry_type": e.entry_type,
                "content_preview": e.content[:140],
            }
            for e in bb_res.scalars().all()
        ]

        mem_count_res = await session.execute(
            select(func.count(AgentMemoryModel.id))
        )
        total_memories = mem_count_res.scalar() or 0

        return {
            "agents": agents,
            "tools": tools,
            "workflows": workflows,
            "pending_hitl": pending_hitl,
            "recent_blackboard_entries": recent_entries,
            "total_memories": total_memories,
        }

    async def _try_ollama_chat(
        self,
        user_msg: str,
        snapshot: Dict[str, Any],
        executed_tools: List[Dict[str, Any]],
    ) -> Optional[str]:
        system_prompt = (
            "You are the Concierge Admin Agent for the Multi-Agent Blackboard Control Plane. "
            "Use the provided live platform state snapshot and executed meta-tool results "
            "to answer the operator concisely in under 100 words."
        )
        compact_summary = {
            "agents": [a["id"] for a in snapshot.get("agents", [])],
            "workflows": [w["id"] for w in snapshot.get("workflows", [])],
            "pending_hitl_count": len(snapshot.get("pending_hitl", [])),
            "total_memories": snapshot.get("total_memories", 0),
        }
        messages_payload = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Operator request: {user_msg}\n"
                    f"Executed meta-tools: {json.dumps(executed_tools)}\n"
                    f"Live state summary: {json.dumps(compact_summary)}"
                ),
            },
        ]
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                resp = await client.post(
                    f"{CONCIERGE_OPENAI_URL.rstrip('/')}/chat/completions",
                    json={
                        "model": CONCIERGE_MODEL,
                        "temperature": 0.2,
                        "max_tokens": 160,
                        "chat_template_kwargs": {"enable_thinking": False},
                        "messages": messages_payload,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices") or []
                    if choices:
                        msg = choices[0].get("message") or {}
                        content = msg.get("content") or msg.get("reasoning_content")
                        if content:
                            return content.strip()
        except Exception as exc:
            logger.warning("Concierge local LLM call failed (%s); checking NIM fallback.", exc)

        # Fallback to NVIDIA NIM free endpoint if API key is configured
        try:
            from app.database import get_redis

            redis_client = await get_redis()
            nim_key = (
                await redis_client.get("llm:config:nim_api_key")
                or os.getenv("NVIDIA_NIM_API_KEY", "")
            )
            nim_model = (
                await redis_client.get("llm:config:default_nim_model")
                or os.getenv("NVIDIA_NIM_DEFAULT_MODEL", "poolside/laguna-xs-2.1")
            )
            nim_base = os.getenv(
                "NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
            ).rstrip("/")
            if nim_key:
                async with httpx.AsyncClient(timeout=22.0) as client:
                    resp = await client.post(
                        f"{nim_base}/chat/completions",
                        headers={"Authorization": f"Bearer {nim_key}"},
                        json={
                            "model": nim_model,
                            "temperature": 0.2,
                            "max_tokens": 160,
                            "messages": messages_payload,
                        },
                    )
                    if resp.status_code == 200:
                        choices = resp.json().get("choices") or []
                        if choices:
                            msg = choices[0].get("message") or {}
                            content = msg.get("content") or msg.get("reasoning_content")
                            if content:
                                return content.strip()
        except Exception as exc:
            logger.warning("Concierge NVIDIA NIM fallback failed: %s", exc)
        return None

    def _format_deterministic_reply(
        self,
        user_msg: str,
        snapshot: Dict[str, Any],
        executed_tools: List[Dict[str, Any]],
    ) -> str:
        agent_list = ", ".join(
            f"`{a['id']}` ({a['role']})" for a in snapshot["agents"]
        )
        wf_list = ", ".join(f"`{w['id']}`" for w in snapshot["workflows"])
        tool_names = ", ".join(
            f"`{t['tool']}`" for t in executed_tools if "tool" in t
        )
        lines = [
            f"**Concierge Admin Report** (Meta-tools invoked: {tool_names})",
            f"- **Active Agents ({len(snapshot['agents'])}):** {agent_list or 'None'}",
            f"- **Registered MCP Tools:** {len(snapshot['tools'])} (RBAC enforced + HITL gates active)",
            f"- **Workflows ({len(snapshot['workflows'])}):** {wf_list or 'None'}",
            f"- **Vector Memories Stored (`pgvector`):** {snapshot['total_memories']}",
            f"- **Pending HITL Approval Tickets:** {len(snapshot['pending_hitl'])}",
        ]
        if any(t["tool"] == "inject_steering" for t in executed_tools):
            lines.append(
                "- **Operator Steering Injected:** Published `entry_type='steering'` to the Blackboard; active workers will consume it on their next turn."
            )
        if any(t["tool"] == "trigger_workflow" for t in executed_tools):
            lines.append(
                "- **Workflow Run Triggered:** Tasks have been pushed to Redis Stream `stream:tasks`."
            )
        if any(t["tool"] == "prune_blackboard" for t in executed_tools):
            lines.append(
                "- **Blackboard Pruned:** Older debate entries were condensed into a `summary` entry."
            )
        if any(t["tool"] == "trigger_meta_workflow" for t in executed_tools):
            lines.append(
                "- **Meta-Architecture Pipeline Triggered:** Dispatched to `architect` to synthesize the workflow specification, followed by `sec-auditor` RBAC review and your final HITL activation gate."
            )
        if any(t["tool"] == "capture_idea" for t in executed_tools):
            item = next(t for t in executed_tools if t["tool"] == "capture_idea")
            lines.append(
                f"- **Backlog Item Captured (`{item['key']}`):** '{item['title']}' saved with status `{item['status']}`. You can review it in the Backlog view or run the Intake Pipeline anytime."
            )
        if any(t["tool"] == "list_backlog" for t in executed_tools):
            b_tool = next(t for t in executed_tools if t["tool"] == "list_backlog")
            items_str = "\n".join(
                f"  • `[{it['key']}]` ({it['priority']}) {it['title']} — *{it['status']}*"
                for it in b_tool.get("items", [])
            )
            lines.append(
                f"- **Backlog & Ideas ({b_tool['count']}):**\n{items_str or '  (No backlog items)'}"
            )
        return "\n".join(lines)


concierge_agent = ConciergeAdminAgent()
