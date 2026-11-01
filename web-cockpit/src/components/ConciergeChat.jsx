import React, { useState } from "react";
import { api } from "../api";

export default function ConciergeChat({
  selectedWorkflowId,
  agents = [],
  tools = [],
  onStateMutated,
}) {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Concierge Admin Agent online. Ask me to inspect platform state, inject steering (`steer: enforce strict typing`), trigger a workflow (`run workflow`), or prune the Blackboard (`prune blackboard`).",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState(
    agents[0]?.id || "architect"
  );
  const [memories, setMemories] = useState([]);
  const [memoryQuery, setMemoryQuery] = useState("memory limits and RBAC");

  async function handleSend(e) {
    e.preventDefault();
    if (!input.trim()) return;
    const userText = input.trim();
    setMessages((prev) => [...prev, { role: "user", content: userText }]);
    setInput("");
    setLoading(true);
    try {
      const res = await api.chatConcierge(userText, selectedWorkflowId);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.reply,
          tools: res.tool_calls_executed,
        },
      ]);
      if (onStateMutated) await onStateMutated();
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleSearchMemories() {
    const res = await api.searchMemories(selectedAgentId, memoryQuery, 3);
    setMemories(res || []);
  }

  const textareaRows = Math.min(5, Math.max(1, input.split("\n").length));

  return (
    <div
      data-testid="concierge-dvh-container"
      className="min-h-[calc(100dvh-10rem)] lg:min-h-0 flex flex-col gap-5 w-full max-w-full overflow-x-hidden"
    >
      {/* Mobile-Keyboard-Aware (dvh) Concierge Chat Card */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col flex-1 min-h-[380px] max-h-[75dvh]">
        <div className="pb-3 border-b border-slate-800">
          <h2 className="text-base font-semibold text-white">
            Concierge Admin Agent (<code>/api/v1/chat</code>)
          </h2>
          <p className="text-xs text-slate-400">
            Keyboard-aware <code>dvh</code> viewport &amp; meta-tool console
          </p>
        </div>

        <div className="flex-1 overflow-y-auto space-y-3 py-3 pr-1">
          {messages.map((m, idx) => (
            <div
              key={idx}
              className={`p-3 rounded-xl text-xs leading-relaxed ${
                m.role === "user"
                  ? "bg-indigo-600/20 border border-indigo-500/40 text-indigo-100 ml-6"
                  : "bg-slate-950 border border-slate-800 text-slate-200 mr-4"
              }`}
            >
              <div className="font-semibold text-[11px] uppercase tracking-wider text-slate-400 mb-1">
                {m.role === "user" ? "Operator" : "Concierge Admin"}
              </div>
              <pre className="whitespace-pre-wrap break-words font-sans">
                {m.content}
              </pre>
            </div>
          ))}
        </div>

        {/* Sticky Bottom Input Field with Auto-Expanding Textarea & Mic/Attach Placeholders */}
        <form
          onSubmit={handleSend}
          className="sticky bottom-0 bg-slate-900 pt-2 border-t border-slate-800 flex items-end gap-1.5"
        >
          <button
            type="button"
            aria-label="Attach artifact"
            data-testid="concierge-attach-btn"
            className="min-h-[44px] min-w-[44px] shrink-0 rounded-lg bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-200 text-xs font-bold flex items-center justify-center"
          >
            +
          </button>
          <button
            type="button"
            aria-label="Voice input"
            data-testid="concierge-mic-btn"
            className="min-h-[44px] min-w-[44px] shrink-0 rounded-lg bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-200 text-xs font-mono flex items-center justify-center"
          >
            MIC
          </button>
          <textarea
            rows={textareaRows}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask Concierge or type 'steer: ...'..."
            className="min-h-[44px] flex-1 resize-none bg-slate-950 border border-slate-800 rounded-lg px-3 py-2.5 text-xs sm:text-sm text-slate-100 focus:outline-none"
          />
          <button
            type="submit"
            disabled={loading}
            className="min-h-[44px] min-w-[44px] shrink-0 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg transition"
          >
            {loading ? "..." : "Send"}
          </button>
        </form>
      </div>

      {/* Scoped pgvector Memory Hydration Inspector */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-semibold text-white">
          Scoped <code>pgvector</code> Memory Inspector
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {agents.map((ag) => (
            <button
              key={ag.id}
              type="button"
              onClick={() => setSelectedAgentId(ag.id)}
              className={`min-h-[44px] min-w-[44px] px-3 py-1.5 rounded-lg text-xs font-mono border transition ${
                selectedAgentId === ag.id
                  ? "bg-indigo-600 text-white border-indigo-500"
                  : "bg-slate-950 text-slate-300 border-slate-800"
              }`}
            >
              @{ag.id}
            </button>
          ))}
        </div>

        <div className="flex gap-2">
          <input
            type="text"
            value={memoryQuery}
            onChange={(e) => setMemoryQuery(e.target.value)}
            className="min-h-[44px] flex-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100"
          />
          <button
            type="button"
            onClick={handleSearchMemories}
            className="min-h-[44px] min-w-[44px] px-3.5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg transition"
          >
            Top-3
          </button>
        </div>

        {memories.length > 0 && (
          <div className="space-y-2">
            {memories.map((mem, idx) => (
              <div
                key={mem.id}
                className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-xs"
              >
                <div className="flex justify-between text-indigo-300 font-mono mb-1">
                  <span>
                    #{idx + 1} [{mem.memory_type}]
                  </span>
                  <span>sim: {mem.similarity}</span>
                </div>
                <div className="text-slate-200">{mem.content}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
