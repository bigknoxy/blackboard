import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import AgentManager from "../components/AgentManager";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    updateAgent: vi.fn().mockResolvedValue({ id: "coder", status: "updated" }),
    createAgent: vi.fn().mockResolvedValue({ id: "tester", status: "created" }),
  },
}));

describe("AgentManager - Persona Roster, Model Hot-Swapping & RBAC Editor", () => {
  const sampleAgents = [
    {
      id: "architect",
      name: "Lead System Architect",
      role: "System Specifications & DAG Topology",
      system_prompt: "You are the lead architect.",
      inference_config: {
        provider: "openai",
        model_name: "qwen3-4b",
        base_url: "http://192.168.1.180:8081/v1",
        temperature: 0.1,
      },
      allowed_tools: ["repo_search"],
    },
    {
      id: "coder",
      name: "Autonomous Core Engineer",
      role: "FastAPI & Async Coder",
      system_prompt: "You write clean async python code.",
      inference_config: {
        provider: "ollama",
        model_name: "qwen2.5-coder:7b",
        base_url: "http://192.168.1.150:11434",
        temperature: 0.2,
      },
      allowed_tools: ["repo_search", "git_commit_push"],
    },
  ];

  const sampleTools = [
    { id: "repo_search", requires_approval: false },
    { id: "git_commit_push", requires_approval: true },
    { id: "ast_complexity_analyzer", requires_approval: false },
  ];

  it("renders active agent roster and populates selected persona editor", () => {
    render(<AgentManager agents={sampleAgents} tools={sampleTools} />);

    expect(screen.getByTestId("agent-card-architect")).toBeDefined();
    expect(screen.getByTestId("agent-card-coder")).toBeDefined();

    // Default selected agent is architect
    const promptInput = screen.getByTestId("agent-prompt-input");
    expect(promptInput.value).toBe("You are the lead architect.");

    const modelInput = screen.getByTestId("agent-model-input");
    expect(modelInput.value).toBe("qwen3-4b");
  });

  it("switches selected persona on click and updates editor form", () => {
    render(<AgentManager agents={sampleAgents} tools={sampleTools} />);

    // Click on @coder card
    fireEvent.click(screen.getByTestId("agent-card-coder"));

    const promptInput = screen.getByTestId("agent-prompt-input");
    expect(promptInput.value).toBe("You write clean async python code.");

    const modelInput = screen.getByTestId("agent-model-input");
    expect(modelInput.value).toBe("qwen2.5-coder:7b");
  });

  it("toggles tool RBAC and calls api.updateAgent on save", async () => {
    const onAgentUpdated = vi.fn();
    render(
      <AgentManager
        agents={sampleAgents}
        tools={sampleTools}
        onAgentUpdated={onAgentUpdated}
      />
    );

    // Switch to coder
    fireEvent.click(screen.getByTestId("agent-card-coder"));

    // Toggle ast_complexity_analyzer tool on
    const astToggle = screen.getByTestId("tool-toggle-ast_complexity_analyzer");
    fireEvent.click(astToggle);

    // Change model to mistral-24b using preset
    const presetBtn = screen.getByText("mistral-24b");
    fireEvent.click(presetBtn);

    const modelInput = screen.getByTestId("agent-model-input");
    expect(modelInput.value).toBe("mistral-24b");

    // Click Save
    const saveBtn = screen.getByTestId("save-agent-btn");
    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(api.updateAgent).toHaveBeenCalledWith(
      "coder",
      expect.objectContaining({
        allowed_tools: expect.arrayContaining(["repo_search", "git_commit_push", "ast_complexity_analyzer"]),
        inference_config: expect.objectContaining({
          model_name: "mistral-24b",
          provider: "openai",
        }),
      })
    );

    expect(screen.getByTestId("agent-save-feedback")).toBeDefined();
    expect(onAgentUpdated).toHaveBeenCalled();
  });

  it("opens create custom agent modal and submits new persona", async () => {
    const onAgentUpdated = vi.fn();
    render(
      <AgentManager
        agents={sampleAgents}
        tools={sampleTools}
        onAgentUpdated={onAgentUpdated}
      />
    );

    const createBtn = screen.getByTestId("create-agent-btn");
    fireEvent.click(createBtn);

    expect(screen.getByText("Spawn New Agent Persona")).toBeDefined();

    const idInput = screen.getByPlaceholderText("e.g. data-analyst");
    fireEvent.change(idInput, { target: { value: "data-analyst" } });

    const submitBtn = screen.getByText("Create Agent");
    await act(async () => {
      fireEvent.click(submitBtn);
    });

    expect(api.createAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "data-analyst",
      })
    );
  });
});
