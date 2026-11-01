import pytest
from app.workflow_engine import workflow_engine


@pytest.mark.asyncio
async def test_falsifier_3_context_overflow_and_state_pruning_breaker():
    """
    Falsifier 3: Context Overflow & State Pruning Breaker
    Hypothesis: "An extended multi-turn agent debate will not exceed maximum model
    context window limits."
    Test: Generate 100 sequential debate entries of 500 tokens each into a single
    workflow blackboard. Trigger the worker hydration step for the next turn.
    Assertion: The context builder executes the pruning/summarization janitor routine,
    reducing the loaded history to under the defined token budget (< 4,000 tokens)
    while preserving the original problem statement and latest critique.
    """
    original_problem_statement = (
        "ORIGINAL_PROBLEM_STATEMENT: Build zero-trust MCP gateway with strict RBAC."
    )
    entries = [
        {
            "id": "entry-0",
            "workflow_id": "code_review_pipeline",
            "task_id": "run-init",
            "agent_id": "operator",
            "entry_type": "spec",
            "content": original_problem_statement,
            "metadata": {"stage": "StartEvent"},
        }
    ]

    # Generate 100 sequential debate entries of 500 tokens (words) each (~50,000 tokens total)
    for idx in range(1, 101):
        entry_type = "critique" if idx % 2 == 0 else "hypothesis"
        filler_500_tokens = " ".join(
            [f"debate_token_{idx}_{w}" for w in range(500)]
        )
        if idx == 100:
            filler_500_tokens = (
                "LATEST_CRITIQUE_MARKER: Verify final lease TTL boundary. "
                + filler_500_tokens
            )
        entries.append(
            {
                "id": f"entry-{idx}",
                "workflow_id": "code_review_pipeline",
                "task_id": f"task-{idx}",
                "agent_id": "sec-auditor" if entry_type == "critique" else "architect",
                "entry_type": entry_type,
                "content": filler_500_tokens,
                "metadata": {"turn": idx},
            }
        )

    raw_token_count = sum(len(e["content"].split()) for e in entries)
    assert raw_token_count >= 50000

    # Execute the janitor pruning & bounded context builder (budget = 4000 tokens)
    bounded_result = workflow_engine.build_bounded_context(
        entries=entries, max_token_budget=4000
    )

    assert bounded_result["pruned"] is True
    assert bounded_result["token_count"] < 4000
    assert original_problem_statement in bounded_result["context_text"]
    assert "LATEST_CRITIQUE_MARKER" in bounded_result["context_text"]
