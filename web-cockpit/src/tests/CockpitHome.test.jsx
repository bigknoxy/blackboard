import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import CockpitHome from "../components/CockpitHome";

vi.mock("../api", () => ({
  api: {
    getChannelsStatus: vi.fn().mockResolvedValue({
      telegram: { enabled: true, configured: true },
      discord: { enabled: false, configured: false },
    }),
    chatConcierge: vi.fn().mockResolvedValue({
      reply: "Platform ready.",
      tool_calls_executed: [],
    }),
    searchMemories: vi.fn().mockResolvedValue([]),
    getLlmStatus: vi.fn().mockResolvedValue({
      prefer_local: true,
      nvidia_nim: { default_model: "meta/llama-3.3-70b-instruct" },
    }),
  },
}));

describe("CockpitHome - Conversational Cockpit Home Landing Surface", () => {
  const mockAgents = [
    { id: "architect", name: "Architect", role: "Spec" },
    { id: "coder", name: "Coder", role: "Implementation" },
  ];

  const mockTools = [
    { id: "ast_complexity_analyzer", name: "ast_complexity_analyzer" },
    { id: "bash_executor", name: "bash_executor" },
  ];

  const mockBlackboardEntries = [
    { id: "b1", entry_type: "spec", agent_id: "architect", content: "Architecture draft v1" },
    { id: "b2", entry_type: "code", agent_id: "coder", content: "Code patch generated" },
  ];

  const mockWorkflow = {
    id: "code_review_pipeline",
    name: "Code Review Pipeline",
    state_graph: {
      nodes: [
        { id: "n1", name: "Architecture Spec" },
        { id: "n2", name: "Code Review Gate" },
      ],
    },
  };

  it("renders ambient cluster status bar with active workflow, gateway status, and stat jump chips", async () => {
    const onNavigateMock = vi.fn();

    render(
      <CockpitHome
        selectedWorkflowId="code_review_pipeline"
        currentWorkflow={mockWorkflow}
        agents={mockAgents}
        tools={mockTools}
        blackboardEntries={mockBlackboardEntries}
        hitlTickets={[]}
        wsConnected={true}
        onNavigate={onNavigateMock}
        onRefresh={vi.fn()}
      />
    );

    expect(screen.getByTestId("cockpit-home-view")).toBeDefined();
    expect(screen.getByTestId("cockpit-ambient-status-bar")).toBeDefined();

    // Verify stat jump chips
    const agentsChip = screen.getByTestId("home-stat-agents");
    const toolsChip = screen.getByTestId("home-stat-tools");
    const approvalsChip = screen.getByTestId("home-stat-approvals");
    const boardChip = screen.getByTestId("home-stat-board");
    const workflowsChip = screen.getByTestId("home-stat-workflows");

    expect(agentsChip.textContent).toContain("2 Agents");
    expect(toolsChip.textContent).toContain("2 Tools");
    expect(approvalsChip.textContent).toContain("0 Approvals");
    expect(boardChip.textContent).toContain("2 Feed");
    expect(workflowsChip.textContent).toContain("BPMN");

    // Test clicking navigation chips
    fireEvent.click(agentsChip);
    expect(onNavigateMock).toHaveBeenCalledWith("agents");

    fireEvent.click(toolsChip);
    expect(onNavigateMock).toHaveBeenCalledWith("tools");

    fireEvent.click(approvalsChip);
    expect(onNavigateMock).toHaveBeenCalledWith("approvals");

    fireEvent.click(boardChip);
    expect(onNavigateMock).toHaveBeenCalledWith("board");

    fireEvent.click(workflowsChip);
    expect(onNavigateMock).toHaveBeenCalledWith("workflows");

    // External channels gateway pill
    await waitFor(() => {
      const pill = screen.getByTestId("home-external-channels-pill");
      expect(pill.textContent).toContain("TG ✓");
      expect(pill.textContent).toContain("DC off");
    });
  });

  it("displays proactive HITL review banner when pending approval tickets exist", () => {
    const onNavigateMock = vi.fn();
    const pendingTickets = [
      { id: "t1", status: "PENDING", tool_id: "production_release_gate" },
      { id: "t2", status: "PENDING", tool_id: "bash_executor" },
    ];

    render(
      <CockpitHome
        selectedWorkflowId="code_review_pipeline"
        currentWorkflow={mockWorkflow}
        agents={mockAgents}
        tools={mockTools}
        blackboardEntries={mockBlackboardEntries}
        hitlTickets={pendingTickets}
        wsConnected={true}
        onNavigate={onNavigateMock}
        onRefresh={vi.fn()}
      />
    );

    const banner = screen.getByTestId("home-pending-hitl-banner");
    expect(banner).toBeDefined();
    expect(banner.textContent).toContain("2 Actions Awaiting Operator Approval");

    const reviewBtn = screen.getByTestId("home-review-approvals-btn");
    fireEvent.click(reviewBtn);
    expect(onNavigateMock).toHaveBeenCalledWith("approvals");
  });

  it("renders live activity sidebar with recent blackboard entries and open board button", () => {
    const onNavigateMock = vi.fn();

    render(
      <CockpitHome
        selectedWorkflowId="code_review_pipeline"
        currentWorkflow={mockWorkflow}
        agents={mockAgents}
        tools={mockTools}
        blackboardEntries={mockBlackboardEntries}
        hitlTickets={[]}
        wsConnected={true}
        onNavigate={onNavigateMock}
        onRefresh={vi.fn()}
      />
    );

    const sidebar = screen.getByTestId("home-live-feed-sidebar");
    expect(sidebar).toBeDefined();
    expect(sidebar.textContent).toContain("#spec");
    expect(sidebar.textContent).toContain("Architecture draft v1");

    const openBoardBtn = screen.getByTestId("home-open-board-btn");
    fireEvent.click(openBoardBtn);
    expect(onNavigateMock).toHaveBeenCalledWith("board");
  });
});
