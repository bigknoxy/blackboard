import asyncio
import json
import logging
import os
import uuid
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
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.concierge import compute_embedding, concierge_agent
from app.database import AsyncSessionLocal, get_db, get_redis, init_db
from app.mcp_gateway import mcp_gateway
from app.models import (
    AgentMemoryModel,
    AgentModel,
    BlackboardEntryModel,
    HITLTicketModel,
    MCPToolModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    BlackboardEntryCreate,
    BlackboardEntryResponse,
    ConciergeChatRequest,
    ConciergeChatResponse,
    HITLDecisionRequest,
    HITLTicketResponse,
    MCPToolCreate,
    MCPToolResponse,
    MCPToolUpdate,
    MemoryCreate,
    MemoryResponse,
    MemorySearchRequest,
    SteeringCreate,
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

LOCAL_OLLAMA_URL = os.getenv("LOCAL_OLLAMA_URL", "http://192.168.1.100:11434")
WORKFLOWS_DIR = Path(os.getenv("WORKFLOWS_DIR", "/workflows"))


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
                "name": "Principal Systems Architect",
                "role": "Research & Specification Architect",
                "system_prompt": (
                    "You are the Principal Systems Architect. Analyze requirements, search the repository, "
                    "formulate hypotheses, and produce clear technical specifications (`spec`) with interface contracts."
                ),
                "inference_config": {
                    "provider": "ollama",
                    "model_name": "qwen2.5-coder:14b",
                    "base_url": LOCAL_OLLAMA_URL,
                    "api_key": None,
                    "temperature": 0.2,
                },
                "allowed_tools": ["repo_search", "ast_complexity_analyzer"],
                "subscribed_events": ["workflow.started", "spec.requested"],
            },
            {
                "id": "coder",
                "name": "Staff Implementation Engineer",
                "role": "Polyglot Code Synthesizer",
                "system_prompt": (
                    "You are the Staff Implementation Engineer. Consume architecture specs and steering instructions "
                    "from the Blackboard and produce clean, memory-safe, production-ready code (`code`)."
                ),
                "inference_config": {
                    "provider": "ollama",
                    "model_name": "qwen2.5-coder:14b",
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
                "name": "Autonomous Verification Engineer",
                "role": "Test & Sandbox Runner",
                "system_prompt": (
                    "You are the Autonomous Verification Engineer. Execute isolated pytest suites against candidate code "
                    "artifacts on the Blackboard and publish deterministic `test_result` entries."
                ),
                "inference_config": {
                    "provider": "ollama",
                    "model_name": "qwen2.5-coder:14b",
                    "base_url": LOCAL_OLLAMA_URL,
                    "api_key": None,
                    "temperature": 0.1,
                },
                "allowed_tools": ["pytest_sandbox_runner", "repo_search"],
                "subscribed_events": ["code.published"],
            },
            {
                "id": "sec-auditor",
                "name": "Application Security Auditor",
                "role": "Security & Compliance Reviewer",
                "system_prompt": (
                    "You are the Application Security Auditor. Run OWASP/CWE static scans on specs and code, "
                    "publish thorough security critiques (`critique`), and gate production releases."
                ),
                "inference_config": {
                    "provider": "ollama",
                    "model_name": "qwen2.5-coder:14b",
                    "base_url": LOCAL_OLLAMA_URL,
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
