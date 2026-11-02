import logging
import uuid
from typing import Any, Dict, List, Optional, Set
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BlackboardEdgeModel,
    BlackboardNodeModel,
    utcnow,
)

logger = logging.getLogger("blackboard.graph")


class GraphCycleException(ValueError):
    """
    Raised when an edge insertion would create a circular dependency
    in the Blackboard Adjacency DAG (Popperian Falsifier 8 guard).
    """
    pass


class BlackboardGraphEngine:
    """
    Topological Context Builder & Blackboard Adjacency DAG Engine.
    - Manages nodes and directed edges between debate contributions.
    - Enforces DAG acyclicity at edge creation time.
    - Causal pruning: Automatically supersedes resolved critique and code branches
      when a revision is committed.
    - Assembles active ancestor lineage for token-bounded LLM worker hydration.
    """

    async def create_node(
        self,
        session: AsyncSession,
        workflow_id: str,
        author_agent_id: str,
        node_type: str,
        payload: Dict[str, Any],
        task_id: Optional[str] = None,
        status: str = "active",
        parent_node_id: Optional[uuid.UUID] = None,
        relation_type: str = "implements",
    ) -> BlackboardNodeModel:
        """Create a graph node and optionally link to a parent node with cycle check."""
        node = BlackboardNodeModel(
            workflow_id=workflow_id,
            task_id=task_id,
            author_agent_id=author_agent_id,
            node_type=node_type,
            status=status,
            payload=payload,
        )
        session.add(node)
        await session.flush()

        if parent_node_id:
            await self.add_edge(
                session=session,
                source_id=parent_node_id,
                target_id=node.id,
                relation_type=relation_type,
            )

        return node

    async def add_edge(
        self,
        session: AsyncSession,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        relation_type: str = "implements",
    ) -> BlackboardEdgeModel:
        """
        Add a directed edge: source_id -> target_id.
        Enforces DAG acyclicity by verifying source_id is not already reachable
        from target_id.
        """
        if source_id == target_id:
            raise GraphCycleException(
                f"Self-cycle detected: Node {source_id} cannot have an edge to itself."
            )

        # Cycle check: is source_id reachable from target_id?
        # If yes, adding source_id -> target_id creates a cycle!
        is_reachable = await self._is_reachable(
            session=session,
            start_id=target_id,
            destination_id=source_id,
        )
        if is_reachable:
            raise GraphCycleException(
                f"Graph cycle detected: Edge {source_id} -> {target_id} would create a circular dependency in the debate DAG."
            )

        edge = BlackboardEdgeModel(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            created_at=utcnow(),
        )
        session.add(edge)
        await session.flush()

        # Causal Pruning: If a node revises an earlier node, supersede the earlier branch
        if relation_type == "revises":
            await self.supersede_subgraph(session, target_id)

        return edge

    async def _is_reachable(
        self,
        session: AsyncSession,
        start_id: uuid.UUID,
        destination_id: uuid.UUID,
    ) -> bool:
        """Check if destination_id is reachable from start_id via directed edges."""
        if start_id == destination_id:
            return True

        visited: Set[uuid.UUID] = set()
        queue: List[uuid.UUID] = [start_id]

        while queue:
            current = queue.pop(0)
            if current == destination_id:
                return True
            if current in visited:
                continue
            visited.add(current)

            stmt = select(BlackboardEdgeModel.target_id).where(
                BlackboardEdgeModel.source_id == current
            )
            res = await session.execute(stmt)
            neighbors = res.scalars().all()
            for nbr in neighbors:
                if nbr not in visited:
                    queue.append(nbr)

        return False

    async def supersede_subgraph(
        self,
        session: AsyncSession,
        superseded_node_id: uuid.UUID,
    ) -> None:
        """
        Mark a node and all incoming critiques targeting it as 'superseded'.
        Keeps DB audit intact while pruning them from active LLM worker context.
        """
        # Mark target node as superseded
        await session.execute(
            update(BlackboardNodeModel)
            .where(BlackboardNodeModel.id == superseded_node_id)
            .values(status="superseded")
        )

        # Also find any critiques pointing to or from this superseded node and supersede them
        critique_edges_stmt = select(BlackboardEdgeModel).where(
            (BlackboardEdgeModel.target_id == superseded_node_id)
            & (BlackboardEdgeModel.relation_type == "critiques")
        )
        critique_res = await session.execute(critique_edges_stmt)
        for c_edge in critique_res.scalars().all():
            await session.execute(
                update(BlackboardNodeModel)
                .where(BlackboardNodeModel.id == c_edge.source_id)
                .values(status="superseded")
            )

        # Also check if any node critiques this superseded node
        critique_incoming_stmt = select(BlackboardEdgeModel).where(
            (BlackboardEdgeModel.source_id == superseded_node_id)
            & (BlackboardEdgeModel.relation_type == "critiques")
        )
        critique_in_res = await session.execute(critique_incoming_stmt)
        for c_edge in critique_in_res.scalars().all():
            await session.execute(
                update(BlackboardNodeModel)
                .where(BlackboardNodeModel.id == c_edge.target_id)
                .values(status="superseded")
            )

        await session.flush()

    async def get_active_lineage(
        self,
        session: AsyncSession,
        workflow_id: str,
        leaf_node_id: Optional[uuid.UUID] = None,
    ) -> List[BlackboardNodeModel]:
        """
        Traverse the ancestor path from the given active leaf node up to root.
        Filters out 'superseded' and 'falsified' nodes so only active, relevant
        context is preserved.
        """
        # If leaf_node_id is not specified, pick the most recent active node in the workflow
        if not leaf_node_id:
            latest_stmt = (
                select(BlackboardNodeModel)
                .where(
                    BlackboardNodeModel.workflow_id == workflow_id,
                    BlackboardNodeModel.status == "active",
                )
                .order_by(BlackboardNodeModel.created_at.desc())
                .limit(1)
            )
            latest_res = await session.execute(latest_stmt)
            leaf = latest_res.scalar_one_or_none()
            if not leaf:
                return []
            leaf_node_id = leaf.id

        # Walk upward from leaf to roots collecting active ancestors
        visited: Set[uuid.UUID] = set()
        active_nodes: List[BlackboardNodeModel] = []
        queue: List[uuid.UUID] = [leaf_node_id]

        while queue:
            curr_id = queue.pop(0)
            if curr_id in visited:
                continue
            visited.add(curr_id)

            node = await session.get(BlackboardNodeModel, curr_id)
            if not node:
                continue

            # Only include active nodes in lineage
            if node.status == "active":
                active_nodes.append(node)

            # Traverse to parents (edges where target_id == curr_id)
            parent_edges_stmt = select(BlackboardEdgeModel).where(
                BlackboardEdgeModel.target_id == curr_id
            )
            parent_res = await session.execute(parent_edges_stmt)
            for edge in parent_res.scalars().all():
                if edge.source_id not in visited:
                    queue.append(edge.source_id)

            # Also check if this node was a revision of something that points to earlier spec:
            # e.g., code_v2 has edge code_v2 -> code_v1 (revises). We follow revises edges to find
            # parents of the revised node!
            revises_stmt = select(BlackboardEdgeModel).where(
                (BlackboardEdgeModel.source_id == curr_id)
                & (BlackboardEdgeModel.relation_type == "revises")
            )
            rev_res = await session.execute(revises_stmt)
            for rev_edge in rev_res.scalars().all():
                # find parents of the revised node (e.g. root spec)
                p_stmt = select(BlackboardEdgeModel.source_id).where(
                    BlackboardEdgeModel.target_id == rev_edge.target_id
                )
                p_res = await session.execute(p_stmt)
                for pid in p_res.scalars().all():
                    if pid not in visited:
                        queue.append(pid)

        # Sort chronologically (root spec first -> leaf last)
        active_nodes.sort(key=lambda n: n.created_at)
        return active_nodes

    async def get_full_graph(
        self,
        session: AsyncSession,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """Fetch all nodes and edges for visualization in Web Cockpit Graph Canvas."""
        nodes_stmt = (
            select(BlackboardNodeModel)
            .where(BlackboardNodeModel.workflow_id == workflow_id)
            .order_by(BlackboardNodeModel.created_at.asc())
        )
        nodes_res = await session.execute(nodes_stmt)
        nodes = nodes_res.scalars().all()

        node_ids = [n.id for n in nodes]
        if not node_ids:
            return {"workflow_id": workflow_id, "nodes": [], "edges": []}

        edges_stmt = (
            select(BlackboardEdgeModel)
            .where(
                BlackboardEdgeModel.source_id.in_(node_ids),
                BlackboardEdgeModel.target_id.in_(node_ids),
            )
            .order_by(BlackboardEdgeModel.created_at.asc())
        )
        edges_res = await session.execute(edges_stmt)
        edges = edges_res.scalars().all()

        return {
            "workflow_id": workflow_id,
            "nodes": nodes,
            "edges": edges,
        }

    async def build_topological_context(
        self,
        session: AsyncSession,
        workflow_id: str,
        target_node_id: Optional[uuid.UUID] = None,
    ) -> str:
        """
        Assemble active ancestor lineage into a structured <context_lineage>
        XML prompt block for worker hydration.
        """
        lineage = await self.get_active_lineage(
            session=session,
            workflow_id=workflow_id,
            leaf_node_id=target_node_id,
        )

        if not lineage:
            return ""

        xml_lines = [f'<context_lineage workflow_id="{workflow_id}">']
        for idx, node in enumerate(lineage, 1):
            payload_content = ""
            if isinstance(node.payload, dict):
                # Format payload into clean lines
                payload_content = "\n".join(
                    f"    <{k}>{v}</{k}>" for k, v in node.payload.items()
                )
            else:
                payload_content = f"    {str(node.payload)}"

            xml_lines.append(
                f'  <node step="{idx}" id="{node.id}" type="{node.node_type}" author="{node.author_agent_id}" status="{node.status}">'
            )
            if payload_content:
                xml_lines.append(payload_content)
            xml_lines.append("  </node>")

        xml_lines.append("</context_lineage>")
        return "\n".join(xml_lines)
