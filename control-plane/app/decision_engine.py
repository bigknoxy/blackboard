"""Shared Decision Engine for semantic routing and system-wide quality gates.

The engine owns runtime configuration, provider failover, cooldown state, and
decision telemetry. Callers provide the decision context; the engine validates
provider output against a closed set of supported choices.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import httpx

from app.database import get_redis

DECISION_CONFIG_KEYS: Tuple[str, ...] = (
    "llm:decision:enabled",
    "llm:decision:provider",
    "llm:decision:gemini_api_key",
    "llm:decision:gemini_model",
    "llm:decision:ollama_model",
)

DECISION_INTENTS = (
    "trigger_workflow",
    "inspect_state",
    "memory_op",
    "tool_exec",
    "chat",
)
GATEWAY_VERDICTS = ("PASS", "REWORK", "ESCALATE_HITL")
COMPLEXITY_CLASSES = ("simple_local", "complex_reasoning")
WORKFLOW_MATCH_STATUSES = ("MATCH", "NO_MATCH", "NEEDS_CLARIFICATION")

DEFAULT_OLLAMA_URL = "http://192.168.8.136:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:0.8b"
LOCAL_COOLDOWN_SECONDS = 60


@dataclass(frozen=True)
class DecisionConfig:
    enabled: bool
    provider: str
    gemini_api_key: str
    gemini_model: str
    ollama_model: str


class DecisionEngine:
    """Central Decision Engine with one shared HTTP connection pool."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._client_class: Optional[type] = None
        self._client_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._client_class = None

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            client_class = httpx.AsyncClient
            if self._client is None or self._client_class is not client_class:
                self._client = client_class(
                    timeout=httpx.Timeout(12.0, connect=1.5),
                    limits=httpx.Limits(
                        max_connections=12,
                        max_keepalive_connections=6,
                    ),
                )
                self._client_class = client_class
            return self._client

    @staticmethod
    async def _resolve_redis(redis_client: Optional[Any] = None) -> Any:
        if redis_client is not None:
            return redis_client
        client = get_redis()
        if inspect.isawaitable(client):
            client = await client
        return client

    async def load_config(
        self, redis_client: Optional[Any] = None
    ) -> DecisionConfig:
        """Load all Decision Model settings with one Redis MGET."""
        redis_client = await self._resolve_redis(redis_client)
        values = await redis_client.mget(*DECISION_CONFIG_KEYS)
        values = list(values or [])
        while len(values) < len(DECISION_CONFIG_KEYS):
            values.append(None)

        def configured(index: int) -> Optional[str]:
            value = values[index]
            if value is None:
                return None
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            text = str(value).strip()
            return text or None

        enabled_value = configured(0)
        provider_value = configured(1)
        api_key_value = configured(2)
        gemini_model_value = configured(3)
        ollama_model_value = configured(4)

        enabled = (
            os.getenv("DECISION_MODEL_ENABLED", "true").lower()
            not in ("false", "0", "no")
            if enabled_value is None
            else enabled_value.lower() not in ("false", "0", "no")
        )
        provider = (
            provider_value
            or os.getenv("DECISION_MODEL_PROVIDER", "local")
        ).strip().lower()
        if provider not in ("local", "gemini"):
            provider = "local"
        return DecisionConfig(
            enabled=enabled,
            provider=provider,
            gemini_api_key=(
                api_key_value or os.getenv("GEMINI_API_KEY", "")
            ).strip(),
            gemini_model=(
                gemini_model_value
                or os.getenv("GEMINI_DECISION_MODEL", "gemini-decision-1")
            ).strip(),
            ollama_model=(
                ollama_model_value
                or os.getenv("OLLAMA_CLASSIFIER_MODEL", DEFAULT_OLLAMA_MODEL)
            ).strip(),
        )

    @staticmethod
    def _cooldown_key(url: str) -> str:
        parsed = urlsplit(url)
        endpoint = f"{parsed.hostname or 'unknown'}_{parsed.port or 'default'}"
        endpoint = re.sub(r"[^a-zA-Z0-9_.-]", "_", endpoint)
        return f"llm:cooldown:decision_{endpoint}"

    @staticmethod
    def _local_llama_endpoints() -> List[Tuple[str, str]]:
        endpoints: List[Tuple[str, str]] = []
        candidates = (
            (
                os.getenv("CLASSIFIER_OPENAI_URL")
                or os.getenv("LAN_CT118_LFM_URL")
                or os.getenv("LAN_LAPTOP_LFM_URL")
                or "http://192.168.8.180:8090/v1",
                os.getenv("CLASSIFIER_MODEL", "lfm3-1.7b"),
            ),
            (
                os.getenv("LAN_LAPTOP_LFM_URL")
                or "http://192.168.8.149:8090/v1",
                os.getenv("CLASSIFIER_MODEL", "lfm3-1.7b"),
            ),
            (
                os.getenv("CONCIERGE_OPENAI_URL")
                or os.getenv("LAN_CT118_QWEN_URL")
                or os.getenv("LAN_LAPTOP_QWEN_URL")
                or "http://192.168.8.180:8081/v1",
                os.getenv("CONCIERGE_MODEL", "qwen3-4b"),
            ),
        )
        seen: set[str] = set()
        for url, model in candidates:
            normalized = url.rstrip("/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                endpoints.append((normalized, model))
        return endpoints

    @staticmethod
    def _workflow_criteria(
        workflows: Sequence[Any],
    ) -> Tuple[List[str], Dict[str, str]]:
        workflow_ids: List[str] = []
        criteria: Dict[str, str] = {}
        for workflow in workflows:
            if isinstance(workflow, str):
                workflow_id = workflow
                description = ""
            elif isinstance(workflow, Mapping):
                workflow_id = str(workflow.get("id") or "").strip()
                description = str(
                    workflow.get("description")
                    or workflow.get("name")
                    or ""
                ).strip()
            else:
                workflow_id = str(getattr(workflow, "id", "") or "").strip()
                description = str(
                    getattr(workflow, "description", "")
                    or getattr(workflow, "name", "")
                    or ""
                ).strip()
            if not workflow_id:
                continue
            workflow_ids.append(workflow_id)
            criteria[workflow_id] = description or workflow_id
        return workflow_ids, criteria

    @staticmethod
    def _contract_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _workflow_match_record(workflow: Any) -> Optional[Dict[str, Any]]:
        if isinstance(workflow, Mapping):
            workflow_id = str(workflow.get("id") or "").strip()
            name = str(workflow.get("name") or "").strip()
            description = str(workflow.get("description") or "").strip()
            state_graph = workflow.get("state_graph") or {}
        else:
            workflow_id = str(getattr(workflow, "id", "") or "").strip()
            name = str(getattr(workflow, "name", "") or "").strip()
            description = str(getattr(workflow, "description", "") or "").strip()
            state_graph = getattr(workflow, "state_graph", {}) or {}
        if hasattr(state_graph, "model_dump"):
            state_graph = state_graph.model_dump()
        if not isinstance(state_graph, Mapping):
            state_graph = {}
        if not workflow_id:
            return None
        contract_source = workflow if isinstance(workflow, Mapping) else state_graph

        def contract_value(field: str) -> Any:
            value = contract_source.get(field)
            if value is None:
                value = state_graph.get(field)
            return value

        def contract_dict(field: str) -> Dict[str, Any]:
            value = contract_value(field)
            return dict(value) if isinstance(value, Mapping) else {}

        return {
            "id": workflow_id,
            "name": name,
            "description": description,
            "capability_tags": DecisionEngine._contract_list(
                contract_value("capability_tags")
            ),
            "required_tools": DecisionEngine._contract_list(
                contract_value("required_tools")
            ),
            "input_contract": contract_dict("input_contract"),
            "output_contract": contract_dict("output_contract"),
        }

    @staticmethod
    def _tool_match_record(tool: Any) -> Optional[Dict[str, Any]]:
        if isinstance(tool, Mapping):
            tool_id = str(tool.get("id") or "").strip()
            consumes_type = tool.get("consumes_type")
            produces_type = tool.get("produces_type")
        else:
            tool_id = str(getattr(tool, "id", "") or "").strip()
            consumes_type = getattr(tool, "consumes_type", None)
            produces_type = getattr(tool, "produces_type", None)
        if not tool_id:
            return None
        return {
            "id": tool_id,
            "consumes_type": consumes_type,
            "produces_type": produces_type,
        }

    @staticmethod
    def _normalized_phrase(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())

    def _deterministic_workflow_match(
        self,
        goal: str,
        workflow_records: List[Dict[str, Any]],
        tool_records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        normalized_goal = self._normalized_phrase(goal)
        goal_tokens = set(normalized_goal.split())
        available_tool_ids = {tool["id"] for tool in tool_records}
        stopwords = {
            "a",
            "an",
            "and",
            "for",
            "me",
            "my",
            "of",
            "or",
            "please",
            "the",
            "to",
            "with",
        }
        content_tokens = goal_tokens - stopwords
        scored: List[Tuple[int, Dict[str, Any], List[str]]] = []
        invalid_matches: List[Tuple[Dict[str, Any], List[str]]] = []

        for workflow in workflow_records:
            missing_tools = sorted(
                set(workflow["required_tools"]) - available_tool_ids
            )
            score = 0
            matched_tags: List[str] = []
            workflow_id_phrase = self._normalized_phrase(workflow["id"])
            workflow_name_phrase = self._normalized_phrase(workflow["name"])
            if workflow_id_phrase and f" {workflow_id_phrase} " in f" {normalized_goal} ":
                score += 10
            if workflow_name_phrase and f" {workflow_name_phrase} " in f" {normalized_goal} ":
                score += 8
            for tag in workflow["capability_tags"]:
                tag_phrase = self._normalized_phrase(tag)
                tag_tokens = set(tag_phrase.split())
                if not tag_phrase:
                    continue
                if f" {tag_phrase} " in f" {normalized_goal} ":
                    score += 5
                    matched_tags.append(tag)
                elif len(tag_tokens) > 1 and tag_tokens.issubset(goal_tokens):
                    score += 3
                    matched_tags.append(tag)
            if score:
                if missing_tools:
                    invalid_matches.append((workflow, missing_tools))
                else:
                    scored.append((score, workflow, matched_tags))

        if invalid_matches and not scored:
            workflow, missing_tools = max(
                invalid_matches,
                key=lambda item: len(item[0]["capability_tags"]),
            )
            return {
                "status": "NO_MATCH",
                "workflow_id": None,
                "confidence": 0.9,
                "reason": "missing_required_tools",
                "missing_tools": missing_tools,
                "matched_capability_tags": workflow["capability_tags"],
            }

        if scored:
            scored.sort(key=lambda item: (-item[0], item[1]["id"]))
            top_score, top_workflow, matched_tags = scored[0]
            if len(scored) == 1 or top_score > scored[1][0]:
                return {
                    "status": "MATCH",
                    "workflow_id": top_workflow["id"],
                    "confidence": 0.95 if top_score >= 8 else 0.9,
                    "reason": "deterministic_contract_match",
                    "missing_tools": [],
                    "matched_capability_tags": matched_tags,
                }
            return {
                "status": "NEEDS_CLARIFICATION",
                "workflow_id": None,
                "confidence": 0.4,
                "reason": "multiple_contract_matches",
                "missing_tools": [],
                "matched_capability_tags": [],
            }

        if not workflow_records:
            return {
                "status": "NO_MATCH",
                "workflow_id": None,
                "confidence": 1.0,
                "reason": "no_registered_workflows",
                "missing_tools": [],
                "matched_capability_tags": [],
            }
        if len(content_tokens) >= 3 and not goal.strip().endswith("?"):
            return {
                "status": "NO_MATCH",
                "workflow_id": None,
                "confidence": 0.8,
                "reason": "no_contract_match",
                "missing_tools": [],
                "matched_capability_tags": [],
            }
        return {
            "status": "NEEDS_CLARIFICATION",
            "workflow_id": None,
            "confidence": 0.3,
            "reason": "insufficient_goal_detail",
            "missing_tools": [],
            "matched_capability_tags": [],
        }

    async def match_workflow_or_gap(
        self,
        goal: str,
        workflows: Sequence[Any],
        tools: Sequence[Any],
        redis_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Match an operator goal to a registered workflow or a closed-set gap."""
        redis_client = await self._resolve_redis(redis_client)
        workflow_records = [
            record
            for workflow in workflows
            if (record := self._workflow_match_record(workflow)) is not None
        ]
        tool_records = [
            record
            for tool in tools
            if (record := self._tool_match_record(tool)) is not None
        ]
        deterministic = self._deterministic_workflow_match(
            goal,
            workflow_records,
            tool_records,
        )
        try:
            config = await self.load_config(redis_client)
        except Exception:
            deterministic["provider"] = "fallback"
            deterministic["reason"] = "deterministic_fallback"
            return deterministic
        if not config.enabled:
            deterministic["provider"] = "disabled"
            try:
                await self._record_telemetry(
                    provider="disabled",
                    latency_ms=0,
                    gate_name="workflow_match",
                    confidence=float(deterministic["confidence"]),
                    failovers=0,
                    redis_client=redis_client,
                )
            except Exception:
                pass
            return deterministic

        choices = {
            f"MATCH:{workflow['id']}": (
                f"{workflow['name']}. {workflow['description']} "
                f"Capabilities: "
                f"{', '.join(workflow['capability_tags']) or 'unspecified'}. "
                f"Required tools: {', '.join(workflow['required_tools']) or 'none'}."
            )
            for workflow in workflow_records
        }
        choices["NO_MATCH"] = (
            "No registered workflow can satisfy the goal or its required tools are unavailable."
        )
        choices["NEEDS_CLARIFICATION"] = (
            "The goal is ambiguous or lacks enough detail to choose one workflow safely."
        )
        try:
            result = await self._classify_choice(
                prompt=(
                    "Choose whether the operator goal matches exactly one registered workflow. "
                    "Use MATCH only when the workflow's declared capabilities and tools satisfy "
                    "the goal. Use NO_MATCH when no workflow fits. Use NEEDS_CLARIFICATION when "
                    "the goal is ambiguous.\n\n"
                    f"Goal: {goal[:1600]}\n"
                    f"Available tools: {json.dumps(tool_records, sort_keys=True)}"
                ),
                choices=tuple(choices),
                criteria=choices,
                fallback="NO_MATCH",
                gate_name="workflow_match",
                redis_client=redis_client,
            )
        except Exception:
            deterministic["provider"] = "fallback"
            deterministic["reason"] = "deterministic_fallback"
            return deterministic
        if result.get("fallback_used"):
            deterministic["provider"] = str(result.get("provider") or "fallback")
            deterministic["reason"] = "deterministic_fallback"
            return deterministic

        choice = str(result.get("choice") or "")
        if choice.startswith("MATCH:"):
            workflow_id = choice.split(":", 1)[1]
            workflow = next(
                (item for item in workflow_records if item["id"] == workflow_id),
                None,
            )
            required_tool_ids = set(workflow["required_tools"]) if workflow else set()
            missing_tools = sorted(
                required_tool_ids - {tool["id"] for tool in tool_records}
            )
            if workflow is None or missing_tools:
                deterministic["provider"] = str(result.get("provider") or "fallback")
                deterministic["reason"] = "invalid_model_match"
                return deterministic
            return {
                "status": "MATCH",
                "workflow_id": workflow_id,
                "confidence": float(result.get("confidence") or 0.0),
                "provider": str(result.get("provider") or "fallback"),
                "reason": "model_match",
                "missing_tools": [],
                "matched_capability_tags": workflow["capability_tags"],
            }
        if choice in WORKFLOW_MATCH_STATUSES:
            return {
                "status": choice,
                "workflow_id": None,
                "confidence": float(result.get("confidence") or 0.0),
                "provider": str(result.get("provider") or "fallback"),
                "reason": "model_decision",
                "missing_tools": [],
                "matched_capability_tags": [],
            }
        deterministic["provider"] = str(result.get("provider") or "fallback")
        deterministic["reason"] = "deterministic_fallback"
        return deterministic

    @staticmethod
    def _parse_choice_payload(data: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
        items = (
            data.get("decisions")
            or data.get("answers")
            or data.get("results")
            or data.get("choices")
        )

        def extract(entry: Any) -> Tuple[Optional[str], Optional[float]]:
            if isinstance(entry, str):
                return entry, None
            if not isinstance(entry, Mapping):
                return None, None
            choice = (
                entry.get("choice")
                or entry.get("choice_id")
                or entry.get("choiceId")
                or entry.get("selected_choice_id")
                or entry.get("answer")
                or entry.get("value")
            )
            if isinstance(choice, Mapping):
                choice = choice.get("id") or choice.get("choice") or choice.get("choice_id")
            confidence = entry.get("confidence")
            if confidence is None:
                confidence = entry.get("score")
            try:
                confidence_value = float(confidence) if confidence is not None else None
            except (TypeError, ValueError):
                confidence_value = None
            return (str(choice).strip() if choice is not None else None), confidence_value

        if isinstance(items, Mapping):
            return extract(items)
        if isinstance(items, list):
            for entry in items:
                if not isinstance(entry, Mapping):
                    continue
                choice, confidence = extract(entry)
                if choice is not None:
                    return choice, confidence
        return None, None

    async def _record_telemetry(
        self,
        *,
        provider: str,
        latency_ms: int,
        gate_name: str,
        confidence: float,
        failovers: int,
        redis_client: Optional[Any] = None,
    ) -> None:
        redis_client = await self._resolve_redis(redis_client)
        telemetry_keys = (
            "llm:telemetry:decision:calls",
            "llm:telemetry:decision:provider",
            "llm:telemetry:decision:latency_ms",
            "llm:telemetry:decision:gate_name",
            "llm:telemetry:decision:confidence",
            "llm:telemetry:decision:failovers",
        )
        values = await redis_client.mget(*telemetry_keys)
        values = list(values or [])
        while len(values) < len(telemetry_keys):
            values.append(None)

        def as_text(value: Any) -> Optional[str]:
            if value is None:
                return None
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value)

        def as_json(value: Any, default: Any) -> Any:
            text = as_text(value)
            if not text:
                return default
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                return default

        calls = int(as_text(values[0]) or 0) + 1
        providers = as_json(values[1], {})
        latency = as_json(values[2], {"count": 0, "total_ms": 0, "average_ms": 0.0, "last_ms": 0})
        gates = as_json(values[3], {})
        confidence_summary = as_json(values[4], {"count": 0, "total": 0.0, "average": 0.0, "last": 0.0})
        failovers_total = int(as_text(values[5]) or 0) + int(failovers)

        providers[provider] = int(providers.get(provider, 0)) + 1
        latency_count = int(latency.get("count", 0)) + 1
        latency_total = int(latency.get("total_ms", 0)) + latency_ms
        latency = {
            "count": latency_count,
            "total_ms": latency_total,
            "average_ms": round(latency_total / latency_count, 2),
            "last_ms": latency_ms,
        }
        gates[gate_name] = int(gates.get(gate_name, 0)) + 1
        confidence_count = int(confidence_summary.get("count", 0)) + 1
        confidence_total = float(confidence_summary.get("total", 0.0)) + confidence
        confidence_summary = {
            "count": confidence_count,
            "total": round(confidence_total, 4),
            "average": round(confidence_total / confidence_count, 4),
            "last": confidence,
        }

        await redis_client.set(telemetry_keys[0], calls)
        await redis_client.set(telemetry_keys[1], json.dumps(providers, sort_keys=True))
        await redis_client.set(telemetry_keys[2], json.dumps(latency, sort_keys=True))
        await redis_client.set(telemetry_keys[3], json.dumps(gates, sort_keys=True))
        await redis_client.set(telemetry_keys[4], json.dumps(confidence_summary, sort_keys=True))
        await redis_client.set(telemetry_keys[5], failovers_total)

        event_key = (
            f"llm:telemetry:decision:event:{int(time.time() * 1000)}:{uuid.uuid4().hex[:8]}"
        )
        await redis_client.setex(
            event_key,
            86400,
            json.dumps(
                {
                    "provider": provider,
                    "latency_ms": latency_ms,
                    "gate_name": gate_name,
                    "confidence": confidence,
                    "failovers": failovers,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                sort_keys=True,
            ),
        )

    async def get_telemetry(
        self, redis_client: Optional[Any] = None
    ) -> Dict[str, Any]:
        redis_client = await self._resolve_redis(redis_client)
        keys = (
            "llm:telemetry:decision:calls",
            "llm:telemetry:decision:provider",
            "llm:telemetry:decision:latency_ms",
            "llm:telemetry:decision:gate_name",
            "llm:telemetry:decision:confidence",
            "llm:telemetry:decision:failovers",
        )
        values = list(await redis_client.mget(*keys) or [])

        def text(index: int) -> Optional[str]:
            value = values[index] if index < len(values) else None
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return str(value) if value is not None else None

        def parsed(index: int, default: Any) -> Any:
            raw = text(index)
            if not raw:
                return default
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                return default

        return {
            "calls": int(text(0) or 0),
            "provider": parsed(1, {}),
            "latency_ms": parsed(2, {"count": 0, "total_ms": 0, "average_ms": 0.0, "last_ms": 0}),
            "gate_name": parsed(3, {}),
            "confidence": parsed(4, {"count": 0, "total": 0.0, "average": 0.0, "last": 0.0}),
            "failovers": int(text(5) or 0),
        }

    async def _gemini_intent(
        self,
        *,
        user_msg: str,
        workflow_ids: List[str],
        workflow_criteria: Dict[str, str],
        config: DecisionConfig,
        redis_client: Any,
    ) -> Optional[Dict[str, Any]]:
        cooldown_key = "llm:cooldown:gemini_decision"
        if await redis_client.get(cooldown_key):
            return None
        criteria = {
            "trigger_workflow": "Run or start a multi-agent workflow pipeline.",
            "inspect_state": "Inspect platform status, blackboard, backlog, agents, or pending approvals.",
            "memory_op": "Remember, recall, or search durable stored memories.",
            "tool_exec": "Execute a supported shell command, web search, or weather lookup.",
            "chat": "General chat, greeting, or explanation.",
        }
        workflow_choices = {
            workflow_id: (
                f"{workflow_criteria.get(workflow_id, workflow_id)}. "
                f"Select `{workflow_id}` only when this workflow matches the request."
            )
            for workflow_id in workflow_ids
        }
        workflow_choices["none"] = "No workflow pipeline is needed."
        payload = {
            "model": (
                config.gemini_model
                if config.gemini_model.startswith("models/")
                else f"models/{config.gemini_model}"
            ),
            "input": user_msg[:800],
            "questions": {
                "intent": {
                    "type": "choice",
                    "instructions": "Classify the operator request intent.",
                    "criteria": criteria,
                },
                "workflow_id": {
                    "type": "choice",
                    "instructions": "Select the matching workflow, including its description, or none.",
                    "criteria": workflow_choices,
                },
            },
        }
        client = await self._get_client()
        try:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/decisions?key={config.gemini_api_key}",
                json=payload,
            )
        except Exception:
            await redis_client.setex(cooldown_key, LOCAL_COOLDOWN_SECONDS, "network_error")
            return None
        if response.status_code == 429:
            await redis_client.setex(cooldown_key, LOCAL_COOLDOWN_SECONDS, "429")
            return None
        if response.status_code != 200:
            return None
        data = response.json() or {}
        items = (
            data.get("decisions")
            or data.get("answers")
            or data.get("results")
            or data.get("choices")
        )
        intent_choice: Optional[str] = None
        workflow_choice: Optional[str] = None
        intent_confidence: Optional[float] = None

        def extract(entry: Any) -> Tuple[Optional[str], Optional[float]]:
            if isinstance(entry, str):
                return entry, None
            if not isinstance(entry, Mapping):
                return None, None
            choice = (
                entry.get("choice")
                or entry.get("choice_id")
                or entry.get("choiceId")
                or entry.get("selected_choice_id")
                or entry.get("answer")
                or entry.get("value")
            )
            if isinstance(choice, Mapping):
                choice = choice.get("id") or choice.get("choice") or choice.get("choice_id")
            raw_confidence = entry.get("confidence")
            if raw_confidence is None:
                raw_confidence = entry.get("score")
            try:
                confidence = float(raw_confidence) if raw_confidence is not None else None
            except (TypeError, ValueError):
                confidence = None
            return (str(choice).strip() if choice is not None else None), confidence

        if isinstance(items, Mapping):
            intent_choice, intent_confidence = extract(items.get("intent"))
            workflow_choice, _ = extract(items.get("workflow_id"))
        elif isinstance(items, list):
            for index, entry in enumerate(items):
                if not isinstance(entry, Mapping):
                    continue
                question_id = str(
                    entry.get("question_id")
                    or entry.get("questionId")
                    or entry.get("id")
                    or ""
                ).strip()
                choice, confidence = extract(entry)
                if question_id == "intent" or (not question_id and index == 0):
                    intent_choice, intent_confidence = choice, confidence
                elif question_id == "workflow_id" or (not question_id and index == 1):
                    workflow_choice = choice
        if intent_choice not in DECISION_INTENTS:
            return None
        selected_workflow = (
            workflow_choice if workflow_choice in workflow_ids else None
        )
        return {
            "intent": intent_choice,
            "workflow_id": selected_workflow,
            "confidence": max(
                0.0,
                min(1.0, intent_confidence if intent_confidence is not None else 0.85),
            ),
            "provider": "gemini",
        }

    async def route_intent(
        self,
        user_msg: str,
        workflows: Sequence[Any],
        *,
        redis_client: Optional[Any] = None,
        gate_name: str = "concierge_router",
    ) -> Dict[str, Any]:
        """Classify an operator message into one of the five supported intents."""
        redis_client = await self._resolve_redis(redis_client)
        config = await self.load_config(redis_client)
        started = time.monotonic()
        if not config.enabled:
            return {
                "intent": "chat",
                "workflow_id": None,
                "confidence": 0.0,
                "provider": "disabled",
                "reason": "disabled",
            }

        workflow_ids, workflow_criteria = self._workflow_criteria(workflows)
        failovers = 0
        result: Optional[Dict[str, Any]] = None
        if config.provider == "gemini" and config.gemini_api_key:
            result = await self._gemini_intent(
                user_msg=user_msg,
                workflow_ids=workflow_ids,
                workflow_criteria=workflow_criteria,
                config=config,
                redis_client=redis_client,
            )
            if result is None:
                failovers += 1

        if result is None:
            client = await self._get_client()
            for base_url, model_name in self._local_llama_endpoints():
                cooldown_key = self._cooldown_key(base_url)
                if await redis_client.get(cooldown_key):
                    failovers += 1
                    continue
                system_prompt = (
                    "Classify the operator request. Reply with only JSON containing "
                    "intent, workflow_id, and confidence.\n"
                    f"Valid intents: {', '.join(DECISION_INTENTS)}\n"
                    f"Workflow criteria: {json.dumps(workflow_criteria, sort_keys=True)}\n"
                    "Use workflow_id only for trigger_workflow and only when its description "
                    "matches the request."
                )
                try:
                    response = await client.post(
                        f"{base_url}/chat/completions",
                        json={
                            "model": model_name,
                            "temperature": 0.0,
                            "max_tokens": 160,
                            "chat_template_kwargs": {"enable_thinking": False},
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_msg[:800]},
                            ],
                        },
                    )
                    if response.status_code != 200:
                        await redis_client.setex(
                            cooldown_key, LOCAL_COOLDOWN_SECONDS, f"http_{response.status_code}"
                        )
                        failovers += 1
                        continue
                    choices = (response.json() or {}).get("choices") or []
                    raw = ((choices[0].get("message") or {}).get("content") or "").strip()
                    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                    parsed = json.loads(cleaned)
                    intent = str(parsed.get("intent") or "chat").strip()
                    if intent not in DECISION_INTENTS:
                        intent = "chat"
                    workflow_id = parsed.get("workflow_id")
                    workflow_id = (
                        str(workflow_id).strip()
                        if workflow_id and str(workflow_id).strip() in workflow_ids
                        else None
                    )
                    result = {
                        "intent": intent,
                        "workflow_id": workflow_id,
                        "confidence": max(
                            0.0,
                            min(1.0, float(parsed.get("confidence") or 0.0)),
                        ),
                        "provider": f"local:{base_url}",
                    }
                    break
                except Exception:
                    await redis_client.setex(
                        cooldown_key, LOCAL_COOLDOWN_SECONDS, "network_error"
                    )
                    failovers += 1

        if result is None:
            ollama_url = (
                os.getenv("LOCAL_OLLAMA_URL") or DEFAULT_OLLAMA_URL
            ).rstrip("/")
            cooldown_key = self._cooldown_key(ollama_url)
            if not await redis_client.get(cooldown_key):
                option_map: Dict[str, Tuple[str, Optional[str]]] = {}
                option_lines: List[str] = []
                index = 1
                for workflow_id in workflow_ids:
                    option_map[str(index)] = ("trigger_workflow", workflow_id)
                    option_lines.append(
                        f"{index} = trigger_workflow:{workflow_id} ({workflow_criteria.get(workflow_id, workflow_id)})"
                    )
                    index += 1
                for intent_name, description in (
                    ("inspect_state", "inspect platform or workflow state"),
                    ("memory_op", "remember, recall, or search memories"),
                    ("tool_exec", "run a shell command, web search, or weather lookup"),
                    ("chat", "general conversation"),
                ):
                    option_map[str(index)] = (intent_name, None)
                    option_lines.append(f"{index} = {intent_name} ({description})")
                    index += 1
                compact_prompt = (
                    "Classify the operator request. Reply with only one option number:\n"
                    + "\n".join(option_lines)
                )
                try:
                    response = await client.post(
                        f"{ollama_url}/api/chat",
                        json={
                            "model": config.ollama_model,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.0, "num_predict": 1},
                            "messages": [
                                {"role": "system", "content": compact_prompt},
                                {"role": "user", "content": user_msg[:500]},
                            ],
                        },
                    )
                    if response.status_code == 200:
                        raw = (
                            ((response.json() or {}).get("message") or {}).get("content")
                            or ""
                        ).strip()
                        digit = re.search(r"\d+", raw)
                        if digit and digit.group(0) in option_map:
                            intent, workflow_id = option_map[digit.group(0)]
                            result = {
                                "intent": intent,
                                "workflow_id": workflow_id,
                                "confidence": 0.8,
                                "provider": f"ollama:{config.ollama_model}",
                            }
                    else:
                        await redis_client.setex(
                            cooldown_key,
                            LOCAL_COOLDOWN_SECONDS,
                            f"http_{response.status_code}",
                        )
                        failovers += 1
                except Exception:
                    await redis_client.setex(
                        cooldown_key, LOCAL_COOLDOWN_SECONDS, "network_error"
                    )
                    failovers += 1

        if result is None:
            result = {
                "intent": "chat",
                "workflow_id": None,
                "confidence": 0.0,
                "provider": "fallback",
                "reason": "offline",
            }
        await self._record_telemetry(
            provider=str(result.get("provider") or "fallback"),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            gate_name=gate_name,
            confidence=float(result.get("confidence") or 0.0),
            failovers=failovers,
            redis_client=redis_client,
        )
        return result

    async def _classify_choice(
        self,
        *,
        prompt: str,
        choices: Sequence[str],
        criteria: Mapping[str, str],
        fallback: str,
        gate_name: str,
        redis_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        redis_client = await self._resolve_redis(redis_client)
        config = await self.load_config(redis_client)
        started = time.monotonic()
        if not config.enabled:
            return {
                "choice": fallback,
                "confidence": 0.0,
                "provider": "disabled",
                "reason": "disabled",
                "fallback_used": True,
            }
        failovers = 0
        result: Optional[Dict[str, Any]] = None
        if config.provider == "gemini" and config.gemini_api_key:
            cooldown_key = "llm:cooldown:gemini_decision"
            if not await redis_client.get(cooldown_key):
                try:
                    client = await self._get_client()
                    response = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/decisions?key={config.gemini_api_key}",
                        json={
                            "model": (
                                config.gemini_model
                                if config.gemini_model.startswith("models/")
                                else f"models/{config.gemini_model}"
                            ),
                            "input": prompt[:1600],
                            "questions": {
                                "choice": {
                                    "type": "choice",
                                    "instructions": "Choose exactly one supported option.",
                                    "criteria": dict(criteria),
                                }
                            },
                        },
                    )
                    if response.status_code == 429:
                        await redis_client.setex(
                            cooldown_key, LOCAL_COOLDOWN_SECONDS, "429"
                        )
                        failovers += 1
                    elif response.status_code == 200:
                        choice, confidence = self._parse_choice_payload(response.json() or {})
                        if choice in choices:
                            result = {
                                "choice": choice,
                                "confidence": max(
                                    0.0,
                                    min(1.0, confidence if confidence is not None else 0.85),
                                ),
                                "provider": "gemini",
                                "fallback_used": False,
                            }
                except Exception:
                    await redis_client.setex(
                        cooldown_key, LOCAL_COOLDOWN_SECONDS, "network_error"
                    )
                    failovers += 1
            else:
                failovers += 1

        if result is None:
            client = await self._get_client()
            for base_url, model_name in self._local_llama_endpoints():
                cooldown_key = self._cooldown_key(base_url)
                if await redis_client.get(cooldown_key):
                    failovers += 1
                    continue
                try:
                    response = await client.post(
                        f"{base_url}/chat/completions",
                        json={
                            "model": model_name,
                            "temperature": 0.0,
                            "max_tokens": 80,
                            "chat_template_kwargs": {"enable_thinking": False},
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "Choose exactly one option. Reply with only JSON "
                                        '{"choice":"<option>","confidence":0.0}. '
                                        f"Options: {json.dumps(dict(criteria), sort_keys=True)}"
                                    ),
                                },
                                {"role": "user", "content": prompt[:1400]},
                            ],
                        },
                    )
                    if response.status_code != 200:
                        await redis_client.setex(
                            cooldown_key,
                            LOCAL_COOLDOWN_SECONDS,
                            f"http_{response.status_code}",
                        )
                        failovers += 1
                        continue
                    choices_payload = (response.json() or {}).get("choices") or []
                    raw = (
                        ((choices_payload[0].get("message") or {}).get("content") or "")
                        .strip()
                    )
                    cleaned = re.sub(
                        r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE
                    ).strip()
                    parsed = json.loads(cleaned)
                    choice = str(parsed.get("choice") or "").strip()
                    fallback_used = choice not in choices
                    if fallback_used:
                        choice = fallback
                    result = {
                        "choice": choice,
                        "confidence": max(
                            0.0, min(1.0, float(parsed.get("confidence") or 0.0))
                        ),
                        "provider": f"local:{base_url}",
                        "fallback_used": fallback_used,
                    }
                    break
                except Exception:
                    await redis_client.setex(
                        cooldown_key, LOCAL_COOLDOWN_SECONDS, "network_error"
                    )
                    failovers += 1

        if result is None:
            ollama_url = (
                os.getenv("LOCAL_OLLAMA_URL") or DEFAULT_OLLAMA_URL
            ).rstrip("/")
            cooldown_key = self._cooldown_key(ollama_url)
            if not await redis_client.get(cooldown_key):
                option_map = {str(index): choice for index, choice in enumerate(choices, 1)}
                option_lines = [
                    f"{index} = {choice}: {criteria.get(choice, choice)}"
                    for choice, index in zip(choices, range(1, len(choices) + 1))
                ]
                try:
                    response = await client.post(
                        f"{ollama_url}/api/chat",
                        json={
                            "model": config.ollama_model,
                            "stream": False,
                            "think": False,
                            "options": {"temperature": 0.0, "num_predict": 1},
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "Choose one option. Reply with only its number:\n"
                                        + "\n".join(option_lines)
                                    ),
                                },
                                {"role": "user", "content": prompt[:1000]},
                            ],
                        },
                    )
                    if response.status_code == 200:
                        raw = (
                            ((response.json() or {}).get("message") or {}).get("content")
                            or ""
                        ).strip()
                        digit = re.search(r"\d+", raw)
                        if digit and digit.group(0) in option_map:
                            result = {
                                "choice": option_map[digit.group(0)],
                                "confidence": 0.8,
                                "provider": f"ollama:{config.ollama_model}",
                                "fallback_used": False,
                            }
                    else:
                        await redis_client.setex(
                            cooldown_key,
                            LOCAL_COOLDOWN_SECONDS,
                            f"http_{response.status_code}",
                        )
                        failovers += 1
                except Exception:
                    await redis_client.setex(
                        cooldown_key, LOCAL_COOLDOWN_SECONDS, "network_error"
                    )
                    failovers += 1

        if result is None:
            result = {
                "choice": fallback,
                "confidence": 0.0,
                "provider": "fallback",
                "reason": "offline",
                "fallback_used": True,
            }
        await self._record_telemetry(
            provider=str(result.get("provider") or "fallback"),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            gate_name=gate_name,
            confidence=float(result.get("confidence") or 0.0),
            failovers=failovers,
            redis_client=redis_client,
        )
        return result

    async def evaluate_gateway_verdict(
        self,
        *,
        criteria: str,
        artifact: str,
        fallback_verdict: str,
        redis_client: Optional[Any] = None,
        gate_name: str = "workflow_gateway",
    ) -> Dict[str, Any]:
        normalized_fallback = fallback_verdict.upper()
        if normalized_fallback == "FAIL":
            normalized_fallback = "REWORK"
        if normalized_fallback not in GATEWAY_VERDICTS:
            normalized_fallback = "PASS"
        result = await self._classify_choice(
            prompt=(
                f"Quality gate criteria: {criteria}\n\n"
                f"Artifact to grade:\n{artifact}\n\n"
                "Grade the artifact against the criteria."
            ),
            choices=GATEWAY_VERDICTS,
            criteria={
                "PASS": "The artifact satisfies the quality criteria.",
                "REWORK": "The artifact is incomplete but can be revised automatically.",
                "ESCALATE_HITL": "The artifact requires a human decision before proceeding.",
            },
            fallback=normalized_fallback,
            gate_name=gate_name,
            redis_client=redis_client,
        )
        return {
            "verdict": result["choice"],
            "confidence": result["confidence"],
            "provider": result["provider"],
            "reason": result.get("reason"),
        }

    async def classify_complexity_details(
        self,
        request_text: str,
        *,
        redis_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        return await self._classify_choice(
            prompt=(
                "Classify the computational complexity of this operator request. "
                "Use simple_local for greetings, lookups, status checks, and routine "
                "operations. Use complex_reasoning for architecture, multi-step analysis, "
                "design, or debugging.\n\n"
                f"Request: {request_text}"
            ),
            choices=COMPLEXITY_CLASSES,
            criteria={
                "simple_local": "A small local model can answer reliably and quickly.",
                "complex_reasoning": "The request needs stronger reasoning and may warrant NIM escalation.",
            },
            fallback="simple_local",
            gate_name="complexity_router",
            redis_client=redis_client,
        )

    async def classify_complexity(
        self,
        request_text: str,
        *,
        redis_client: Optional[Any] = None,
    ) -> str:
        details = await self.classify_complexity_details(
            request_text, redis_client=redis_client
        )
        return str(details["choice"])

    async def assess_command_risk(
        self,
        command: str,
        *,
        tool_id: str = "bash_executor",
        redis_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Classify a shell command into read_only_safe, mutating_require_hitl, or destructive_block."""
        cmd = (command or "").strip()
        lowered = cmd.lower()

        # Deterministic safety guardrail: always block catastrophic destructive commands immediately
        destructive_patterns = (
            r"\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+/(?:\s|$|\*)",
            r"\brm\s+-[a-z]*f[a-z]*r[a-z]*\s+/(?:\s|$|\*)",
            r"\bmkfs(?:\.[a-z0-9]+)?\b",
            r"\bdd\s+if=.*\bof=/dev/(?:sd|nvme|vd|hd)",
            r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
            r"\b(?:shutdown|reboot|poweroff|halt)\b",
            r">\s*/dev/(?:sd|nvme|vd|hd)",
            r"\bdrop\s+database\b",
        )
        if any(re.search(pat, lowered) for pat in destructive_patterns):
            return {
                "risk_level": "destructive_block",
                "confidence": 1.0,
                "provider": "deterministic_guard",
                "reason": "matched_destructive_pattern",
            }

        read_only_prefixes = (
            "ls", "pwd", "whoami", "uname", "date", "df", "free", "uptime",
            "cat ", "head ", "tail ", "wc ", "git status", "git log", "git diff",
            "git branch", "ps ", "echo ", "env", "printenv", "which ",
        )
        fallback_risk = (
            "read_only_safe"
            if any(lowered == p.strip() or lowered.startswith(p) for p in read_only_prefixes)
            and not any(op in lowered for op in (">", "| sh", "| bash", "&& rm", "; rm"))
            else "mutating_require_hitl"
        )

        result = await self._classify_choice(
            prompt=(
                f"Assess the operational safety risk of running this command via `{tool_id}`:\n"
                f"Command: {cmd[:600]}"
            ),
            choices=("read_only_safe", "mutating_require_hitl", "destructive_block"),
            criteria={
                "read_only_safe": "Strictly read-only inspection (ls, git status, df, uname, cat) with no system/data mutation.",
                "mutating_require_hitl": "Modifies files, packages, git state, services, or processes; requires human review.",
                "destructive_block": "Irreversible destructive or dangerous operation (wiping root/disks, fork bombs, dropping DBs, exfiltrating secrets).",
            },
            fallback=fallback_risk,
            gate_name="mcp_risk_triage",
            redis_client=redis_client,
        )
        return {
            "risk_level": result["choice"],
            "confidence": result["confidence"],
            "provider": result["provider"],
            "reason": result.get("reason"),
        }

    async def evaluate_memory_worthiness(
        self,
        text: str,
        *,
        explicit_request: bool = False,
        redis_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Decide whether text is a durable operator fact/preference worth storing in agent_memories."""
        normalized = " ".join((text or "").strip().split())
        if len(normalized) < 4:
            return {"should_store": False, "confidence": 1.0, "provider": "deterministic_guard", "reason": "too_short"}
        if explicit_request:
            return {"should_store": True, "confidence": 1.0, "provider": "explicit_operator", "reason": "explicit_remember"}

        lowered = normalized.lower()
        if lowered.endswith("?") or lowered in ("hi", "hello", "hey", "thanks", "thank you", "ok", "okay"):
            return {"should_store": False, "confidence": 1.0, "provider": "deterministic_guard", "reason": "ephemeral_chatter"}

        result = await self._classify_choice(
            prompt=f"Should this text be persisted as a long-term memory for future sessions?\nText: {normalized[:600]}",
            choices=("store", "skip"),
            criteria={
                "store": "Contains durable user preferences, environment facts, architecture decisions, or reusable knowledge.",
                "skip": "Ephemeral chatter, greetings, one-off questions, or transient status text.",
            },
            fallback="store" if len(normalized) >= 16 else "skip",
            gate_name="memory_worthiness",
            redis_client=redis_client,
        )
        return {
            "should_store": result["choice"] == "store",
            "confidence": result["confidence"],
            "provider": result["provider"],
            "reason": result.get("reason"),
        }


decision_engine = DecisionEngine()
