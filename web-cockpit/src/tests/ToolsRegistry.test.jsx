import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ToolsRegistry from "../components/ToolsRegistry";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    updateTool: vi.fn().mockResolvedValue({ id: "web_search", requires_approval: true }),
    invokeTool: vi.fn().mockResolvedValue({
      status: "EXECUTED",
      result: {
        tool: "web_search",
        query: "blackboard architecture",
        results: [{ title: "Research Result", url: "https://docs.local" }],
      },
    }),
    resolveToolChain: vi.fn().mockResolvedValue({
      tool_chain: ["repo_search", "ast_complexity_analyzer"],
    }),
    createTool: vi.fn().mockResolvedValue({ id: "doc_crawler", name: "doc_crawler" }),
  },
}));

describe("ToolsRegistry - MCP Skills & Tools Registry UX", () => {
  const mockTools = [
    {
      id: "web_search",
      name: "web_search",
      description: "Execute live web and documentation search.",
      server_name: "research-mcp",
      transport: "builtin",
      consumes_type: "search_query",
      produces_type: "search_results",
      requires_approval: false,
    },
    {
      id: "repo_search",
      name: "repo_search",
      description: "Search workspace code.",
      server_name: "workspace-mcp",
      transport: "builtin",
      consumes_type: "query_keyword",
      produces_type: "code_snippet",
      requires_approval: false,
    },
    {
      id: "git_branch_commit",
      name: "git_branch_commit",
      description: "Commit verified code.",
      server_name: "git-mcp",
      transport: "builtin",
      consumes_type: "verified_artifact",
      produces_type: "git_commit_sha",
      requires_approval: true,
    },
  ];

  it("renders tools roster, server badges, and input/output capability contracts", () => {
    render(<ToolsRegistry tools={mockTools} agents={[]} />);

    expect(screen.getByTestId("tools-registry-container")).toBeDefined();
    expect(screen.getByText("3 Tools Active")).toBeDefined();
    expect(screen.getByText(/Execute live web and documentation search/)).toBeDefined();

    // Check capability type badges
    expect(screen.getByText("in: search_query")).toBeDefined();
    expect(screen.getByText("out: search_results")).toBeDefined();
  });

  it("toggles HITL approval requirement live with API call", async () => {
    const onToolUpdated = vi.fn();
    render(<ToolsRegistry tools={mockTools} agents={[]} onToolUpdated={onToolUpdated} />);

    const toggleBtn = screen.getByTestId("toggle-hitl-web_search");
    expect(toggleBtn.textContent).toContain("Autonomous");

    fireEvent.click(toggleBtn);
    await waitFor(() => {
      expect(api.updateTool).toHaveBeenCalledWith("web_search", { requires_approval: true });
      expect(onToolUpdated).toHaveBeenCalled();
    });
  });

  it("opens the interactive tester modal and executes live tool invocation", async () => {
    render(<ToolsRegistry tools={mockTools} agents={[]} />);

    const testBtn = screen.getByTestId("test-tool-btn-web_search");
    fireEvent.click(testBtn);

    // Tester modal should open
    expect(screen.getByTestId("tool-tester-modal")).toBeDefined();
    const argsInput = screen.getByTestId("tester-arguments-input");
    expect(argsInput.value).toContain("multi-agent blackboard architecture");

    // Execute tool
    const execBtn = screen.getByTestId("execute-test-btn");
    fireEvent.click(execBtn);

    await waitFor(() => {
      expect(api.invokeTool).toHaveBeenCalledWith(
        expect.objectContaining({
          tool_id: "web_search",
          agent_id: "operator",
        })
      );
      expect(screen.getByTestId("tester-result-box")).toBeDefined();
      expect(screen.getByText("EXECUTED")).toBeDefined();
    });
  });

  it("resolves tool chain composition paths via Capability Graph pathfinding", async () => {
    render(<ToolsRegistry tools={mockTools} agents={[]} />);

    const findBtn = screen.getByTestId("find-chain-btn");
    fireEvent.click(findBtn);

    await waitFor(() => {
      expect(api.resolveToolChain).toHaveBeenCalled();
      const banner = screen.getByTestId("resolved-chain-banner");
      expect(banner).toBeDefined();
      expect(banner.textContent).toContain("repo_search");
      expect(banner.textContent).toContain("ast_complexity_analyzer");
    });
  });
});
