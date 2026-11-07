import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    inference_config: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {
            "provider": "ollama",
            "model_name": "qwen2.5-coder:14b",
            "base_url": None,
            "api_key": None,
            "temperature": 0.2,
        },
    )
    allowed_tools: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    subscribed_events: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    memories: Mapped[List["AgentMemoryModel"]] = relationship(
        "AgentMemoryModel",
        back_populates="agent",
        cascade="all, delete-orphan",
    )


class AgentMemoryModel(Base):
    __tablename__ = "agent_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    memory_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="episodic"
    )  # episodic | semantic | reflection
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(768), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    agent: Mapped["AgentModel"] = relationship(
        "AgentModel", back_populates="memories"
    )


class MCPToolModel(Base):
    __tablename__ = "mcp_tools"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    server_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="builtin"
    )
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="builtin"
    )  # stdio | sse | http | builtin
    endpoint_config: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    input_schema: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    consumes_type: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    produces_type: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class BlackboardEntryModel(Base):
    __tablename__ = "blackboard_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    entry_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )  # hypothesis | critique | spec | code | test_result | steering | summary
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_blackboard_workflow_created", "workflow_id", "created_at"),
    )


class BlackboardNodeModel(Base):
    __tablename__ = "blackboard_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    author_agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    node_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )  # hypothesis | spec | code | critique | steering | test_result | summary
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True
    )  # active | superseded | falsified
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_blackboard_nodes_wf_status", "workflow_id", "status"),
        Index("ix_blackboard_nodes_created", "workflow_id", "created_at"),
    )


class BlackboardEdgeModel(Base):
    __tablename__ = "blackboard_edges"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blackboard_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("blackboard_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    relation_type: Mapped[str] = mapped_column(
        String(64), primary_key=True, default="implements"
    )  # implements | critiques | revises | verifies | steers | summarizes
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_blackboard_edges_target", "target_id", "relation_type"),
    )



class WorkflowModel(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state_graph: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    yaml_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bpmn_xml: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="RUNNING"
    )  # RUNNING | AWAITING_APPROVAL | COMPLETED | FAILED
    current_nodes: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    completed_nodes: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    context: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class HITLTicketModel(Base):
    __tablename__ = "hitl_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    gate_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="tool_approval"
    )  # tool_approval | user_task
    tool_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tool_arguments: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", index=True
    )  # PENDING | APPROVED | REJECTED
    reviewer_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    execution_result: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BacklogItemModel(Base):
    __tablename__ = "backlog_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="feature"
    )  # feature | workflow | agent | tool | improvement | bug | research
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="P1"
    )  # P0 | P1 | P2 | P3
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="idea", index=True
    )  # idea | triaged | ready | in_progress | completed | tabled
    tags: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    metadata_: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AgentRevisionModel(Base):
    __tablename__ = "agent_revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_tools: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    inference_config: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    fitness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mutation_reason: Mapped[str] = mapped_column(
        String(128), nullable=False, default="autonomous_promotion"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class OptimizationRunModel(Base):
    __tablename__ = "optimization_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ANALYZING", index=True
    )  # ANALYZING | SHADOW_TESTING | PROMOTED | REJECTED_REGRESSION | ROLLED_BACK
    baseline_fitness: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    candidate_fitness: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    candidate_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    candidate_tools: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    metrics_telemetry: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


