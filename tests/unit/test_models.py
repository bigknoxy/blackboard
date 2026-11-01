import uuid
import pytest
from pydantic import ValidationError

from app.schemas import (
    AgentCreate,
    BlackboardEntryCreate,
    InferenceConfig,
    MCPToolCreate,
    MemoryCreate,
    WorkflowCreate,
)


def test_agent_create_schema_valid():
    agent = AgentCreate(
        id="sec-auditor",
        name="Security Auditor",
        role="OWASP & CWE Compliance Reviewer",
        system_prompt="Review all specs and patches for security flaws.",
        inference_config=InferenceConfig(
            provider="ollama",
            model_name="qwen2.5-coder:14b",
            base_url="http://192.168.1.100:11434",
            temperature=0.1,
        ),
        allowed_tools=["static_security_scanner"],
        subscribed_events=["code.published"],
    )
    assert agent.id == "sec-auditor"
    assert agent.inference_config.provider == "ollama"
    assert "static_security_scanner" in agent.allowed_tools


def test_inference_config_rejects_invalid_provider():
    with pytest.raises(ValidationError):
        InferenceConfig(provider="invalid_vendor", model_name="foo")


def test_blackboard_entry_types_enforced():
    for valid_type in (
        "hypothesis",
        "critique",
        "spec",
        "code",
        "test_result",
        "steering",
    ):
        entry = BlackboardEntryCreate(
            workflow_id="code_review_pipeline",
            task_id="task-1",
            agent_id="coder",
            entry_type=valid_type,
            content=f"Sample {valid_type}",
        )
        assert entry.entry_type == valid_type

    with pytest.raises(ValidationError):
        BlackboardEntryCreate(
            workflow_id="code_review_pipeline",
            task_id="task-1",
            agent_id="coder",
            entry_type="invalid_entry_type",
            content="Invalid",
        )


def test_memory_create_schema_types():
    mem = MemoryCreate(
        memory_type="reflection",
        content="Learned to enforce Redis SET NX EX 60 lease TTLs.",
        embedding=[0.01] * 768,
    )
    assert mem.memory_type == "reflection"
    assert len(mem.embedding) == 768
