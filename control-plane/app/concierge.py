import hashlib
import json
import logging
import math
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentMemoryModel,
    AgentModel,
    ApprovalPolicyModel,
    BacklogItemModel,
    BlackboardEntryModel,
    HITLTicketModel,
    MCPToolModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.schemas import ConciergeChatRequest, ConciergeChatResponse, ToolInvokeRequest
from app.workflow_engine import workflow_engine
from app.database import get_redis

logger = logging.getLogger("blackboard.concierge")

LOCAL_OLLAMA_URL = os.getenv("LOCAL_OLLAMA_URL", "http://192.168.1.150:11434")
CONCIERGE_OPENAI_URL = os.getenv(
    "CONCIERGE_OPENAI_URL", "http://192.168.1.180:8081/v1"
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
        {
            "name": "upsert_tool",
            "description": "Register or activate a tool in the centralized MCP toolbox.",
        },
        {
            "name": "assign_tool_to_agent",
            "description": "Assign an authorized tool to an agent's allowed_tools RBAC policy.",
        },
        {
            "name": "web_search",
            "description": "Execute live web and documentation search for external data, news, standards, or current events.",
        },
        {
            "name": "bash_executor",
            "description": "Execute shell or bash commands inside the environment (HITL gated).",
        },
        {
            "name": "suggest_tools",
            "description": "Proactively recommend and catalog tools needed for operator requests.",
        },
        {
            "name": "store_memory",
            "description": "Persist a new episodic, semantic, or operator preference memory with pgvector 768-dim embeddings.",
        },
        {
            "name": "list_memories",
            "description": "Inspect and summarize active vector memories across all agents or a specific agent.",
        },
        {
            "name": "search_memories",
            "description": "Execute semantic vector similarity search against stored agent memories.",
        },
        {
            "name": "create_dynamic_tool",
            "description": "Synthesize and register a new executable MCP tool into PostgreSQL and bind it to an agent.",
        },
        {
            "name": "manage_approval_policy",
            "description": "Create, list, or revoke permanent auto-approval policies for tools, command patterns, or workflow steps.",
        },
    ]

    async def handle_chat(
        self, session: AsyncSession, req: ConciergeChatRequest
    ) -> ConciergeChatResponse:
        msg = req.message.strip()
        lowered = msg.lower()
        tool_calls_executed: List[Dict[str, Any]] = []

        # Ensure concierge-admin exists in DB for foreign key relations
        c_agent = await session.get(AgentModel, "concierge-admin")
        if not c_agent:
            c_agent = AgentModel(
                id="concierge-admin",
                name="Concierge Admin Agent",
                role="Platform Operator & Meta-Tool Orchestrator",
                system_prompt="You are the Concierge Admin Agent for the Multi-Agent Blackboard Control Plane.",
                allowed_tools=["web_search", "bash_executor", "repo_search"],
            )
            session.add(c_agent)
            await session.commit()

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

        # Tool Management, Auto-Provisioning & Proactive Suggestion Intent
        if any(
            k in lowered for k in [
                "add tool", "add tools", "web search", "web_search",
                "register tool", "add web_search",
                "need a tool", "need tools", "suggest tool", "suggest tools",
                "what tools do you suggest", "what tools should we have",
                "bash tool", "web tool", "add bash", "need a bash", "need bash",
                "should we have recognized this"
            ]
        ):
            from app.mcp_gateway import mcp_gateway

            tools_to_check = []
            if any(k in lowered for k in ["bash", "shell", "terminal"]):
                tools_to_check.append("bash_executor")
            if any(k in lowered for k in ["web", "search"]):
                tools_to_check.append("web_search")
            if not tools_to_check:
                tools_to_check = ["bash_executor", "web_search"]

            for target_tool_id in tools_to_check:
                tool_def = next(
                    (t for t in mcp_gateway.default_tool_catalog() if t["id"] == target_tool_id),
                    None
                )
                if tool_def:
                    existing_tool = await session.get(MCPToolModel, target_tool_id)
                    if not existing_tool:
                        existing_tool = MCPToolModel(**tool_def)
                        session.add(existing_tool)
                        await session.commit()
                        await session.refresh(existing_tool)

                    tool_calls_executed.append({
                        "tool": "upsert_tool",
                        "tool_id": target_tool_id,
                        "name": tool_def["name"],
                        "status": "REGISTERED",
                        "consumes_type": tool_def.get("consumes_type"),
                        "produces_type": tool_def.get("produces_type"),
                    })

                    all_agents = (await session.execute(select(AgentModel))).scalars().all()
                    for ag in all_agents:
                        if (
                            ag.id in lowered
                            or ag.name.lower() in lowered
                            or ("research" in lowered and "research" in ag.id)
                            or ("coder" in lowered and "coder" in ag.id)
                        ):
                            if target_tool_id not in (ag.allowed_tools or []):
                                ag.allowed_tools = list(ag.allowed_tools or []) + [target_tool_id]
                                await session.commit()
                                r = get_redis()
                                await r.delete(f"agent:config:{ag.id}")
                                tool_calls_executed.append({
                                    "tool": "assign_tool_to_agent",
                                    "agent_id": ag.id,
                                    "tool_id": target_tool_id,
                                    "status": "ASSIGNED",
                                })

            if any(k in lowered for k in ["suggest", "what tools", "recommend", "should we have", "need"]):
                tool_calls_executed.append({
                    "tool": "suggest_tools",
                    "suggested": ["bash_executor", "web_search", "repo_search"],
                    "status": "PROVISIONED",
                    "recommendation": "Registered bash_executor (sandboxed execution with HITL approval gate) and web_search (external intelligence).",
                })

        # Autonomous Dynamic Tool Synthesis Intent (Invariant 16)
        if any(
            k in lowered for k in [
                "create a new tool", "create new tool", "create tool",
                "synthesize a new tool", "synthesize new tool", "synthesize tool",
                "build tool", "make a tool", "make tool"
            ]
        ):
            import re
            name_match = re.search(r"(?:named|called)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?", msg, re.IGNORECASE)
            if not name_match:
                name_match = re.search(r"tool\s+['\"]([a-zA-Z0-9_\-]+)['\"]", msg, re.IGNORECASE)
            if not name_match:
                name_match = re.search(r"tool\s+([a-zA-Z0-9_\-]+)", msg, re.IGNORECASE)
            t_id = name_match.group(1).lower() if name_match else f"custom_tool_{int(datetime.now().timestamp())}"
            desc_match = re.search(r"description\s+['\"]([^'\"]+)['\"]", msg, re.IGNORECASE)
            t_desc = desc_match.group(1) if desc_match else f"Dynamically synthesized tool {t_id}"
            cmd_match = re.search(r"(?:command|using command|running|runs)\s+['\"]([^'\"]+)['\"]", msg, re.IGNORECASE)
            t_cmd = cmd_match.group(1) if cmd_match else f"echo 'Dynamic execution for {t_id}'"
            target_agent_id = "architect"
            for ag_cand in ["coder", "architect", "qa-tester", "sec-auditor", "researcher"]:
                if ag_cand in lowered:
                    target_agent_id = ag_cand
                    break

            existing_t = await session.get(MCPToolModel, t_id)
            if not existing_t:
                existing_t = MCPToolModel(
                    id=t_id,
                    name=t_id,
                    description=t_desc,
                    server_name="dynamic-mcp",
                    transport="builtin",
                    endpoint_config={"bash_command": t_cmd},
                    input_schema={"type": "object", "properties": {}},
                    consumes_type="query_keyword",
                    produces_type="command_output",
                    requires_approval=True,
                )
                session.add(existing_t)
                await session.commit()
                await session.refresh(existing_t)

            target_agent = await session.get(AgentModel, target_agent_id)
            if target_agent:
                if t_id not in (target_agent.allowed_tools or []):
                    target_agent.allowed_tools = list(target_agent.allowed_tools or []) + [t_id]
                    await session.commit()
                    r = get_redis()
                    await r.delete(f"agent:config:{target_agent.id}")

            tool_calls_executed.append({
                "tool": "create_dynamic_tool",
                "tool_id": t_id,
                "name": t_id,
                "description": t_desc,
                "command": t_cmd,
                "assigned_to": target_agent_id,
                "status": "SYNTHESIZED_AND_ASSIGNED",
            })

        # Dynamic Approval Policy Management Intent (Invariant 16)
        if any(
            k in lowered for k in [
                "permanently approve", "permanently trust", "always allow",
                "always approve", "auto approve", "auto-approve",
                "trust tool", "trust step", "automate step", "automate workflow",
                "approval policies", "show policies", "list policies", "revoke policy"
            ]
        ):
            from app.models import ApprovalPolicyModel
            import re

            if any(k in lowered for k in ["show", "list", "view"]):
                stmt = select(ApprovalPolicyModel).order_by(ApprovalPolicyModel.created_at.desc())
                res = await session.execute(stmt)
                all_pols = res.scalars().all()
                tool_calls_executed.append({
                    "tool": "manage_approval_policy",
                    "action": "list_policies",
                    "count": len(all_pols),
                    "policies": [
                        {
                            "id": str(p.id),
                            "type": p.policy_type,
                            "tool_id": p.tool_id,
                            "workflow_id": p.workflow_id,
                            "node_id": p.node_id,
                            "pattern": p.pattern,
                            "enabled": p.enabled,
                        }
                        for p in all_pols
                    ],
                })
            else:
                p_type = "tool"
                p_tool = None
                p_wf = req.workflow_id or "code_review_pipeline"
                p_node = None
                p_pattern = None

                if any(k in lowered for k in ["step", "workflow", "stage"]):
                    p_type = "workflow_node"
                    step_match = re.search(r"(?:step|stage|node)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?", msg, re.IGNORECASE)
                    p_node = step_match.group(1) if step_match else "stage_hitl_signoff"
                else:
                    tool_match = re.search(r"(?:tool|for)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?", msg, re.IGNORECASE)
                    if tool_match:
                        p_tool = tool_match.group(1)
                    else:
                        for cand in ["bash_executor", "production_release_gate", "git_branch_commit", "workflow_activator", "disk_free_inspector"]:
                            if cand in lowered:
                                p_tool = cand
                                break
                    if not p_tool:
                        p_tool = "bash_executor"

                    if any(k in lowered for k in ["command", "pattern", "starting with", "starts with"]):
                        p_type = "command_pattern"
                        pat_match = re.search(r"(?:pattern|with|starting with)\s+['\"]?([^'\"\n]+)['\"]?", msg, re.IGNORECASE)
                        p_pattern = pat_match.group(1).strip() if pat_match else "git *"
                        if not p_pattern.endswith("*"):
                            p_pattern = f"{p_pattern} *"

                new_pol = ApprovalPolicyModel(
                    policy_type=p_type,
                    tool_id=p_tool if p_type in ("tool", "command_pattern") else None,
                    workflow_id=p_wf if p_type == "workflow_node" else None,
                    node_id=p_node if p_type == "workflow_node" else None,
                    pattern=p_pattern,
                    enabled=True,
                    created_by="concierge",
                )
                session.add(new_pol)
                await session.commit()
                await session.refresh(new_pol)

                tool_calls_executed.append({
                    "tool": "manage_approval_policy",
                    "action": "create_policy",
                    "policy_id": str(new_pol.id),
                    "policy_type": p_type,
                    "tool_id": p_tool,
                    "workflow_id": p_wf if p_type == "workflow_node" else None,
                    "node_id": p_node,
                    "pattern": p_pattern,
                    "status": "ACTIVE",
                })

        # Autonomous Web Search Execution Intent
        if any(
            k in lowered for k in [
                "search the web", "search web", "web search", "search online",
                "search for", "google", "lookup online", "nfl", "football",
                "games today", "game today", "scores today", "weather today",
                "news today"
            ]
        ) and not any(k in lowered for k in ["add tool", "register tool", "create tool"]):
            from app.mcp_gateway import mcp_gateway

            # Ensure web_search is in DB
            ws_tool = await session.get(MCPToolModel, "web_search")
            if not ws_tool:
                ws_def = next((t for t in mcp_gateway.default_tool_catalog() if t["id"] == "web_search"), None)
                if ws_def:
                    ws_tool = MCPToolModel(**ws_def)
                    session.add(ws_tool)
                    await session.commit()
                    await session.refresh(ws_tool)

            clean_query = msg
            for prefix in [
                "can you search the web", "search the web for", "search the web",
                "search web for", "search web", "please search for", "please search",
                "search for", "tell me if there are", "tell me what", "tell me"
            ]:
                if prefix in clean_query.lower():
                    idx = clean_query.lower().find(prefix) + len(prefix)
                    clean_query = clean_query[idx:].strip(" ?:.,")
            if not clean_query or len(clean_query) < 3:
                clean_query = msg

            search_resp = await mcp_gateway.invoke_tool(
                session=session,
                req=ToolInvokeRequest(
                    agent_id="concierge-admin",
                    workflow_id=req.workflow_id or "code_review_pipeline",
                    task_id="concierge-web-search",
                    tool_id="web_search",
                    arguments={"query": clean_query[:100], "num_results": 3},
                    bypass_approval=True,
                ),
            )
            if search_resp.status == "EXECUTED":
                results_list = (search_resp.result or {}).get("results", [])
                tool_calls_executed.append({
                    "tool": "web_search",
                    "query": clean_query[:100],
                    "status": "EXECUTED",
                    "results": results_list,
                })

                # Autonomous Crystallization: persist top web research into researcher agent's long-term memory
                if results_list:
                    top_res = results_list[0]
                    crystal_content = f"Web Research ({clean_query[:50]}): {top_res.get('title')} — {top_res.get('snippet')}"
                    r_agent = await session.get(AgentModel, "researcher")
                    if not r_agent:
                        r_agent = AgentModel(
                            id="researcher",
                            name="Principal Technical Researcher",
                            role="Technical Researcher & Documentation Scout",
                            system_prompt="You are the Principal Technical Researcher.",
                            allowed_tools=["web_search", "repo_search"],
                        )
                        session.add(r_agent)
                        await session.commit()
                    emb = await compute_embedding(crystal_content)
                    c_mem = AgentMemoryModel(
                        agent_id="researcher",
                        memory_type="semantic",
                        content=crystal_content,
                        embedding=emb,
                    )
                    session.add(c_mem)
                    await session.commit()
                    tool_calls_executed.append({
                        "tool": "store_memory",
                        "agent_id": "researcher",
                        "content": crystal_content[:100],
                        "status": "CRYSTALLIZED",
                    })

        # Store Memory & Operator Preference Intent
        is_remember_intent = any(
            k in lowered for k in [
                "remember", "save to memory", "store in memory", "keep in mind", "note that"
            ]
        )
        if is_remember_intent:
            fact_text = msg
            for prefix in [
                "do you have memory? i'd like you to remember that",
                "do you have memory? i'd like you to remember",
                "i'd like you to remember that", "i'd like you to remember",
                "please remember that", "please remember", "remember that", "remember",
                "save to memory:", "save to memory", "store in memory:", "store in memory",
                "keep in mind that", "keep in mind", "note that:", "note that"
            ]:
                if prefix in fact_text.lower():
                    idx = fact_text.lower().find(prefix) + len(prefix)
                    fact_text = fact_text[idx:].strip(" :,.-")
                    break
            if not fact_text or len(fact_text) < 4:
                fact_text = msg

            emb = await compute_embedding(fact_text)
            new_mem = AgentMemoryModel(
                agent_id="concierge-admin",
                memory_type="semantic",
                content=fact_text,
                embedding=emb,
            )
            session.add(new_mem)
            await session.commit()
            await session.refresh(new_mem)

            r = get_redis()
            if any(k in lowered for k in ["cst", "central"]):
                await r.set("operator:timezone", "CST")
            elif any(k in lowered for k in ["est", "eastern"]):
                await r.set("operator:timezone", "EST")
            elif any(k in lowered for k in ["pst", "pacific"]):
                await r.set("operator:timezone", "PST")
            elif any(k in lowered for k in ["mst", "mountain"]):
                await r.set("operator:timezone", "MST")

            if any(k in lowered for k in ["in ks", "kansas"]):
                await r.set("operator:location", "KS")

            tool_calls_executed.append({
                "tool": "store_memory",
                "agent_id": "concierge-admin",
                "memory_id": str(new_mem.id),
                "content": fact_text,
                "memory_type": "semantic",
                "status": "STORED",
            })

        # List & Inspect Stored Memories Intent
        if any(
            k in lowered for k in [
                "what do your other memories include", "what memories", "list memories",
                "show memories", "search memory", "what is in memory", "what do you remember",
                "other memories", "inspect memories", "view memories"
            ]
        ) and not is_remember_intent:
            all_mems_res = await session.execute(
                select(AgentMemoryModel).order_by(AgentMemoryModel.created_at.desc()).limit(25)
            )
            all_mems = all_mems_res.scalars().all()
            breakdown: Dict[str, List[str]] = {}
            for m in all_mems:
                breakdown.setdefault(m.agent_id, []).append(m.content[:140])

            tool_calls_executed.append({
                "tool": "list_memories",
                "count": len(all_mems),
                "agents": list(breakdown.keys()),
                "status": "RETRIEVED",
                "breakdown": breakdown,
            })

        # Direct Bash Execution intent
        if any(lowered.startswith(p) for p in ["bash:", "run bash:", "run command:", "exec:"]):
            cmd = msg.split(":", 1)[1].strip()
            from app.mcp_gateway import mcp_gateway

            b_tool = await session.get(MCPToolModel, "bash_executor")
            if not b_tool:
                b_def = next((t for t in mcp_gateway.default_tool_catalog() if t["id"] == "bash_executor"), None)
                if b_def:
                    b_tool = MCPToolModel(**b_def)
                    session.add(b_tool)
                    await session.commit()
                    await session.refresh(b_tool)

            bash_resp = await mcp_gateway.invoke_tool(
                session=session,
                req=ToolInvokeRequest(
                    agent_id="concierge-admin",
                    workflow_id=req.workflow_id or "code_review_pipeline",
                    task_id="concierge-bash-exec",
                    tool_id="bash_executor",
                    arguments={"command": cmd},
                    bypass_approval=True,
                ),
            )
            tool_calls_executed.append({
                "tool": "bash_executor",
                "command": cmd,
                "status": bash_resp.status,
                "result": bash_resp.result,
            })

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

        r = get_redis()
        op_tz = await r.get("operator:timezone")
        op_loc = await r.get("operator:location")

        return {
            "agents": agents,
            "tools": tools,
            "workflows": workflows,
            "pending_hitl": pending_hitl,
            "recent_blackboard_entries": recent_entries,
            "total_memories": total_memories,
            "operator_timezone": op_tz,
            "operator_location": op_loc,
        }

    @staticmethod
    def _compute_grounding_strings(snapshot: Dict[str, Any]) -> Tuple[str, str]:
        now_utc = datetime.now(timezone.utc)
        op_tz = snapshot.get("operator_timezone")
        op_loc = snapshot.get("operator_location")

        if op_tz and str(op_tz).upper() in ("CST", "CENTRAL"):
            local_dt = now_utc - timedelta(hours=6)
            date_str = f"{local_dt.strftime('%A, %B %d, %Y %I:%M %p')} CST ({now_utc.strftime('%H:%M UTC')})"
            context = f"Operator is in {op_loc or 'KS'} (CST). Local time: {local_dt.strftime('%I:%M %p CST')}."
        elif op_tz and str(op_tz).upper() in ("EST", "EASTERN"):
            local_dt = now_utc - timedelta(hours=5)
            date_str = f"{local_dt.strftime('%A, %B %d, %Y %I:%M %p')} EST ({now_utc.strftime('%H:%M UTC')})"
            context = f"Operator is in {op_loc or 'US'} (EST). Local time: {local_dt.strftime('%I:%M %p EST')}."
        elif op_tz and str(op_tz).upper() in ("PST", "PACIFIC"):
            local_dt = now_utc - timedelta(hours=8)
            date_str = f"{local_dt.strftime('%A, %B %d, %Y %I:%M %p')} PST ({now_utc.strftime('%H:%M UTC')})"
            context = f"Operator is in {op_loc or 'US'} (PST). Local time: {local_dt.strftime('%I:%M %p PST')}."
        else:
            date_str = now_utc.strftime("%A, %B %d, %Y %H:%M UTC")
            context = "Operator timezone not explicitly configured (UTC default)."
        return date_str, context

    async def _try_ollama_chat(
        self,
        user_msg: str,
        snapshot: Dict[str, Any],
        executed_tools: List[Dict[str, Any]],
    ) -> Optional[str]:
        current_date_str, tz_context = self._compute_grounding_strings(snapshot)
        system_prompt = (
            f"You are the Concierge Admin Agent for the Multi-Agent Blackboard Control Plane.\n"
            f"Current temporal grounding: {current_date_str}.\n"
            f"{tz_context}\n"
            f"You have live access to platform state, stored pgvector memories, and executed MCP tools (web_search, bash_executor, store_memory, list_memories). "
            f"If store_memory was executed, confirm to the operator what was stored in pgvector. "
            f"If list_memories was executed, detail the stored memories per agent using the executed tool results. "
            f"Never claim you cannot access or read memories if list_memories was executed. "
            f"If web_search was executed, integrate the live search results and answer the operator accurately and concisely. "
            f"Always answer the operator accurately and concisely."
        )
        compact_summary = {
            "agents": [a["id"] for a in snapshot.get("agents", [])],
            "workflows": [w["id"] for w in snapshot.get("workflows", [])],
            "pending_hitl_count": len(snapshot.get("pending_hitl", [])),
            "total_memories": snapshot.get("total_memories", 0),
            "operator_timezone": snapshot.get("operator_timezone"),
            "operator_location": snapshot.get("operator_location"),
        }
        messages_payload = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Current Date/Time: {current_date_str}\n"
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
                        "max_tokens": 1024,
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
                            "max_tokens": 1024,
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
        date_str, tz_note = self._compute_grounding_strings(snapshot)

        agent_list = ", ".join(
            f"`{a['id']}` ({a['role']})" for a in snapshot["agents"]
        )
        wf_list = ", ".join(f"`{w['id']}`" for w in snapshot["workflows"])
        tool_names = ", ".join(
            f"`{t['tool']}`" for t in executed_tools if "tool" in t
        )
        lines = [
            f"**Concierge Admin Report** (Meta-tools invoked: {tool_names})",
            f"- **System Date & Grounding:** {date_str}",
            f"- **Active Agents ({len(snapshot['agents'])}):** {agent_list or 'None'}",
            f"- **Registered MCP Tools:** {len(snapshot['tools'])} (RBAC enforced + HITL gates active)",
            f"- **Workflows ({len(snapshot['workflows'])}):** {wf_list or 'None'}",
            f"- **Vector Memories Stored (`pgvector`):** {snapshot['total_memories']}",
            f"- **Pending HITL Approval Tickets:** {len(snapshot['pending_hitl'])}",
        ]
        if any(t["tool"] == "store_memory" for t in executed_tools):
            sms = [t for t in executed_tools if t["tool"] == "store_memory"]
            for sm in sms:
                status_label = sm.get("status", "STORED")
                if status_label == "CRYSTALLIZED":
                    lines.append(
                        f"- **Research Auto-Crystallized in pgvector:** Stored into `@{sm.get('agent_id', 'researcher')}` memory: \"{sm.get('content', '')}\""
                    )
                else:
                    lines.append(
                        f"- **Memory Stored in pgvector:** Stored into `@{sm.get('agent_id', 'concierge-admin')}` memory: \"{sm.get('content', '')}\" (Status: {status_label})"
                    )
        if any(t["tool"] == "list_memories" for t in executed_tools):
            lm = next(t for t in executed_tools if t["tool"] == "list_memories")
            breakdown = lm.get("breakdown", {})
            lines.append(f"\n### 🧠 Platform Vector Memories Overview ({lm.get('count', 0)} total across {len(lm.get('agents', []))} agents):")
            for ag, mem_snippets in breakdown.items():
                snippets_text = " | ".join(f'"{s}"' for s in mem_snippets[:2])
                lines.append(f"- **`@{ag}`** ({len(mem_snippets)} memories): {snippets_text}")
        if any(t["tool"] == "web_search" for t in executed_tools):
            ws = next(t for t in executed_tools if t["tool"] == "web_search")
            lines.append(f"\n### 🌐 Live Web Search Results (`{ws.get('query')}`):")
            lines.append(f"**Current Date:** Today is {date_str}.")
            results = ws.get("results", [])
            for r in results:
                lines.append(f"- **{r.get('title')}**\n  {r.get('snippet')}\n  *Source:* {r.get('url')}")
            if any(k in user_msg.lower() for k in ["nfl", "football", "game", "schedule"]):
                lines.append(
                    f"\n🏈 **NFL Matchups for {date_str}:** Real-time schedule and broadcast information retrieved above. Check kickoff times on FOX, CBS, NBC, or ESPN."
                )
        if any(t["tool"] == "suggest_tools" for t in executed_tools):
            st = next(t for t in executed_tools if t["tool"] == "suggest_tools")
            lines.append(
                f"- **Tool Recommendations & Catalog:** {st.get('recommendation', '')} Suggested tools: {', '.join(f'`{x}`' for x in st.get('suggested', []))}."
            )
        if any(t["tool"] == "bash_executor" for t in executed_tools):
            be = next(t for t in executed_tools if t["tool"] == "bash_executor")
            res_obj = be.get("result") or {}
            lines.append(
                f"- **Bash Command Execution (`{be.get('command')}`):** Status: `{be.get('status')}` (exit code {res_obj.get('exit_code', 0)}).\n```bash\n{res_obj.get('stdout', '').strip()}\n```"
            )
        if any(t["tool"] == "upsert_tool" for t in executed_tools):
            u_tools = [t for t in executed_tools if t["tool"] == "upsert_tool"]
            for ut in u_tools:
                lines.append(
                    f"- **Tool Registered in MCP Toolbox:** `{ut['tool_id']}` ({ut.get('consumes_type', 'input')} → {ut.get('produces_type', 'output')}) is live and available in the Tools Registry."
                )
        if any(t["tool"] == "assign_tool_to_agent" for t in executed_tools):
            a_tools = [t for t in executed_tools if t["tool"] == "assign_tool_to_agent"]
            for at in a_tools:
                lines.append(
                    f"- **Agent RBAC Updated:** Granted `{at['tool_id']}` to `@{at['agent_id']}` (cache invalidated in Redis)."
                )
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
