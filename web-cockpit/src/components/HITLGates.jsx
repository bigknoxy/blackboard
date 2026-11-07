import React, { useState, useMemo } from "react";
import { api } from "../api";

export default function HITLGates({
  tickets = [],
  selectedWorkflowId,
  onRefresh,
}) {
  const [commentByTicket, setCommentByTicket] = useState({});
  const [confirmState, setConfirmState] = useState(null); // { ticket, decision }
  const [busyId, setBusyId] = useState(null);
  const [simulating, setSimulating] = useState(false);
  const [filter, setFilter] = useState("all"); // 'all' | 'pending' | 'approved' | 'rejected'
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedIds, setExpandedIds] = useState({});

  const pendingTickets = useMemo(
    () => tickets.filter((t) => t.status === "PENDING"),
    [tickets]
  );
  const approvedTickets = useMemo(
    () => tickets.filter((t) => t.status === "APPROVED"),
    [tickets]
  );
  const rejectedTickets = useMemo(
    () => tickets.filter((t) => t.status === "REJECTED"),
    [tickets]
  );

  const filteredTickets = useMemo(() => {
    let list = tickets;
    if (filter === "pending") list = pendingTickets;
    else if (filter === "approved") list = approvedTickets;
    else if (filter === "rejected") list = rejectedTickets;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(
        (t) =>
          (t.tool_id && t.tool_id.toLowerCase().includes(q)) ||
          (t.agent_id && t.agent_id.toLowerCase().includes(q)) ||
          (t.workflow_id && t.workflow_id.toLowerCase().includes(q)) ||
          (t.status && t.status.toLowerCase().includes(q))
      );
    }
    return list;
  }, [tickets, filter, searchQuery, pendingTickets, approvedTickets, rejectedTickets]);

  function toggleCardExpanded(ticketId) {
    setExpandedIds((prev) => ({
      ...prev,
      [ticketId]: !prev[ticketId],
    }));
  }

  function expandAllResolved() {
    const next = { ...expandedIds };
    tickets.forEach((t) => {
      if (t.status !== "PENDING") next[t.id] = true;
    });
    setExpandedIds(next);
  }

  function minimizeAllResolved() {
    const next = { ...expandedIds };
    tickets.forEach((t) => {
      if (t.status !== "PENDING") next[t.id] = false;
    });
    setExpandedIds(next);
  }

  async function executeConfirmedDecision() {
    if (!confirmState) return;
    const { ticket, decision } = confirmState;
    setBusyId(ticket.id);
    try {
      await api.decideHitlTicket(
        ticket.id,
        decision,
        commentByTicket[ticket.id] ||
          `Operator ${decision.toLowerCase()} via mobile-ready HITL drawer`
      );
      setConfirmState(null);
      if (onRefresh) await onRefresh();
    } finally {
      setBusyId(null);
    }
  }

  async function handleSimulateGatedToolCall() {
    setSimulating(true);
    try {
      await api.invokeTool({
        agent_id: "sec-auditor",
        workflow_id: selectedWorkflowId || "code_review_pipeline",
        task_id: `task-hitl-${Date.now().toString().slice(-4)}`,
        tool_id: "production_release_gate",
        arguments: {
          environment: "production-eu-central",
          release_tag: "v2.4.0-hardened",
          verification_hash: "sha256-42f8c9b1e0d3",
        },
        bypass_approval: false,
      });
      if (onRefresh) await onRefresh();
    } finally {
      setSimulating(false);
    }
  }

  const anyResolvedExpanded = tickets.some(
    (t) => t.status !== "PENDING" && expandedIds[t.id]
  );

  return (
    <div
      data-testid="hitl-mobile-drawer"
      className="bg-slate-900 border border-slate-800 rounded-xl p-4 sm:p-5 space-y-4 w-full max-w-full overflow-x-hidden"
    >
      {/* Top Header & Simulation Trigger */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3.5">
        <div>
          <h2 className="text-base sm:text-lg font-bold text-white flex items-center gap-2">
            <span>🔒</span>
            <span>Human-in-the-Loop Approvals</span>
          </h2>
          <p className="text-xs text-slate-400">
            Audit-safe gatekeeper: review pending actions and inspect execution receipts
          </p>
        </div>
        <button
          type="button"
          onClick={handleSimulateGatedToolCall}
          disabled={simulating}
          className="min-h-[44px] min-w-[44px] px-3.5 py-2 bg-amber-600/20 hover:bg-amber-600/30 border border-amber-500/40 text-amber-300 text-xs font-semibold rounded-lg transition"
        >
          {simulating ? "Queueing..." : "+ Queue Test HITL Gate"}
        </button>
      </div>

      {/* Metric Triaging Banner (Action Required vs Resolved) */}
      <div className="grid grid-cols-3 gap-2 sm:gap-3">
        <button
          type="button"
          onClick={() => setFilter("pending")}
          className={`p-2.5 sm:p-3 rounded-xl border text-left transition ${
            filter === "pending"
              ? "bg-amber-950/40 border-amber-500 shadow-md shadow-amber-950/40"
              : "bg-slate-950/80 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-amber-400">
              Needs Review
            </span>
            {pendingTickets.length > 0 && (
              <span className="animate-pulse w-2 h-2 rounded-full bg-amber-400"></span>
            )}
          </div>
          <div className="text-xl sm:text-2xl font-black text-amber-300 mt-0.5">
            {pendingTickets.length}
          </div>
        </button>

        <button
          type="button"
          onClick={() => setFilter("approved")}
          className={`p-2.5 sm:p-3 rounded-xl border text-left transition ${
            filter === "approved"
              ? "bg-emerald-950/40 border-emerald-500 shadow-md shadow-emerald-950/40"
              : "bg-slate-950/80 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] font-semibold uppercase tracking-wider text-emerald-400">
            Approved
          </div>
          <div className="text-xl sm:text-2xl font-black text-emerald-300 mt-0.5">
            {approvedTickets.length}
          </div>
        </button>

        <button
          type="button"
          onClick={() => setFilter("rejected")}
          className={`p-2.5 sm:p-3 rounded-xl border text-left transition ${
            filter === "rejected"
              ? "bg-rose-950/40 border-rose-500 shadow-md shadow-rose-950/40"
              : "bg-slate-950/80 border-slate-800 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] font-semibold uppercase tracking-wider text-rose-400">
            Rejected
          </div>
          <div className="text-xl sm:text-2xl font-black text-rose-300 mt-0.5">
            {rejectedTickets.length}
          </div>
        </button>
      </div>

      {/* Triaging Filter Controls & Search */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 pt-1">
        <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5">
          <button
            type="button"
            data-testid="hitl-filter-all"
            onClick={() => setFilter("all")}
            className={`min-h-[44px] min-w-[44px] px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              filter === "all"
                ? "bg-indigo-600 text-white shadow"
                : "bg-slate-950 text-slate-400 hover:text-slate-200 border border-slate-800"
            }`}
          >
            All ({tickets.length})
          </button>
          <button
            type="button"
            data-testid="hitl-filter-pending"
            onClick={() => setFilter("pending")}
            className={`min-h-[44px] min-w-[44px] px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 ${
              filter === "pending"
                ? "bg-amber-600 text-white shadow"
                : "bg-slate-950 text-amber-400 hover:text-amber-300 border border-slate-800"
            }`}
          >
            <span>⚠️ Needs Review</span>
            <span className="px-1.5 py-0.2 rounded-full bg-slate-900/60 text-[10px]">
              {pendingTickets.length}
            </span>
          </button>
          <button
            type="button"
            data-testid="hitl-filter-approved"
            onClick={() => setFilter("approved")}
            className={`min-h-[44px] min-w-[44px] px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              filter === "approved"
                ? "bg-emerald-600 text-white shadow"
                : "bg-slate-950 text-emerald-400 hover:text-emerald-300 border border-slate-800"
            }`}
          >
            Approved ({approvedTickets.length})
          </button>
          <button
            type="button"
            data-testid="hitl-filter-rejected"
            onClick={() => setFilter("rejected")}
            className={`min-h-[44px] min-w-[44px] px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              filter === "rejected"
                ? "bg-rose-600 text-white shadow"
                : "bg-slate-950 text-rose-400 hover:text-rose-300 border border-slate-800"
            }`}
          >
            Rejected ({rejectedTickets.length})
          </button>
        </div>

        {/* Search input and bulk minimize/expand toggle */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            data-testid="hitl-search-input"
            placeholder="Search tool or @agent..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="min-h-[44px] flex-1 sm:w-48 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
          />
          {filter !== "pending" && (
            <button
              type="button"
              data-testid="hitl-toggle-all-resolved"
              onClick={anyResolvedExpanded ? minimizeAllResolved : expandAllResolved}
              className="min-h-[44px] min-w-[44px] px-3 py-1.5 rounded-lg bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] font-medium text-slate-300 transition whitespace-nowrap"
            >
              {anyResolvedExpanded ? "Minimize Resolved" : "Expand Resolved"}
            </button>
          )}
        </div>
      </div>

      {/* Ticket List View */}
      {filteredTickets.length === 0 ? (
        <div className="py-12 text-center rounded-xl bg-slate-950/50 border border-slate-800/80 space-y-2">
          <div className="text-2xl">
            {filter === "pending" ? "🎉" : "📋"}
          </div>
          <div className="text-sm font-semibold text-slate-300">
            {filter === "pending"
              ? "All caught up! Zero pending approvals."
              : "No approval tickets match the filter."}
          </div>
          <p className="text-xs text-slate-500 max-w-sm mx-auto">
            {filter === "pending"
              ? "All automated agent tools and gates have been reviewed and decided."
              : "Try switching to 'All' or clearing the search query."}
          </p>
          {filter === "pending" && tickets.length > 0 && (
            <button
              type="button"
              onClick={() => setFilter("all")}
              className="mt-2 min-h-[44px] px-3.5 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/40 text-xs font-semibold transition"
            >
              View Resolved History ({approvedTickets.length + rejectedTickets.length})
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {filteredTickets.map((ticket) => {
            const isPending = ticket.status === "PENDING";
            const isExpanded = isPending || Boolean(expandedIds[ticket.id]);

            return (
              <div
                key={ticket.id}
                data-testid={`hitl-ticket-card-${ticket.id}`}
                className={`rounded-xl border transition-all duration-150 ${
                  isPending
                    ? "bg-amber-950/20 border-amber-500/50 shadow-lg shadow-amber-950/20 p-4"
                    : "bg-slate-950/80 border-slate-800 hover:border-slate-700 p-3 sm:p-4"
                }`}
              >
                {/* Card Header Bar */}
                <div
                  className="flex items-center justify-between gap-2 cursor-pointer select-none"
                  onClick={() => !isPending && toggleCardExpanded(ticket.id)}
                >
                  <div className="flex items-center gap-2 flex-wrap min-w-0">
                    <span
                      className={`text-xs font-mono px-2 py-0.5 rounded font-bold ${
                        ticket.status === "PENDING"
                          ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                          : ticket.status === "APPROVED"
                          ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                          : "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                      }`}
                    >
                      {ticket.status}
                    </span>

                    <span className="text-sm font-bold text-white font-mono truncate">
                      {ticket.tool_id}
                    </span>

                    <span className="text-xs text-slate-400 font-mono">
                      &bull; @{ticket.agent_id}
                    </span>

                    {/* Exit code summary for resolved tickets */}
                    {!isPending && ticket.execution_result && (
                      <span
                        className={`text-[10px] font-mono px-1.5 py-0.2 rounded ${
                          ticket.execution_result.exit_code === 0
                            ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                            : "bg-rose-950 text-rose-400 border border-rose-800"
                        }`}
                      >
                        Exit {ticket.execution_result.exit_code ?? 0}
                      </span>
                    )}
                  </div>

                  {/* Toggle Caret for Resolved (Minimized) Tickets */}
                  {!isPending && (
                    <button
                      type="button"
                      data-testid={`hitl-toggle-card-${ticket.id}`}
                      aria-label={isExpanded ? "Collapse ticket details" : "Expand ticket details"}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleCardExpanded(ticket.id);
                      }}
                      className="min-h-[44px] min-w-[44px] flex items-center justify-center text-slate-400 hover:text-slate-200 transition"
                    >
                      <span className="text-xs font-mono">
                        {isExpanded ? "▲" : "▼"}
                      </span>
                    </button>
                  )}
                </div>

                {/* Sub-header context */}
                <div className="flex items-center gap-2 text-[11px] text-slate-400 font-mono mt-1 mb-2">
                  <span>Gate: {ticket.gate_type}</span>
                  {ticket.workflow_id && (
                    <span>&bull; Pipeline: {ticket.workflow_id}</span>
                  )}
                </div>

                {/* Minimized preview snippet for resolved items */}
                {!isPending && !isExpanded && (
                  <div
                    onClick={() => toggleCardExpanded(ticket.id)}
                    className="text-xs text-slate-400 font-mono truncate bg-slate-900/60 p-2 rounded cursor-pointer hover:text-slate-200"
                  >
                    {ticket.tool_id === "bash_executor" && ticket.tool_arguments?.command
                      ? `$ ${ticket.tool_arguments.command}`
                      : ticket.reviewer_comment
                      ? `Note: ${ticket.reviewer_comment}`
                      : JSON.stringify(ticket.tool_arguments || {})}
                  </div>
                )}

                {/* Full Expanded Content (Always shown for PENDING, toggled for RESOLVED) */}
                {isExpanded && (
                  <div className="mt-3 space-y-3 pt-2 border-t border-slate-800/80">
                    {/* Bash Command Preview or JSON Payload */}
                    {ticket.tool_id === "bash_executor" && ticket.tool_arguments?.command ? (
                      <div
                        data-testid={`hitl-bash-preview-${ticket.id}`}
                        className="rounded-lg border border-slate-700 bg-black/90 p-3 font-mono text-xs"
                      >
                        <div className="flex items-center justify-between border-b border-slate-800 pb-1.5 mb-2 text-[10px] text-slate-400">
                          <span className="flex items-center gap-1.5">
                            <span className="w-2 h-2 rounded-full bg-rose-500"></span>
                            <span className="w-2 h-2 rounded-full bg-amber-500"></span>
                            <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
                            <span className="font-semibold text-slate-300 ml-1">Terminal Command Preview</span>
                          </span>
                          <span
                            className={`text-[10px] font-bold tracking-wider ${
                              isPending ? "text-amber-400" : "text-slate-400"
                            }`}
                          >
                            {isPending ? "HITL GATED" : "EXECUTED"}
                          </span>
                        </div>
                        <div className="text-emerald-400 font-bold whitespace-pre-wrap break-all">
                          <span className="text-slate-500 select-none">$ </span>
                          {ticket.tool_arguments.command}
                        </div>
                      </div>
                    ) : (
                      <pre className="text-xs bg-slate-950 p-2.5 rounded border border-slate-800 text-slate-300 overflow-x-auto font-mono">
                        {JSON.stringify(ticket.tool_arguments, null, 2)}
                      </pre>
                    )}

                    {/* Execution Result Box (when present) */}
                    {ticket.execution_result && (
                      <div
                        data-testid={`hitl-exec-result-${ticket.id}`}
                        className="p-3 rounded-lg border border-slate-700/80 bg-slate-950 font-mono text-xs space-y-1.5"
                      >
                        <div className="flex items-center justify-between text-[11px] font-semibold">
                          <span className="text-slate-300 flex items-center gap-1">
                            <span>⚡</span>
                            <span>Execution Output</span>
                          </span>
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] ${
                              ticket.execution_result.exit_code === 0
                                ? "bg-emerald-950 text-emerald-300 border border-emerald-800"
                                : "bg-rose-950 text-rose-300 border border-rose-800"
                            }`}
                          >
                            Exit Code: {ticket.execution_result.exit_code ?? 0}
                          </span>
                        </div>
                        {ticket.execution_result.stdout && (
                          <div className="text-emerald-300 whitespace-pre-wrap break-all bg-black/60 p-2 rounded border border-slate-900">
                            {ticket.execution_result.stdout}
                          </div>
                        )}
                        {ticket.execution_result.stderr && (
                          <div className="text-rose-300 whitespace-pre-wrap break-all bg-black/60 p-2 rounded border border-slate-900">
                            {ticket.execution_result.stderr}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Reviewer Note or Action Form */}
                    {isPending ? (
                      <div className="space-y-2.5 pt-1">
                        <input
                          type="text"
                          placeholder="Reviewer audit note (optional for approve, recommended for reject)..."
                          value={commentByTicket[ticket.id] || ""}
                          onChange={(e) =>
                            setCommentByTicket((prev) => ({
                              ...prev,
                              [ticket.id]: e.target.value,
                            }))
                          }
                          className="min-h-[44px] w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                        />
                        <div className="grid grid-cols-2 gap-2.5">
                          <button
                            type="button"
                            data-testid={`hitl-approve-btn-${ticket.id}`}
                            onClick={() =>
                              setConfirmState({ ticket, decision: "APPROVED" })
                            }
                            disabled={busyId === ticket.id}
                            className="min-h-[44px] min-w-[44px] py-2.5 px-3 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold rounded-lg shadow transition"
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            data-testid={`hitl-reject-btn-${ticket.id}`}
                            onClick={() =>
                              setConfirmState({ ticket, decision: "REJECTED" })
                            }
                            disabled={busyId === ticket.id}
                            className="min-h-[44px] min-w-[44px] py-2.5 px-3 bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold rounded-lg shadow transition"
                          >
                            Reject with Note
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400 pt-1">
                        <div>
                          Reviewer note:{" "}
                          <span className="text-slate-200">
                            {ticket.reviewer_comment || "N/A"}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => toggleCardExpanded(ticket.id)}
                          className="text-[11px] text-indigo-400 hover:text-indigo-300"
                        >
                          Collapse details ▲
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Safety Confirmation Dialog for Accidental Mobile Taps */}
      {confirmState && (
        <div
          data-testid="hitl-confirmation-dialog"
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-end sm:items-center justify-center p-3"
        >
          <div className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-2xl p-5 space-y-4 shadow-2xl">
            <h3 className="text-base font-bold text-white">
              Confirm {confirmState.decision === "APPROVED" ? "Approval" : "Rejection"}
            </h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              Are you sure you want to mark tool{" "}
              <code className="text-indigo-300 font-mono font-bold">
                {confirmState.ticket.tool_id}
              </code>{" "}
              for agent <code>@{confirmState.ticket.agent_id}</code> as{" "}
              <strong>{confirmState.decision}</strong>?
            </p>
            {confirmState.ticket.tool_arguments?.command && (
              <div className="text-xs font-mono bg-black/60 p-2 rounded text-emerald-400 border border-slate-800 truncate">
                $ {confirmState.ticket.tool_arguments.command}
              </div>
            )}
            <div className="flex gap-2.5 pt-2">
              <button
                type="button"
                onClick={() => setConfirmState(null)}
                className="min-h-[44px] min-w-[44px] flex-1 rounded-lg bg-slate-800 text-xs font-semibold text-slate-200"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="hitl-confirm-submit-btn"
                onClick={executeConfirmedDecision}
                className={`min-h-[44px] min-w-[44px] flex-1 rounded-lg text-xs font-bold text-white ${
                  confirmState.decision === "APPROVED"
                    ? "bg-emerald-600 hover:bg-emerald-500"
                    : "bg-rose-600 hover:bg-rose-500"
                }`}
              >
                Confirm {confirmState.decision}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
