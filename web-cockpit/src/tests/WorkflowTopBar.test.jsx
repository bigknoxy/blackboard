import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import WorkflowTopBar from "../components/WorkflowTopBar";

vi.mock("../api", () => ({
  api: {
    triggerWorkflow: vi.fn().mockResolvedValue({ status: "RUNNING", run_id: "test-run" }),
    getChannelsStatus: vi.fn().mockResolvedValue({
      telegram: { configured: true },
      discord: { configured: false },
    }),
  },
}));

describe("WorkflowTopBar - Persistent Top-of-Page Workflow Ribbon", () => {
  const mockWorkflows = [
    {
      id: "code_review_pipeline",
      name: "Code Review Pipeline",
      state_graph: { nodes: [{ id: "n1" }, { id: "n2" }] },
    },
    {
      id: "meta_workflow_architect",
      name: "Meta Architect Pipeline",
      state_graph: { nodes: [{ id: "m1" }] },
    },
  ];

  it("renders active workflow pill, switcher, and + New Workflow button", () => {
    const onSelectWorkflow = vi.fn();
    const onNavigate = vi.fn();

    render(
      <WorkflowTopBar
        workflows={mockWorkflows}
        selectedWorkflowId="code_review_pipeline"
        onSelectWorkflow={onSelectWorkflow}
        currentWorkflow={mockWorkflows[0]}
        agents={[{ id: "architect" }, { id: "coder" }]}
        tools={[{ id: "bash_executor" }]}
        hitlTickets={[{ id: "t1", status: "PENDING" }]}
        backlogCount={3}
        wsConnected={true}
        onNavigate={onNavigate}
      />
    );

    expect(screen.getByTestId("workflow-top-bar")).toBeDefined();

    // Verify workflow select
    const select = screen.getByLabelText("Active Workflow Switcher");
    expect(select.value).toBe("code_review_pipeline");

    fireEvent.change(select, { target: { value: "meta_workflow_architect" } });
    expect(onSelectWorkflow).toHaveBeenCalledWith("meta_workflow_architect");

    // Verify + New Workflow button
    const newWfBtn = screen.getByTestId("topbar-new-workflow-btn");
    expect(newWfBtn).toBeDefined();
    fireEvent.click(newWfBtn);
    expect(onNavigate).toHaveBeenCalledWith(
      "home",
      expect.objectContaining({
        prefillPrompt: expect.stringContaining("Create a new workflow with architect design"),
      })
    );
  });

  it("triggers workflow execution on Run button click", async () => {
    const onRefresh = vi.fn();
    const { api } = await import("../api");

    render(
      <WorkflowTopBar
        workflows={mockWorkflows}
        selectedWorkflowId="code_review_pipeline"
        currentWorkflow={mockWorkflows[0]}
        onRefresh={onRefresh}
      />
    );

    const runBtn = screen.getByTestId("topbar-run-workflow-btn");
    expect(runBtn).toBeDefined();
    fireEvent.click(runBtn);

    expect(api.triggerWorkflow).toHaveBeenCalledWith(
      "code_review_pipeline",
      expect.objectContaining({
        initial_prompt: expect.stringContaining("Quick run"),
      })
    );
  });

  it("navigates to BPMN editor on BPMN button click", () => {
    const onNavigate = vi.fn();

    render(
      <WorkflowTopBar
        workflows={mockWorkflows}
        selectedWorkflowId="code_review_pipeline"
        currentWorkflow={mockWorkflows[0]}
        onNavigate={onNavigate}
      />
    );

    const bpmnBtn = screen.getByTestId("topbar-bpmn-btn");
    fireEvent.click(bpmnBtn);
    expect(onNavigate).toHaveBeenCalledWith("workflows");
  });

  it("renders ambient telemetry chips with >=36px touch targets and pending alert", () => {
    const onNavigate = vi.fn();
    const onOpenBacklog = vi.fn();

    render(
      <WorkflowTopBar
        workflows={mockWorkflows}
        selectedWorkflowId="code_review_pipeline"
        currentWorkflow={mockWorkflows[0]}
        agents={[{ id: "architect" }, { id: "coder" }]}
        tools={[{ id: "tool1" }]}
        hitlTickets={[{ id: "t1", status: "PENDING" }]}
        backlogCount={5}
        onNavigate={onNavigate}
        onOpenBacklog={onOpenBacklog}
      />
    );

    const agentsChip = screen.getByTestId("topbar-stat-agents");
    const toolsChip = screen.getByTestId("topbar-stat-tools");
    const approvalsChip = screen.getByTestId("topbar-stat-approvals");
    const backlogChip = screen.getByTestId("topbar-stat-backlog");

    expect(agentsChip.textContent).toContain("2");
    expect(toolsChip.textContent).toContain("1");
    expect(approvalsChip.textContent).toContain("1");
    expect(backlogChip.textContent).toContain("5");

    // Click navigation
    fireEvent.click(agentsChip);
    expect(onNavigate).toHaveBeenCalledWith("agents");

    fireEvent.click(approvalsChip);
    expect(onNavigate).toHaveBeenCalledWith("approvals");

    fireEvent.click(backlogChip);
    expect(onOpenBacklog).toHaveBeenCalled();
  });
});
