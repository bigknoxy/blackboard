import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import BlackboardGraphCanvas from "./BlackboardGraphCanvas";

const BADGE_STYLES = {
  hypothesis: "bg-purple-500/20 text-purple-300 border-purple-500/40",
  spec: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  code: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  test_result: "bg-teal-500/20 text-teal-300 border-teal-500/40",
  critique: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  steering: "bg-rose-500/20 text-rose-300 border-rose-500/50",
  summary: "bg-indigo-500/20 text-indigo-300 border-indigo-500/40",
};

const FILTER_TAGS = [
  { id: "ALL", label: "All" },
  { id: "spec", label: "#spec" },
  { id: "critique", label: "#critique" },
  { id: "code", label: "#code" },
  { id: "steering", label: "#steering" },
  { id: "hypothesis", label: "#hypothesis" },
  { id: "test_result", label: "#test_result" },
];

const PAGE_SIZE = 20;

/**
 * Human-readable relative time formatter (e.g. "just now", "15s ago", "2m ago", "1h ago", "2d ago")
 */
export function formatRelativeTime(dateString, nowTimestamp = Date.now()) {
  if (!dateString) return "just now";
  const then = new Date(dateString).getTime();
  if (isNaN(then) || then === 0) return "just now";
  const diffSec = Math.max(0, Math.floor((nowTimestamp - then) / 1000));
  if (diffSec < 10) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMonth = Math.floor(diffDay / 30);
  if (diffMonth < 12) return `${diffMonth}mo ago`;
  return `${Math.floor(diffMonth / 12)}y ago`;
}

export default function BlackboardFeed({
  entries = [],
  selectedWorkflowId,
  onSelectWorkflow,
  workflows = [],
  onRefresh,
  liveEvents = [],
}) {
  const [steeringText, setSteeringText] = useState("");
  const [filterType, setFilterType] = useState("ALL");
  const [workflowFilter, setWorkflowFilter] = useState("ALL"); // Default: Entire view across all workflows
  const [page, setPage] = useState(1);
  const [expandedIds, setExpandedIds] = useState({});
  const [userToggledCards, setUserToggledCards] = useState({});
  const [expandedContentIds, setExpandedContentIds] = useState({});
  const [showSteeringInput, setShowSteeringInput] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [showTelemetry, setShowTelemetry] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [pruning, setPruning] = useState(false);
  const [activeTab, setActiveTab] = useState("feed"); // "feed" | "dag_graph"
  const [now, setNow] = useState(() => Date.now());

  // Dynamic 15s interval tick so relative timestamps ("5s ago", "1m ago") update live without reload
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);

  // Compute entry count per workflow for badge chips
  const workflowStats = useMemo(() => {
    const counts = {};
    for (const e of entries) {
      const wId = e.workflow_id || "unassigned";
      counts[wId] = (counts[wId] || 0) + 1;
    }
    return counts;
  }, [entries]);

  // Combined list of workflow options (All + known from entries + known from registered workflows)
  const availableWorkflows = useMemo(() => {
    const set = new Set();
    workflows.forEach((w) => w.id && set.add(w.id));
    entries.forEach((e) => e.workflow_id && set.add(e.workflow_id));
    return Array.from(set);
  }, [workflows, entries]);

  // Sort entries reverse-chronologically (most recent at the top)
  const sortedEntries = useMemo(() => {
    return [...entries].sort((a, b) => {
      const timeA = a.created_at ? new Date(a.created_at).getTime() : 0;
      const timeB = b.created_at ? new Date(b.created_at).getTime() : 0;
      return timeB - timeA;
    });
  }, [entries]);

  // Filter by both entry type and workflow (defaults to entire view when workflowFilter === "ALL")
  const filteredEntries = useMemo(() => {
    return sortedEntries.filter((item) => {
      const matchesType =
        filterType === "ALL" ? true : item.entry_type === filterType;
      const matchesWf =
        workflowFilter === "ALL" ? true : item.workflow_id === workflowFilter;
      return matchesType && matchesWf;
    });
  }, [sortedEntries, filterType, workflowFilter]);

  // Paginated window for optimal mobile frame rates
  const totalPages = Math.max(1, Math.ceil(filteredEntries.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visibleEntries = useMemo(() => {
    const startIdx = (currentPage - 1) * PAGE_SIZE;
    return filteredEntries.slice(startIdx, startIdx + PAGE_SIZE);
  }, [filteredEntries, currentPage]);

  // Detect currently active/running tasks from live stream events
  const activeTaskIds = useMemo(() => {
    const active = new Set();
    const finished = new Set();
    for (const evt of liveEvents) {
      const tid = evt.task_id || (evt.data && evt.data.task_id);
      if (!tid) continue;
      const name = evt.event || "";
      if (
        name.includes("completed") ||
        name.includes("released") ||
        name.includes("finish") ||
        name.includes("failed")
      ) {
        finished.add(tid);
      } else if (
        name.includes("start") ||
        name.includes("acquired") ||
        name.includes("running") ||
        name.includes("progress")
      ) {
        if (!finished.has(tid)) {
          active.add(tid);
        }
      }
    }
    return active;
  }, [liveEvents]);

  function isEntryInProgress(entry) {
    if (!entry) return false;
    const meta = entry.metadata_ || entry.metadata || {};
    if (
      meta.status === "in_progress" ||
      meta.status === "running" ||
      meta.in_progress === true
    ) {
      return true;
    }
    if (
      entry.status === "in_progress" ||
      entry.status === "running" ||
      entry.in_progress === true
    ) {
      return true;
    }
    if (entry.task_id && activeTaskIds.has(entry.task_id)) {
      return true;
    }
    return false;
  }

  function toggleExpand(id) {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  function toggleCardCollapse(id, isCurrentlyExpanded) {
    setUserToggledCards((prev) => ({
      ...prev,
      [id]: !isCurrentlyExpanded,
    }));
  }

  function toggleContentExpand(id) {
    setExpandedContentIds((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  function handleCollapseAll() {
    const all = {};
    visibleEntries.forEach((e) => {
      all[e.id] = false;
    });
    setUserToggledCards(all);
  }

  function handleExpandAll() {
    const all = {};
    const allContent = {};
    visibleEntries.forEach((e) => {
      all[e.id] = true;
      allContent[e.id] = true;
    });
    setUserToggledCards(all);
    setExpandedContentIds(allContent);
  }

  async function handleCopyContent(entry) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(entry.content);
      }
      setCopiedId(entry.id);
      setTimeout(() => setCopiedId(null), 1500);
    } catch {
      setCopiedId(entry.id);
    }
  }

  async function handleInjectSteering(e) {
    e.preventDefault();
    if (!steeringText.trim()) return;
    setSubmitting(true);
    try {
      const targetWf =
        workflowFilter !== "ALL"
          ? workflowFilter
          : selectedWorkflowId || "code_review_pipeline";
      await api.postSteering({
        workflow_id: targetWf,
        content: steeringText.trim(),
      });
      setSteeringText("");
      if (onRefresh) await onRefresh();
    } finally {
      setSubmitting(false);
    }
  }

  async function handlePruneDebate() {
    setPruning(true);
    try {
      const targetWf =
        workflowFilter !== "ALL"
          ? workflowFilter
          : selectedWorkflowId || "code_review_pipeline";
      await api.pruneBlackboard(targetWf, 3, 2);
      if (onRefresh) await onRefresh();
    } finally {
      setPruning(false);
    }
  }

  function handleSelectWorkflowPill(wfId) {
    setWorkflowFilter(wfId);
    setPage(1);
    if (onSelectWorkflow && wfId !== "ALL") {
      onSelectWorkflow(wfId);
    }
  }

  return (
    <div className="space-y-4 w-full max-w-full overflow-x-hidden">
      <div className="flex flex-col bg-slate-900/95 border border-slate-800 rounded-xl p-3 sm:p-5 shadow-lg">
        {/* Header & Controls */}
        <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base sm:text-lg font-semibold text-white">
                Live Blackboard Feed
              </h2>
              <span className="px-2 py-0.5 rounded-full text-xs font-mono font-medium bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                {filteredEntries.length} {workflowFilter === "ALL" ? "Total" : workflowFilter}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Full board view · newest at top · live relative timestamps
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* View Mode Switcher */}
            <div className="flex bg-slate-950 p-1 rounded-lg border border-slate-800 text-xs">
              <button
                type="button"
                onClick={() => setActiveTab("feed")}
                className={`min-h-[36px] px-3 py-1 rounded-md font-medium transition ${
                  activeTab === "feed"
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                Feed
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("dag_graph")}
                className={`min-h-[36px] px-3 py-1 rounded-md font-medium transition ${
                  activeTab === "dag_graph"
                    ? "bg-cyan-600 text-white"
                    : "text-slate-400 hover:text-white"
                }`}
              >
                DAG Graph
              </button>
            </div>

            <button
              onClick={handlePruneDebate}
              disabled={pruning}
              className="min-h-[44px] min-w-[44px] px-3 py-2 text-xs font-semibold rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 transition"
            >
              {pruning ? "Pruning..." : "Condense"}
            </button>
            <button
              onClick={() => setShowTelemetry((prev) => !prev)}
              className="min-h-[44px] min-w-[44px] px-3 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition"
            >
              {showTelemetry ? "Hide Bus" : `Bus (${liveEvents.length})`}
            </button>
          </div>
        </div>

        {activeTab === "dag_graph" ? (
          <div className="mt-4">
            <BlackboardGraphCanvas
              workflowId={workflowFilter !== "ALL" ? workflowFilter : selectedWorkflowId}
            />
          </div>
        ) : (
          <>
            {/* 1. Workflow Filter Carousel (Defaults to "All Workflows" for unconstrained overview) */}
            <div className="pt-3 pb-2 border-b border-slate-800/60">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                  <span>🌐</span>
                  <span>Pipeline Scope:</span>
                </span>
                {workflowFilter !== "ALL" && (
                  <button
                    type="button"
                    onClick={() => handleSelectWorkflowPill("ALL")}
                    className="text-[11px] text-indigo-400 hover:text-indigo-300 underline font-medium"
                  >
                    Reset to All Workflows
                  </button>
                )}
              </div>
              <div
                role="group"
                aria-label="Workflow Scope Filter"
                className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5"
              >
                <button
                  type="button"
                  data-testid="filter-workflow-all"
                  onClick={() => handleSelectWorkflowPill("ALL")}
                  className={`min-h-[38px] px-3 py-1.5 rounded-lg text-xs font-medium border transition shrink-0 flex items-center gap-1.5 ${
                    workflowFilter === "ALL"
                      ? "bg-indigo-600 text-white border-indigo-500 shadow-sm"
                      : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700 hover:text-white"
                  }`}
                >
                  <span>🌐 All Workflows</span>
                  <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-black/30 font-mono">
                    {entries.length}
                  </span>
                </button>
                {availableWorkflows.map((wfId) => {
                  const active = workflowFilter === wfId;
                  const count = workflowStats[wfId] || 0;
                  return (
                    <button
                      key={wfId}
                      type="button"
                      data-testid={`filter-workflow-${wfId}`}
                      onClick={() => handleSelectWorkflowPill(wfId)}
                      className={`min-h-[38px] px-3 py-1.5 rounded-lg text-xs font-medium border transition shrink-0 flex items-center gap-1.5 ${
                        active
                          ? "bg-indigo-600 text-white border-indigo-500 shadow-sm"
                          : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700 hover:text-white"
                      }`}
                    >
                      <span className="font-mono">{wfId}</span>
                      <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-black/30 font-mono">
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* 2. Sticky Entry Type Filter Tag Bar + Collapse All/Expand All Controls */}
            <div
              role="toolbar"
              aria-label="Filter Blackboard Artifacts"
              className="sticky top-0 z-20 bg-slate-900/95 backdrop-blur py-2.5 flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80"
            >
              <div className="flex items-center gap-2 overflow-x-auto no-scrollbar">
                {FILTER_TAGS.map((tag) => {
                  const active = filterType === tag.id;
                  return (
                    <button
                      key={tag.id}
                      data-testid={`filter-tag-${tag.id.toLowerCase()}`}
                      onClick={() => {
                        setFilterType(tag.id);
                        setPage(1);
                      }}
                      className={`min-h-[44px] min-w-[44px] shrink-0 px-3.5 py-2 rounded-lg text-xs font-mono font-semibold border transition ${
                        active
                          ? "bg-indigo-600 text-white border-indigo-500 shadow"
                          : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
                      }`}
                    >
                      {tag.label}
                    </button>
                  );
                })}
              </div>

              {/* Master Collapse / Expand Controls */}
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  type="button"
                  data-testid="collapse-all-btn"
                  onClick={handleCollapseAll}
                  title="Collapse all visible cards to 1-line summaries"
                  className="min-h-[36px] px-2.5 py-1 text-xs font-medium rounded-md bg-slate-950 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-800 transition"
                >
                  Collapse All
                </button>
                <button
                  type="button"
                  data-testid="expand-all-btn"
                  onClick={handleExpandAll}
                  title="Expand all visible cards"
                  className="min-h-[36px] px-2.5 py-1 text-xs font-medium rounded-md bg-slate-950 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-800 transition"
                >
                  Expand All
                </button>
              </div>
            </div>

            {/* Operator Steering Injection Bar */}
            <div className="mt-3">
              <div className="flex items-center justify-between">
                <button
                  type="button"
                  data-testid="toggle-steering-btn"
                  onClick={() => setShowSteeringInput((prev) => !prev)}
                  className="min-h-[38px] px-3 py-1.5 text-xs font-semibold rounded-lg bg-rose-950/40 hover:bg-rose-900/40 text-rose-300 border border-rose-800/40 transition flex items-center gap-1.5"
                >
                  <span>⚡ Live Operator Steering</span>
                  <span className="text-[10px]">{showSteeringInput ? "▲" : "▼"}</span>
                </button>
              </div>

              {showSteeringInput && (
                <form
                  onSubmit={handleInjectSteering}
                  className="mt-2 flex flex-col sm:flex-row gap-2 bg-slate-950/80 p-2.5 rounded-lg border border-rose-500/30"
                >
                  <input
                    type="text"
                    value={steeringText}
                    onChange={(e) => setSteeringText(e.target.value)}
                    placeholder="Inject live Operator Steering (`#steering`) for active agents..."
                    className="min-h-[44px] flex-1 bg-transparent px-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none"
                  />
                  <button
                    type="submit"
                    disabled={submitting}
                    className="min-h-[44px] min-w-[44px] px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold rounded-md transition"
                  >
                    {submitting ? "Injecting..." : "Steer Agents"}
                  </button>
                </form>
              )}
            </div>

            {/* Windowed / Paginated Card Feed */}
            <div className="mt-4 space-y-3">
              {visibleEntries.length === 0 ? (
                <div className="text-center py-10 text-slate-500 text-sm">
                  No blackboard artifacts match this filter.
                </div>
              ) : (
                visibleEntries.map((entry) => {
                  const badgeClass =
                    BADGE_STYLES[entry.entry_type] ||
                    "bg-slate-700 text-slate-200 border-slate-600";
                  const inProgress = isEntryInProgress(entry);
                  const isExpandedMeta = Boolean(expandedIds[entry.id]);
                  const isContentExpanded = Boolean(expandedContentIds[entry.id]);
                  const userChoice = userToggledCards[entry.id];
                  const isCardExpanded =
                    userChoice !== undefined ? userChoice : inProgress;
                  const isCardCollapsed = !isCardExpanded;
                  const lines = entry.content ? entry.content.split("\n") : [];
                  const isLongContent =
                    lines.length > 4 || (entry.content && entry.content.length > 220);
                  const relTime = formatRelativeTime(entry.created_at, now);
                  const fullDateStr = entry.created_at
                    ? new Date(entry.created_at).toLocaleString()
                    : "";

                  // 1. Sleek 1-line collapsed summary chip
                  if (isCardCollapsed) {
                    return (
                      <article
                        key={entry.id}
                        data-testid="blackboard-entry-card"
                        className="p-2.5 sm:p-3 rounded-xl bg-slate-950/80 border border-slate-800/80 hover:border-slate-700 transition flex items-center justify-between gap-2 cursor-pointer w-full"
                        onClick={() => toggleCardCollapse(entry.id, false)}
                      >
                        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 flex-1 overflow-hidden">
                          <span
                            className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border shrink-0 ${badgeClass}`}
                          >
                            #{entry.entry_type}
                          </span>
                          <span className="text-xs font-semibold text-indigo-300 shrink-0">
                            @{entry.agent_id}
                          </span>
                          {entry.workflow_id && (
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400 shrink-0 max-w-[120px] truncate hidden xs:inline">
                              {entry.workflow_id}
                            </span>
                          )}
                          {inProgress && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-500/10 text-amber-300 border border-amber-500/30 shrink-0">
                              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                              In Progress
                            </span>
                          )}
                          <span className="text-xs text-slate-400 truncate">
                            {lines[0] || "(empty payload)"}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0 ml-auto">
                          <span
                            title={fullDateStr}
                            className="inline-flex items-center gap-1 text-[11px] text-slate-400 font-mono bg-slate-900/90 px-2 py-0.5 rounded border border-slate-800 shrink-0"
                          >
                            <span className="text-[10px] text-indigo-400">🕒</span>
                            <span>{relTime}</span>
                          </span>
                          <button
                            type="button"
                            data-testid="uncollapse-card-btn"
                            aria-label="Expand entry card"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleCardCollapse(entry.id, false);
                            }}
                            className="min-h-[36px] min-w-[36px] flex items-center justify-center rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-white border border-slate-800 text-xs font-mono transition"
                          >
                            ▼
                          </button>
                        </div>
                      </article>
                    );
                  }

                  // 2. Full or line-clamped card
                  return (
                    <article
                      key={entry.id}
                      data-testid="blackboard-entry-card"
                      className="p-3.5 sm:p-4 rounded-xl bg-slate-950/90 border border-slate-800 hover:border-slate-700 transition"
                    >
                      {/* Consistent Header with Fixed Top-Right Caret Button & Relative Time */}
                      <div className="flex items-center justify-between gap-2 mb-2.5 w-full">
                        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 flex-1 overflow-hidden flex-wrap">
                          <span
                            className={`text-[10px] sm:text-[11px] font-mono uppercase px-2 py-0.5 rounded border shrink-0 ${badgeClass}`}
                          >
                            #{entry.entry_type}
                          </span>
                          <span className="text-xs font-semibold text-indigo-300 shrink-0">
                            @{entry.agent_id}
                          </span>
                          {entry.workflow_id && (
                            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400 shrink-0 truncate max-w-[140px]">
                              {entry.workflow_id}
                            </span>
                          )}
                          {inProgress && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-500/10 text-amber-300 border border-amber-500/30 shrink-0">
                              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                              In Progress
                            </span>
                          )}
                          {entry.task_id && (
                            <span className="text-[11px] text-slate-500 font-mono truncate hidden sm:inline">
                              {entry.task_id}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0 ml-auto">
                          <span
                            title={fullDateStr}
                            className="inline-flex items-center gap-1 text-[11px] text-slate-400 font-mono bg-slate-900/90 px-2 py-0.5 rounded border border-slate-800 shrink-0"
                          >
                            <span className="text-[10px] text-indigo-400">🕒</span>
                            <span>{relTime}</span>
                          </span>
                          <button
                            type="button"
                            data-testid="collapse-card-btn"
                            aria-label="Collapse entry to 1 line"
                            title="Collapse entry to 1 line"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleCardCollapse(entry.id, true);
                            }}
                            className="min-h-[36px] min-w-[36px] flex items-center justify-center rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-white border border-slate-800 text-xs font-mono transition"
                          >
                            ▲
                          </button>
                        </div>
                      </div>

                      {/* Content block with Line-Clamping & Gradient Fade */}
                      <div className="relative">
                        <pre
                          className={`text-xs sm:text-sm text-slate-200 whitespace-pre-wrap break-words font-sans leading-relaxed ${
                            isLongContent && !isContentExpanded
                              ? "max-h-[110px] overflow-hidden"
                              : ""
                          }`}
                        >
                          {entry.content}
                        </pre>

                        {isLongContent && !isContentExpanded && (
                          <div className="absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-slate-950 to-transparent pointer-events-none" />
                        )}
                      </div>

                      {/* Show More / Show Less Toggle Button */}
                      {isLongContent && (
                        <div className="mt-1.5 flex justify-center">
                          <button
                            type="button"
                            data-testid={isContentExpanded ? "show-less-btn" : "show-more-btn"}
                            onClick={() => toggleContentExpand(entry.id)}
                            className="min-h-[36px] px-3 py-1 rounded-md bg-indigo-950/60 hover:bg-indigo-900/60 text-indigo-300 border border-indigo-800/50 text-xs font-medium transition flex items-center gap-1.5"
                          >
                            <span>
                              {isContentExpanded
                                ? "Show Less"
                                : `Show More (${lines.length} lines)`}
                            </span>
                            <span className="text-[10px]">
                              {isContentExpanded ? "▲" : "▼"}
                            </span>
                          </button>
                        </div>
                      )}

                      {/* Touch-friendly >=44x44px Action Row */}
                      <div className="mt-3 pt-2 border-t border-slate-900 flex items-center justify-end gap-2">
                        <button
                          type="button"
                          data-testid="copy-diff-btn"
                          onClick={() => handleCopyContent(entry)}
                          className="min-h-[44px] min-w-[44px] px-3 py-2 rounded-lg bg-slate-900 hover:bg-slate-800 text-xs font-medium text-slate-300 border border-slate-800 transition"
                        >
                          {copiedId === entry.id ? "Copied!" : "Copy Diff"}
                        </button>
                        <button
                          type="button"
                          data-testid="expand-entry-btn"
                          onClick={() => toggleExpand(entry.id)}
                          className="min-h-[44px] min-w-[44px] px-3 py-2 rounded-lg bg-slate-900 hover:bg-slate-800 text-xs font-medium text-indigo-300 border border-slate-800 transition"
                        >
                          {isExpandedMeta ? "Hide Metadata" : "Inspect Metadata"}
                        </button>
                      </div>

                      {isExpandedMeta && (
                        <div
                          data-testid="expanded-entry-metadata"
                          className="mt-2 p-3 rounded-lg bg-slate-900/90 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto"
                        >
                          <div>Entry ID: {entry.id}</div>
                          <div>Workflow: {entry.workflow_id}</div>
                          <div>Agent: @{entry.agent_id}</div>
                          <div>
                            Created: {fullDateStr} ({relTime})
                          </div>
                          <div>
                            Metadata: {JSON.stringify(entry.metadata_ || entry.metadata || {})}
                          </div>
                        </div>
                      )}
                    </article>
                  );
                })
              )}
            </div>

            {/* Pagination / Virtual Window Controls for 50+ Event Streams */}
            {filteredEntries.length > PAGE_SIZE && (
              <div className="mt-4 pt-3 border-t border-slate-800 flex items-center justify-between gap-2">
                <button
                  type="button"
                  data-testid="pagination-prev-btn"
                  disabled={currentPage <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="min-h-[44px] min-w-[44px] px-4 py-2 rounded-lg bg-slate-800 disabled:opacity-40 text-xs font-semibold text-slate-200"
                >
                  Previous
                </button>
                <span className="text-xs text-slate-400 font-mono">
                  Page {currentPage} of {totalPages} ({filteredEntries.length} items)
                </span>
                <button
                  type="button"
                  data-testid="pagination-next-btn"
                  disabled={currentPage >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  className="min-h-[44px] min-w-[44px] px-4 py-2 rounded-lg bg-indigo-600 disabled:opacity-40 text-xs font-semibold text-white"
                >
                  Next Window
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Collapsible Live Redis Pub/Sub Telemetry Panel */}
      {showTelemetry && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white mb-2">
            Live Redis Stream Bus (<code>channel:blackboard:events</code>)
          </h3>
          <div className="space-y-2 max-h-[260px] overflow-y-auto text-xs font-mono">
            {liveEvents.map((ev, idx) => (
              <div
                key={idx}
                className="p-2 rounded bg-slate-950 border border-slate-800 text-slate-300 break-all"
              >
                {JSON.stringify(ev)}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
