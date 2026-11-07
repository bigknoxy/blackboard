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
    const memoryBtn = screen.getByText(/Memory & Context/i);
    expect(memoryBtn).toBeDefined();

    const steerBtn = screen.getByText(/Steer: Strict Typing/i);
    expect(steerBtn).toBeDefined();

    const webSearchBtn = screen.getByText(/Web Search & NFL/i);
    expect(webSearchBtn).toBeDefined();

    const bashBtn = screen.getByText(/Bash & Tools Suggestion/i);
    expect(bashBtn).toBeDefined();

    fireEvent.click(memoryBtn);
    expect(textarea.value).toContain("Do you have memory?");

    fireEvent.click(webSearchBtn);
    expect(textarea.value).toContain("Tell me what today's date is and if there are any NFL games today");

    fireEvent.click(steerBtn);
    expect(textarea.value).toBe("steer: prioritize strict typing and robust tests");
  });

  it("renders visual action receipt cards for web_search, bash_executor, store_memory, list_memories and tool suggestions", async () => {
    const { api } = await import("../api");
    api.chatConcierge.mockResolvedValueOnce({
      reply: "Executed web search and provisioned bash tool.",
      tool_calls_executed: [
        {
          tool: "store_memory",
          content: "Operator is located in KS with timezone CST.",
          status: "STORED",
        },
        {
          tool: "list_memories",
          count: 5,
          agents: ["architect", "coder", "concierge-admin"],
          status: "RETRIEVED",
        },
        {
          tool: "web_search",
          query: "NFL games today",
          status: "EXECUTED",
        },
        {
          tool: "bash_executor",
          command: "uname -a",
          status: "SUCCESS",
        },
        {
          tool: "suggest_tools",
          recommendation: "Registered bash_executor and web_search.",
          status: "PROVISIONED",
        },
      ],
    });

    render(<ConciergeChat />);
    const textarea = screen.getByPlaceholderText("Ask Concierge or type 'steer: ...'");
    const sendBtn = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(textarea, { target: { value: "Search web and suggest bash" } });
    await act(async () => {
      fireEvent.click(sendBtn);
    });

    expect(screen.getByText(/Executed web search and provisioned bash tool/i)).toBeDefined();
    const receiptsContainer = screen.getByTestId("concierge-action-receipts");
    expect(receiptsContainer).toBeDefined();
    expect(receiptsContainer.textContent).toContain("store_memory");
    expect(receiptsContainer.textContent).toContain("list_memories");
    expect(receiptsContainer.textContent).toContain('Memory: "Operator is located in KS with timezone CST."');
    expect(receiptsContainer.textContent).toContain("5 memories across: architect, coder, concierge-admin");
    expect(receiptsContainer.textContent).toContain("web_search");
    expect(receiptsContainer.textContent).toContain("bash_executor");
    expect(receiptsContainer.textContent).toContain("suggest_tools");
    expect(receiptsContainer.textContent).toContain('Query: "NFL games today"');
    expect(receiptsContainer.textContent).toContain("Cmd: uname -a");
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
