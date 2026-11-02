import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import BacklogModal from "../components/BacklogModal";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    listBacklog: vi.fn(),
    createBacklogItem: vi.fn(),
    updateBacklogItem: vi.fn(),
    deleteBacklogItem: vi.fn(),
    triggerBacklogIntake: vi.fn(),
    getBacklogArtifacts: vi.fn(),
  },
}));

describe("BacklogIntakeUX - Enhanced Intake Lifecycle & Stepper", () => {
  const mockBacklogItems = [
    {
      id: "item-1",
      key: "IDEA-3",
      title: "Mobile Gesture & Touch Optimization",
      description: "Harden horizontal swipe sensitivity with angle/velocity gating",
      category: "improvement",
      priority: "P0",
      status: "triaged",
      tags: ["mobile", "ui", "touch"],
      metadata: {
        intake_run_id: "run-e15dba3a",
        intake_step: "stage_security_impact",
        artifacts: [
          {
            id: "art-1",
            agent_id: "architect",
            entry_type: "spec",
            content: "Technical Spec: Implement angleThreshold ±15° and velocityThreshold 0.3px/ms",
          },
        ],
      },
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    api.listBacklog.mockResolvedValue(mockBacklogItems);
    api.getBacklogArtifacts.mockResolvedValue([
      {
        id: "art-1",
        agent_id: "architect",
        entry_type: "spec",
        content: "Technical Spec: Implement angleThreshold ±15° and velocityThreshold 0.3px/ms",
      },
    ]);
  });

  it("renders live progress stepper with Architect, Security, and Sign-off stages", async () => {
    render(
      <BacklogModal
        isOpen={true}
        onClose={vi.fn()}
        onIntakeTriggered={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("IDEA-3")).toBeDefined();
    });

    // Check that stepper stages exist
    expect(screen.getByText(/2\. Architect Spec/i)).toBeDefined();
    expect(screen.getByText(/3\. Security Audit/i)).toBeDefined();
    expect(screen.getByText(/4\. Sign-Off Gate/i)).toBeDefined();
  });

  it("renders inline artifact preview chips and expands content on click", async () => {
    render(
      <BacklogModal
        isOpen={true}
        onClose={vi.fn()}
        onIntakeTriggered={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("IDEA-3")).toBeDefined();
    });

    const specChip = await screen.findByRole("button", {
      name: /View Architect Spec/i,
    });
    expect(specChip).toBeDefined();

    fireEvent.click(specChip);
    expect(
      screen.getByText(/Technical Spec: Implement angleThreshold/i)
    ).toBeDefined();
  });

  it("provides one-tap 'View Live Blackboard' action when intake pipeline is running", async () => {
    const onIntakeTriggeredMock = vi.fn();
    const onCloseMock = vi.fn();

    render(
      <BacklogModal
        isOpen={true}
        onClose={onCloseMock}
        onIntakeTriggered={onIntakeTriggeredMock}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("IDEA-3")).toBeDefined();
    });

    const liveBtn = screen.getByRole("button", {
      name: /View Live Blackboard/i,
    });
    expect(liveBtn).toBeDefined();

    fireEvent.click(liveBtn);
    expect(onIntakeTriggeredMock).toHaveBeenCalledWith(
      expect.objectContaining({ workflow_id: "intake_pipeline" })
    );
    expect(onCloseMock).toHaveBeenCalled();
  });
});
