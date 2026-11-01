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

describe("HITLApprovalDrawer - Thumb-Accessible Drawer & Confirmation Dialog", () => {
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
  ];

  it("renders thumb-accessible [Approve] and [Reject with Note] buttons with confirmation modal", () => {
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
});
