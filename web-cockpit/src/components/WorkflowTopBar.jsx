import React, { useState, useEffect } from "react";
import { api } from "../api";

/**
 * WorkflowTopBar Component
 *
 * Persistent top-of-page workflow ribbon anchored across ALL views.
 * Provides instant workflow switching, one-tap execution, BPMN modeling jump,
 * "+ New Workflow" meta-workflow generator shortcut, and ambient telemetry.
 */
export default function WorkflowTopBar({
  workflows = [],
  selectedWorkflowId,
  onSelectWorkflow,
  currentWorkflow,
  agents = [],
  tools = [],
  hitlTickets = [],
  backlogCount = 0,
  wsConnected = false,
  onNavigate,
  onOpenBacklog,
  onRefresh,
}) {
  const [running, setRunning] = useState(false);
  const [channelStatus, setChannelStatus] = useState(null);

  useEffect(() => {
    async function fetchChannels() {
      try {
        if (api.getChannelsStatus) {
          const res = await api.getChannelsStatus();
          setChannelStatus(res);
        }
      } catch {
        // ignore offline
      }
    }
    fetchChannels();
  }, []);

  const pendingTickets = (hitlTickets || []).filter((t) => t.status === "PENDING");
  const pendingCount = pendingTickets.length;

  async function handleQuickRun() {
    if (!selectedWorkflowId) return;
    setRunning(true);
    try {
      if (api.triggerWorkflow) {
        await api.triggerWorkflow(selectedWorkflowId, {
          initial_prompt: "Quick run triggered from Workflow Top Bar",
        });
      }
      if (onRefresh) await onRefresh();
    } catch (err) {
      console.warn("Failed to trigger workflow:", err);
    } finally {
      setRunning(false);
    }
  }

  function handleCreateWorkflow() {
    if (onNavigate) {
      onNavigate("home", {
        prefillPrompt:
          "Create a new workflow with architect design and security verification for ",
      });
    }
  }

  const activeNodesCount = currentWorkflow?.state_graph?.nodes?.length || 0;

  return (
    <div
      data-testid="workflow-top-bar"
      className="sticky top-0 z-20 bg-slate-950/95 backdrop-blur border-b border-slate-800 px-3 py-2 sm:px-4 shadow-sm"
    >
      <div className="max-w-[1600px] mx-auto flex flex-wrap items-center justify-between gap-2.5">
        {/* Left: Active Workflow Status & Switcher */}
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1">
            <span className="relative flex h-2.5 w-2.5">
              {wsConnected && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              )}
              <span
                className={`relative inline-flex rounded-full h-2.5 w-2.5 ${
                  wsConnected ? "bg-emerald-500" : "bg-rose-500"
                }`}
              ></span>
            </span>
            <span className="text-[10px] uppercase font-bold tracking-wider text-indigo-400 font-mono">
              Workflow:
            </span>
            <select
              aria-label="Active Workflow Switcher"
              value={selectedWorkflowId || ""}
              onChange={(e) => onSelectWorkflow && onSelectWorkflow(e.target.value)}
              className="bg-transparent text-xs font-mono font-bold text-slate-200 outline-none cursor-pointer max-w-[170px] sm:max-w-xs truncate"
            >
              {workflows.map((w) => (
                <option key={w.id} value={w.id} className="bg-slate-900 text-slate-200">
                  {w.name} ({w.id})
                </option>
              ))}
            </select>
            {activeNodesCount > 0 && (
              <span className="hidden sm:inline-block px-1.5 py-0.2 rounded text-[10px] font-mono bg-slate-800 text-slate-400">
                {activeNodesCount} nodes
              </span>
            )}
          </div>

          {/* Quick Action Buttons */}
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              data-testid="topbar-run-workflow-btn"
              onClick={handleQuickRun}
              disabled={running}
              className="min-h-[36px] px-2.5 py-1 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/40 text-emerald-300 text-xs font-semibold flex items-center gap-1 transition"
              title="Execute Active Workflow"
            >
              <span>{running ? "⏳" : "▶"}</span>
              <span className="hidden sm:inline">{running ? "Running..." : "Run"}</span>
            </button>

            <button
              type="button"
              data-testid="topbar-bpmn-btn"
              onClick={() => onNavigate && onNavigate("workflows")}
              className="min-h-[36px] px-2.5 py-1 rounded-lg bg-indigo-950/80 hover:bg-indigo-900 border border-indigo-800 text-indigo-300 text-xs font-semibold flex items-center gap-1 transition"
              title="Inspect Workflow BPMN 2.0 Diagram"
            >
              <span>⚡</span>
              <span className="hidden sm:inline">BPMN</span>
            </button>

            <button
              type="button"
              data-testid="topbar-new-workflow-btn"
              onClick={handleCreateWorkflow}
              className="min-h-[36px] px-2.5 py-1 rounded-lg bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white text-xs font-semibold flex items-center gap-1 shadow-sm transition"
              title="Synthesize New Workflow via Architect & Verification Sub-Workflow"
            >
              <span>+</span>
              <span className="hidden md:inline">New Workflow</span>
            </button>
          </div>
        </div>

        {/* Right: Ambient Telemetry & Navigation Chips */}
        <div className="flex items-center gap-1 sm:gap-2 flex-wrap text-xs">
          {/* External Gateway Status */}
          <div
            data-testid="topbar-gateway-status"
            className="hidden md:flex items-center gap-1 px-2 py-1 rounded bg-slate-900 border border-slate-800 text-[10px] font-mono text-slate-400"
          >
            <span>📡 Gateway:</span>
            <span
              className={
                channelStatus?.telegram?.configured
                  ? "text-sky-400 font-bold"
                  : "text-slate-500"
              }
            >
              TG {channelStatus?.telegram?.configured ? "✓" : "off"}
            </span>
            <span>•</span>
            <span
              className={
                channelStatus?.discord?.configured
                  ? "text-indigo-400 font-bold"
                  : "text-slate-500"
              }
            >
              DC {channelStatus?.discord?.configured ? "✓" : "off"}
            </span>
          </div>

          {/* Quick Telemetry Chips (Touch-friendly >=36px on top bar) */}
          <button
            type="button"
            data-testid="topbar-stat-agents"
            onClick={() => onNavigate && onNavigate("agents")}
            className="min-h-[36px] px-2.5 py-1 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition flex items-center gap-1"
            title="Inspect Agent Personas"
          >
            <span>🤖</span>
            <span>{agents.length}</span>
            <span className="hidden lg:inline text-slate-400 font-normal">Agents</span>
          </button>

          <button
            type="button"
            data-testid="topbar-stat-tools"
            onClick={() => onNavigate && onNavigate("tools")}
            className="min-h-[36px] px-2.5 py-1 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition flex items-center gap-1"
            title="Inspect MCP Tools Registry"
          >
            <span>🛠️</span>
            <span>{tools.length}</span>
            <span className="hidden lg:inline text-slate-400 font-normal">Tools</span>
          </button>

          <button
            type="button"
            data-testid="topbar-stat-approvals"
            onClick={() => onNavigate && onNavigate("approvals")}
            className={`min-h-[36px] px-2.5 py-1 rounded-lg border text-xs font-semibold transition flex items-center gap-1 ${
              pendingCount > 0
                ? "bg-amber-950/70 border-amber-500 text-amber-200 shadow-sm animate-pulse"
                : "bg-slate-900 hover:bg-slate-800 border-slate-800 text-slate-300"
            }`}
            title="Human-in-the-Loop Approvals"
          >
            <span>🔒</span>
            <span>{pendingCount}</span>
            {pendingCount > 0 && <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>}
            <span className="hidden lg:inline text-slate-400 font-normal">Approvals</span>
          </button>

          {/* Backlog launcher */}
          <button
            type="button"
            data-testid="topbar-stat-backlog"
            onClick={() => onOpenBacklog && onOpenBacklog()}
            className="min-h-[36px] px-2.5 py-1 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition flex items-center gap-1"
            title="Open Ideas & Backlog"
          >
            <span>💡</span>
            <span>{backlogCount}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
