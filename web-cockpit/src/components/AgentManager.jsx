import React, { useState, useEffect } from "react";
import { api } from "../api";

const LAN_MODEL_PRESETS = [
  { label: "qwen2.5-coder:7b", provider: "ollama", url: "http://192.168.1.150:11434" },
  { label: "qwen3-4b", provider: "openai", url: "http://192.168.1.180:8081/v1" },
  { label: "groot-19b", provider: "openai", url: "http://192.168.1.180:8080/v1" },
  { label: "lfm3-1.7b", provider: "openai", url: "http://192.168.1.180:8090/v1" },
  { label: "mistral-24b", provider: "openai", url: "http://192.168.1.149:8080/v1" },
  { label: "meta/llama-3.3-70b-instruct", provider: "nvidia_nim", url: "https://integrate.api.nvidia.com/v1" },
];

export default function AgentManager({
  agents = [],
  tools = [],
  onAgentUpdated,
}) {
  const [selectedAgentId, setSelectedAgentId] = useState(
    agents[0]?.id || "architect"
  );
  const [editPrompt, setEditPrompt] = useState("");
  const [editRole, setEditRole] = useState("");
  const [editName, setEditName] = useState("");
  const [editProvider, setEditProvider] = useState("ollama");
  const [editModelName, setEditModelName] = useState("");
  const [editBaseUrl, setEditBaseUrl] = useState("");
  const [editTemperature, setEditTemperature] = useState(0.2);
  const [editAllowedTools, setEditAllowedTools] = useState([]);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newAgentId, setNewAgentId] = useState("");
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentRole, setNewAgentRole] = useState("");
  const [newAgentPrompt, setNewAgentPrompt] = useState("");
  const [creating, setCreating] = useState(false);

  const selectedAgent = agents.find((a) => a.id === selectedAgentId);

  useEffect(() => {
    if (selectedAgent) {
      setEditName(selectedAgent.name || "");
      setEditRole(selectedAgent.role || "");
      setEditPrompt(selectedAgent.system_prompt || "");
      const cfg = selectedAgent.inference_config || {};
      setEditProvider(cfg.provider || "ollama");
      setEditModelName(cfg.model_name || "qwen2.5-coder:7b");
      setEditBaseUrl(cfg.base_url || "");
      setEditTemperature(cfg.temperature ?? 0.2);
      setEditAllowedTools(selectedAgent.allowed_tools || []);
      setSaveStatus(null);
    }
  }, [selectedAgentId, selectedAgent]);

  function handleToggleTool(toolId) {
    setEditAllowedTools((prev) =>
      prev.includes(toolId)
        ? prev.filter((t) => t !== toolId)
        : [...prev, toolId]
    );
  }

  function handleApplyPreset(preset) {
    setEditModelName(preset.label);
    setEditProvider(preset.provider);
    setEditBaseUrl(preset.url);
  }

  async function handleSaveProfile(e) {
    e?.preventDefault();
    if (!selectedAgent) return;
    setSaving(true);
    setSaveStatus(null);
    try {
      const payload = {
        name: editName,
        role: editRole,
        system_prompt: editPrompt,
        allowed_tools: editAllowedTools,
        inference_config: {
          provider: editProvider,
          model_name: editModelName,
          base_url: editBaseUrl || undefined,
          temperature: parseFloat(editTemperature),
        },
      };
      await api.updateAgent(selectedAgent.id, payload);
      setSaveStatus({ ok: true, msg: "Profile saved & Redis cache invalidated!" });
      if (onAgentUpdated) await onAgentUpdated();
    } catch (err) {
      setSaveStatus({ ok: false, msg: `Save failed: ${err.message}` });
    } finally {
      setSaving(false);
    }
  }

  async function handleCreateAgent(e) {
    e?.preventDefault();
    if (!newAgentId.trim()) return;
    setCreating(true);
    try {
      const payload = {
        id: newAgentId.trim().toLowerCase(),
        name: newAgentName.trim() || newAgentId.trim(),
        role: newAgentRole.trim() || "Autonomous Specialist",
        system_prompt: newAgentPrompt.trim() || "You are a specialized agent.",
        allowed_tools: ["repo_search"],
        inference_config: {
          provider: "ollama",
          model_name: "qwen2.5-coder:7b",
          temperature: 0.2,
        },
      };
      await api.createAgent(payload);
      setShowCreateModal(false);
      setNewAgentId("");
      setNewAgentName("");
      setNewAgentRole("");
      setNewAgentPrompt("");
      if (onAgentUpdated) await onAgentUpdated();
      setSelectedAgentId(payload.id);
    } catch (err) {
      alert(`Create agent failed: ${err.message}`);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div
      data-testid="agent-manager-view"
      className="space-y-4 w-full max-w-full overflow-x-hidden text-slate-100"
    >
      {/* Top Header Card */}
      <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-4 sm:p-5 shadow-lg flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-indigo-400 animate-pulse"></span>
            <h2 className="text-base sm:text-lg font-semibold text-white">
              Agent Persona &amp; Model Roster
            </h2>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Zero-Hardcoded Personas &bull; Live Model Hot-Swapping &bull; Tool RBAC
          </p>
        </div>

        <button
          type="button"
          data-testid="create-agent-btn"
          onClick={() => setShowCreateModal(true)}
          className="min-h-[44px] min-w-[44px] px-3.5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg transition flex items-center justify-center gap-1.5 shadow"
        >
          <span>+</span>
          <span>Spawn Custom Agent</span>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Left Column: Agent Selector List */}
        <div className="lg:col-span-4 space-y-2">
          <div className="text-xs font-medium text-slate-400 uppercase tracking-wider px-1">
            Active Personas ({agents.length})
          </div>

          <div className="space-y-2">
            {agents.map((ag) => {
              const isSelected = ag.id === selectedAgentId;
              const cfg = ag.inference_config || {};
              return (
                <button
                  key={ag.id}
                  type="button"
                  data-testid={`agent-card-${ag.id}`}
                  onClick={() => setSelectedAgentId(ag.id)}
                  className={`w-full text-left p-3.5 rounded-xl border transition flex flex-col gap-1 min-h-[44px] ${
                    isSelected
                      ? "bg-slate-800/90 border-indigo-500 shadow-sm"
                      : "bg-slate-900/80 border-slate-800 hover:border-slate-700 hover:bg-slate-800/40"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-indigo-300 font-mono">
                      @{ag.id}
                    </span>
                    <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-950 border border-slate-800 text-slate-400">
                      {cfg.provider || "ollama"}
                    </span>
                  </div>
                  <div className="text-xs text-slate-300 font-medium">
                    {ag.role || ag.name}
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-500 font-mono mt-1">
                    <span className="truncate max-w-[180px]">{cfg.model_name || "default"}</span>
                    <span>{(ag.allowed_tools || []).length} tools</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Column: Profile Editor Pane */}
        <div className="lg:col-span-8">
          {selectedAgent ? (
            <form
              onSubmit={handleSaveProfile}
              data-testid="agent-edit-form"
              className="bg-slate-900/95 border border-slate-800 rounded-xl p-4 sm:p-5 shadow-lg space-y-4"
            >
              {/* Header Info */}
              <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800">
                <div>
                  <h3 className="text-sm sm:text-base font-semibold text-white flex items-center gap-2">
                    <span className="text-indigo-400 font-mono">@{selectedAgent.id}</span>
                    <span className="text-slate-500 text-xs font-normal">/</span>
                    <span className="text-slate-300 text-xs">{editRole}</span>
                  </h3>
                  <p className="text-[11px] text-slate-400">
                    Mutations apply live to PostgreSQL and Redis cache
                  </p>
                </div>

                <button
                  type="submit"
                  disabled={saving}
                  data-testid="save-agent-btn"
                  className="min-h-[44px] min-w-[44px] px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg transition flex items-center gap-1.5 shadow"
                >
                  {saving ? "Saving..." : "Save Profile & Hot-Swap"}
                </button>
              </div>

              {saveStatus && (
                <div
                  data-testid="agent-save-feedback"
                  className={`p-2.5 rounded-lg text-xs font-mono border ${
                    saveStatus.ok
                      ? "bg-emerald-950/60 border-emerald-700 text-emerald-300"
                      : "bg-rose-950/60 border-rose-700 text-rose-300"
                  }`}
                >
                  {saveStatus.msg}
                </div>
              )}

              {/* Name & Role Fields */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">
                    Display Name
                  </label>
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">
                    Role Description
                  </label>
                  <input
                    type="text"
                    value={editRole}
                    onChange={(e) => setEditRole(e.target.value)}
                    className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100"
                  />
                </div>
              </div>

              {/* Model & Inference Configuration */}
              <div className="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                    Model &amp; LAN Endpoint Routing
                  </span>
                  <span className="text-[11px] text-slate-500 font-mono">
                    temp: {editTemperature}
                  </span>
                </div>

                {/* LAN Presets */}
                <div>
                  <div className="text-[11px] text-slate-400 mb-1.5">Quick LAN Cluster Presets:</div>
                  <div className="flex flex-wrap gap-1.5">
                    {LAN_MODEL_PRESETS.map((p) => (
                      <button
                        key={p.label}
                        type="button"
                        onClick={() => handleApplyPreset(p)}
                        className={`min-h-[32px] px-2.5 py-1 rounded text-[11px] font-mono border transition ${
                          editModelName === p.label
                            ? "bg-indigo-600 text-white border-indigo-500"
                            : "bg-slate-900 text-slate-300 border-slate-800 hover:border-slate-700"
                        }`}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1 font-mono">
                      Provider
                    </label>
                    <select
                      value={editProvider}
                      onChange={(e) => setEditProvider(e.target.value)}
                      className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-2 text-xs text-slate-200 font-mono"
                    >
                      <option value="ollama">ollama</option>
                      <option value="openai">openai (llama.cpp)</option>
                      <option value="nvidia_nim">nvidia_nim</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1 font-mono">
                      Model Identifier
                    </label>
                    <input
                      type="text"
                      data-testid="agent-model-input"
                      value={editModelName}
                      onChange={(e) => setEditModelName(e.target.value)}
                      className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] text-slate-400 mb-1 font-mono">
                      Endpoint Base URL
                    </label>
                    <input
                      type="text"
                      value={editBaseUrl}
                      onChange={(e) => setEditBaseUrl(e.target.value)}
                      placeholder="e.g. http://192.168.1.180:8081/v1"
                      className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono"
                    />
                  </div>
                </div>
              </div>

              {/* System Prompt */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  System Prompt (Persona Instruction)
                </label>
                <textarea
                  rows={6}
                  data-testid="agent-prompt-input"
                  value={editPrompt}
                  onChange={(e) => setEditPrompt(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 focus:border-indigo-500 rounded-xl p-3 text-xs sm:text-sm text-slate-200 font-mono leading-relaxed"
                />
              </div>

              {/* Tool RBAC Matrix */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs font-medium text-slate-400">
                    Allowed MCP Tools (Strict RBAC Guard)
                  </label>
                  <span className="text-[11px] text-slate-500 font-mono">
                    {editAllowedTools.length} enabled
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {tools.map((t) => {
                    const isChecked = editAllowedTools.includes(t.id);
                    return (
                      <label
                        key={t.id}
                        data-testid={`tool-toggle-${t.id}`}
                        className={`min-h-[44px] flex items-center justify-between p-2.5 rounded-lg border cursor-pointer transition select-none ${
                          isChecked
                            ? "bg-slate-950 border-indigo-500/70 text-slate-200"
                            : "bg-slate-950/60 border-slate-800/80 text-slate-500 hover:border-slate-700"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => handleToggleTool(t.id)}
                            className="w-4 h-4 rounded text-indigo-600 focus:ring-0 cursor-pointer"
                          />
                          <span className="text-xs font-mono font-medium">
                            {t.id}
                          </span>
                        </div>
                        {t.requires_approval && (
                          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-950/70 border border-amber-800 text-amber-300">
                            HITL Gate
                          </span>
                        )}
                      </label>
                    );
                  })}
                </div>
              </div>
            </form>
          ) : (
            <div className="text-center py-12 text-slate-500 text-sm">
              Select an agent persona from the left to view and edit.
            </div>
          )}
        </div>
      </div>

      {/* Spawn Custom Agent Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-md w-full p-5 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <h3 className="text-sm font-semibold text-white">
                Spawn New Agent Persona
              </h3>
              <button
                type="button"
                onClick={() => setShowCreateModal(false)}
                className="text-slate-400 hover:text-white text-xs font-mono"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateAgent} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  Agent ID (Unique handle, e.g. "data-analyst")
                </label>
                <input
                  type="text"
                  required
                  value={newAgentId}
                  onChange={(e) => setNewAgentId(e.target.value)}
                  placeholder="e.g. data-analyst"
                  className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  Display Name
                </label>
                <input
                  type="text"
                  value={newAgentName}
                  onChange={(e) => setNewAgentName(e.target.value)}
                  placeholder="Data Analyst Agent"
                  className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  Role
                </label>
                <input
                  type="text"
                  value={newAgentRole}
                  onChange={(e) => setNewAgentRole(e.target.value)}
                  placeholder="Metrics & Data Pipeline Specialist"
                  className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">
                  System Prompt
                </label>
                <textarea
                  rows={3}
                  value={newAgentPrompt}
                  onChange={(e) => setNewAgentPrompt(e.target.value)}
                  placeholder="Instructions for this agent..."
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-slate-200 font-mono"
                />
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="min-h-[44px] flex-1 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating || !newAgentId.trim()}
                  className="min-h-[44px] flex-1 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg transition"
                >
                  {creating ? "Spawning..." : "Create Agent"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
