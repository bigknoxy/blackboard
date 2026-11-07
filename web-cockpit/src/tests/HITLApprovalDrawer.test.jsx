import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import HITLGates from "../components/HITLGates";

vi.mock("../api", () => ({
  api: {
    decideHitlTicket: vi.fn().mockResolvedValue({ status: "APPROVED" }),
    invokeTool: vi.fn().mockResolvedValue({ status: "AWAITING_APPROVAL" }),
  },
}));

describe("HITLApprovalDrawer - Thumb-Accessible Drawer, Triaging & Minimized Resolved UX", () => {
  const sampleTickets = [
    {
      id: "ticket-hitl-99",
      workflow_id: "code_review_pipeline",
      task_id: "task-42",
      agent_id: "coder",
      gate_type: "tool_approval",
      tool_id: "git_branch_commit",
      tool_arguments: {
        branch: "release/mobile-ready",
        commit_message: "feat: add mobile bottom sheet",
      },
      status: "PENDING",
    },
    {
      id: "ticket-bash-1",
      workflow_id: "code_review_pipeline",
      task_id: "task-bash-42",
      agent_id: "sec-auditor",
      gate_type: "tool_approval",
      tool_id: "bash_executor",
      tool_arguments: {
        command: "uname -a && uptime",
      },
      status: "APPROVED",
      reviewer_comment: "Approved by SecOps",
      execution_result: {
        exit_code: 0,
        stdout: "Linux homelab-node-1 6.8.0-generic x86_64",
        stderr: "",
      },
    },
  ];

  it("renders thumb-accessible [Approve] and [Reject with Note] buttons with confirmation modal for pending tickets", () => {
    render(
      <HITLGates
        tickets={sampleTickets}
        selectedWorkflowId="code_review_pipeline"
      />
    );

    const approveBtn = screen.getByTestId("hitl-approve-btn-ticket-hitl-99");
    const rejectBtn = screen.getByTestId("hitl-reject-btn-ticket-hitl-99");

    expect(approveBtn).toBeDefined();
    expect(rejectBtn).toBeDefined();
    expect(approveBtn.className).toContain("min-h-[44px]");
    expect(rejectBtn.className).toContain("min-h-[44px]");
    expect(rejectBtn.textContent).toContain("Reject with Note");

    // Tapping [Approve] opens confirmation dialog to prevent accidental mobile taps
    fireEvent.click(approveBtn);
    const confirmModal = screen.getByTestId("hitl-confirmation-dialog");
    expect(confirmModal).toBeDefined();

    const confirmExecBtn = screen.getByTestId("hitl-confirm-submit-btn");
    fireEvent.click(confirmExecBtn);
  });

  it("minimizes approved tickets by default and expands on click to reveal terminal preview and execution results", () => {
    render(
      <HITLGates
        tickets={sampleTickets}
        selectedWorkflowId="code_review_pipeline"
      />
    );

    // Resolved ticket should be rendered in minimized state by default
    const resolvedCard = screen.getByTestId("hitl-ticket-card-ticket-bash-1");
    expect(resolvedCard).toBeDefined();
    expect(resolvedCard.textContent).toContain("Exit 0");
    expect(resolvedCard.textContent).toContain("APPROVED");

    // Click toggle button to expand details
    const toggleBtn = screen.getByTestId("hitl-toggle-card-ticket-bash-1");
    expect(toggleBtn).toBeDefined();
    expect(toggleBtn.className).toContain("min-h-[44px]");
    fireEvent.click(toggleBtn);

    // Once expanded, terminal preview and execution result are visible
    const bashPreview = screen.getByTestId("hitl-bash-preview-ticket-bash-1");
    expect(bashPreview).toBeDefined();
    expect(bashPreview.textContent).toContain("uname -a && uptime");

    const execResult = screen.getByTestId("hitl-exec-result-ticket-bash-1");
    expect(execResult).toBeDefined();
    expect(execResult.textContent).toContain("Linux homelab-node-1 6.8.0-generic");
    expect(execResult.textContent).toContain("Exit Code: 0");
  });

  it("filters tickets by status (Needs Review vs Approved) and provides bulk toggle", () => {
    render(
      <HITLGates
        tickets={sampleTickets}
        selectedWorkflowId="code_review_pipeline"
      />
    );

    const pendingFilter = screen.getByTestId("hitl-filter-pending");
    const approvedFilter = screen.getByTestId("hitl-filter-approved");

    // Click Pending filter
    fireEvent.click(pendingFilter);
    expect(screen.getByTestId("hitl-ticket-card-ticket-hitl-99")).toBeDefined();
    expect(screen.queryByTestId("hitl-ticket-card-ticket-bash-1")).toBeNull();

    // Click Approved filter
    fireEvent.click(approvedFilter);
    expect(screen.getByTestId("hitl-ticket-card-ticket-bash-1")).toBeDefined();
    expect(screen.queryByTestId("hitl-ticket-card-ticket-hitl-99")).toBeNull();

    // Bulk toggle: Expand Resolved expands all resolved tickets at once
    const bulkToggle = screen.getByTestId("hitl-toggle-all-resolved");
    expect(bulkToggle.textContent).toContain("Expand Resolved");
    fireEvent.click(bulkToggle);

    expect(screen.getByTestId("hitl-bash-preview-ticket-bash-1")).toBeDefined();
  });
});
