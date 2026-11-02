import React, { useState, useEffect } from "react";
import { api } from "../api";

export default function BacklogModal({ isOpen, onClose, onIntakeTriggered }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");
  const [showAddForm, setShowAddForm] = useState(false);
  const [actionMsg, setActionMsg] = useState("");

  // New Item Form State
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("feature");
  const [priority, setPriority] = useState("P1");
  const [status, setStatus] = useState("idea");
  const [tags, setTags] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadBacklog();
    }
  }, [isOpen, filterStatus]);

  async function loadBacklog() {
    setLoading(true);
    setActionMsg("");
    try {
      const params = filterStatus !== "all" ? { status: filterStatus } : {};
      const res = await api.listBacklog(params);
      setItems(res || []);
    } catch (err) {
      setActionMsg(`Failed to load backlog: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e) {
    e.preventDefault();
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      const tagList = tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      await api.createBacklogItem({
        title: title.trim(),
        description: description.trim(),
        category,
        priority,
        status,
        tags: tagList,
      });
      setTitle("");
      setDescription("");
      setTags("");
      setShowAddForm(false);
      setActionMsg("Successfully added idea to backlog!");
      await loadBacklog();
    } catch (err) {
      setActionMsg(`Create failed: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleStatusChange(itemKey, newStatus) {
    try {
      await api.updateBacklogItem(itemKey, { status: newStatus });
      setActionMsg(`Updated ${itemKey} status to ${newStatus}`);
      await loadBacklog();
    } catch (err) {
      setActionMsg(`Status update failed: ${err.message}`);
    }
  }

  async function handleTriggerIntake(itemKey) {
    try {
      setActionMsg(`Triggering Intake Pipeline for ${itemKey}...`);
      const res = await api.triggerBacklogIntake(itemKey);
      setActionMsg(`Intake Pipeline started! Run ID: ${res.run_id?.slice(0, 8)}`);
      await loadBacklog();
      if (onIntakeTriggered) onIntakeTriggered(res);
    } catch (err) {
      setActionMsg(`Intake trigger failed: ${err.message}`);
    }
  }

  async function handleDelete(itemKey) {
    if (!window.confirm(`Delete backlog item ${itemKey}?`)) return;
    try {
      await api.deleteBacklogItem(itemKey);
      setActionMsg(`Deleted ${itemKey}`);
      await loadBacklog();
    } catch (err) {
      setActionMsg(`Delete failed: ${err.message}`);
    }
  }

  if (!isOpen) return null;

  return (
    <div
      data-no-swipe="true"
      className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/80 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-4xl max-h-[92dvh] flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="px-4 sm:px-6 py-3.5 border-b border-slate-800 flex items-center justify-between bg-slate-900/90 sticky top-0 z-10">
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold text-sm">
              💡
            </div>
            <div>
              <h2 className="text-base sm:text-lg font-bold text-white flex items-center gap-2">
                Backlog &amp; Idea Intake
                <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-950 border border-indigo-700 text-indigo-300 font-mono">
                  {items.length} items
                </span>
              </h2>
              <p className="text-[11px] text-slate-400">
                Track future features, architecture proposals, and trigger Intake Pipelines
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close modal"
            className="min-h-[44px] min-w-[44px] rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm font-bold flex items-center justify-center"
          >
            ✕
          </button>
        </div>

        {/* Action / Notification Banner */}
        {actionMsg && (
          <div className="px-4 py-2 bg-indigo-950/60 border-b border-indigo-800 text-indigo-200 text-xs flex justify-between items-center">
            <span>{actionMsg}</span>
            <button
              onClick={() => setActionMsg("")}
              className="text-slate-400 hover:text-white px-2 py-1 text-xs"
            >
              dismiss
            </button>
          </div>
        )}

        {/* Filter bar & Add Idea Button */}
        <div className="px-4 sm:px-6 py-3 border-b border-slate-800 bg-slate-950/50 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1.5 overflow-x-auto pb-1 sm:pb-0">
            {[
              { id: "all", label: "All Items" },
              { id: "idea", label: "💡 Ideas" },
              { id: "triaged", label: "📐 Triaged" },
              { id: "ready", label: "🚀 Ready" },
              { id: "in_progress", label: "⚙️ In Progress" },
              { id: "tabled", label: "⏸️ Tabled" },
              { id: "completed", label: "✅ Done" },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setFilterStatus(tab.id)}
                className={`min-h-[44px] px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                  filterStatus === tab.id
                    ? "bg-indigo-600 text-white"
                    : "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setShowAddForm(!showAddForm)}
            className="min-h-[44px] px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold rounded-lg transition flex items-center gap-1.5 shadow"
          >
            {showAddForm ? "Cancel" : "+ New Idea / Feature"}
          </button>
        </div>

        {/* Add Idea Collapsible Form */}
        {showAddForm && (
          <form
            onSubmit={handleCreate}
            className="p-4 sm:p-5 bg-slate-950 border-b border-slate-800 space-y-3"
          >
            <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-400">
              Capture New Backlog Item or Idea
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
              <div className="sm:col-span-2">
                <input
                  type="text"
                  required
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Feature or idea title..."
                  className="min-h-[44px] w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs sm:text-sm text-slate-100 placeholder-slate-500"
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className="min-h-[44px] bg-slate-900 border border-slate-700 rounded-lg px-2.5 text-xs text-slate-100"
                >
                  <option value="feature">Feature</option>
                  <option value="workflow">Workflow</option>
                  <option value="agent">Agent</option>
                  <option value="tool">Tool</option>
                  <option value="improvement">Improvement</option>
                  <option value="bug">Bug</option>
                  <option value="research">Research</option>
                </select>
                <select
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  className="min-h-[44px] bg-slate-900 border border-slate-700 rounded-lg px-2.5 text-xs text-slate-100 font-mono font-bold"
                >
                  <option value="P0">P0 (Critical)</option>
                  <option value="P1">P1 (High)</option>
                  <option value="P2">P2 (Medium)</option>
                  <option value="P3">P3 (Low)</option>
                </select>
              </div>
            </div>

            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe user intent, architectural requirements, tools involved, or why it is tabled..."
              className="w-full bg-slate-900 border border-slate-700 rounded-lg p-3 text-xs sm:text-sm text-slate-100 placeholder-slate-500 resize-none"
            />

            <div className="flex flex-col sm:flex-row gap-2.5 items-center justify-between">
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="Tags separated by commas (e.g. discord, channels, bot)"
                className="min-h-[44px] flex-1 w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono"
              />
              <div className="flex gap-2 w-full sm:w-auto">
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                  className="min-h-[44px] bg-slate-900 border border-slate-700 rounded-lg px-3 text-xs text-slate-200"
                >
                  <option value="idea">Status: Idea</option>
                  <option value="tabled">Status: Tabled</option>
                  <option value="ready">Status: Ready</option>
                </select>
                <button
                  type="submit"
                  disabled={submitting}
                  className="min-h-[44px] px-5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold rounded-lg transition shrink-0"
                >
                  {submitting ? "Saving..." : "Save to Backlog"}
                </button>
              </div>
            </div>
          </form>
        )}

        {/* Scrollable Backlog Items Feed */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-3.5">
          {loading && (
            <div className="text-center py-10 text-slate-400 text-xs font-mono">
              Loading backlog items...
            </div>
          )}

          {!loading && items.length === 0 && (
            <div className="text-center py-12 bg-slate-950/40 rounded-xl border border-slate-800 p-6">
              <div className="text-2xl mb-2">📋</div>
              <h4 className="text-sm font-semibold text-slate-200">
                No items match filter '{filterStatus}'
              </h4>
              <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
                Capture new ideas using "+ New Idea / Feature" or tell Concierge in chat to "capture this as a future feature".
              </p>
            </div>
          )}

          {!loading &&
            items.map((item) => (
              <div
                key={item.id}
                className="bg-slate-950 border border-slate-800 hover:border-slate-700 rounded-xl p-4 transition space-y-3"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="px-2 py-0.5 rounded text-[11px] font-mono font-bold bg-indigo-950 border border-indigo-700 text-indigo-300">
                        {item.key}
                      </span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono uppercase ${
                          item.priority === "P0"
                            ? "bg-rose-950 border border-rose-700 text-rose-300"
                            : item.priority === "P1"
                            ? "bg-amber-950 border border-amber-700 text-amber-300"
                            : "bg-slate-800 text-slate-300"
                        }`}
                      >
                        {item.priority}
                      </span>
                      <span className="px-2 py-0.5 rounded text-[10px] uppercase font-semibold bg-slate-800 text-slate-300">
                        {item.category}
                      </span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] uppercase font-bold ${
                          item.status === "completed"
                            ? "bg-emerald-950 text-emerald-300 border border-emerald-700"
                            : item.status === "in_progress"
                            ? "bg-sky-950 text-sky-300 border border-sky-700"
                            : item.status === "triaged"
                            ? "bg-purple-950 text-purple-300 border border-purple-700"
                            : item.status === "tabled"
                            ? "bg-zinc-900 text-zinc-400 border border-zinc-700"
                            : "bg-indigo-950 text-indigo-300 border border-indigo-700"
                        }`}
                      >
                        {item.status}
                      </span>
                    </div>
                    <h3 className="text-sm sm:text-base font-semibold text-slate-100">
                      {item.title}
                    </h3>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => handleTriggerIntake(item.key)}
                      className="min-h-[44px] px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold transition flex items-center gap-1 shadow"
                      title="Run automated Architect & Security Auditor intake pipeline"
                    >
                      <span>📐</span> Run Intake
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(item.key)}
                      className="min-h-[44px] min-w-[44px] rounded-lg bg-slate-900 hover:bg-rose-950/50 hover:text-rose-300 text-slate-400 text-xs font-bold border border-slate-800"
                      title="Delete item"
                    >
                      🗑
                    </button>
                  </div>
                </div>

                {item.description && (
                  <p className="text-xs text-slate-300 leading-relaxed font-sans bg-slate-900/40 p-2.5 rounded-lg border border-slate-800/80">
                    {item.description}
                  </p>
                )}

                <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-slate-800/60 text-[11px]">
                  <div className="flex flex-wrap gap-1">
                    {item.tags?.map((tag) => (
                      <span
                        key={tag}
                        className="px-1.5 py-0.5 rounded bg-slate-900 text-slate-400 font-mono text-[10px]"
                      >
                        #{tag}
                      </span>
                    ))}
                  </div>

                  <div className="flex items-center gap-2">
                    <label className="text-slate-400">Change Status:</label>
                    <select
                      value={item.status}
                      onChange={(e) => handleStatusChange(item.key, e.target.value)}
                      className="min-h-[38px] bg-slate-900 border border-slate-700 rounded px-2 text-xs text-slate-200"
                    >
                      <option value="idea">idea</option>
                      <option value="triaged">triaged</option>
                      <option value="ready">ready</option>
                      <option value="in_progress">in_progress</option>
                      <option value="completed">completed</option>
                      <option value="tabled">tabled</option>
                    </select>
                  </div>
                </div>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
