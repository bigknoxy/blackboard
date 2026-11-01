import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import App from "../App";

vi.mock("../api", () => ({
  WS_URL: "ws://localhost:8000/ws/blackboard",
  api: {
    listAgents: vi.fn().mockResolvedValue([
      { id: "architect", name: "Architect", role: "Spec", allowed_tools: [] },
    ]),
    listTools: vi.fn().mockResolvedValue([]),
    listWorkflows: vi.fn().mockResolvedValue([
      {
        id: "code_review_pipeline",
        name: "Code Review Pipeline",
        state_graph: {
          nodes: [
            { id: "n1", name: "Research", type: "ServiceTask", agent_id: "architect" },
          ],
          edges: [],
        },
        yaml_source: "id: code_review_pipeline\n",
      },
    ]),
    listBlackboard: vi.fn().mockResolvedValue([]),
    listHitlTickets: vi.fn().mockResolvedValue([
      {
        id: "ticket-1",
        workflow_id: "code_review_pipeline",
        task_id: "task-1",
        agent_id: "coder",
        gate_type: "tool_approval",
        tool_id: "git_branch_commit",
        tool_arguments: { branch: "main" },
        status: "PENDING",
      },
    ]),
  },
}));

describe("MobileNavigation - Responsive Shell & Bottom Navigation Bar", () => {
  beforeEach(() => {
    global.WebSocket = class {
      constructor() {
        setTimeout(() => this.onopen && this.onopen(), 5);
      }
      send() {}
      close() {}
    };
  });

  it("renders persistent mobile bottom navigation bar with [Board], [Workflows], [Approvals (with badge)], [Concierge]", async () => {
    render(<App />);

    const bottomNav = screen.getByTestId("mobile-bottom-nav");
    expect(bottomNav).toBeDefined();

    const boardBtn = screen.getByTestId("mobile-nav-board");
    const workflowsBtn = screen.getByTestId("mobile-nav-workflows");
    const approvalsBtn = screen.getByTestId("mobile-nav-approvals");
    const conciergeBtn = screen.getByTestId("mobile-nav-concierge");

    expect(boardBtn.textContent).toContain("Board");
    expect(workflowsBtn.textContent).toContain("Workflows");
    expect(approvalsBtn.textContent).toContain("Approvals");
    expect(conciergeBtn.textContent).toContain("Concierge");

    // Verify 44x44px minimum touch target classes
    expect(boardBtn.className).toContain("min-h-[44px]");
    expect(approvalsBtn.className).toContain("min-h-[44px]");

    // Switch mobile tabs via bottom bar
    fireEvent.click(conciergeBtn);
    expect(screen.getByTestId("concierge-dvh-container")).toBeDefined();
  });
});
