import pytest
from app.memory import InMemoryScopedMemoryStore, retrieve_top_k_memories


@pytest.mark.asyncio
async def test_falsifier_2_memory_bleed_between_agents_prevented():
    """
    Falsifier 2: Memory Bleed Between Agents (Isolation Breach)
    Hypothesis: "An agent's private episodic memory can never be retrieved by
    or leak into another agent's context window."
    Test: Insert a unique, distinct secret token into `agent_memories` strictly
    assigned to `agent_id: "sec-auditor"`. Run a semantic search / hydration query
    using `agent_id: "coder"` using an identical prompt embedding.
    Assertion: Top-k retrieval for `coder` returns zero matches for the secret token.
    Verify database query strictly binds `WHERE agent_memories.agent_id = :agent_id`.
    """
    secret_token = "SECRET_CANARY_TOKEN_98412_XRAY_VULN"
    prompt_text = f"Audit security vulnerability token {secret_token}"

    store = InMemoryScopedMemoryStore()
    await store.insert_memory(
        agent_id="sec-auditor",
        memory_type="episodic",
        content=f"Classified security finding: {secret_token}",
    )
    await store.insert_memory(
        agent_id="coder",
        memory_type="episodic",
        content="Standard Python FastAPI coding guideline for async endpoints.",
    )

    # Query as `coder` with the exact prompt containing `sec-auditor`'s secret token
    coder_memories, compiled_sql, bound_params = (
        await retrieve_top_k_memories(
            agent_id="coder",
            task_description=prompt_text,
            top_k=3,
            memory_store=store,
            return_query_audit=True,
        )
    )

    # 1. Verify SQL query strictly binds `WHERE agent_memories.agent_id = :agent_id`
    assert "WHERE agent_memories.agent_id =" in compiled_sql
    assert bound_params.get("agent_id") == "coder"

    # 2. Verify zero leakage of `sec-auditor`'s secret token into `coder`'s top-k results
    for mem in coder_memories:
        assert mem["agent_id"] == "coder"
        assert secret_token not in mem["content"]

    # 3. Confirm `sec-auditor` CAN retrieve its own secret memory
    auditor_memories = await retrieve_top_k_memories(
        agent_id="sec-auditor",
        task_description=prompt_text,
        top_k=3,
        memory_store=store,
    )
    assert len(auditor_memories) == 1
    assert secret_token in auditor_memories[0]["content"]
