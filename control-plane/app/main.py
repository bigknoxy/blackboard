import asyncio
import json
import logging
import os
import uuid
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.concierge import compute_embedding, concierge_agent
from app.database import AsyncSessionLocal, get_db, get_redis, init_db
from app.graph import BlackboardGraphEngine, GraphCycleException
from app.mcp_gateway import mcp_gateway
from app.models import (
    AgentMemoryModel,
    AgentModel,
    BacklogItemModel,
    BlackboardEdgeModel,
    BlackboardEntryModel,
    BlackboardNodeModel,
    HITLTicketModel,
    MCPToolModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    BacklogItemCreate,
    BacklogItemResponse,
    BacklogItemUpdate,
    BlackboardEdgeCreate,
    BlackboardEdgeResponse,
    BlackboardEntryCreate,
    BlackboardEntryResponse,
    BlackboardGraphResponse,
    BlackboardNodeCreate,
    BlackboardNodeResponse,
    ConciergeChatRequest,
    ConciergeChatResponse,
    ChannelConfigRequest,
    ChannelIncomingMessage,
    HITLDecisionRequest,
    HITLTicketResponse,
    LLMRouterConfigRequest,
    MCPToolCreate,
    MCPToolResponse,
    MCPToolUpdate,
    MemoryCreate,
    MemoryResponse,
    MemorySearchRequest,
    SteeringCreate,
    ToolChainResponse,
    ToolInvokeRequest,
    ToolInvokeResponse,
    WorkflowCreate,
    WorkflowResponse,
    WorkflowRunResponse,
    WorkflowTriggerRequest,
    WorkflowUpdate,
)
from app.workflow_engine import REDIS_EVENTS_CHANNEL, workflow_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("blackboard.main")

LOCAL_OLLAMA_URL = os.getenv("LOCAL_OLLAMA_URL", "http://192.168.1.150:11434")
WORKFLOWS_DIR = Path(os.getenv("WORKFLOWS_DIR", "/workflows"))
graph_engine = BlackboardGraphEngine()


class WebSocketManager:
    """Manages active WebSocket clients and broadcasts Redis Pub/Sub blackboard events."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []
        self._pubsub_task: Optional[asyncio.Task] = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active_connections:
            self.active_connections.remove(ws)

    async def broadcast(self, message: str) -> None:
        stale: List[WebSocket] = []
        for conn in list(self.active_connections):
            try:
                await conn.send_text(message)
            except Exception:
                stale.append(conn)
        for s in stale:
            self.disconnect(s)

    async def listen_redis_events(self) -> None:
        redis_client = get_redis()
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(REDIS_EVENTS_CHANNEL)
        logger.info("Subscribed WebSocket bridge to %s", REDIS_EVENTS_CHANNEL)
        try:
            async for msg in pubsub.listen():
                if msg and msg.get("type") == "message":
                    data = msg.get("data")
                    if isinstance(data, str):
                        await self.broadcast(data)
        except asyncio.CancelledError:
            await pubsub.unsubscribe(REDIS_EVENTS_CHANNEL)
        except Exception as exc:
            logger.warning("Redis Pub/Sub listener error: %s", exc)


ws_manager = WebSocketManager()


async def seed_initial_state() -> None:
    """Seed default MCP tools, dynamic agent personas, memories, and YAML workflows."""
    async with AsyncSessionLocal() as session:
        # 1. Seed built-in MCP tools
        for tool_def in mcp_gateway.default_tool_catalog():
            existing_tool = await session.get(MCPToolModel, tool_def["id"])
            if not existing_tool:
                session.add(MCPToolModel(**tool_def))
        await session.commit()

        # Sync any configured external MCP servers
        await mcp_gateway.sync_external_mcp_servers(session)
        await session.commit()

        # 2. Seed dynamic agent roster (Zero hardcoded logic in workers!)
        default_agents = [
            {
                "id": "architect",
                "name": "Principal Systems Architect (CT118 qwen3-4b)",
                "role": "Research & Specification Architect",
                "system_prompt": (
                    "You are the Principal Systems Architect. Analyze requirements, search the repository, "
                    "formulate hypotheses, and produce clear, concise technical specifications (`spec`) with interface contracts."
                ),
                "inference_config": {
                    "provider": "openai",
                    "model_name": "qwen3-4b",
                    "base_url": "http://192.168.1.180:8081/v1",
                    "api_key": None,
                    "temperature": 0.2,
                },
                "allowed_tools": [
                    "repo_search",
                    "ast_complexity_analyzer",
                    "workflow_architect_validator",
                    "workflow_activator",
                ],
                "subscribed_events": ["workflow.started", "spec.requested"],
            },
            {
                "id": "coder",
                "name": "Staff Implementation Engineer (Ollama qwen2.5-coder:0.5b)",
                "role": "Polyglot Code Synthesizer",
                "system_prompt": (
                    "You are the Staff Implementation Engineer. Consume architecture specs and steering instructions "
                    "from the Blackboard and produce clean, memory-safe, production-ready code (`code`)."
                ),
                "inference_config": {
                    "provider": "ollama",
                    "model_name": "qwen2.5-coder:0.5b",
                    "base_url": LOCAL_OLLAMA_URL,
                    "api_key": None,
                    "temperature": 0.15,
                },
                "allowed_tools": [
                    "repo_search",
                    "ast_complexity_analyzer",
                    "git_branch_commit",
                ],
                "subscribed_events": ["spec.published", "critique.published"],
            },
            {
                "id": "qa-tester",
                "name": "Autonomous Verification Engineer (CT118 lfm3-1.7b)",
                "role": "Test & Sandbox Runner",
                "system_prompt": (
                    "You are the Autonomous Verification Engineer. Execute isolated pytest suites against candidate code "
                    "artifacts on the Blackboard and publish concise `test_result` entries."
                ),
                "inference_config": {
                    "provider": "openai",
                    "model_name": "lfm3-1.7b",
                    "base_url": "http://192.168.1.180:8090/v1",
                    "api_key": None,
                    "temperature": 0.1,
                },
                "allowed_tools": ["pytest_sandbox_runner", "repo_search"],
                "subscribed_events": ["code.published"],
            },
            {
                "id": "sec-auditor",
                "name": "Application Security Auditor (CT118 lfm3-1.7b)",
                "role": "Security & Compliance Reviewer",
                "system_prompt": (
                    "You are the Application Security Auditor. Run OWASP/CWE static scans on specs and code, "
                    "publish concise security critiques (`critique`), and gate production releases."
                ),
                "inference_config": {
                    "provider": "openai",
                    "model_name": "lfm3-1.7b",
                    "base_url": "http://192.168.1.180:8090/v1",
                    "api_key": None,
                    "temperature": 0.1,
                },
                "allowed_tools": [
                    "static_security_scanner",
                    "ast_complexity_analyzer",
                    "production_release_gate",
                ],
                "subscribed_events": ["code.published", "test_result.published"],
            },
        ]

        for agent_data in default_agents:
            existing_agent = await session.get(AgentModel, agent_data["id"])
            if not existing_agent:
                session.add(AgentModel(**agent_data))
        await session.commit()

        # 3. Seed initial pgvector episodic/semantic/reflection memories per agent
        seed_memories = [
            (
                "architect",
                "semantic",
                "On 32GB homelab nodes, cap SQLAlchemy asyncpg pool_size=5 and Redis maxmemory=256MB to prevent OOM contention.",
            ),
            (
                "coder",
                "reflection",
                "Always validate untrusted payloads with Pydantic v2 schemas and avoid blocking I/O inside FastAPI async routes.",
            ),
            (
                "qa-tester",
                "episodic",
                "Previous pipeline run caught race condition in lease expiration; verify Redis SET NX EX 60 atomic lock semantics.",
            ),
            (
                "sec-auditor",
                "reflection",
                "Enforce RBAC tool filtering at the MCP Gateway layer and require HITL approval before git_branch_commit or production_release_gate.",
            ),
        ]
        existing_mem_res = await session.execute(
            select(AgentMemoryModel).limit(1)
        )
        if not existing_mem_res.scalar_one_or_none():
            for agent_id, mtype, content in seed_memories:
                emb = await compute_embedding(content)
                session.add(
                    AgentMemoryModel(
                        agent_id=agent_id,
                        memory_type=mtype,
                        content=content,
                        embedding=emb,
                    )
                )
            await session.commit()

        # 4. Load sample YAML workflows from /workflows directory
        yaml_candidates = []
        if WORKFLOWS_DIR.exists():
            yaml_candidates.extend(WORKFLOWS_DIR.glob("*.yaml"))
        local_wf_dir = (
            Path(__file__).resolve().parent.parent.parent / "workflows"
        )
        if local_wf_dir.exists():
            yaml_candidates.extend(local_wf_dir.glob("*.yaml"))

        seen_wf_ids = set()
        for yaml_path in yaml_candidates:
            try:
                raw_yaml = yaml_path.read_text(encoding="utf-8")
                parsed = workflow_engine.parse_yaml_to_graph(raw_yaml)
                wf_id = parsed["id"]
                if wf_id in seen_wf_ids:
                    continue
                seen_wf_ids.add(wf_id)
                existing_wf = await session.get(WorkflowModel, wf_id)
                if not existing_wf:
                    state_graph = {
                        "nodes": parsed["nodes"],
                        "edges": parsed["edges"],
                    }
                    bpmn_xml = workflow_engine.graph_to_bpmn_xml(
                        wf_id, parsed["name"], state_graph
                    )
                    session.add(
                        WorkflowModel(
                            id=wf_id,
                            name=parsed["name"],
                            description=parsed.get("description", ""),
                            state_graph=state_graph,
                            yaml_source=raw_yaml,
                            bpmn_xml=bpmn_xml,
                        )
                    )
                    await session.flush()
            except Exception as exc:
                logger.warning(
                    "Failed loading workflow file %s: %s", yaml_path, exc
                )
        await session.commit()

        # 5. Seed initial backlog & ideas if table is empty
        res_backlog = await session.execute(select(BacklogItemModel).limit(1))
        if not res_backlog.scalars().first():
            initial_backlog = [
                BacklogItemModel(
                    key="IDEA-1",
                    title="Discord 2-Way Interactive Bot Gateway",
                    description=(
                        "Tabled for now per operator directive. When activated, run a discord.py "
                        "WebSocket gateway client with Message Content Intent enabled so users can "
                        "chat directly with Concierge inside #blackboard without the Web Cockpit."
                    ),
                    category="feature",
                    priority="P1",
                    status="tabled",
                    tags=["discord", "channels", "bot", "interactive"],
                    metadata_={"suggested_by": "operator", "channel": "discord"},
                ),
                BacklogItemModel(
                    key="IDEA-2",
                    title="Autonomous Workflow & Agent Generator",
                    description=(
                        "Pipeline allowing the Architect Agent to generate, validate, and hot-reload "
                        "new BPMN 2.0 workflows and agent personas via chat directives under operator sign-off."
                    ),
                    category="workflow",
                    priority="P1",
                    status="triaged",
                    tags=["architecture", "meta-workflow", "self-modification"],
                    metadata_={"suggested_by": "operator"},
                ),
                BacklogItemModel(
                    key="IDEA-3",
                    title="Mobile Gesture & Touch Optimization",
                    description=(
                        "Harden horizontal swipe sensitivity with angle/velocity gating and ignore-zones "
                        "for BPMN canvas, YAML viewers, and code blocks."
                    ),
                    category="improvement",
                    priority="P0",
                    status="completed",
                    tags=["mobile", "ui", "touch", "gestures"],
                    metadata_={"suggested_by": "operator", "resolved_version": "v1.2.0"},
                ),
            ]
            for b_item in initial_backlog:
                session.add(b_item)
            await session.commit()



@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_initial_state()
    ws_manager._pubsub_task = asyncio.create_task(
        ws_manager.listen_redis_events()
    )
    yield
    if ws_manager._pubsub_task:
        ws_manager._pubsub_task.cancel()


app = FastAPI(
    title="Multi-Agent Blackboard Control Plane",
    description="Config-Driven Multi-Agent Blackboard System with Dynamic MCP, pgvector Memory, BPMN 2.0 & Stateless Worker Pool",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "blackboard-control-plane",
        "local_ollama_url": LOCAL_OLLAMA_URL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ==============================================================================
# 1. DYNAMIC AGENT ROSTER CRUD (/api/v1/agents)
# ==============================================================================
@app.get("/api/v1/agents", response_model=List[AgentResponse])
async def list_agents(
    session: AsyncSession = Depends(get_db),
) -> List[AgentModel]:
    result = await session.execute(
        select(AgentModel).order_by(AgentModel.id.asc())
    )
    return list(result.scalars().all())


@app.post("/api/v1/agents", response_model=AgentResponse, status_code=201)
async def create_agent(
    payload: AgentCreate, session: AsyncSession = Depends(get_db)
) -> AgentModel:
    existing = await session.get(AgentModel, payload.id)
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Agent '{payload.id}' already exists."
        )
    agent = AgentModel(
        id=payload.id,
        name=payload.name,
        role=payload.role,
        system_prompt=payload.system_prompt,
        inference_config=payload.inference_config.model_dump(),
        allowed_tools=payload.allowed_tools,
        subscribed_events=payload.subscribed_events,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    # Also cache agent config in Redis for ultra-fast worker hydration
    redis_client = get_redis()
    await redis_client.set(
        f"agent:config:{agent.id}",
        json.dumps(payload.model_dump()),
    )
    return agent


@app.get("/api/v1/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str, session: AsyncSession = Depends(get_db)
) -> AgentModel:
    agent = await session.get(AgentModel, agent_id)
    if not agent:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_id}' not found."
        )
    return agent


@app.put("/api/v1/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    session: AsyncSession = Depends(get_db),
) -> AgentModel:
    agent = await session.get(AgentModel, agent_id)
    if not agent:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_id}' not found."
        )
    updates = payload.model_dump(exclude_unset=True)
    if "inference_config" in updates and updates["inference_config"] is not None:
        agent.inference_config = payload.inference_config.model_dump()
        updates.pop("inference_config")
    for k, v in updates.items():
        setattr(agent, k, v)
    await session.commit()
    await session.refresh(agent)

    redis_client = get_redis()
    await redis_client.delete(f"agent:config:{agent.id}")
    return agent


@app.delete("/api/v1/agents/{agent_id}")
async def delete_agent(
    agent_id: str, session: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    agent = await session.get(AgentModel, agent_id)
    if not agent:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_id}' not found."
        )
    await session.delete(agent)
    await session.commit()
    redis_client = get_redis()
    await redis_client.delete(f"agent:config:{agent_id}")
    return {"status": "deleted", "agent_id": agent_id}


@app.get(
    "/api/v1/agents/{agent_id}/tools", response_model=List[MCPToolResponse]
)
async def get_agent_allowed_tools(
    agent_id: str, session: AsyncSession = Depends(get_db)
) -> List[MCPToolModel]:
    """Return RBAC-filtered MCP tool schemas exposed to this specific agent."""
    return await mcp_gateway.get_tools_for_agent(session, agent_id)


# ==============================================================================
# 2. PER-AGENT PERSISTENT MEMORY CRUD & PGVECTOR RETRIEVAL
# ==============================================================================
@app.get(
    "/api/v1/agents/{agent_id}/memories", response_model=List[MemoryResponse]
)
async def list_agent_memories(
    agent_id: str,
    memory_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> List[AgentMemoryModel]:
    stmt = (
        select(AgentMemoryModel)
        .where(AgentMemoryModel.agent_id == agent_id)
        .order_by(AgentMemoryModel.created_at.desc())
        .limit(limit)
    )
    if memory_type:
        stmt = stmt.where(AgentMemoryModel.memory_type == memory_type)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@app.post(
    "/api/v1/agents/{agent_id}/memories",
    response_model=MemoryResponse,
    status_code=201,
)
async def create_agent_memory(
    agent_id: str,
    payload: MemoryCreate,
    session: AsyncSession = Depends(get_db),
) -> AgentMemoryModel:
    agent = await session.get(AgentModel, agent_id)
    if not agent:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_id}' not found."
        )
    embedding = payload.embedding or await compute_embedding(payload.content)
    mem = AgentMemoryModel(
        agent_id=agent_id,
        memory_type=payload.memory_type,
        content=payload.content,
        embedding=embedding,
    )
    session.add(mem)
    await session.commit()
    await session.refresh(mem)
    return mem


@app.post(
    "/api/v1/agents/{agent_id}/memories/search",
    response_model=List[MemoryResponse],
)
async def search_agent_memories(
    agent_id: str,
    payload: MemorySearchRequest,
    session: AsyncSession = Depends(get_db),
) -> List[MemoryResponse]:
    """
    Hydration Memory Retrieval:
    Embed the task description and retrieve top-k nearest memories scoped to `agent_id`
    using pgvector cosine distance (`<=>`).
    """
    query_vec = await compute_embedding(payload.query)
    distance_expr = AgentMemoryModel.embedding.cosine_distance(query_vec)

    stmt = select(AgentMemoryModel, distance_expr.label("distance")).where(
        AgentMemoryModel.agent_id == agent_id
    )
    if payload.memory_type:
        stmt = stmt.where(AgentMemoryModel.memory_type == payload.memory_type)
    stmt = stmt.order_by(distance_expr.asc()).limit(payload.top_k)

    result = await session.execute(stmt)
    items: List[MemoryResponse] = []
    for mem, dist in result.all():
        sim = max(0.0, round(1.0 - float(dist or 0.0), 4))
        items.append(
            MemoryResponse(
                id=mem.id,
                agent_id=mem.agent_id,
                memory_type=mem.memory_type,
                content=mem.content,
                similarity=sim,
                created_at=mem.created_at,
            )
        )
    return items


@app.delete("/api/v1/agents/{agent_id}/memories/{memory_id}")
async def delete_agent_memory(
    agent_id: str,
    memory_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    mem = await session.get(AgentMemoryModel, memory_id)
    if not mem or mem.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Memory not found.")
    await session.delete(mem)
    await session.commit()
    return {"status": "deleted", "memory_id": str(memory_id)}


# ==============================================================================
# 3. CENTRALIZED MCP TOOLBOX GATEWAY (/api/v1/tools)
# ==============================================================================
@app.get("/api/v1/tools", response_model=List[MCPToolResponse])
async def list_tools(
    agent_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> List[MCPToolModel]:
    if agent_id:
        return await mcp_gateway.get_tools_for_agent(session, agent_id)
    # Ensure default builtins exist in DB
    for tool_def in mcp_gateway.default_tool_catalog():
        existing = await session.get(MCPToolModel, tool_def["id"])
        if not existing:
            session.add(MCPToolModel(**tool_def))
    await session.commit()
    result = await session.execute(
        select(MCPToolModel).order_by(MCPToolModel.id.asc())
    )
    return list(result.scalars().all())


@app.post("/api/v1/tools", response_model=MCPToolResponse, status_code=201)
async def create_tool(
    payload: MCPToolCreate, session: AsyncSession = Depends(get_db)
) -> MCPToolModel:
    existing = await session.get(MCPToolModel, payload.id)
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Tool '{payload.id}' already exists."
        )
    tool = MCPToolModel(**payload.model_dump())
    session.add(tool)
    await session.commit()
    await session.refresh(tool)
    return tool


@app.get("/api/v1/tools/{tool_id}", response_model=MCPToolResponse)
async def get_tool(
    tool_id: str, session: AsyncSession = Depends(get_db)
) -> MCPToolModel:
    tool = await session.get(MCPToolModel, tool_id)
    if not tool:
        raise HTTPException(
            status_code=404, detail=f"Tool '{tool_id}' not found."
        )
    return tool


@app.put("/api/v1/tools/{tool_id}", response_model=MCPToolResponse)
async def update_tool(
    tool_id: str,
    payload: MCPToolUpdate,
    session: AsyncSession = Depends(get_db),
) -> MCPToolModel:
    tool = await session.get(MCPToolModel, tool_id)
    if not tool:
        raise HTTPException(
            status_code=404, detail=f"Tool '{tool_id}' not found."
        )
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(tool, k, v)
    await session.commit()
    await session.refresh(tool)
    return tool


@app.delete("/api/v1/tools/{tool_id}")
async def delete_tool(
    tool_id: str, session: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    tool = await session.get(MCPToolModel, tool_id)
    if not tool:
        raise HTTPException(
            status_code=404, detail=f"Tool '{tool_id}' not found."
        )
    await session.delete(tool)
    await session.commit()
    return {"status": "deleted", "tool_id": tool_id}


@app.post("/api/v1/tools/invoke", response_model=ToolInvokeResponse)
async def invoke_mcp_tool(
    payload: ToolInvokeRequest, session: AsyncSession = Depends(get_db)
) -> ToolInvokeResponse:
    return await mcp_gateway.invoke_tool(session, payload)


@app.post("/api/v1/tools/sync")
async def sync_mcp_servers(
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    discovered = await mcp_gateway.sync_external_mcp_servers(session)
    await session.commit()
    return {"status": "synced", "external_tools_discovered": discovered}


# ==============================================================================
# 4. WORKFLOW ENGINE & BPMN 2.0 DUAL REPRESENTATION (/api/v1/workflows)
# ==============================================================================
@app.get("/api/v1/workflows", response_model=List[WorkflowResponse])
async def list_workflows(
    session: AsyncSession = Depends(get_db),
) -> List[WorkflowModel]:
    result = await session.execute(
        select(WorkflowModel).order_by(WorkflowModel.id.asc())
    )
    return list(result.scalars().all())


@app.post(
    "/api/v1/workflows", response_model=WorkflowResponse, status_code=201
)
async def create_workflow(
    payload: WorkflowCreate, session: AsyncSession = Depends(get_db)
) -> WorkflowModel:
    existing = await session.get(WorkflowModel, payload.id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Workflow '{payload.id}' already exists.",
        )

    state_graph: Dict[str, Any] = {"nodes": [], "edges": []}
    yaml_source = payload.yaml_source
    bpmn_xml = payload.bpmn_xml

    if payload.state_graph:
        state_graph = payload.state_graph.model_dump()
    elif yaml_source:
        parsed = workflow_engine.parse_yaml_to_graph(yaml_source)
        state_graph = {"nodes": parsed["nodes"], "edges": parsed["edges"]}
    elif bpmn_xml:
        state_graph = workflow_engine.bpmn_xml_to_graph(bpmn_xml)

    if not yaml_source:
        yaml_source = workflow_engine.graph_to_yaml(
            payload.id, payload.name, payload.description, state_graph
        )
    if not bpmn_xml:
        bpmn_xml = workflow_engine.graph_to_bpmn_xml(
            payload.id, payload.name, state_graph
        )

    wf = WorkflowModel(
        id=payload.id,
        name=payload.name,
        description=payload.description,
        state_graph=state_graph,
        yaml_source=yaml_source,
        bpmn_xml=bpmn_xml,
    )
    session.add(wf)
    await session.commit()
    await session.refresh(wf)
    return wf


@app.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str, session: AsyncSession = Depends(get_db)
) -> WorkflowModel:
    wf = await session.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' not found."
        )
    return wf


@app.put("/api/v1/workflows/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    session: AsyncSession = Depends(get_db),
) -> WorkflowModel:
    wf = await session.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' not found."
        )

    if payload.name is not None:
        wf.name = payload.name
    if payload.description is not None:
        wf.description = payload.description

    if payload.bpmn_xml is not None:
        wf.bpmn_xml = payload.bpmn_xml
        wf.state_graph = workflow_engine.bpmn_xml_to_graph(payload.bpmn_xml)
        wf.yaml_source = workflow_engine.graph_to_yaml(
            wf.id, wf.name, wf.description, wf.state_graph
        )
    elif payload.yaml_source is not None:
        wf.yaml_source = payload.yaml_source
        parsed = workflow_engine.parse_yaml_to_graph(payload.yaml_source)
        wf.state_graph = {"nodes": parsed["nodes"], "edges": parsed["edges"]}
        wf.bpmn_xml = workflow_engine.graph_to_bpmn_xml(
            wf.id, wf.name, wf.state_graph
        )
    elif payload.state_graph is not None:
        wf.state_graph = payload.state_graph.model_dump()
        wf.yaml_source = workflow_engine.graph_to_yaml(
            wf.id, wf.name, wf.description, wf.state_graph
        )
        wf.bpmn_xml = workflow_engine.graph_to_bpmn_xml(
            wf.id, wf.name, wf.state_graph
        )

    await session.commit()
    await session.refresh(wf)
    return wf


@app.delete("/api/v1/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: str, session: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    wf = await session.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' not found."
        )
    await session.delete(wf)
    await session.commit()
    return {"status": "deleted", "workflow_id": workflow_id}


@app.post("/api/v1/workflows/convert")
async def convert_workflow_representation(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Bi-directional converter between YAML/JSON state_graph and BPMN 2.0 XML."""
    wf_id = payload.get("id", "converted_workflow")
    name = payload.get("name", "Converted Workflow")
    description = payload.get("description", "")

    if payload.get("bpmn_xml"):
        graph = workflow_engine.bpmn_xml_to_graph(payload["bpmn_xml"])
        yaml_str = workflow_engine.graph_to_yaml(
            wf_id, name, description, graph
        )
        return {
            "state_graph": graph,
            "yaml_source": yaml_str,
            "bpmn_xml": payload["bpmn_xml"],
        }

    if payload.get("yaml_source"):
        parsed = workflow_engine.parse_yaml_to_graph(payload["yaml_source"])
        graph = {"nodes": parsed["nodes"], "edges": parsed["edges"]}
        bpmn_xml = workflow_engine.graph_to_bpmn_xml(
            parsed.get("id", wf_id), parsed.get("name", name), graph
        )
        return {
            "state_graph": graph,
            "yaml_source": payload["yaml_source"],
            "bpmn_xml": bpmn_xml,
        }

    graph = payload.get("state_graph") or {"nodes": [], "edges": []}
    return {
        "state_graph": graph,
        "yaml_source": workflow_engine.graph_to_yaml(
            wf_id, name, description, graph
        ),
        "bpmn_xml": workflow_engine.graph_to_bpmn_xml(wf_id, name, graph),
    }


@app.post(
    "/api/v1/workflows/{workflow_id}/run",
    response_model=WorkflowRunResponse,
    status_code=201,
)
async def trigger_workflow_run(
    workflow_id: str,
    payload: WorkflowTriggerRequest,
    session: AsyncSession = Depends(get_db),
) -> WorkflowRunModel:
    wf = await session.get(WorkflowModel, workflow_id)
    if not wf:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' not found."
        )
    return await workflow_engine.start_workflow_run(
        session, wf, payload.initial_prompt, payload.context
    )


@app.get(
    "/api/v1/workflows/{workflow_id}/runs",
    response_model=List[WorkflowRunResponse],
)
async def list_workflow_runs(
    workflow_id: str, session: AsyncSession = Depends(get_db)
) -> List[WorkflowRunModel]:
    stmt = (
        select(WorkflowRunModel)
        .where(WorkflowRunModel.workflow_id == workflow_id)
        .order_by(WorkflowRunModel.created_at.desc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


# ==============================================================================
# 5. SHARED BLACKBOARD SYSTEM & HITL GATES (/api/v1/blackboard)
# ==============================================================================
@app.get(
    "/api/v1/blackboard", response_model=List[BlackboardEntryResponse]
)
async def list_blackboard_entries(
    workflow_id: Optional[str] = Query(default=None),
    entry_type: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> List[BlackboardEntryModel]:
    stmt = select(BlackboardEntryModel).order_by(
        BlackboardEntryModel.created_at.desc()
    )
    if workflow_id:
        stmt = stmt.where(BlackboardEntryModel.workflow_id == workflow_id)
    if entry_type:
        stmt = stmt.where(BlackboardEntryModel.entry_type == entry_type)
    if agent_id:
        stmt = stmt.where(BlackboardEntryModel.agent_id == agent_id)
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@app.post(
    "/api/v1/blackboard",
    response_model=BlackboardEntryResponse,
    status_code=201,
)
async def create_blackboard_entry(
    payload: BlackboardEntryCreate, session: AsyncSession = Depends(get_db)
) -> BlackboardEntryModel:
    entry = BlackboardEntryModel(
        workflow_id=payload.workflow_id,
        task_id=payload.task_id,
        agent_id=payload.agent_id,
        entry_type=payload.entry_type,
        content=payload.content,
        metadata_=payload.metadata,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    # Publish real-time event to Redis Pub/Sub `channel:blackboard:events`
    await workflow_engine._publish_event(
        {
            "event": "blackboard.entry_created",
            "id": str(entry.id),
            "workflow_id": entry.workflow_id,
            "task_id": entry.task_id,
            "agent_id": entry.agent_id,
            "entry_type": entry.entry_type,
            "content": entry.content,
            "metadata": entry.metadata_,
            "created_at": entry.created_at.isoformat(),
        }
    )

    # Also mirror into DAG blackboard_nodes
    try:
        latest_nodes = await session.execute(
            select(BlackboardNodeModel.id)
            .where(BlackboardNodeModel.workflow_id == payload.workflow_id)
            .order_by(BlackboardNodeModel.created_at.desc())
            .limit(1)
        )
        prev_node_id = latest_nodes.scalar_one_or_none()
        relation = (
            "implements"
            if payload.entry_type == "code"
            else (
                "critiques"
                if payload.entry_type == "critique"
                else "steers" if payload.entry_type == "steering" else "implements"
            )
        )
        await graph_engine.create_node(
            session=session,
            workflow_id=payload.workflow_id,
            author_agent_id=payload.agent_id,
            node_type=payload.entry_type,
            payload={"content": payload.content, **payload.metadata},
            task_id=payload.task_id,
            parent_node_id=prev_node_id,
            relation_type=relation,
        )
        await session.commit()
    except Exception as exc:
        logger.warning(f"DAG mirror notice: {exc}")

    # Trigger automated state pruning if debate exceeds threshold
    await workflow_engine.prune_and_summarize_blackboard(
        session, payload.workflow_id
    )
    return entry


@app.post(
    "/api/v1/blackboard/steer",
    response_model=BlackboardEntryResponse,
    status_code=201,
)
async def post_operator_steering(
    payload: SteeringCreate, session: AsyncSession = Depends(get_db)
) -> BlackboardEntryModel:
    """
    Operator Steering Endpoint:
    Writes a `steering` entry onto the Blackboard that running agents consume on their next turn.
    """
    entry = BlackboardEntryModel(
        workflow_id=payload.workflow_id,
        task_id=payload.task_id,
        agent_id=payload.agent_id,
        entry_type="steering",
        content=payload.content,
        metadata_={"target_agent_id": payload.target_agent_id, "active": True},
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    await workflow_engine._publish_event(
        {
            "event": "blackboard.steering_injected",
            "id": str(entry.id),
            "workflow_id": entry.workflow_id,
            "agent_id": entry.agent_id,
            "target_agent_id": payload.target_agent_id,
            "entry_type": "steering",
            "content": entry.content,
            "created_at": entry.created_at.isoformat(),
        }
    )
    return entry


@app.post("/api/v1/blackboard/prune")
async def prune_blackboard_endpoint(
    workflow_id: str = Query(...),
    threshold: int = Query(default=5, ge=2),
    keep_recent: int = Query(default=2, ge=1),
    session: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    summary = await workflow_engine.prune_and_summarize_blackboard(
        session, workflow_id, threshold=threshold, keep_recent=keep_recent
    )
    if not summary:
        return {
            "status": "no_prune_needed",
            "workflow_id": workflow_id,
        }
    return {
        "status": "pruned",
        "workflow_id": workflow_id,
        "summary_entry_id": str(summary.id),
        "summary_content": summary.content,
    }


# --- BLACKBOARD ADJACENCY DAG & TOPOLOGICAL LINEAGE ENDPOINTS ---
@app.post(
    "/api/v1/blackboard/nodes",
    response_model=BlackboardNodeResponse,
    status_code=201,
)
async def create_blackboard_node(
    payload: BlackboardNodeCreate, session: AsyncSession = Depends(get_db)
) -> BlackboardNodeModel:
    try:
        node = await graph_engine.create_node(
            session=session,
            workflow_id=payload.workflow_id,
            author_agent_id=payload.author_agent_id,
            node_type=payload.node_type,
            payload=payload.payload,
            task_id=payload.task_id,
            status=payload.status,
            parent_node_id=payload.parent_node_id,
            relation_type=payload.relation_type,
        )
        await session.commit()
        await session.refresh(node)

        await workflow_engine._publish_event(
            {
                "event": "blackboard.node_created",
                "id": str(node.id),
                "workflow_id": node.workflow_id,
                "node_type": node.node_type,
                "author_agent_id": node.author_agent_id,
                "status": node.status,
            }
        )
        return node
    except GraphCycleException as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post(
    "/api/v1/blackboard/edges",
    response_model=BlackboardEdgeResponse,
    status_code=201,
)
async def create_blackboard_edge(
    payload: BlackboardEdgeCreate, session: AsyncSession = Depends(get_db)
) -> BlackboardEdgeModel:
    try:
        edge = await graph_engine.add_edge(
            session=session,
            source_id=payload.source_id,
            target_id=payload.target_id,
            relation_type=payload.relation_type,
        )
        await session.commit()
        return edge
    except GraphCycleException as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(
    "/api/v1/blackboard/graph/{workflow_id}",
    response_model=BlackboardGraphResponse,
)
async def get_blackboard_graph(
    workflow_id: str, session: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    graph_data = await graph_engine.get_full_graph(session, workflow_id)
    lineage = await graph_engine.get_active_lineage(session, workflow_id)
    return {
        "workflow_id": workflow_id,
        "nodes": graph_data["nodes"],
        "edges": graph_data["edges"],
        "active_lineage": lineage,
    }


@app.get(
    "/api/v1/blackboard/lineage/{node_id}",
    response_model=List[BlackboardNodeResponse],
)
async def get_node_lineage(
    node_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> List[BlackboardNodeModel]:
    node = await session.get(BlackboardNodeModel, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return await graph_engine.get_active_lineage(
        session, node.workflow_id, leaf_node_id=node.id
    )


@app.get("/api/v1/mcp/tool-chain", response_model=ToolChainResponse)
async def resolve_mcp_tool_chain(
    source_type: str = Query(...),
    target_type: str = Query(...),
) -> Dict[str, Any]:
    chain = mcp_gateway.resolve_tool_chain(source_type, target_type)
    return {
        "source_type": source_type,
        "target_type": target_type,
        "tool_chain": chain,
        "resolved": chain is not None,
    }



@app.post("/api/v1/blackboard/task-complete")
async def notify_task_completed(
    payload: Dict[str, Any], session: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Callback invoked by stateless `agent-worker` instances upon finishing a task,
    advancing the workflow state machine to the next node(s).
    """
    workflow_id = payload.get("workflow_id")
    run_id = payload.get("run_id")
    node_id = payload.get("node_id")
    verdict = payload.get("verdict", "PASS")

    if workflow_id and run_id and node_id:
        wf = await session.get(WorkflowModel, workflow_id)
        try:
            run_uuid = uuid.UUID(str(run_id))
            run = await session.get(WorkflowRunModel, run_uuid)
        except ValueError:
            run = None

        if wf and run:
            ctx = dict(run.context or {})
            ctx["last_verdict"] = verdict
            run.context = ctx
            await workflow_engine.advance_from_node(
                session, wf, run, node_id, branch_outcome=verdict
            )
            return {
                "status": "workflow_advanced",
                "run_id": str(run.id),
                "run_status": run.status,
                "current_nodes": run.current_nodes,
            }

    return {"status": "acknowledged"}


# --- Human-in-the-Loop (HITL) Queue Endpoints ---
@app.get("/api/v1/blackboard/hitl", response_model=List[HITLTicketResponse])
async def list_hitl_tickets(
    status: Optional[str] = Query(default=None),
    workflow_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> List[HITLTicketModel]:
    stmt = select(HITLTicketModel).order_by(HITLTicketModel.created_at.desc())
    if status:
        stmt = stmt.where(HITLTicketModel.status == status)
    if workflow_id:
        stmt = stmt.where(HITLTicketModel.workflow_id == workflow_id)
    res = await session.execute(stmt)
    return list(res.scalars().all())


@app.post(
    "/api/v1/blackboard/hitl/{ticket_id}/decide",
    response_model=HITLTicketResponse,
)
async def decide_hitl_ticket(
    ticket_id: uuid.UUID,
    payload: HITLDecisionRequest,
    session: AsyncSession = Depends(get_db),
) -> HITLTicketModel:
    ticket = await session.get(HITLTicketModel, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="HITL ticket not found.")
    if ticket.status != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"Ticket already resolved with status {ticket.status}.",
        )

    ticket.status = payload.decision
    ticket.reviewer_comment = payload.reviewer_comment
    ticket.resolved_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(ticket)

    # If approved and this was a tool_approval ticket, execute the tool with bypass_approval=True
    tool_result = None
    if payload.decision == "APPROVED" and ticket.gate_type == "tool_approval":
        if ticket.tool_id:
            invoke_res = await mcp_gateway.invoke_tool(
                session,
                ToolInvokeRequest(
                    agent_id=ticket.agent_id,
                    workflow_id=ticket.workflow_id,
                    task_id=ticket.task_id,
                    tool_id=ticket.tool_id,
                    arguments=ticket.tool_arguments or {},
                    bypass_approval=True,
                ),
            )
            tool_result = invoke_res.result

            # Record approved tool output on the Blackboard
            bb_entry = BlackboardEntryModel(
                workflow_id=ticket.workflow_id,
                task_id=ticket.task_id,
                agent_id=ticket.agent_id,
                entry_type="test_result",
                content=(
                    f"HITL Approved Tool `{ticket.tool_id}` executed.\n"
                    f"Reviewer note: {payload.reviewer_comment or 'Approved'}\n"
                    f"Result: {json.dumps(tool_result)}"
                ),
                metadata_={"hitl_ticket_id": str(ticket.id)},
            )
            session.add(bb_entry)
            await session.commit()

            # If workflow_activator was approved, persist the new workflow into the database
            if ticket.tool_id == "workflow_activator" and isinstance(tool_result, dict):
                act_wf_id = tool_result.get("workflow_id")
                act_yaml = (ticket.tool_arguments or {}).get("yaml_content")
                if act_wf_id and act_yaml:
                    existing_wf = await session.get(WorkflowModel, act_wf_id)
                    parsed_g = workflow_engine.parse_yaml_to_graph(act_yaml)
                    bpmn_x = workflow_engine.graph_to_bpmn_xml(act_wf_id, tool_result.get("workflow_name", act_wf_id), parsed_g)
                    if existing_wf:
                        existing_wf.name = tool_result.get("workflow_name", existing_wf.name)
                        existing_wf.description = tool_result.get("description", existing_wf.description)
                        existing_wf.state_graph = parsed_g
                        existing_wf.yaml_source = act_yaml
                        existing_wf.bpmn_xml = bpmn_x
                    else:
                        new_wf = WorkflowModel(
                            id=act_wf_id,
                            name=tool_result.get("workflow_name", act_wf_id),
                            description=tool_result.get("description", "Architect-generated workflow"),
                            state_graph=parsed_g,
                            yaml_source=act_yaml,
                            bpmn_xml=bpmn_x,
                        )
                        session.add(new_wf)
                    await session.commit()

    # If this was a workflow UserTask gate, advance the workflow run
    if ticket.gate_type == "user_task":
        run_id_str = (ticket.tool_arguments or {}).get("run_id")
        node_id = ticket.tool_id
        wf = await session.get(WorkflowModel, ticket.workflow_id)
        if wf and run_id_str and node_id:
            try:
                run = await session.get(
                    WorkflowRunModel, uuid.UUID(str(run_id_str))
                )
            except ValueError:
                run = None
            if run:
                if payload.decision == "APPROVED":
                    await workflow_engine.advance_from_node(
                        session, wf, run, node_id, branch_outcome="PASS"
                    )
                else:
                    run.status = "FAILED"
                    await session.commit()

    await workflow_engine._publish_event(
        {
            "event": "hitl.ticket_resolved",
            "ticket_id": str(ticket.id),
            "workflow_id": ticket.workflow_id,
            "task_id": ticket.task_id,
            "agent_id": ticket.agent_id,
            "decision": ticket.status,
            "reviewer_comment": ticket.reviewer_comment,
            "tool_result": tool_result,
        }
    )
    return ticket


# ==============================================================================
# 6. CONCIERGE ADMIN AGENT (/api/v1/chat) & WEBSOCKET STREAM (/ws/blackboard)
# ==============================================================================
@app.post("/api/v1/chat", response_model=ConciergeChatResponse)
async def chat_with_concierge(
    payload: ConciergeChatRequest, session: AsyncSession = Depends(get_db)
) -> ConciergeChatResponse:
    return await concierge_agent.handle_chat(session, payload)


# ==============================================================================
# 7. MULTI-TIER LLM ROUTER & NVIDIA NIM GATEWAY (/api/v1/llm)
# ==============================================================================
@app.get("/api/v1/llm/status")
async def get_llm_router_status() -> Dict[str, Any]:
    import time as _time

    redis_client = await get_redis()
    env_nim_key = os.getenv("NVIDIA_NIM_API_KEY", "")
    r_nim_key = await redis_client.get("llm:config:nim_api_key")
    effective_key = r_nim_key or env_nim_key

    r_model = await redis_client.get("llm:config:default_nim_model")
    default_nim_model = r_model or os.getenv(
        "NVIDIA_NIM_DEFAULT_MODEL", "poolside/laguna-xs-2.1"
    )
    r_rpm = await redis_client.get("llm:config:nim_max_rpm")
    max_rpm = int(r_rpm) if r_rpm else int(os.getenv("NIM_MAX_RPM", "35"))

    r_pref = await redis_client.get("llm:config:prefer_local")
    prefer_local = (
        True
        if r_pref is None
        else str(r_pref).lower() not in ("false", "0", "no")
    )

    minute_bucket = int(_time.time() // 60)
    curr_rpm_raw = await redis_client.get(f"llm:nim:rpm:{minute_bucket}")
    current_rpm = int(curr_rpm_raw) if curr_rpm_raw else 0

    masked_key = None
    nim_models_list = [
        "poolside/laguna-xs-2.1",
        "minimaxai/minimax-m3.1-flash",
        "google/diffusiongemma-26b-a4b-it",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "nvidia/riva-translate-4b-instruct-v2",
        "meta/llama-guard-4-12b",
    ]
    if effective_key:
        if len(effective_key) > 10:
            masked_key = f"{effective_key[:6]}...{effective_key[-4:]}"
        else:
            masked_key = "***configured***"
        # Try fetching real-time model list from NVIDIA NIM /v1/models (cached for 1 hour in redis)
        try:
            cached_models_raw = await redis_client.get("llm:nim:models_cache")
            if cached_models_raw:
                nim_models_list = json.loads(cached_models_raw)
            else:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    res = await client.get(
                        f"{os.getenv('NVIDIA_NIM_BASE_URL', 'https://integrate.api.nvidia.com/v1').rstrip('/')}/models",
                        headers={"Authorization": f"Bearer {effective_key}"},
                    )
                    if res.status_code == 200:
                        live_data = res.json().get("data", [])
                        live_ids = [m["id"] for m in live_data if "id" in m]
                        if live_ids:
                            # Prioritize confirmed active models first
                            active_top = [
                                "poolside/laguna-xs-2.1",
                                "minimaxai/minimax-m3.1-flash",
                                "google/diffusiongemma-26b-a4b-it",
                                "nvidia/nemotron-3.5-lightning-30b-a3b",
                                "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                                "nvidia/riva-translate-4b-instruct-v2",
                                "meta/llama-guard-4-12b",
                            ]
                            combined = active_top + [i for i in live_ids if i not in active_top]
                            nim_models_list = combined
                            await redis_client.setex(
                                "llm:nim:models_cache", 3600, json.dumps(combined)
                            )
        except Exception as exc:
            logger.debug("Failed to pull live NIM models list: %s", exc)

    cooldowns: Dict[str, str] = {}
    stats: Dict[str, Any] = {}
    try:
        if hasattr(redis_client, "keys"):
            cd_keys = await redis_client.keys("llm:cooldown:*")
            for k in cd_keys:
                val = await redis_client.get(k)
                if val:
                    cooldowns[k.replace("llm:cooldown:", "")] = val
            st_keys = await redis_client.keys("llm:stats:*")
            for k in st_keys:
                val = await redis_client.get(k)
                if val:
                    try:
                        stats[k.replace("llm:stats:", "")] = json.loads(val)
                    except Exception:
                        pass
    except Exception:
        pass

    return {
        "strategy": (
            "local_first_with_nim_failover"
            if prefer_local
            else "nim_first_with_local_failover"
        ),
        "prefer_local": prefer_local,
        "nvidia_nim": {
            "base_url": os.getenv(
                "NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
            ),
            "api_key_configured": bool(effective_key),
            "api_key_masked": masked_key,
            "default_model": default_nim_model,
            "max_rpm": max_rpm,
            "current_minute_rpm": current_rpm,
            "free_tier_models": nim_models_list,
        },
        "local_cluster": {
            "ollama": {
                "url": os.getenv(
                    "LOCAL_OLLAMA_URL", "http://192.168.1.150:11434"
                ),
                "models": [
                    "qwen2.5-coder:0.5b",
                    "qwen2.5-coder:7b",
                    "qwen3:8b",
                    "qwen3:1.7b",
                    "deepseek-coder-v2:16b-lite-instruct-q2_K",
                    "phi4-mini-reasoning:latest",
                    "gemma4:e2b",
                ],
            },
            "ct118": [
                {
                    "url": "http://192.168.1.180:8080/v1",
                    "model": "groot-19b",
                    "provider": "openai",
                },
                {
                    "url": "http://192.168.1.180:8081/v1",
                    "model": "qwen3-4b",
                    "provider": "openai",
                },
                {
                    "url": "http://192.168.1.180:8090/v1",
                    "model": "lfm3-1.7b",
                    "provider": "openai",
                },
            ],
            "laptop": [
                {
                    "url": "http://192.168.1.149:8080/v1",
                    "model": "mistral-24b",
                    "provider": "openai",
                },
                {
                    "url": "http://192.168.1.149:8081/v1",
                    "model": "qwen3-4b",
                    "provider": "openai",
                },
                {
                    "url": "http://192.168.1.149:8090/v1",
                    "model": "lfm3-1.7b",
                    "provider": "openai",
                },
            ],
        },
        "active_cooldowns": cooldowns,
        "endpoint_telemetry": stats,
    }


@app.post("/api/v1/llm/config")
async def update_llm_router_config(
    payload: LLMRouterConfigRequest,
) -> Dict[str, Any]:
    redis_client = await get_redis()
    if payload.nvidia_nim_api_key is not None:
        await redis_client.set(
            "llm:config:nim_api_key", payload.nvidia_nim_api_key.strip()
        )
    if payload.default_nim_model is not None:
        await redis_client.set(
            "llm:config:default_nim_model", payload.default_nim_model.strip()
        )
    if payload.nim_max_rpm is not None:
        await redis_client.set(
            "llm:config:nim_max_rpm", str(payload.nim_max_rpm)
        )
    if payload.prefer_local is not None:
        await redis_client.set(
            "llm:config:prefer_local",
            "true" if payload.prefer_local else "false",
        )
    if payload.clear_cooldowns and hasattr(redis_client, "keys"):
        cd_keys = await redis_client.keys("llm:cooldown:*")
        for k in cd_keys:
            await redis_client.delete(k)

    return await get_llm_router_status()


# ==============================================================================
# 8. EXTERNAL CHAT CHANNELS GATEWAY (Telegram / Discord / Easy Webhook)
# ==============================================================================
@app.get("/api/v1/channels/status")
async def get_channels_status() -> Dict[str, Any]:
    redis_client = await get_redis()
    tg_token = await redis_client.get("channel:telegram:token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = await redis_client.get("channel:telegram:chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")
    tg_enabled_raw = await redis_client.get("channel:telegram:enabled")
    tg_enabled = (str(tg_enabled_raw).lower() in ("true", "1")) if tg_enabled_raw is not None else bool(tg_token and tg_chat_id)

    dc_webhook = await redis_client.get("channel:discord:webhook") or os.getenv("DISCORD_WEBHOOK_URL", "")
    dc_enabled_raw = await redis_client.get("channel:discord:enabled")
    dc_enabled = (str(dc_enabled_raw).lower() in ("true", "1")) if dc_enabled_raw is not None else bool(dc_webhook)

    return {
        "telegram": {
            "enabled": tg_enabled,
            "configured": bool(tg_token and tg_chat_id),
            "bot_token_masked": f"{tg_token[:5]}...{tg_token[-4:]}" if len(tg_token) > 10 else None,
            "chat_id_masked": f"***{tg_chat_id[-4:]}" if len(tg_chat_id) > 4 else tg_chat_id or None,
            "webhook_path": "/api/v1/channels/telegram/webhook",
        },
        "discord": {
            "enabled": dc_enabled,
            "configured": bool(dc_webhook),
            "webhook_url_masked": f"{dc_webhook[:25]}...{dc_webhook[-6:]}" if len(dc_webhook) > 35 else None,
            "inbound_path": "/api/v1/channels/message",
        },
        "easy_chat": {
            "endpoint": "/api/v1/channels/message",
            "supported_actions": ["steer: <directive>", "run workflow", "inspect state", "prune blackboard"],
        },
    }


@app.post("/api/v1/channels/config")
async def update_channels_config(payload: ChannelConfigRequest) -> Dict[str, Any]:
    redis_client = await get_redis()
    if payload.telegram_bot_token is not None:
        await redis_client.set("channel:telegram:token", payload.telegram_bot_token.strip())
    if payload.telegram_chat_id is not None:
        await redis_client.set("channel:telegram:chat_id", payload.telegram_chat_id.strip())
    if payload.telegram_enabled is not None:
        await redis_client.set("channel:telegram:enabled", "true" if payload.telegram_enabled else "false")

    if payload.discord_webhook_url is not None:
        await redis_client.set("channel:discord:webhook", payload.discord_webhook_url.strip())
    if payload.discord_enabled is not None:
        await redis_client.set("channel:discord:enabled", "true" if payload.discord_enabled else "false")

    return await get_channels_status()


@app.post("/api/v1/channels/test")
async def test_channels_broadcast(
    message: Optional[str] = Query(default="🔔 [Blackboard Test] Discord webhook connectivity verified!")
) -> Dict[str, Any]:
    """Manually send a test broadcast message to all enabled external channels."""
    redis_client = await get_redis()
    results: Dict[str, Any] = {}
    
    dc_webhook = await redis_client.get("channel:discord:webhook") or os.getenv("DISCORD_WEBHOOK_URL", "")
    dc_en = await redis_client.get("channel:discord:enabled")
    if dc_webhook and str(dc_en).lower() in ("true", "1", "none"):
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.post(dc_webhook, json={"content": message})
                results["discord"] = {"status": "DELIVERED", "http_code": res.status_code}
        except Exception as exc:
            results["discord"] = {"status": "ERROR", "error": str(exc)}
    else:
        results["discord"] = {"status": "SKIPPED", "reason": "not configured or disabled"}

    tg_token = await redis_client.get("channel:telegram:token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat_id = await redis_client.get("channel:telegram:chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")
    tg_en = await redis_client.get("channel:telegram:enabled")
    if tg_token and tg_chat_id and str(tg_en).lower() in ("true", "1", "none"):
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat_id, "text": message},
                )
                results["telegram"] = {"status": "DELIVERED", "http_code": res.status_code}
        except Exception as exc:
            results["telegram"] = {"status": "ERROR", "error": str(exc)}
    else:
        results["telegram"] = {"status": "SKIPPED", "reason": "not configured or disabled"}

    return {"message": message, "results": results}


async def _notify_external_channels(text: str) -> None:
    """Publish a notification out to Telegram or Discord if enabled."""
    try:
        redis_client = await get_redis()
        # Discord Webhook Notification
        dc_webhook = await redis_client.get("channel:discord:webhook") or os.getenv("DISCORD_WEBHOOK_URL", "")
        dc_en = await redis_client.get("channel:discord:enabled")
        if dc_webhook and str(dc_en).lower() in ("true", "1", "none"):
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(dc_webhook, json={"content": text[:1900]})
        
        # Telegram Bot Notification
        tg_token = await redis_client.get("channel:telegram:token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat_id = await redis_client.get("channel:telegram:chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")
        tg_en = await redis_client.get("channel:telegram:enabled")
        if tg_token and tg_chat_id and str(tg_en).lower() in ("true", "1", "none"):
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat_id, "text": text[:4000], "parse_mode": "Markdown"},
                )
    except Exception as exc:
        logger.warning("External channel notification failed: %s", exc)


@app.post("/api/v1/channels/message")
async def incoming_channel_message(
    payload: ChannelIncomingMessage, session: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Unified inbound message endpoint for Telegram / Discord / Custom webhooks.
    Executes Concierge meta-tools (steering, workflow triggers, pruning, state queries)
    and broadcasts the result back.
    """
    concierge_res = await concierge_agent.handle_chat(
        session,
        ConciergeChatRequest(
            message=payload.message,
            workflow_id=payload.workflow_id or "code_review_pipeline",
        ),
    )

    reply_text = f"**[Blackboard {payload.channel.upper()}]**\n{concierge_res.reply}"
    asyncio.create_task(_notify_external_channels(reply_text))

    return {
        "channel": payload.channel,
        "sender": payload.sender_name or "external_user",
        "reply": concierge_res.reply,
        "tool_calls_executed": concierge_res.tool_calls_executed,
    }


@app.post("/api/v1/channels/telegram/webhook")
async def telegram_webhook_endpoint(
    request_data: Dict[str, Any], session: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    """Standard Telegram Bot webhook receiver."""
    message = request_data.get("message", {})
    text = message.get("text", "")
    from_user = message.get("from", {}).get("username") or str(message.get("from", {}).get("id", "user"))
    chat_id = str(message.get("chat", {}).get("id", ""))

    if text:
        concierge_res = await concierge_agent.handle_chat(
            session,
            ConciergeChatRequest(message=text, workflow_id="code_review_pipeline"),
        )
        redis_client = await get_redis()
        tg_token = await redis_client.get("channel:telegram:token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if tg_token and chat_id:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{tg_token}/sendMessage",
                        json={"chat_id": chat_id, "text": concierge_res.reply[:4000]},
                    )
            except Exception as e:
                logger.warning("Telegram reply failed: %s", e)

    return {"status": "ok"}


# ==============================================================================
# 9. BACKLOG, IDEAS & INTAKE SYSTEM (/api/v1/backlog)
# ==============================================================================
@app.get("/api/v1/backlog", response_model=List[BacklogItemResponse])
async def list_backlog_items(
    status: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> List[BacklogItemResponse]:
    stmt = select(BacklogItemModel).order_by(BacklogItemModel.created_at.desc())
    if status:
        stmt = stmt.where(BacklogItemModel.status == status)
    if category:
        stmt = stmt.where(BacklogItemModel.category == category)
    if priority:
        stmt = stmt.where(BacklogItemModel.priority == priority)
    result = await session.execute(stmt)
    items = result.scalars().all()
    response_items = []
    for item in items:
        meta = dict(item.metadata_ or {})
        run_id = meta.get("intake_run_id")
        if run_id:
            try:
                run_uuid = uuid.UUID(run_id)
                wf_run = await session.get(WorkflowRunModel, run_uuid)
                if wf_run:
                    meta["intake_run_status"] = wf_run.status
                    meta["intake_current_nodes"] = wf_run.current_nodes
                    meta["intake_completed_nodes"] = wf_run.completed_nodes
            except Exception:
                pass

        response_items.append(
            BacklogItemResponse(
                id=item.id,
                key=item.key,
                title=item.title,
                description=item.description,
                category=item.category,
                priority=item.priority,
                status=item.status,
                tags=item.tags,
                metadata=meta,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
        )
    return response_items


@app.post("/api/v1/backlog", response_model=BacklogItemResponse)
async def create_backlog_item(
    payload: BacklogItemCreate, session: AsyncSession = Depends(get_db)
) -> BacklogItemResponse:
    # Generate sequential IDEA key
    count_res = await session.execute(select(func.count(BacklogItemModel.id)))
    next_num = (count_res.scalar_one() or 0) + 1
    key = f"IDEA-{next_num}"

    item = BacklogItemModel(
        key=key,
        title=payload.title,
        description=payload.description,
        category=payload.category,
        priority=payload.priority,
        status=payload.status,
        tags=payload.tags,
        metadata_=payload.metadata,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    # Publish blackboard notification
    await workflow_engine._publish_event(
        {
            "event": "backlog.created",
            "id": str(item.id),
            "key": item.key,
            "title": item.title,
            "priority": item.priority,
            "status": item.status,
        }
    )

    return BacklogItemResponse(
        id=item.id,
        key=item.key,
        title=item.title,
        description=item.description,
        category=item.category,
        priority=item.priority,
        status=item.status,
        tags=item.tags,
        metadata=item.metadata_,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@app.get("/api/v1/backlog/{item_id}", response_model=BacklogItemResponse)
async def get_backlog_item(
    item_id: str, session: AsyncSession = Depends(get_db)
) -> BacklogItemResponse:
    stmt = select(BacklogItemModel).where(
        (BacklogItemModel.key == item_id) | (BacklogItemModel.id.cast(String) == item_id)
    )
    res = await session.execute(stmt)
    item = res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Backlog item not found.")
    return BacklogItemResponse(
        id=item.id,
        key=item.key,
        title=item.title,
        description=item.description,
        category=item.category,
        priority=item.priority,
        status=item.status,
        tags=item.tags,
        metadata=item.metadata_,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@app.put("/api/v1/backlog/{item_id}", response_model=BacklogItemResponse)
async def update_backlog_item(
    item_id: str,
    payload: BacklogItemUpdate,
    session: AsyncSession = Depends(get_db),
) -> BacklogItemResponse:
    stmt = select(BacklogItemModel).where(
        (BacklogItemModel.key == item_id) | (BacklogItemModel.id.cast(String) == item_id)
    )
    res = await session.execute(stmt)
    item = res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Backlog item not found.")

    if payload.title is not None:
        item.title = payload.title
    if payload.description is not None:
        item.description = payload.description
    if payload.category is not None:
        item.category = payload.category
    if payload.priority is not None:
        item.priority = payload.priority
    if payload.status is not None:
        item.status = payload.status
    if payload.tags is not None:
        item.tags = payload.tags
    if payload.metadata is not None:
        item.metadata_ = payload.metadata

    await session.commit()
    await session.refresh(item)
    return BacklogItemResponse(
        id=item.id,
        key=item.key,
        title=item.title,
        description=item.description,
        category=item.category,
        priority=item.priority,
        status=item.status,
        tags=item.tags,
        metadata=item.metadata_,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@app.delete("/api/v1/backlog/{item_id}")
async def delete_backlog_item(
    item_id: str, session: AsyncSession = Depends(get_db)
) -> Dict[str, str]:
    stmt = select(BacklogItemModel).where(
        (BacklogItemModel.key == item_id) | (BacklogItemModel.id.cast(String) == item_id)
    )
    res = await session.execute(stmt)
    item = res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Backlog item not found.")
    await session.delete(item)
    await session.commit()
    return {"status": "deleted", "item_id": item_id}


@app.post("/api/v1/backlog/{item_id}/intake")
async def trigger_backlog_intake(
    item_id: str, session: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Trigger the Intake & Triage pipeline workflow for this backlog item."""
    stmt = select(BacklogItemModel).where(
        (BacklogItemModel.key == item_id) | (BacklogItemModel.id.cast(String) == item_id)
    )
    res = await session.execute(stmt)
    item = res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Backlog item not found.")

    intake_wf = await session.get(WorkflowModel, "intake_pipeline")
    if not intake_wf:
        intake_wf = await session.get(WorkflowModel, "meta_workflow_architect")
    if not intake_wf:
        raise HTTPException(status_code=404, detail="No intake workflow registered.")

    prompt = (
        f"INTAKE REVIEW FOR [{item.key}] {item.title}\n"
        f"Category: {item.category} | Priority: {item.priority}\n\n"
        f"Description:\n{item.description}\n\n"
        f"Tags: {', '.join(item.tags)}\n"
        f"Evaluate architectural feasibility, tool requirements, and security risks."
    )
    run = await workflow_engine.start_workflow_run(
        session, intake_wf, initial_prompt=prompt, context={"backlog_key": item.key}
    )

    item.metadata_ = {
        **(item.metadata_ or {}),
        "intake_run_id": str(run.id),
        "intake_workflow_id": intake_wf.id,
    }
    item.status = "triaged"
    await session.commit()

    return {
        "status": "intake_started",
        "backlog_key": item.key,
        "workflow_id": intake_wf.id,
        "run_id": str(run.id),
    }


@app.get("/api/v1/backlog/{item_id}/artifacts")
async def get_backlog_artifacts(
    item_id: str, session: AsyncSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """Return all Blackboard artifacts generated during the intake pipeline for this backlog item."""
    stmt = select(BacklogItemModel).where(
        (BacklogItemModel.key == item_id) | (BacklogItemModel.id.cast(String) == item_id)
    )
    res = await session.execute(stmt)
    item = res.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Backlog item not found.")

    run_id = (item.metadata_ or {}).get("intake_run_id")
    bb_stmt = select(BlackboardEntryModel).where(
        (BlackboardEntryModel.workflow_id == "intake_pipeline")
        | (BlackboardEntryModel.workflow_id == "meta_workflow_architect")
    ).order_by(BlackboardEntryModel.created_at.asc())

    bb_res = await session.execute(bb_stmt)
    entries = bb_res.scalars().all()

    filtered = []
    for e in entries:
        e_meta = e.metadata_ or {}
        if (
            e_meta.get("backlog_key") == item.key
            or (run_id and e_meta.get("run_id") == run_id)
            or (item.key in e.content)
            or (run_id and run_id in e.task_id)
        ):
            filtered.append(
                {
                    "id": str(e.id),
                    "task_id": e.task_id,
                    "agent_id": e.agent_id,
                    "entry_type": e.entry_type,
                    "content": e.content,
                    "created_at": e.created_at.isoformat(),
                }
            )
    return filtered



@app.websocket("/ws/blackboard")
async def websocket_blackboard_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "event": "ws.connected",
                    "channel": REDIS_EVENTS_CHANNEL,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(
                    json.dumps({"event": "ws.pong"})
                )
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
