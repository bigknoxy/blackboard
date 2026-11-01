import React, { useState } from "react";
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
        agent_id: "coder",
        workflow_id: selectedWorkflowId || "code_review_pipeline",
        task_id: `task-hitl-${Date.now().toString().slice(-4)}`,
        tool_id: "git_branch_commit",
        arguments: {
          branch: "release/hardened-blackboard",
          commit_message: "feat: enforce Redis SET NX EX 60 task leases & RBAC",
        },
        bypass_approval: false,
      });
      if (onRefresh) await onRefresh();
    } finally {
      setSimulating(false);
    }
  }

  return (
    <div
      data-testid="hitl-mobile-drawer"
      className="bg-slate-900 border border-slate-800 rounded-xl p-4 sm:p-5 space-y-4 w-full max-w-full overflow-x-hidden"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
        <div>
          <h2 className="text-base sm:text-lg font-semibold text-white">
            HITL Approvals & Steering Drawer
          </h2>
          <p className="text-xs text-slate-400">
            Thumb-accessible sign-off with confirmation guard
          </p>
        </div>
        <button
          type="button"
          onClick={handleSimulateGatedToolCall}
          disabled={simulating}
          className="min-h-[44px] min-w-[44px] px-3.5 py-2 bg-amber-600/20 hover:bg-amber-600/30 border border-amber-500/40 text-amber-300 text-xs font-semibold rounded-lg transition"
        >
          {simulating ? "Queueing..." : "Queue Test HITL Gate"}
        </button>
      </div>

      {tickets.length === 0 ? (
        <div className="py-10 text-center text-sm text-slate-500">
          No pending HITL approval tickets.
        </div>
      ) : (
        <div className="space-y-3">
          {tickets.map((ticket) => {
            const isPending = ticket.status === "PENDING";
            return (
              <div
                key={ticket.id}
                className={`p-4 rounded-xl border ${
                  isPending
                    ? "bg-amber-950/20 border-amber-500/40"
                    : "bg-slate-950/70 border-slate-800"
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span
                    className={`text-xs font-mono px-2 py-0.5 rounded ${
                      ticket.status === "PENDING"
                        ? "bg-amber-500/20 text-amber-300"
                        : ticket.status === "APPROVED"
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "bg-rose-500/20 text-rose-300"
                    }`}
                  >
                    {ticket.status}
                  </span>
                  <span className="text-xs text-slate-400 font-mono">
                    {ticket.gate_type} &bull; @{ticket.agent_id}
                  </span>
                </div>

                <div className="text-sm font-semibold text-white mb-1">
                  Tool / Gate:{" "}
                  <span className="text-indigo-300 font-mono">
                    {ticket.tool_id}
                  </span>
                </div>

                <pre className="text-xs bg-slate-950 p-2.5 rounded border border-slate-800 text-slate-300 overflow-x-auto mb-3">
                  {JSON.stringify(ticket.tool_arguments, null, 2)}
                </pre>

                {isPending ? (
                  <div className="space-y-2.5">
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
                      className="min-h-[44px] w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-100"
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
                  <div className="text-xs text-slate-400">
                    Reviewer note:{" "}
                    <span className="text-slate-200">
                      {ticket.reviewer_comment || "N/A"}
                    </span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Mobile Bottom Sheet / Modal Confirmation Guard to Prevent Accidental Taps */}
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
              <code className="text-indigo-300">
                {confirmState.ticket.tool_id}
              </code>{" "}
              for agent <code>@{confirmState.ticket.agent_id}</code> as{" "}
              <strong>{confirmState.decision}</strong>?
            </p>
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
