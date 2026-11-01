// Dynamically resolve LAN / host IP so accessing from a mobile phone on Wi-Fi
// (e.g. http://192.168.1.50:5173) automatically talks to 192.168.1.50:8000
// instead of failing on phone-local "localhost".
function resolveApiBaseUrl() {
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (
    typeof window !== "undefined" &&
    window.location &&
    window.location.hostname &&
    window.location.hostname !== "localhost" &&
    window.location.hostname !== "127.0.0.1"
  ) {
    const proto = window.location.protocol === "https:" ? "https:" : "http:";
    return `${proto}//${window.location.hostname}:8000/api/v1`;
  }
  return envUrl || "http://localhost:8000/api/v1";
}

function resolveWsUrl() {
  const envWs = import.meta.env.VITE_WS_URL;
  if (
    typeof window !== "undefined" &&
    window.location &&
    window.location.hostname &&
    window.location.hostname !== "localhost" &&
    window.location.hostname !== "127.0.0.1"
  ) {
    const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProto}//${window.location.hostname}:8000/ws/blackboard`;
  }
  return envWs || "ws://localhost:8000/ws/blackboard";
}

export const API_BASE_URL = resolveApiBaseUrl();
export const WS_URL = resolveWsUrl();

async function request(path, options = {}) {
  const baseUrl = resolveApiBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Agents & Memories
  listAgents: () => request("/agents"),
  createAgent: (payload) =>
    request("/agents", { method: "POST", body: JSON.stringify(payload) }),
  updateAgent: (id, payload) =>
    request(`/agents/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteAgent: (id) => request(`/agents/${id}`, { method: "DELETE" }),
  listMemories: (agentId) => request(`/agents/${agentId}/memories`),
  searchMemories: (agentId, query, topK = 3) =>
    request(`/agents/${agentId}/memories/search`, {
      method: "POST",
      body: JSON.stringify({ query, top_k: topK }),
    }),
  createMemory: (agentId, payload) =>
    request(`/agents/${agentId}/memories`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // MCP Tools
  listTools: (agentId) =>
    request(
      agentId ? `/tools?agent_id=${encodeURIComponent(agentId)}` : "/tools"
    ),
  createTool: (payload) =>
    request("/tools", { method: "POST", body: JSON.stringify(payload) }),
  invokeTool: (payload) =>
    request("/tools/invoke", { method: "POST", body: JSON.stringify(payload) }),

  // Workflows & BPMN 2.0
  listWorkflows: () => request("/workflows"),
  getWorkflow: (id) => request(`/workflows/${id}`),
  updateWorkflow: (id, payload) =>
    request(`/workflows/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  convertWorkflow: (payload) =>
    request("/workflows/convert", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  triggerWorkflowRun: (id, initialPrompt, context = {}) =>
    request(`/workflows/${id}/run`, {
      method: "POST",
      body: JSON.stringify({ initial_prompt: initialPrompt, context }),
    }),
  listWorkflowRuns: (id) => request(`/workflows/${id}/runs`),

  // Blackboard & Steering & HITL
  listBlackboard: (workflowId) =>
    request(
      workflowId
        ? `/blackboard?workflow_id=${encodeURIComponent(workflowId)}`
        : "/blackboard"
    ),
  postBlackboardEntry: (payload) =>
    request("/blackboard", { method: "POST", body: JSON.stringify(payload) }),
  postSteering: (payload) =>
    request("/blackboard/steer", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  pruneBlackboard: (workflowId, threshold = 4, keepRecent = 2) =>
    request(
      `/blackboard/prune?workflow_id=${encodeURIComponent(
        workflowId
      )}&threshold=${threshold}&keep_recent=${keepRecent}`,
      { method: "POST" }
    ),
  listHitlTickets: (status) =>
    request(
      status
        ? `/blackboard/hitl?status=${encodeURIComponent(status)}`
        : "/blackboard/hitl"
    ),
  decideHitlTicket: (ticketId, decision, reviewerComment = "") =>
    request(`/blackboard/hitl/${ticketId}/decide`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        reviewer_comment: reviewerComment,
      }),
    }),

  // Concierge Admin Agent
  chatConcierge: (message, workflowId) =>
    request("/chat", {
      method: "POST",
      body: JSON.stringify({ message, workflow_id: workflowId }),
    }),

  // Multi-Tier LLM Router & NVIDIA NIM Gateway
  getLlmStatus: () => request("/llm/status"),
  updateLlmConfig: (payload) =>
    request("/llm/config", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // External Channels & Bridge
  getChannelsStatus: () => request("/channels/status"),
  updateChannelsConfig: (payload) =>
    request("/channels/config", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  sendChannelMessage: (payload) =>
    request("/channels/message", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
