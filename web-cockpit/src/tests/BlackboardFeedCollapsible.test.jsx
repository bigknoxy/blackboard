import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import BlackboardFeed from "../components/BlackboardFeed";

describe("BlackboardFeed - Collapsible Cards & Screen Real-Estate Optimization", () => {
  const sampleLongEntry = {
    id: "entry-long-1",
    workflow_id: "code_review_pipeline",
    task_id: "task-53cf7c1c36",
    agent_id: "sec-auditor",
    entry_type: "critique",
    content: `## Security Critique — Task task-53cf7c1c36\n### Scope\nStatic analysis of candidate patch implementing token-bucket rate limiter.\n### Findings\n| Category | Severity | Description |\n| Injection | High | FastAPI request.body() not validated |\n**MCP Tool Invoked ('ast_complexity_analyzer'):**\n{\n  "loc": 45,\n  "cyclomatic_complexity": 8\n}`,
    created_at: new Date().toISOString(),
  };

  const sampleShortEntry = {
    id: "entry-short-2",
    workflow_id: "code_review_pipeline",
    task_id: "task-short",
    agent_id: "architect",
    entry_type: "spec",
    content: "Minimal 1-line spec definition.",
    created_at: new Date().toISOString(),
  };

  it("line-clamps long multi-line card content and toggles Show More / Show Less", () => {
    render(
      <BlackboardFeed
        entries={[sampleLongEntry, sampleShortEntry]}
        selectedWorkflowId="code_review_pipeline"
      />
    );

    // Long entry should render a Show More button with line count
    const showMoreBtn = screen.getByTestId("show-more-btn");
    expect(showMoreBtn).toBeDefined();
    expect(showMoreBtn.textContent).toContain("Show More");

    // Click Show More
    fireEvent.click(showMoreBtn);

    // Button should now be Show Less
    const showLessBtn = screen.getByTestId("show-less-btn");
    expect(showLessBtn).toBeDefined();
    expect(showLessBtn.textContent).toContain("Show Less");

    // Click Show Less to re-clamp
    fireEvent.click(showLessBtn);
    expect(screen.getByTestId("show-more-btn")).toBeDefined();
  });

  it("collapses card into a sleek 1-line summary chip via collapse button and uncollapses it", () => {
    render(
      <BlackboardFeed
        entries={[sampleLongEntry]}
        selectedWorkflowId="code_review_pipeline"
      />
    );

    // Initial state: full card is visible with collapse chevron button ▲
    const collapseBtn = screen.getByTestId("collapse-card-btn");
    expect(collapseBtn).toBeDefined();

    // Click collapse button
    fireEvent.click(collapseBtn);

    // Now card is a compact 1-line chip containing uncollapse button ▼
    const uncollapseBtn = screen.getByTestId("uncollapse-card-btn");
    expect(uncollapseBtn).toBeDefined();
    expect(screen.getByText(/@sec-auditor/)).toBeDefined();

    // Uncollapse by clicking the button
    fireEvent.click(uncollapseBtn);
    expect(screen.getByTestId("collapse-card-btn")).toBeDefined();
  });

  it("collapses all and expands all cards with master toolbar buttons", () => {
    render(
      <BlackboardFeed
        entries={[sampleLongEntry, sampleShortEntry]}
        selectedWorkflowId="code_review_pipeline"
      />
    );

    const collapseAllBtn = screen.getByTestId("collapse-all-btn");
    const expandAllBtn = screen.getByTestId("expand-all-btn");

    expect(collapseAllBtn).toBeDefined();
    expect(expandAllBtn).toBeDefined();

    // Click Collapse All
    fireEvent.click(collapseAllBtn);

    // Both cards should now be 1-line chips with uncollapse buttons
    const uncollapseBtns = screen.getAllByTestId("uncollapse-card-btn");
    expect(uncollapseBtns.length).toBe(2);

    // Click Expand All
    fireEvent.click(expandAllBtn);
    const collapseBtns = screen.getAllByTestId("collapse-card-btn");
    expect(collapseBtns.length).toBe(2);
  });

  it("toggles live operator steering form open and closed to save mobile space", () => {
    render(
      <BlackboardFeed
        entries={[sampleShortEntry]}
        selectedWorkflowId="code_review_pipeline"
      />
    );

    const toggleSteeringBtn = screen.getByTestId("toggle-steering-btn");
    expect(toggleSteeringBtn).toBeDefined();

    // Initially steering input form is collapsed
    expect(screen.queryByPlaceholderText(/Inject live Operator Steering/)).toBeNull();

    // Tap to open
    fireEvent.click(toggleSteeringBtn);
    expect(screen.getByPlaceholderText(/Inject live Operator Steering/)).toBeDefined();

    // Tap to collapse
    fireEvent.click(toggleSteeringBtn);
    expect(screen.queryByPlaceholderText(/Inject live Operator Steering/)).toBeNull();
  });
});
