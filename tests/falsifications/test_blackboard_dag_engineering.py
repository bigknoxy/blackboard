import uuid
import pytest
from app.database import AsyncSessionLocal
from app.graph import (
    BlackboardGraphEngine,
    GraphCycleException,
)
from app.mcp_gateway import MCPToolboxGateway


@pytest.mark.asyncio
async def test_falsifier_8_dag_acyclicity_enforced(db_session):
    """
    Falsifier 8: Adjacency DAG Acyclicity
    Hypothesis to falsify:
    'Agents in debate loops can insert cyclic edges (e.g. A critiques B, B critiques C,
    C critiques A), which creates infinite recursion in context builders and memory leaks.'

    Invariant:
    Any attempt to create an edge (source -> target) where source is already reachable
    from target MUST raise GraphCycleException and be aborted before touching the DB.
    """
    engine = BlackboardGraphEngine()
    workflow_id = f"test-dag-acyclic-{uuid.uuid4().hex[:8]}"

    session = db_session
    # Create 3 debate nodes
    node_a = await engine.create_node(
        session=session,
        workflow_id=workflow_id,
        author_agent_id="architect",
        node_type="spec",
        payload={"summary": "System architectural blueprint v1"},
    )
    node_b = await engine.create_node(
        session=session,
        workflow_id=workflow_id,
        author_agent_id="coder",
        node_type="code",
        payload={"code": "class Engine: pass"},
        parent_node_id=node_a.id,
        relation_type="implements",
    )
    node_c = await engine.create_node(
        session=session,
        workflow_id=workflow_id,
        author_agent_id="qa-tester",
        node_type="critique",
        payload={"issue": "Missing error handling in Engine"},
        parent_node_id=node_b.id,
        relation_type="critiques",
    )
    await session.commit()

    # Path exists: A -> B -> C
    # An attempt to add edge from C -> A creates cycle A -> B -> C -> A
    with pytest.raises(GraphCycleException) as exc_info:
        await engine.add_edge(
            session=session,
            source_id=node_c.id,
            target_id=node_a.id,
            relation_type="revises",
        )

    assert "cycle" in str(exc_info.value).lower()

    # Self-cycle attempt (A -> A) must also raise GraphCycleException
    with pytest.raises(GraphCycleException):
        await engine.add_edge(
            session=session,
            source_id=node_a.id,
            target_id=node_a.id,
            relation_type="revises",
        )


@pytest.mark.asyncio
async def test_falsifier_9_causal_lineage_pruning_filters_superseded_branches(db_session):
    """
    Falsifier 9: Causal Lineage Pruning & Context Bounding
    Hypothesis to falsify:
    'When a revised solution is committed, resolved critiques and superseded code
    remain in the worker context lineage, causing token bloat and hallucinated revisions.'

    Invariant:
    When a new node revises an earlier node, the earlier node and its incoming
    critiques are marked as 'superseded'. The topological context builder for the
    active leaf node includes ONLY active lineage, preserving root spec while
    excluding superseded code and resolved critiques.
    """
    engine = BlackboardGraphEngine()
    workflow_id = f"test-causal-pruning-{uuid.uuid4().hex[:8]}"

    session = db_session
    # Step 1: Root Spec
    node_spec = await engine.create_node(
        session=session,
        workflow_id=workflow_id,
        author_agent_id="architect",
        node_type="spec",
        payload={"title": "JWT Auth Microservice Spec"},
    )

    # Step 2: Initial flawed code (implements spec)
    node_code_v1 = await engine.create_node(
        session=session,
        workflow_id=workflow_id,
        author_agent_id="coder",
        node_type="code",
        payload={"code": "def verify(token): return True # flawed"},
        parent_node_id=node_spec.id,
        relation_type="implements",
    )

    # Step 3: Security critique against code_v1
    node_critique = await engine.create_node(
        session=session,
        workflow_id=workflow_id,
        author_agent_id="sec-auditor",
        node_type="critique",
        payload={"flaw": "verify() hardcodes True bypass"},
        parent_node_id=node_code_v1.id,
        relation_type="critiques",
    )

    # Step 4: Revised code_v2 that resolves critique and revises code_v1
    node_code_v2 = await engine.create_node(
        session=session,
        workflow_id=workflow_id,
        author_agent_id="coder",
        node_type="code",
        payload={"code": "def verify(token): return jwt.decode(token, key)"},
        parent_node_id=node_spec.id,
        relation_type="implements",
    )

    # Link code_v2 as 'revises' code_v1 and resolving node_critique
    await engine.add_edge(
        session=session,
        source_id=node_code_v2.id,
        target_id=node_code_v1.id,
        relation_type="revises",
    )
    await session.commit()

    # Build active topological lineage for node_code_v2
    lineage = await engine.get_active_lineage(session, workflow_id, leaf_node_id=node_code_v2.id)
    lineage_ids = [n.id for n in lineage]

    # Root spec must be preserved
    assert node_spec.id in lineage_ids
    # Active revised code must be present
    assert node_code_v2.id in lineage_ids

    # Flawed code_v1 and resolved critique MUST be pruned
    assert node_code_v1.id not in lineage_ids
    assert node_critique.id not in lineage_ids

    # Verify XML prompt block formatting
    xml_block = await engine.build_topological_context(session, workflow_id, target_node_id=node_code_v2.id)
    assert "<context_lineage" in xml_block
    assert "JWT Auth Microservice Spec" in xml_block
    assert "jwt.decode(token, key)" in xml_block
    assert "def verify(token): return True" not in xml_block
    assert "hardcodes True bypass" not in xml_block



def test_falsifier_10_deterministic_tool_chain_pathfinding():
    """
    Falsifier 10: Tool Capability Graph & Deterministic Multi-Step Composition
    Hypothesis to falsify:
    'Sequential tool dependencies must be planned by the LLM through trial-and-error,
    causing small models to fail multi-step workflows.'

    Invariant:
    Given tools with declared consumes_type and produces_type, resolve_tool_chain
    determines the optimal shortest directed tool execution sequence, and returns
    None for unreachable disjoint types without crashing.
    """
    gateway = MCPToolboxGateway()

    # Path exists: query_keyword -> repo_search -> code_snippet -> ast_complexity_analyzer -> ast_report -> static_security_scanner -> security_audit_report
    chain = gateway.resolve_tool_chain(
        source_type="query_keyword",
        target_type="security_audit_report",
    )
    assert chain == [
        "repo_search",
        "ast_complexity_analyzer",
        "static_security_scanner",
    ]

    # Path from code_snippet to ast_report
    short_chain = gateway.resolve_tool_chain(
        source_type="code_snippet",
        target_type="ast_report",
    )
    assert short_chain == ["ast_complexity_analyzer"]

    # When source equals target, return empty list (already at target)
    same_chain = gateway.resolve_tool_chain(
        source_type="code_snippet",
        target_type="code_snippet",
    )
    assert same_chain == []

    # Disjoint unreachable types return None gracefully
    disjoint_chain = gateway.resolve_tool_chain(
        source_type="query_keyword",
        target_type="nuclear_reactor_telemetry",
    )
    assert disjoint_chain is None
