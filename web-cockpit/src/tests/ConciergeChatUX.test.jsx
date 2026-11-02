import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import ConciergeChat from "../components/ConciergeChat";

// Mock api methods
vi.mock("../api", () => ({
  api: {
    getLlmStatus: vi.fn().mockResolvedValue({ prefer_local: true, nvidia_nim: {} }),
    getChannelsStatus: vi.fn().mockResolvedValue({ telegram: {}, discord: {} }),
    chatConcierge: vi.fn().mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () =>
              resolve({
                reply: "Synthesis complete.",
                tool_calls_executed: ["list_agents"],
              }),
            100
          )
        )
    ),
    searchMemories: vi.fn().mockResolvedValue([]),
  },
}));

describe("ConciergeChat - Mobile Input Ergonomics & Thinking Indicator", () => {
  it("renders mobile-optimized full-width textarea and quick prompt suggestions", () => {
    render(<ConciergeChat />);

    const textarea = screen.getByPlaceholderText("Ask Concierge or type 'steer: ...'");
    expect(textarea).toBeDefined();
    expect(textarea.className).toContain("min-h-[50px]");
    expect(textarea.className).toContain("flex-1");

    // Check quick prompt buttons
    const steerBtn = screen.getByText(/Steer: Strict Typing/i);
    expect(steerBtn).toBeDefined();

    fireEvent.click(steerBtn);
    expect(textarea.value).toBe("steer: prioritize strict typing and robust tests");
  });

  it("displays animated thinking/processing indicator while awaiting agent response", async () => {
    render(<ConciergeChat />);

    const textarea = screen.getByPlaceholderText("Ask Concierge or type 'steer: ...'");
    const sendBtn = screen.getByRole("button", { name: /Send/i });

    // Type a prompt and send
    fireEvent.change(textarea, { target: { value: "inspect platform state" } });
    
    await act(async () => {
      fireEvent.click(sendBtn);
    });

    // Thinking indicator should be visible while request is pending
    const thinkingIndicator = screen.getByTestId("concierge-thinking-indicator");
    expect(thinkingIndicator).toBeDefined();
    expect(screen.getByText(/Agent is thinking, consulting models & executing tools.../i)).toBeDefined();

    // After response resolves, reply is rendered
    await act(async () => {
      await new Promise((r) => setTimeout(r, 150));
    });

    expect(screen.getByText(/Synthesis complete/i)).toBeDefined();
    expect(screen.queryByTestId("concierge-thinking-indicator")).toBeNull();
  });
});
