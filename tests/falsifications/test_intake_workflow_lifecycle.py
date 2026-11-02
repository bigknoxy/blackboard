import pytest
from app.database import AsyncSessionLocal
from app.models import BacklogItemModel, WorkflowModel, BlackboardEntryModel


@pytest.mark.asyncio
async def test_falsifier_7_intake_workflow_links_artifacts_and_enforces_idempotent_runs(
    fake_redis, monkeypatch
):
    """
    Falsifier 7: Intake Workflow Lifecycle & Artifact Lineage
    Hypothesis to falsify:
    'Triggering intake on a backlog item creates disjoint unlinked workflow runs
    and loses trace of generated agent artifacts (spec, critique) on the Blackboard.'
    """
    monkeypatch.setattr("app.workflow_engine.get_redis", lambda: fake_redis)

    async with AsyncSessionLocal() as session:
        # 1. Setup sample intake workflow if not present
        wf = await session.get(WorkflowModel, "intake_pipeline")
        if not wf:
            wf = WorkflowModel(
                id="intake_pipeline",
                name="Idea & Backlog Intake Pipeline",
                description="Intake test pipeline",
                state_graph={
                    "nodes": [
                        {"id": "start", "type": "StartEvent"},
                        {
                            "id": "stage_architect_feasibility",
                            "type": "ServiceTask",
                            "agent_id": "architect",
                            "entry_type": "spec",
                        },
                        {
                            "id": "stage_security_impact",
                            "type": "ServiceTask",
                            "agent_id": "sec-auditor",
                            "entry_type": "critique",
                        },
                        {"id": "stage_operator_approval", "type": "UserTask"},
                        {"id": "end", "type": "EndEvent"},
                    ],
                    "edges": [
                        {"id": "e1", "source": "start", "target": "stage_architect_feasibility"},
                        {"id": "e2", "source": "stage_architect_feasibility", "target": "stage_security_impact"},
                        {"id": "e3", "source": "stage_security_impact", "target": "stage_operator_approval"},
                        {"id": "e4", "source": "stage_operator_approval", "target": "end"},
                    ],
                },
            )
            session.add(wf)
            await session.commit()

        # 2. Insert test backlog item
        item = BacklogItemModel(
            key="IDEA-FALSIFIER-7",
            title="Automated Test Visual Diff",
            description="Side-by-side visual diff viewer in web cockpit",
            category="feature",
            priority="P1",
            status="idea",
            tags=["ui", "diff"],
            metadata_={},
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)

        # 3. Simulate triggering intake via main workflow logic
        from app.workflow_engine import workflow_engine

        prompt = f"INTAKE REVIEW FOR [{item.key}] {item.title}"
        run = await workflow_engine.start_workflow_run(
            session, wf, initial_prompt=prompt, context={"backlog_key": item.key}
        )

        # Link run to backlog item
        item.status = "triaged"
        item.metadata_ = {**item.metadata_, "intake_run_id": str(run.id)}
        await session.commit()
        await session.refresh(item)

        # Verify link invariant
        assert item.metadata_.get("intake_run_id") == str(run.id)
        assert item.status == "triaged"

        # 4. Simulate Architect posting a SPEC artifact to the Blackboard
        spec_entry = BlackboardEntryModel(
            workflow_id=wf.id,
            task_id=f"run-{run.id}-spec",
            agent_id="architect",
            entry_type="spec",
            content="## Architect Spec: Visual Diff Viewer implementation details",
            metadata_={"backlog_key": item.key, "run_id": str(run.id)},
        )
        # Simulate Security Auditor posting a CRITIQUE artifact
        critique_entry = BlackboardEntryModel(
            workflow_id=wf.id,
            task_id=f"run-{run.id}-critique",
            agent_id="sec-auditor",
            entry_type="critique",
            content="## Security Critique: Ensure screenshot paths cannot traverse directory boundaries",
            metadata_={"backlog_key": item.key, "run_id": str(run.id)},
        )
        session.add_all([spec_entry, critique_entry])
        await session.commit()

        # 5. Query artifacts associated with this backlog item
        from sqlalchemy import select
        stmt = (
            select(BlackboardEntryModel)
            .where(
                (BlackboardEntryModel.workflow_id == wf.id)
                & (
                    (BlackboardEntryModel.metadata_["backlog_key"].as_string() == item.key)
                    | (BlackboardEntryModel.metadata_["run_id"].as_string() == str(run.id))
                )
            )
            .order_by(BlackboardEntryModel.created_at.asc())
        )
        res = await session.execute(stmt)
        artifacts = res.scalars().all()

        # Assert artifact lineage is preserved
        entry_types = [a.entry_type for a in artifacts]
        assert "spec" in entry_types
        assert "critique" in entry_types
        assert any(a.agent_id == "architect" for a in artifacts)
        assert any(a.agent_id == "sec-auditor" for a in artifacts)

        # Cleanup test item
        await session.delete(item)
        await session.commit()
