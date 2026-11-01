import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import BlackboardFeed from "../components/BlackboardFeed";

describe("BlackboardFeed - Mobile-First Ergonomics & Pagination", () => {
  const generateEntries = (count) =>
    Array.from({ length: count }, (_, idx) => ({
      id: `entry-${idx + 1}`,
      workflow_id: "code_review_pipeline",
      task_id: `task-${idx + 1}`,
      agent_id: idx % 2 === 0 ? "architect" : "sec-auditor",
      entry_type: idx % 3 === 0 ? "spec" : idx % 3 === 1 ? "critique" : "code",
      content: `Detailed artifact payload #${idx + 1} with diff lines.`,
      created_at: new Date().toISOString(),
    }));

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

    const expandBtns = screen.getAllByTestId("expand-entry-btn");
    expect(expandBtns[0].className).toContain("min-h-[44px]");
    fireEvent.click(expandBtns[0]);
    expect(screen.getByTestId("expanded-entry-metadata")).toBeDefined();
  });
});
