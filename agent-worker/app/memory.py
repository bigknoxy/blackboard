import hashlib
import logging
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, String, Text, bindparam, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger("agent_worker.memory")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://blackboard:blackboard_secret@postgres:5432/blackboard_db",
)
LOCAL_OLLAMA_URL = os.getenv("LOCAL_OLLAMA_URL", "http://192.168.1.100:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "768"))

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=int(os.getenv("DB_POOL_SIZE", "3")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
    pool_pre_ping=True,
)
WorkerSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


class WorkerAgentMemory(Base):
    __tablename__ = "agent_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[List[float]] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a)) or 1.0
    norm_b = math.sqrt(sum(b * b for b in vec_b)) or 1.0
    return max(0.0, round(dot / (norm_a * norm_b), 4))


class InMemoryScopedMemoryStore:
    """
    Isolated vector store for unit & Popperian falsification tests (Falsifier 2).
    Enforces strict `WHERE agent_memories.agent_id = :agent_id` scoping.
    """

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []

    async def insert_memory(
        self,
        agent_id: str,
        memory_type: str,
        content: str,
        embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        emb = embedding or await embed_text(content)
        rec = {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "memory_type": memory_type,
            "content": content,
            "embedding": emb,
            "created_at": datetime.now(timezone.utc),
        }
        self._records.append(rec)
        return rec

    def search_scoped(
        self, agent_id: str, query_vec: List[float], top_k: int = 3
    ) -> List[Dict[str, Any]]:
        scoped = [r for r in self._records if r["agent_id"] == agent_id]
        scored = [
            {
                "id": r["id"],
                "agent_id": r["agent_id"],
                "memory_type": r["memory_type"],
                "content": r["content"],
                "similarity": _cosine_similarity(query_vec, r["embedding"]),
            }
            for r in scoped
        ]
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[:top_k]


async def embed_text(text_input: str) -> List[float]:
    """
    Generate a 768-dim vector via local/LAN Ollama (`nomic-embed-text`),
    with a deterministic unit-normalized semantic hash vector fallback.
    """
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.post(
                f"{LOCAL_OLLAMA_URL.rstrip('/')}/api/embeddings",
                json={"model": EMBEDDING_MODEL, "prompt": text_input},
            )
            if resp.status_code == 200:
                emb = resp.json().get("embedding")
                if isinstance(emb, list) and len(emb) == EMBEDDING_DIMENSION:
                    return [float(x) for x in emb]
    except Exception:
        pass

    vec = [0.0] * EMBEDDING_DIMENSION
    tokens = [
        tok.strip(".,:;!?()[]{}\"'").lower()
        for tok in text_input.split()
        if tok.strip()
    ] or ["empty"]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, len(digest), 2):
            idx = ((digest[i] << 8) | digest[i + 1]) % EMBEDDING_DIMENSION
            sign = 1.0 if (digest[i] & 1) == 0 else -1.0
            vec[idx] += sign * (1.0 + (digest[i + 1] / 255.0))
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def build_scoped_memory_query(
    agent_id: str, query_vec: List[float], top_k: int = 3
):
    """
    Build the parameterized SQLAlchemy statement that strictly scopes vector
    retrieval to `WHERE agent_memories.agent_id = :agent_id`.
    """
    distance_expr = WorkerAgentMemory.embedding.cosine_distance(query_vec)
    stmt = (
        select(WorkerAgentMemory, distance_expr.label("distance"))
        .where(
            WorkerAgentMemory.agent_id
            == bindparam("agent_id", value=agent_id)
        )
        .order_by(distance_expr.asc())
        .limit(top_k)
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    return stmt, str(compiled), dict(compiled.params)


async def retrieve_top_k_memories(
    agent_id: str,
    task_description: str,
    top_k: int = 3,
    memory_store: Optional[InMemoryScopedMemoryStore] = None,
    return_query_audit: bool = False,
) -> Union[
    List[Dict[str, Any]],
    Tuple[List[Dict[str, Any]], str, Dict[str, Any]],
]:
    """
    Hydration Memory Retrieval:
    Embed the task description and retrieve top-k nearest memories from `agent_memories`
    strictly scoped to `agent_id` using pgvector cosine distance.
    """
    query_vec = await embed_text(task_description)
    stmt, compiled_sql, bound_params = build_scoped_memory_query(
        agent_id=agent_id, query_vec=query_vec, top_k=top_k
    )

    if memory_store is not None:
        memories = memory_store.search_scoped(
            agent_id=agent_id, query_vec=query_vec, top_k=top_k
        )
        if return_query_audit:
            return memories, compiled_sql, bound_params
        return memories

    async with WorkerSessionLocal() as session:
        result = await session.execute(stmt, {"agent_id": agent_id})
        memories = []
        for mem, dist in result.all():
            memories.append(
                {
                    "id": str(mem.id),
                    "agent_id": mem.agent_id,
                    "memory_type": mem.memory_type,
                    "content": mem.content,
                    "similarity": max(0.0, round(1.0 - float(dist or 0.0), 4)),
                }
            )
        if return_query_audit:
            return memories, compiled_sql, bound_params
        return memories


def format_prior_experience_block(memories: List[Dict[str, Any]]) -> str:
    """Format retrieved memories inside a `<prior_experience>` XML context block."""
    if not memories:
        return (
            "<prior_experience>\n"
            "  No prior episodic or reflection memories recorded for this agent yet.\n"
            "</prior_experience>"
        )
    lines = ["<prior_experience>"]
    for idx, mem in enumerate(memories, start=1):
        mtype = mem.get("memory_type", "episodic")
        sim = mem.get("similarity", 0.0)
        content = mem.get("content", "")
        lines.append(
            f'  <memory rank="{idx}" type="{mtype}" similarity="{sim}">{content}</memory>'
        )
    lines.append("</prior_experience>")
    return "\n".join(lines)


async def store_agent_reflection(
    agent_id: str,
    reflection_content: str,
    memory_type: str = "reflection",
) -> str:
    """Embed and store post-task agent reflection into `agent_memories`."""
    emb = await embed_text(reflection_content)
    async with WorkerSessionLocal() as session:
        record = WorkerAgentMemory(
            agent_id=agent_id,
            memory_type=memory_type,
            content=reflection_content,
            embedding=emb,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return str(record.id)
