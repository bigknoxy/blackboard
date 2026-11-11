import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import BlackboardFeed, { formatRelativeTime } from "../components/BlackboardFeed";

describe("BlackboardFeed - Mobile-First Ergonomics & Pagination", () => {
  const generateEntries = (count) =>
    Array.from({ length: count }, (_, idx) => ({
      id: `entry-${idx + 1}`,
      workflow_id: idx % 2 === 0 ? "code_review_pipeline" : "intake_pipeline",
      task_id: `task-${idx + 1}`,
      agent_id: idx % 2 === 0 ? "architect" : "sec-auditor",
      entry_type: idx % 3 === 0 ? "spec" : idx % 3 === 1 ? "critique" : "code",
      content: `Detailed artifact payload #${idx + 1} with diff lines.`,
      created_at: new Date(Date.now() - idx * 60000).toISOString(),
    }));

  it("formats relative time correctly for diverse intervals", () => {
    const now = 1700000000000;
    expect(formatRelativeTime(new Date(now - 5000).toISOString(), now)).toBe("just now");
    expect(formatRelativeTime(new Date(now - 25000).toISOString(), now)).toBe("25s ago");
    expect(formatRelativeTime(new Date(now - 120000).toISOString(), now)).toBe("2m ago");
    expect(formatRelativeTime(new Date(now - 7200000).toISOString(), now)).toBe("2h ago");
    expect(formatRelativeTime(new Date(now - 172800000).toISOString(), now)).toBe("2d ago");
  });

  it("renders sticky filter tags (#spec, #critique, #code, #steering) with >=44px touch targets", () => {
    render(
      <BlackboardFeed
        entries={generateEntries(5)}
        selectedWorkflowId="code_review_pipeline"
        liveEvents={[]}
      />
    );

    const specTag = screen.getByTestId("filter-tag-spec");
    const critiqueTag = screen.getByTestId("filter-tag-critique");
    const codeTag = screen.getByTestId("filter-tag-code");
    const steeringTag = screen.getByTestId("filter-tag-steering");

    expect(specTag).toBeDefined();
    expect(critiqueTag).toBeDefined();
    expect(codeTag).toBeDefined();
    expect(steeringTag).toBeDefined();

    // Verify minimum 44x44px touch ergonomics classes
    expect(specTag.className).toContain("min-h-[44px]");
    expect(specTag.className).toContain("min-w-[44px]");
  });

  it("defaults to an entire view across all workflows and allows filtering by workflow pill", () => {
    const multiWfEntries = [
      {
        id: "entry-cr-1",
        workflow_id: "code_review_pipeline",
        agent_id: "architect",
        entry_type: "spec",
        content: "Code review spec",
        created_at: new Date(Date.now() - 5000).toISOString(),
      },
      {
        id: "entry-intake-2",
        workflow_id: "intake_pipeline",
        agent_id: "sec-auditor",
        entry_type: "critique",
        content: "Intake critique",
        created_at: new Date(Date.now() - 15000).toISOString(),
      },
    ];

    render(
      <BlackboardFeed
        entries={multiWfEntries}
        selectedWorkflowId="code_review_pipeline"
        liveEvents={[]}
      />
    );

    // Default: Entire view across all workflows (both entries mounted)
    const allPill = screen.getByTestId("filter-workflow-all");
    expect(allPill).toBeDefined();
    expect(allPill.textContent).toContain("All Workflows");
    expect(allPill.textContent).toContain("2");

    const allCards = screen.getAllByTestId("blackboard-entry-card");
    expect(allCards.length).toBe(2);

    // Tapping intake_pipeline pill filters to only intake entries
    const intakePill = screen.getByTestId("filter-workflow-intake_pipeline");
    expect(intakePill).toBeDefined();
    fireEvent.click(intakePill);

    const filteredCards = screen.getAllByTestId("blackboard-entry-card");
    expect(filteredCards.length).toBe(1);

    // Resetting back to all workflows restores entire view
    fireEvent.click(allPill);
    expect(screen.getAllByTestId("blackboard-entry-card").length).toBe(2);
  });

  it("orders entries with the most recent at the top and displays relative timestamps", () => {
    const olderTime = new Date(Date.now() - 3600000).toISOString(); // 1h ago
    const newerTime = new Date(Date.now() - 10000).toISOString();   // 10s ago

    const entries = [
      {
        id: "older-entry",
        workflow_id: "code_review_pipeline",
        agent_id: "architect",
        entry_type: "spec",
        content: "Older specification",
        created_at: olderTime,
      },
      {
        id: "newer-entry",
        workflow_id: "code_review_pipeline",
        agent_id: "coder",
        entry_type: "code",
        content: "Newer code commit",
        created_at: newerTime,
      },
    ];

    render(
      <BlackboardFeed
        entries={entries}
        selectedWorkflowId="code_review_pipeline"
        liveEvents={[]}
      />
    );

    const cards = screen.getAllByTestId("blackboard-entry-card");
    expect(cards.length).toBe(2);
    // Newer entry must be ordered first
    expect(cards[0].textContent).toContain("Newer code commit");
    expect(cards[1].textContent).toContain("Older specification");

    // Relative timestamps displayed
    expect(cards[0].textContent).toContain("s ago");
    expect(cards[1].textContent).toContain("1h ago");
  });

  it("paginates / virtualizes feed when 60+ events arrive to prevent mobile frame drops", () => {
    const manyEntries = generateEntries(65);
    render(
      <BlackboardFeed
        entries={manyEntries}
        selectedWorkflowId="code_review_pipeline"
        liveEvents={[]}
      />
    );

    const renderedCards = screen.getAllByTestId("blackboard-entry-card");
    // Only the current page window (<= 20 items) is mounted in DOM at once
    expect(renderedCards.length).toBeLessThanOrEqual(20);

    const loadMoreBtn = screen.getByTestId("pagination-next-btn");
    expect(loadMoreBtn).toBeDefined();
    fireEvent.click(loadMoreBtn);
  });

  it("filters entries when a sticky tag is tapped and expands metadata details", () => {
    const entries = generateEntries(6);
    render(
      <BlackboardFeed
        entries={entries}
        selectedWorkflowId="code_review_pipeline"
        liveEvents={[]}
      />
    );

    fireEvent.click(screen.getByTestId("filter-tag-critique"));
    const cards = screen.getAllByTestId("blackboard-entry-card");
    expect(cards.length).toBe(2);

    // Cards default to collapsed; uncollapse first card to view inspect metadata button
    const uncollapseBtns = screen.getAllByTestId("uncollapse-card-btn");
    expect(uncollapseBtns.length).toBeGreaterThan(0);
    fireEvent.click(uncollapseBtns[0]);

    const expandBtns = screen.getAllByTestId("expand-entry-btn");
    expect(expandBtns[0].className).toContain("min-h-[44px]");
    fireEvent.click(expandBtns[0]);
    expect(screen.getByTestId("expanded-entry-metadata")).toBeDefined();
  });
});
