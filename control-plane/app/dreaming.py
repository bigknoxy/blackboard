"""Offline, copy-on-write consolidation of agent memories."""

import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import bindparam, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.concierge import compute_embedding
from app.database import get_redis
from app.models import AgentMemoryModel, AgentModel, BlackboardEntryModel

logger = logging.getLogger("blackboard.dreaming")

ACTIVE_MEMORY_TYPES = ("semantic", "episodic", "reflection")
GROUND_TRUTH_ENTRY_TYPES = ("test_result", "critique", "architecture_decision")
INDEX_MEMORY_TYPE = "index"
MAX_INDEX_LINES = 25
MAX_INDEX_BYTES = 2048

_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?previous\s+instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(?:all\s+)?previous\s+instructions\b", re.IGNORECASE),
    re.compile(r"(?:^|\s)system\s*:", re.IGNORECASE),
    re.compile(r"\bexfiltrat(?:e|ing|ion)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"<\s*script\b", re.IGNORECASE),
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9_]+")
_CONTRADICTION_MARKERS = (
    "incorrect",
    "not correct",
    "wrong",
    "false",
    "stale",
    "obsolete",
    "deprecated",
    "no longer valid",
    "superseded",
    "must not",
    "does not work",
    "broken",
    "regression",
)
_FAILURE_MARKERS = ("failed", "failure", "error", "broken", "not passing", "does not pass")
_DECISION_CHANGE_MARKERS = (
    "instead of",
    "replace",
    "replaced",
    "migrate",
    "migrated",
    "supersedes",
    "no longer use",
)
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
}


def _normalized_text(content: str) -> str:
    return _WHITESPACE_RE.sub(" ", content.strip().lower())


def _tokens(content: str) -> Set[str]:
    return set(_WORD_RE.findall(content.lower()))


def _token_overlap(left: str, right: str) -> float:
    """Return overlap coefficient, treating identical token sets as duplicates."""
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    if _normalized_text(left) == _normalized_text(right):
        return 1.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _meaningful_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left) - _STOP_WORDS
    right_tokens = _tokens(right) - _STOP_WORDS
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def detect_prompt_injection(content: str) -> bool:
    """Detect instruction-like content that must not leave quarantine."""
    return any(pattern.search(content or "") for pattern in _INJECTION_PATTERNS)


def sanitize_web_content(content: str) -> str:
    """Remove presentation markup while preserving the external finding's text."""
    decoded = html.unescape(content or "")
    without_tags = _HTML_TAG_RE.sub(" ", decoded)
    return _WHITESPACE_RE.sub(" ", without_tags).strip()


def _contradiction_evidence(
    content: str, ground_truth: List[BlackboardEntryModel]
) -> Optional[BlackboardEntryModel]:
    for entry in ground_truth:
        entry_text = entry.content.lower()
        if entry.entry_type == "critique" and any(
            marker in entry_text for marker in _CONTRADICTION_MARKERS
        ):
            if _meaningful_overlap(content, entry.content) >= 0.25:
                return entry
        elif entry.entry_type == "test_result" and any(
            marker in entry_text for marker in _FAILURE_MARKERS
        ):
            if _meaningful_overlap(content, entry.content) >= 0.25:
                return entry
        elif entry.entry_type == "architecture_decision" and any(
            marker in entry_text for marker in _DECISION_CHANGE_MARKERS
        ):
            if _meaningful_overlap(content, entry.content) >= 0.25:
                return entry
    return None


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _build_index(
    agent_id: str,
    retained: List[AgentMemoryModel],
    ground_truth: List[BlackboardEntryModel],
    promoted_count: int,
) -> str:
    lines = [f"Memory Index: {agent_id}", f"Active: {len(retained)} | Promoted: {promoted_count}"]
    for memory in retained:
        content = " ".join(memory.content.split())
        lines.append(f"- [{memory.memory_type}] {content}")
    for entry in ground_truth[:3]:
        content = " ".join(entry.content.split())
        lines.append(f"- [{entry.entry_type}] {content}")

    lines = lines[:MAX_INDEX_LINES]
    while len("\n".join(lines).encode("utf-8")) > MAX_INDEX_BYTES and len(lines) > 2:
        lines.pop()
    return _truncate_utf8("\n".join(lines), MAX_INDEX_BYTES)


def _transition(
    memory: AgentMemoryModel,
    target_type: str,
    reason: str,
    content: Optional[str] = None,
) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "memory_id": str(memory.id),
        "agent_id": memory.agent_id,
        "from": memory.memory_type,
        "to": target_type,
        "reason": reason,
    }
    if content is not None and content != memory.content:
        item["content"] = content
    return item


def _classify_transitions(
    transitions: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    classified: Dict[str, List[Dict[str, Any]]] = {
        "promote": [],
        "reject": [],
        "archive": [],
        "deduplicate": [],
    }
    for transition in transitions:
        reason = transition["reason"]
        target = transition["to"]
        if target == "semantic" and transition["from"] == "quarantined_web":
            classified["promote"].append(transition)
        elif target == "rejected_injection":
            classified["reject"].append(transition)
        elif reason == "near_duplicate":
            classified["deduplicate"].append(transition)
        elif target == "archived_dream":
            classified["archive"].append(transition)
    return classified


class MemoryDreamingWorker:
    """Consolidate one agent's memories without exposing cross-agent rows."""

    async def run_dream_pass(
        self,
        session: AsyncSession,
        agent_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if agent_id is None:
            result = await session.execute(select(AgentModel.id).order_by(AgentModel.id))
            agent_ids = list(result.scalars().all())
            reports = [
                await self._run_for_agent(session, target_agent_id, dry_run=dry_run)
                for target_agent_id in agent_ids
            ]
            return {
                "agent_id": None,
                "dry_run": dry_run,
                "agents": reports,
                "summary": self._summarize(reports),
            }

        report = await self._run_for_agent(session, agent_id, dry_run=dry_run)
        report["summary"] = self._summarize([report])
        return report

    async def _run_for_agent(
        self, session: AsyncSession, agent_id: str, dry_run: bool
    ) -> Dict[str, Any]:
        agent = await session.get(AgentModel, agent_id)
        if not agent:
            raise ValueError(f"Agent '{agent_id}' not found.")

        memories_result = await session.execute(
            select(AgentMemoryModel)
            .where(
                AgentMemoryModel.agent_id == bindparam("agent_id"),
                AgentMemoryModel.memory_type.in_(
                    (*ACTIVE_MEMORY_TYPES, "quarantined_web")
                ),
            )
            .order_by(AgentMemoryModel.created_at.asc()),
            {"agent_id": agent_id},
        )
        memories = list(memories_result.scalars().all())

        ground_truth_result = await session.execute(
            select(BlackboardEntryModel)
            .where(
                BlackboardEntryModel.agent_id == bindparam("agent_id"),
                BlackboardEntryModel.entry_type.in_(GROUND_TRUTH_ENTRY_TYPES),
            )
            .order_by(BlackboardEntryModel.created_at.desc())
            .limit(50),
            {"agent_id": agent_id},
        )
        ground_truth = list(ground_truth_result.scalars().all())

        transitions: List[Dict[str, Any]] = []
        retained: List[AgentMemoryModel] = []
        canonical_contents: List[str] = []
        by_id = {str(memory.id): memory for memory in memories}

        active_memories = [
            memory for memory in memories if memory.memory_type in ACTIVE_MEMORY_TYPES
        ]
        quarantined_memories = [
            memory for memory in memories if memory.memory_type == "quarantined_web"
        ]

        for memory in active_memories:
            evidence = _contradiction_evidence(memory.content, ground_truth)
            if evidence is not None:
                transitions.append(
                    _transition(
                        memory,
                        "archived_dream",
                        f"contradicted_by_{evidence.entry_type}",
                    )
                )
                continue
            if any(
                _token_overlap(memory.content, canonical) >= 0.85
                for canonical in canonical_contents
            ):
                transitions.append(
                    _transition(memory, "archived_dream", "near_duplicate")
                )
                continue
            retained.append(memory)
            canonical_contents.append(memory.content)

        promoted_count = 0
        for memory in quarantined_memories:
            if detect_prompt_injection(memory.content):
                transitions.append(
                    _transition(memory, "rejected_injection", "prompt_injection")
                )
                continue

            sanitized = sanitize_web_content(memory.content)
            if not sanitized:
                transitions.append(
                    _transition(memory, "rejected_injection", "empty_after_sanitize")
                )
                continue

            evidence = _contradiction_evidence(sanitized, ground_truth)
            if evidence is not None:
                transitions.append(
                    _transition(
                        memory,
                        "archived_dream",
                        f"contradicted_by_{evidence.entry_type}",
                    )
                )
                continue
            if any(
                _token_overlap(sanitized, canonical) >= 0.85
                for canonical in canonical_contents
            ):
                transitions.append(
                    _transition(memory, "archived_dream", "near_duplicate")
                )
                continue

            transitions.append(
                _transition(memory, "semantic", "clean_web_evidence", sanitized)
            )
            canonical_contents.append(sanitized)
            promoted_count += 1

        retained_after_promotion = list(retained)
        promoted_objects = [
            by_id[transition["memory_id"]]
            for transition in transitions
            if transition["to"] == "semantic"
            and transition["from"] == "quarantined_web"
        ]
        retained_after_promotion.extend(promoted_objects)

        index_text = _build_index(
            agent_id, retained_after_promotion, ground_truth, promoted_count
        )
        plan = {
            "transitions": transitions,
            **self._classify_transitions(transitions),
            "index": {
                "action": "replace",
                "memory_type": INDEX_MEMORY_TYPE,
                "content": index_text,
                "lines": len(index_text.splitlines()),
                "bytes": len(index_text.encode("utf-8")),
            },
        }
        validation_errors = self._validate_plan(plan, agent_id)
        status = "validated" if not validation_errors else "aborted"
        report: Dict[str, Any] = {
            "agent_id": agent_id,
            "dry_run": dry_run,
            "status": status,
            "plan": plan,
            "changes": transitions,
            "index": index_text,
            "validation": {
                "valid": not validation_errors,
                "errors": validation_errors,
            },
            "counts": {
                "promoted": len(plan["promote"]),
                "rejected": len(plan["reject"]),
                "deduplicated": len(plan["deduplicate"]),
                "archived": len(plan["archive"]),
            },
        }

        if validation_errors:
            return report
        if dry_run:
            report["status"] = "dry_run"
            return report

        try:
            for transition in transitions:
                memory = by_id[transition["memory_id"]]
                memory.memory_type = transition["to"]
                if "content" in transition:
                    memory.content = transition["content"]

            await session.execute(
                delete(AgentMemoryModel).where(
                    AgentMemoryModel.agent_id == bindparam("agent_id"),
                    AgentMemoryModel.memory_type == INDEX_MEMORY_TYPE,
                ),
                {"agent_id": agent_id},
            )
            index_embedding = await compute_embedding(index_text)
            session.add(
                AgentMemoryModel(
                    agent_id=agent_id,
                    memory_type=INDEX_MEMORY_TYPE,
                    content=index_text,
                    embedding=index_embedding,
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Memory dream failed validation for agent %s", agent_id)
            report["status"] = "aborted"
            report["validation"]["valid"] = False
            report["validation"]["errors"].append("commit_failed")
            return report

        metadata = self._dream_metadata(report)
        try:
            redis_client = get_redis()
            await redis_client.set(
                f"memory:dream:last:{agent_id}", json.dumps(metadata, sort_keys=True)
            )
        except Exception:
            logger.warning("Could not record last dream metadata for %s", agent_id)

        report["status"] = "committed"
        report["last_dream"] = metadata
        return report

    def _validate_plan(self, plan: Dict[str, Any], agent_id: str) -> List[str]:
        errors: List[str] = []
        allowed_targets = {
            "semantic",
            "archived_dream",
            "rejected_injection",
        }
        for transition in plan["transitions"]:
            if transition["agent_id"] != agent_id:
                errors.append(f"cross_agent_transition:{transition['memory_id']}")
            if transition["to"] not in allowed_targets:
                errors.append(f"invalid_target:{transition['memory_id']}")
            if (
                transition["from"] == "quarantined_web"
                and transition["to"] == "semantic"
                and detect_prompt_injection(transition.get("content", ""))
            ):
                errors.append(f"injection_not_sanitized:{transition['memory_id']}")
        index = plan["index"]
        if index["lines"] > MAX_INDEX_LINES:
            errors.append("index_too_many_lines")
        if index["bytes"] > MAX_INDEX_BYTES:
            errors.append("index_too_large")
        return errors

    def _classify_transitions(
        self, transitions: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        return _classify_transitions(transitions)

    def _dream_metadata(self, report: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "agent_id": report["agent_id"],
            "dreamed_at": datetime.now(timezone.utc).isoformat(),
            "status": report["status"],
            "counts": report["counts"],
            "index_lines": report["plan"]["index"]["lines"],
            "index_bytes": report["plan"]["index"]["bytes"],
        }

    def _summarize(self, reports: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        reports = list(reports)
        return {
            "agents": len(reports),
            "promoted": sum(report["counts"]["promoted"] for report in reports),
            "rejected": sum(report["counts"]["rejected"] for report in reports),
            "deduplicated": sum(
                report["counts"]["deduplicated"] for report in reports
            ),
            "archived": sum(report["counts"]["archived"] for report in reports),
        }


async def get_agent_memory_index(
    session: AsyncSession, agent_id: str
) -> Dict[str, Any]:
    """Return an agent's compact index and current memory-state counts."""
    index_result = await session.execute(
        select(AgentMemoryModel)
        .where(
            AgentMemoryModel.agent_id == bindparam("agent_id"),
            AgentMemoryModel.memory_type == INDEX_MEMORY_TYPE,
        )
        .order_by(AgentMemoryModel.created_at.desc())
        .limit(1),
        {"agent_id": agent_id},
    )
    index_memory = index_result.scalars().first()
    index_text = index_memory.content if index_memory else ""

    counts_result = await session.execute(
        select(AgentMemoryModel.memory_type, AgentMemoryModel.id)
        .where(AgentMemoryModel.agent_id == bindparam("agent_id")),
        {"agent_id": agent_id},
    )
    type_counts: Dict[str, int] = {}
    for memory_type, _ in counts_result.all():
        type_counts[memory_type] = type_counts.get(memory_type, 0) + 1

    active_count = sum(type_counts.get(memory_type, 0) for memory_type in ACTIVE_MEMORY_TYPES)
    quarantined_count = type_counts.get("quarantined_web", 0)
    archived_count = type_counts.get("archived_dream", 0)

    last_dream: Optional[Dict[str, Any]] = None
    try:
        redis_client = get_redis()
        raw_metadata = await redis_client.get(f"memory:dream:last:{agent_id}")
        if raw_metadata:
            if isinstance(raw_metadata, bytes):
                raw_metadata = raw_metadata.decode("utf-8")
            last_dream = json.loads(raw_metadata)
    except Exception:
        logger.warning("Could not read last dream metadata for %s", agent_id)

    counts = {
        "active": active_count,
        "quarantined": quarantined_count,
        "archived": archived_count,
        "rejected": type_counts.get("rejected_injection", 0),
    }
    return {
        "agent_id": agent_id,
        "index": index_text,
        "index_lines": len(index_text.splitlines()),
        "index_bytes": len(index_text.encode("utf-8")),
        "counts": counts,
        "active_count": active_count,
        "quarantined_count": quarantined_count,
        "archived_count": archived_count,
        "last_dream": last_dream,
    }


memory_dreaming_worker = MemoryDreamingWorker()
