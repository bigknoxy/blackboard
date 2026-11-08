import React, { useEffect, useState } from "react";
import ConciergeChat from "./ConciergeChat";
import { api } from "../api";

/**
 * CockpitHome Component (Conversational Cockpit Home)
 *
 * Implements the "Chat Front Door" paradigm:
 * 1. Prominent Concierge Admin Agent conversational surface for steering, synthesis & queries.
 * 2. Ambient Cluster Status Bar with real-time stats and jump links (SRP/SOLID).
 * 3. Proactive Human-in-the-Loop review banner when actions await operator decision.
 * 4. Desktop Live Activity Sidebar showing recent Blackboard events with 1-click deep jump.
 */
export default function CockpitHome({
  selectedWorkflowId,
  currentWorkflow,
  agents = [],
  tools = [],
  blackboardEntries = [],
  hitlTickets = [],
  liveEvents = [],
  wsConnected = false,
  onNavigate,
  onRefresh,
}) {
  const [channelStatus, setChannelStatus] = useState(null);

  useEffect(() => {
    async function loadChannels() {
      try {
        if (api.getChannelsStatus) {
          const res = await api.getChannelsStatus();
          setChannelStatus(res);
        }
      } catch {
        // ignore in offline/mock env
      }
    }
    loadChannels();
  }, []);

  const pendingTickets = hitlTickets.filter((t) => t.status === "PENDING");
  const pendingCount = pendingTickets.length;

  // Most recent blackboard entries (top 4)
  const recentEntries = blackboardEntries.slice(0, 4);

  return (
    <div
      data-testid="cockpit-home-view"
      className="space-y-4 max-w-full overflow-x-hidden"
    >
      {/* 1. Ambient Cluster Status & Quick Navigation Bar */}
      <section
        data-testid="cockpit-ambient-status-bar"
        className="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 flex flex-wrap items-center justify-between gap-3 shadow-sm"
      >
        {/* Left: Active Workflow & Cluster Mode */}
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
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
            <span className="text-xs font-mono font-bold text-slate-300">
              {selectedWorkflowId || "code_review_pipeline"}
            </span>
            <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800">
              Active Pipeline
            </span>
          </div>

          {/* External Channels Status Pill */}
          <div
            data-testid="home-external-channels-pill"
            className="flex items-center gap-1.5 text-[10px] font-mono px-2 py-0.5 rounded bg-slate-950 border border-slate-800 text-slate-400"
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
        </div>

        {/* Right: Quick Stat Jump Chips (Touch-friendly >=44px) */}
        <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
          <button
            type="button"
            data-testid="home-stat-agents"
            onClick={() => onNavigate && onNavigate("agents")}
            className="min-h-[44px] px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition flex items-center gap-1.5"
            title="Inspect Agent Personas"
          >
            <span>🤖</span>
            <span>{agents.length} Agents</span>
          </button>

          <button
            type="button"
            data-testid="home-stat-tools"
            onClick={() => onNavigate && onNavigate("tools")}
            className="min-h-[44px] px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition flex items-center gap-1.5"
            title="Inspect MCP Tools Registry"
          >
            <span>🛠️</span>
            <span>{tools.length} Tools</span>
          </button>

          <button
            type="button"
            data-testid="home-stat-approvals"
            onClick={() => onNavigate && onNavigate("approvals")}
            className={`min-h-[44px] px-3 py-1.5 rounded-lg border text-xs font-semibold transition flex items-center gap-1.5 ${
              pendingCount > 0
                ? "bg-amber-950/60 border-amber-500 text-amber-200 shadow-sm shadow-amber-950/50"
                : "bg-slate-950 hover:bg-slate-800 border-slate-800 text-slate-300"
            }`}
            title="Review Human-in-the-Loop Approvals"
          >
            <span>🔒</span>
            <span>{pendingCount} Approvals</span>
            {pendingCount > 0 && (
              <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
            )}
          </button>

          <button
            type="button"
            data-testid="home-stat-board"
            onClick={() => onNavigate && onNavigate("board")}
            className="min-h-[44px] px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition flex items-center gap-1.5"
            title="View Full Live Blackboard Feed"
          >
            <span>📋</span>
            <span>{blackboardEntries.length} Feed</span>
          </button>

          <button
            type="button"
            data-testid="home-stat-workflows"
            onClick={() => onNavigate && onNavigate("workflows")}
            className="min-h-[44px] px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-300 text-xs font-semibold transition flex items-center gap-1.5"
            title="Open BPMN 2.0 Modeler"
          >
            <span>⚡</span>
            <span>BPMN</span>
          </button>
        </div>
      </section>

      {/* 2. Proactive HITL Action Required Alert Banner */}
      {pendingCount > 0 && (
        <section
          data-testid="home-pending-hitl-banner"
          className="bg-amber-950/40 border border-amber-500/60 rounded-xl p-3.5 sm:p-4 flex items-center justify-between gap-3 shadow-md shadow-amber-950/30"
        >
          <div className="flex items-center gap-3">
            <span className="text-xl sm:text-2xl">⚠️</span>
            <div>
              <h3 className="text-xs sm:text-sm font-bold text-amber-200">
                {pendingCount} Action{pendingCount > 1 ? "s" : ""} Awaiting Operator Approval
              </h3>
              <p className="text-[11px] text-amber-300/80">
                Workers halted on protected MCP gates. Review and graduate trust policies.
              </p>
            </div>
          </div>
          <button
            type="button"
            data-testid="home-review-approvals-btn"
            onClick={() => onNavigate && onNavigate("approvals")}
            className="min-h-[44px] px-4 py-2 bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold text-xs rounded-lg shadow transition shrink-0"
          >
            Review Approvals ➔
          </button>
        </section>
      )}

      {/* 3. Main Grid Layout: Primary Conversational Surface + Ambient Sidebar */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* Dominant Conversational Surface (8 cols on desktop, full width on mobile) */}
        <section className="col-span-1 lg:col-span-8 space-y-4">
          <ConciergeChat
            selectedWorkflowId={selectedWorkflowId}
            agents={agents}
            tools={tools}
            onStateMutated={onRefresh}
          />
        </section>

        {/* Ambient Activity & Blackboard Sidebar (4 cols on desktop, hidden on mobile) */}
        <section
          data-testid="home-live-feed-sidebar"
          className="hidden lg:block col-span-4 space-y-4"
        >
          {/* Quick Header */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-2.5">
              <div className="flex items-center gap-2">
                <span className="text-sm">📋</span>
                <h3 className="text-sm font-bold text-white">Live Activity Stream</h3>
              </div>
              <button
                type="button"
                data-testid="home-open-board-btn"
                onClick={() => onNavigate && onNavigate("board")}
                className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold underline"
              >
                Open Full Board ➔
              </button>
            </div>

            {/* Recent Cards */}
            {recentEntries.length === 0 ? (
              <div className="text-xs text-slate-500 text-center py-6">
                No blackboard events posted yet. Use Concierge to trigger a workflow.
              </div>
            ) : (
              <div className="space-y-2.5">
                {recentEntries.map((entry) => (
                  <div
                    key={entry.id}
                    className="p-2.5 rounded-lg bg-slate-950 border border-slate-800/80 hover:border-slate-700 transition text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between text-[10px] font-mono">
                      <span className="px-1.5 py-0.2 rounded font-bold uppercase tracking-wider bg-slate-800 text-slate-300">
                        #{entry.entry_type}
                      </span>
                      <span className="text-slate-400">@{entry.agent_id}</span>
                    </div>
                    <div className="text-slate-300 line-clamp-2 leading-relaxed">
                      {entry.content}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Quick Workflow Stepper Widget */}
          {currentWorkflow && (
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-2.5">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                  Pipeline Nodes ({currentWorkflow.state_graph?.nodes?.length || 0})
                </h4>
                <button
                  type="button"
                  onClick={() => onNavigate && onNavigate("workflows")}
                  className="text-[11px] text-indigo-400 hover:text-indigo-300 underline font-semibold"
                >
                  Edit BPMN ➔
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto pr-1">
                {currentWorkflow.state_graph?.nodes?.map((node) => (
                  <span
                    key={node.id}
                    className="px-2 py-1 rounded bg-slate-950 border border-slate-800 text-[11px] font-mono text-slate-300 flex items-center gap-1"
                  >
                    <span className="text-indigo-400">●</span>
                    <span>{node.name || node.id}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
