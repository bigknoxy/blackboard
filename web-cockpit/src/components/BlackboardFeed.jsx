import React, { useMemo, useState } from "react";
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

export default function BlackboardFeed({
  entries = [],
  selectedWorkflowId,
  onRefresh,
  liveEvents = [],
}) {
  const [steeringText, setSteeringText] = useState("");
  const [filterType, setFilterType] = useState("ALL");
  const [page, setPage] = useState(1);
  const [expandedIds, setExpandedIds] = useState({});
  // Track explicit operator clicks: id -> boolean (true = explicitly expanded, false = explicitly collapsed)
  // When an id is not in userToggledCards, it defaults to COLLAPSED unless in_progress
  const [userToggledCards, setUserToggledCards] = useState({});
  const [expandedContentIds, setExpandedContentIds] = useState({});
  const [showSteeringInput, setShowSteeringInput] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [showTelemetry, setShowTelemetry] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [pruning, setPruning] = useState(false);
  const [activeTab, setActiveTab] = useState("feed"); // "feed" | "dag_graph"

  const filteredEntries = useMemo(
    () =>
      entries.filter((item) =>
        filterType === "ALL" ? true : item.entry_type === filterType
      ),
    [entries, filterType]
  );

  // Paginated / virtualized window so mobile browsers never drop frames on 50+ events
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
      all[e.id] = false; // explicitly collapsed
    });
    setUserToggledCards(all);
  }

  function handleExpandAll() {
    const all = {};
    const allContent = {};
    visibleEntries.forEach((e) => {
      all[e.id] = true; // explicitly expanded
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
      await api.postSteering({
        workflow_id: selectedWorkflowId || "code_review_pipeline",
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
      await api.pruneBlackboard(
        selectedWorkflowId || "code_review_pipeline",
        3,
        2
      );
      if (onRefresh) await onRefresh();
    } finally {
      setPruning(false);
    }
  }

  return (
    <div className="space-y-4 w-full max-w-full overflow-x-hidden">
      <div className="flex flex-col bg-slate-900/95 border border-slate-800 rounded-xl p-3 sm:p-5 shadow-lg">
        {/* Header & Janitor Prune Action */}
        <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800">
          <div>
            <h2 className="text-base sm:text-lg font-semibold text-white">
              Live Blackboard Feed ({filteredEntries.length})
            </h2>
            <p className="text-xs text-slate-400">
              Touch-friendly artifact cards with windowed rendering
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
            <BlackboardGraphCanvas workflowId={selectedWorkflowId} />
          </div>
        ) : (
          <>
            {/* Toolbar: Sticky Filter Tag Bar + Collapse All/Expand All Controls */}
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

            {/* Operator Steering Injection Bar (Collapsible to save mobile space) */}
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
                  // Defaults to COLLAPSED unless in progress.
                  // Once not in progress, only expanded if explicitly opened by user ("looking into it").
                  const isCardExpanded =
                    userChoice !== undefined ? userChoice : inProgress;
                  const isCardCollapsed = !isCardExpanded;
                  const lines = entry.content ? entry.content.split("\n") : [];
                  const isLongContent =
                    lines.length > 4 || (entry.content && entry.content.length > 220);

                  // 1. Sleek 1-line collapsed summary chip
                  if (isCardCollapsed) {
                    return (
                      <article
                        key={entry.id}
                        data-testid="blackboard-entry-card"
                        className="p-2.5 sm:p-3 rounded-xl bg-slate-950/80 border border-slate-800/80 hover:border-slate-700 transition flex items-center justify-between gap-2 cursor-pointer w-full"
                        onClick={() => toggleCardCollapse(entry.id, false)}
                      >
                        <div className="flex items-center gap-2 min-w-0 flex-1 overflow-hidden">
                          <span
                            className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded border shrink-0 ${badgeClass}`}
                          >
                            #{entry.entry_type}
                          </span>
                          <span className="text-xs font-semibold text-indigo-300 shrink-0">
                            @{entry.agent_id}
                          </span>
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
                        <div className="flex items-center gap-2 shrink-0 ml-auto">
                          <span className="text-[11px] text-slate-500 hidden sm:inline font-mono">
                            {entry.created_at
                              ? new Date(entry.created_at).toLocaleTimeString()
                              : ""}
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
                      {/* Consistent Header with Fixed Top-Right Caret Button */}
                      <div className="flex items-center justify-between gap-2 mb-2.5 w-full">
                        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 flex-1 overflow-hidden">
                          <span
                            className={`text-[10px] sm:text-[11px] font-mono uppercase px-2 py-0.5 rounded border shrink-0 ${badgeClass}`}
                          >
                            #{entry.entry_type}
                          </span>
                          <span className="text-xs font-semibold text-indigo-300 shrink-0">
                            @{entry.agent_id}
                          </span>
                          {inProgress && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-500/10 text-amber-300 border border-amber-500/30 shrink-0">
                              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                              In Progress
                            </span>
                          )}
                          {entry.task_id && (
                            <span className="text-[11px] text-slate-500 font-mono truncate">
                              {entry.task_id}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 shrink-0 ml-auto">
                          <span className="text-[11px] text-slate-500 hidden sm:inline font-mono">
                            {entry.created_at
                              ? new Date(entry.created_at).toLocaleTimeString()
                              : ""}
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
                            Metadata: {JSON.stringify(entry.metadata || {})}
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
