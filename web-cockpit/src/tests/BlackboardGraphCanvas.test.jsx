import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import BlackboardGraphCanvas from "../components/BlackboardGraphCanvas";

vi.mock("../api", () => ({
  api: {
    getBlackboardGraph: vi.fn().mockResolvedValue({
      workflow_id: "code_review_pipeline",
      nodes: [
        {
          id: "node-1",
          author_agent_id: "architect",
          node_type: "spec",
          status: "active",
          payload: { title: "Auth Spec v1" },
        },
        {
          id: "node-2",
          author_agent_id: "coder",
          node_type: "code",
          status: "superseded",
          payload: { code: "old code" },
        },
        {
          id: "node-3",
          author_agent_id: "coder",
          node_type: "code",
          status: "active",
          payload: { code: "revised code" },
        },
      ],
      edges: [
        { source_id: "node-1", target_id: "node-2", relation_type: "implements" },
        { source_id: "node-3", target_id: "node-2", relation_type: "revises" },
      ],
      active_lineage: [
        {
          id: "node-1",
          author_agent_id: "architect",
          node_type: "spec",
          status: "active",
          payload: { title: "Auth Spec v1" },
        },
        {
          id: "node-3",
          author_agent_id: "coder",
          node_type: "code",
          status: "active",
          payload: { code: "revised code" },
        },
      ],
    }),
    resolveToolChain: vi.fn().mockResolvedValue({
      source_type: "query_keyword",
      target_type: "security_audit_report",
      tool_chain: ["repo_search", "ast_complexity_analyzer", "static_security_scanner"],
      resolved: true,
    }),
  },
}));

describe("BlackboardGraphCanvas - DAG Lineage & Tool Capability Pathfinding", () => {
  it("renders active lineage by default and excludes superseded nodes", async () => {
    await act(async () => {
      render(<BlackboardGraphCanvas workflowId="code_review_pipeline" />);
    });

    // Check title and badges
    expect(screen.getByText(/Blackboard DAG & Lineage Pruning/i)).toBeDefined();
    expect(screen.getByText(/Active Lineage \(2\)/i)).toBeDefined();

    // In active lineage mode, only node-1 and node-3 should be rendered
    expect(screen.getByText(/Auth Spec v1/i)).toBeDefined();
    expect(screen.getByText(/revised code/i)).toBeDefined();
    expect(screen.queryByText(/old code/i)).toBeNull();
  });

  it("switches to full graph mode and resolves deterministic tool chain", async () => {
    await act(async () => {
      render(<BlackboardGraphCanvas workflowId="code_review_pipeline" />);
    });

    // Switch to All Nodes
    const allNodesBtn = screen.getByText(/All Nodes \(3\)/i);
    await act(async () => {
      fireEvent.click(allNodesBtn);
    });

    // In full graph mode, superseded node is rendered with SUPERSEDED status
    expect(screen.getByText("SUPERSEDED")).toBeDefined();
    expect(screen.getByText(/old code/i)).toBeDefined();

    // Test tool chain resolver form
    const resolveBtn = screen.getByRole("button", { name: /Resolve Tool Chain/i });
    await act(async () => {
      fireEvent.click(resolveBtn);
    });

    expect(screen.getByText(/1\. repo_search/i)).toBeDefined();
    expect(screen.getByText(/2\. ast_complexity_analyzer/i)).toBeDefined();
    expect(screen.getByText(/3\. static_security_scanner/i)).toBeDefined();
  });
});
