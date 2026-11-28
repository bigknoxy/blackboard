"""Legacy keyword and regex rules used by the Concierge fallback router.

This module is the compatibility boundary for Stage-1 routing heuristics.  The
semantic :class:`app.decision_engine.DecisionEngine` is preferred whenever it
returns a high-confidence decision; this module only supplies the fallback
classification when semantic routing is disabled or uncertain.

The compatibility path can be disabled without restarting the control plane by
setting ``ENABLE_LEGACY_CONCIERGE_RULES=false`` or the Redis key
``llm:concierge:legacy_rules_enabled`` to ``false``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional

LEGACY_RULES_REDIS_KEY = "llm:concierge:legacy_rules_enabled"
LEGACY_RULES_ENV_VAR = "ENABLE_LEGACY_CONCIERGE_RULES"

_FALSE_VALUES = frozenset({"false", "0", "no", "off"})
_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})

_EXPLICIT_WORKFLOW_RE = re.compile(
    r"\b(?:run|start|trigger|execute)\s+(?:the\s+)?([a-z0-9_-]+_(?:pipeline|workflow))\b"
)


def parse_enabled(value: Any, *, default: bool = True) -> bool:
    """Parse a boolean-ish Redis or environment value."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    normalized = str(value).strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _TRUE_VALUES:
        return True
    return default


def legacy_rules_enabled_from_value(redis_value: Any = None) -> bool:
    """Resolve the single legacy-rules toggle, preferring Redis over the environment."""
    if redis_value is None:
        return parse_enabled(os.getenv(LEGACY_RULES_ENV_VAR), default=True)
    return parse_enabled(redis_value, default=True)


async def load_legacy_rules_enabled(redis_client: Any) -> bool:
    """Read the runtime legacy-rules toggle from Redis with environment fallback."""
    redis_value = await redis_client.get(LEGACY_RULES_REDIS_KEY)
    return legacy_rules_enabled_from_value(redis_value)


def is_conversational_question(message: str) -> bool:
    """Return whether a message reads as a question rather than an operator command."""
    lowered = " ".join((message or "").strip().lower().split())
    if not lowered:
        return False
    if lowered.endswith("?"):
        return True
    return lowered.startswith(
        (
            "what ",
            "why ",
            "how ",
            "when ",
            "where ",
            "who ",
            "which ",
            "is ",
            "are ",
            "do ",
            "does ",
            "did ",
            "can ",
            "could ",
            "should ",
        )
    )


def is_recall_question(message: str) -> bool:
    """Return whether the operator is asking to recall a memory, not store one."""
    lowered = " ".join((message or "").strip().lower().split())
    return any(
        marker in lowered
        for marker in (
            "what do you remember",
            "do you remember",
            "can you remember",
            "tell me what you remember",
            "what did i ask you to remember",
        )
    )


def is_explicit_remember_command(message: str) -> bool:
    """Return whether a message is an explicit deterministic memory-store command."""
    lowered = " ".join((message or "").strip().lower().split())
    if not lowered or is_recall_question(lowered):
        return False
    explicit_phrases = (
        "remember ",
        "remember that ",
        "please remember ",
        "like you to remember ",
        "want you to remember ",
        "save to memory:",
        "save to memory ",
        "store in memory:",
        "store in memory ",
        "keep in mind:",
        "keep in mind ",
        "note that:",
        "note that ",
    )
    return any(phrase in lowered for phrase in explicit_phrases)


def match_explicit_workflow(message: str) -> Optional[str]:
    """Match an explicit workflow/pipeline identifier in an operator command."""
    match = _EXPLICIT_WORKFLOW_RE.search((message or "").strip().lower())
    return match.group(1) if match else None


def match_fallback_workflow(message: str) -> Optional[str]:
    """Match the legacy research-planning fallback without capturing ordinary questions."""
    lowered = " ".join((message or "").strip().lower().split())
    if not lowered or is_conversational_question(lowered):
        return None
    if not any(
        marker in lowered
        for marker in (
            "do planning and research",
            "plan and research",
            "planning and research",
            "research and add",
        )
    ):
        return None
    if any(marker in lowered for marker in ("did you", "have you", "what research", "status")):
        return None
    return "research_plan_verify_pipeline"


@dataclass(frozen=True)
class LegacyRuleMatch:
    """Classification of explicit commands and deletable fallback heuristics."""

    explicit_steering: bool = False
    legacy_steering: bool = False
    explicit_workflow_id: Optional[str] = None
    explicit_workflow_trigger: bool = False
    legacy_workflow_id: Optional[str] = None
    explicit_prune: bool = False
    explicit_workflow_architect: bool = False
    explicit_capture_idea: bool = False
    explicit_list_backlog: bool = False
    explicit_tool_registration: bool = False
    legacy_tool_management: bool = False
    legacy_capabilities: bool = False
    legacy_tool_gap: bool = False
    explicit_dynamic_tool: bool = False
    explicit_approval_policy: bool = False
    legacy_weather: bool = False
    legacy_web_search: bool = False
    explicit_remember: bool = False
    recall_question: bool = False
    legacy_list_memories: bool = False
    explicit_bash: bool = False

    @property
    def has_explicit_operator_command(self) -> bool:
        return any(
            (
                self.explicit_steering,
                self.explicit_workflow_id is not None,
                self.explicit_workflow_trigger,
                self.explicit_prune,
                self.explicit_workflow_architect,
                self.explicit_capture_idea,
                self.explicit_list_backlog,
                self.explicit_tool_registration,
                self.explicit_dynamic_tool,
                self.explicit_approval_policy,
                self.explicit_remember,
                self.explicit_bash,
            )
        )


def classify_legacy_rules(message: str) -> LegacyRuleMatch:
    """Classify an operator message using the isolated legacy compatibility rules."""
    lowered = " ".join((message or "").strip().lower().split())
    explicit_workflow_id = match_explicit_workflow(lowered)
    explicit_workflow_trigger = explicit_workflow_id is not None or any(
        marker in lowered
        for marker in (
            "run workflow",
            "start workflow",
            "trigger pipeline",
            "execute pipeline",
            "execute workflow",
        )
    )

    return LegacyRuleMatch(
        explicit_steering=lowered.startswith("steer:"),
        legacy_steering=any(marker in lowered for marker in ("steer ", "instruct ", "override ")),
        explicit_workflow_id=explicit_workflow_id,
        explicit_workflow_trigger=explicit_workflow_trigger,
        legacy_workflow_id=match_fallback_workflow(lowered),
        explicit_prune=lowered.startswith(("prune", "summarize blackboard", "condense blackboard")),
        explicit_workflow_architect=lowered.startswith(
            (
                "create workflow",
                "new workflow",
                "build workflow",
                "add workflow",
                "create a workflow",
                "architect workflow",
                "design workflow",
                "update workflow",
                "modify workflow",
                "change workflow",
                "add step to workflow",
                "add node to workflow",
                "update the workflow",
                "create agent",
                "new agent",
            )
        ),
        explicit_capture_idea=lowered.startswith(
            (
                "capture this as a future feature",
                "table this for now",
                "capture this",
                "add to backlog",
                "save idea",
                "log idea",
                "capture idea",
                "future feature",
                "table this",
                "add idea",
                "new idea",
            )
        ),
        explicit_list_backlog=lowered.startswith(
            (
                "show backlog",
                "list backlog",
                "what ideas",
                "view ideas",
                "show ideas",
                "backlog items",
                "what is on the backlog",
                "check backlog",
                "check the backlog",
                "did you add",
            )
        ),
        explicit_tool_registration=lowered.startswith(
            ("add tool", "add tools", "create tool", "register tool", "add web_search")
        ),
        legacy_tool_management=any(
            marker in lowered
            for marker in (
                "web search",
                "web_search",
                "need a tool",
                "need tools",
                "suggest tool",
                "suggest tools",
                "what tools do you suggest",
                "what tools should we have",
                "bash tool",
                "web tool",
                "add bash",
                "need a bash",
                "need bash",
                "should we have recognized this",
            )
        ),
        legacy_capabilities=any(
            marker in lowered
            for marker in (
                "what all can you do",
                "what can you do",
                "what are your capabilities",
                "what all do you do",
                "what do you do",
                "how can you help",
                "what are you capable of",
                "list capabilities",
                "show capabilities",
                "help",
            )
        ),
        legacy_tool_gap=(
            any(
                marker in lowered
                for marker in (
                    "tools we are missing",
                    "tools are missing",
                    "missing tools",
                    "what tools are we missing",
                    "tool gap",
                    "analyze tool inventory",
                    "architect agent to figure out what tools",
                    "architect to figure out what tools",
                    "architect figure out what tools",
                    "architect to analyze tools",
                    "architect analyze tools",
                    "what tools would benefit us",
                )
            )
            or (
                "architect" in lowered
                and "tools" in lowered
                and any(
                    marker in lowered
                    for marker in ("missing", "gap", "benefit", "usage")
                )
            )
        ),
        explicit_dynamic_tool=any(
            marker in lowered
            for marker in (
                "create a new tool",
                "create new tool",
                "create tool",
                "add tool",
                "synthesize a new tool",
                "synthesize new tool",
                "synthesize tool",
                "build tool",
                "make a tool",
                "make tool",
                "update tool",
                "modify tool",
                "create skill",
                "add skill",
                "new skill",
                "synthesize skill",
                "build skill",
                "create a skill",
            )
        ),
        explicit_approval_policy=any(
            marker in lowered
            for marker in (
                "permanently approve",
                "permanently trust",
                "always allow",
                "always approve",
                "auto approve",
                "auto-approve",
                "trust tool",
                "trust step",
                "automate step",
                "automate workflow",
                "approval policies",
                "show policies",
                "list policies",
                "revoke policy",
            )
        ),
        legacy_weather=any(
            marker in lowered
            for marker in ("weather", "forecast", "temperature", "how cold", "how hot", "how warm")
        ),
        legacy_web_search=any(
            marker in lowered
            for marker in (
                "search the web",
                "search web",
                "web search",
                "search online",
                "search for",
                "google",
                "lookup online",
                "nfl",
                "football",
                "games today",
                "game today",
                "scores today",
                "weather today",
                "news today",
            )
        ),
        explicit_remember=is_explicit_remember_command(lowered),
        recall_question=is_recall_question(lowered),
        legacy_list_memories=any(
            marker in lowered
            for marker in (
                "what do your other memories include",
                "what memories",
                "list memories",
                "show memories",
                "search memory",
                "what is in memory",
                "what do you remember",
                "other memories",
                "inspect memories",
                "view memories",
                "did you conduct",
                "what research",
                "research results",
                "any research",
                "check memory",
                "check memories",
                "stored memories",
                "what did you find",
                "what did the researcher",
                "retrieve the stored memories",
            )
        ),
        explicit_bash=lowered.startswith(("bash:", "run bash:", "run command:", "exec:")),
    )
