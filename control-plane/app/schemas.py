import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


# --- Agent Schemas ---
class InferenceConfig(BaseModel):
    provider: Literal["ollama", "nvidia_nim", "openai"] = "ollama"
    model_name: str = "qwen2.5-coder:14b"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout_seconds: Optional[float] = None
    slow_threshold_seconds: Optional[float] = None
    nim_model_name: Optional[str] = None
    fallback_endpoints: List[Dict[str, Any]] = Field(default_factory=list)


class AgentCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    name: str
    role: str
    system_prompt: str
    inference_config: InferenceConfig = Field(default_factory=InferenceConfig)
    allowed_tools: List[str] = Field(default_factory=list)
    subscribed_events: List[str] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    system_prompt: Optional[str] = None
    inference_config: Optional[InferenceConfig] = None
    allowed_tools: Optional[List[str]] = None
    subscribed_events: Optional[List[str]] = None


class AgentResponse(AgentCreate):
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Agent Memory Schemas ---
class MemoryCreate(BaseModel):
    memory_type: Literal["episodic", "semantic", "reflection"] = "episodic"
    content: str
    embedding: Optional[List[float]] = None


class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=3, ge=1, le=20)
    memory_type: Optional[Literal["episodic", "semantic", "reflection"]] = None


class MemoryResponse(BaseModel):
    id: uuid.UUID
    agent_id: str
    memory_type: str
    content: str
    similarity: Optional[float] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- MCP Tool Schemas ---
class MCPToolCreate(BaseModel):
    id: str
    name: str
    description: str
    server_name: str = "builtin"
    transport: Literal["stdio", "sse", "http", "builtin"] = "builtin"
    endpoint_config: Dict[str, Any] = Field(default_factory=dict)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False


class MCPToolUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    server_name: Optional[str] = None
    transport: Optional[Literal["stdio", "sse", "http", "builtin"]] = None
    endpoint_config: Optional[Dict[str, Any]] = None
    input_schema: Optional[Dict[str, Any]] = None
    requires_approval: Optional[bool] = None


class MCPToolResponse(MCPToolCreate):
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ToolInvokeRequest(BaseModel):
    agent_id: str
    workflow_id: str
    task_id: str
    tool_id: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    bypass_approval: bool = False


class ToolInvokeResponse(BaseModel):
    status: Literal["EXECUTED", "AWAITING_APPROVAL", "DENIED", "ERROR"]
    result: Optional[Any] = None
    hitl_ticket_id: Optional[uuid.UUID] = None
    message: Optional[str] = None


# --- Blackboard Schemas ---
EntryTypeLiteral = Literal[
    "hypothesis",
    "critique",
    "spec",
    "code",
    "test_result",
    "steering",
    "summary",
]


class BlackboardEntryCreate(BaseModel):
    workflow_id: str
    task_id: str
    agent_id: str
    entry_type: EntryTypeLiteral
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BlackboardEntryResponse(BaseModel):
    id: uuid.UUID
    workflow_id: str
    task_id: str
    agent_id: str
    entry_type: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SteeringCreate(BaseModel):
    workflow_id: str
    task_id: str = "operator-steering"
    agent_id: str = "operator"
    content: str
    target_agent_id: Optional[str] = None


# --- Workflow & BPMN Schemas ---
class WorkflowNode(BaseModel):
    id: str
    name: str
    type: Literal[
        "StartEvent",
        "EndEvent",
        "ServiceTask",
        "ParallelGateway",
        "ExclusiveGateway",
        "UserTask",
    ]
    agent_id: Optional[str] = None
    entry_type: Optional[EntryTypeLiteral] = "spec"
    prompt_template: Optional[str] = None
    condition_expression: Optional[str] = None


class WorkflowEdge(BaseModel):
    id: str
    source: str
    target: str
    condition: Optional[str] = None


class WorkflowStateGraph(BaseModel):
    nodes: List[WorkflowNode] = Field(default_factory=list)
    edges: List[WorkflowEdge] = Field(default_factory=list)


class WorkflowCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    state_graph: Optional[WorkflowStateGraph] = None
    yaml_source: Optional[str] = None
    bpmn_xml: Optional[str] = None


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    state_graph: Optional[WorkflowStateGraph] = None
    yaml_source: Optional[str] = None
    bpmn_xml: Optional[str] = None


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: str
    state_graph: Dict[str, Any]
    yaml_source: Optional[str] = None
    bpmn_xml: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowTriggerRequest(BaseModel):
    initial_prompt: str
    context: Dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResponse(BaseModel):
    id: uuid.UUID
    workflow_id: str
    status: str
    current_nodes: List[str]
    completed_nodes: List[str]
    context: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- HITL Ticket Schemas ---
class HITLDecisionRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    reviewer_comment: Optional[str] = None


class HITLTicketResponse(BaseModel):
    id: uuid.UUID
    workflow_id: str
    task_id: str
    agent_id: str
    gate_type: str
    tool_id: Optional[str] = None
    tool_arguments: Dict[str, Any]
    status: str
    reviewer_comment: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# --- Concierge Chat Schemas ---
class ConciergeChatRequest(BaseModel):
    message: str
    workflow_id: Optional[str] = None
    history: List[Dict[str, str]] = Field(default_factory=list)


class ConciergeChatResponse(BaseModel):
    reply: str
    tool_calls_executed: List[Dict[str, Any]] = Field(default_factory=list)
    state_snapshot: Dict[str, Any] = Field(default_factory=dict)


# --- Multi-Tier LLM Router & NVIDIA NIM Config Schemas ---
class LLMRouterConfigRequest(BaseModel):
    nvidia_nim_api_key: Optional[str] = None
    default_nim_model: Optional[str] = None
    nim_max_rpm: Optional[int] = Field(default=None, ge=1, le=600)
    prefer_local: Optional[bool] = None
    clear_cooldowns: bool = False


# --- External Chat Bridge Schemas (Telegram / Discord / Webhook) ---
class ChannelConfigRequest(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: Optional[bool] = None
    discord_webhook_url: Optional[str] = None
    discord_enabled: Optional[bool] = None


class ChannelIncomingMessage(BaseModel):
    channel: Literal["telegram", "discord", "webhook", "easy_chat"]
    user_id: Optional[str] = None
    sender_name: Optional[str] = None
    message: str
    workflow_id: Optional[str] = None
