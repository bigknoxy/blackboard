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

  function toggleExpand(id) {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
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
            {/* Sticky Filter Tag Bar (#spec, #critique, #code, #steering) with >=44x44px touch targets */}
        <div
          role="toolbar"
          aria-label="Filter Blackboard Artifacts"
          className="sticky top-0 z-20 bg-slate-900/95 backdrop-blur py-2.5 flex items-center gap-2 overflow-x-auto border-b border-slate-800/80"
        >
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

        {/* Operator Steering Injection Bar */}
        <form
          onSubmit={handleInjectSteering}
          className="mt-3 flex flex-col sm:flex-row gap-2 bg-slate-950/80 p-2.5 rounded-lg border border-rose-500/30"
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
              const isExpanded = Boolean(expandedIds[entry.id]);
              return (
                <article
                  key={entry.id}
                  data-testid="blackboard-entry-card"
                  className="p-3.5 sm:p-4 rounded-xl bg-slate-950/90 border border-slate-800 hover:border-slate-700 transition"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`text-[11px] font-mono uppercase px-2 py-0.5 rounded border ${badgeClass}`}
                      >
                        #{entry.entry_type}
                      </span>
                      <span className="text-xs font-semibold text-indigo-300">
                        @{entry.agent_id}
                      </span>
                      <span className="text-xs text-slate-500 font-mono">
                        {entry.task_id}
                      </span>
                    </div>
                    <span className="text-[11px] text-slate-500">
                      {entry.created_at
                        ? new Date(entry.created_at).toLocaleTimeString()
                        : ""}
                    </span>
                  </div>

                  <pre className="text-xs sm:text-sm text-slate-200 whitespace-pre-wrap break-words font-sans leading-relaxed">
                    {entry.content}
                  </pre>

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
                      {isExpanded ? "Hide Metadata" : "Inspect Metadata"}
                    </button>
                  </div>

                  {isExpanded && (
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
