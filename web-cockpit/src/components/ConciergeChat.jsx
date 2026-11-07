import React, { useEffect, useState, useRef } from "react";
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
  const [llmStatus, setLlmStatus] = useState(null);
  const [nimKeyInput, setNimKeyInput] = useState("");
  const [nimModelInput, setNimModelInput] = useState("meta/llama-3.3-70b-instruct");
  const [preferLocal, setPreferLocal] = useState(true);
  const [savingNim, setSavingNim] = useState(false);
  const [channelStatus, setChannelStatus] = useState(null);
  const [tgTokenInput, setTgTokenInput] = useState("");
  const [tgChatIdInput, setTgChatIdInput] = useState("");
  const [dcWebhookInput, setDcWebhookInput] = useState("");
  const [savingChannel, setSavingChannel] = useState(false);
  const [testingChannels, setTestingChannels] = useState(false);
  const [channelTestResult, setChannelTestResult] = useState(null);
  const chatEndRef = useRef(null);

  useEffect(() => {
    if (typeof chatEndRef.current?.scrollIntoView === "function") {
      chatEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading]);

  async function refreshChannelStatus() {
    try {
      if (api.getChannelsStatus) {
        const cs = await api.getChannelsStatus();
        setChannelStatus(cs);
      }
    } catch {
      // ignore
    }
  }

  async function refreshLlmStatus() {
    try {
      if (api.getLlmStatus) {
        const st = await api.getLlmStatus();
        setLlmStatus(st);
        if (st?.nvidia_nim?.default_model) {
          setNimModelInput(st.nvidia_nim.default_model);
        }
        if (typeof st?.prefer_local === "boolean") {
          setPreferLocal(st.prefer_local);
        }
      }
    } catch {
      // ignore in unit tests where getLlmStatus may not be mocked
    }
  }

  useEffect(() => {
    refreshLlmStatus();
    refreshChannelStatus();
  }, []);

  async function handleSaveChannels(e) {
    e.preventDefault();
    if (!api.updateChannelsConfig) return;
    setSavingChannel(true);
    try {
      const payload = {};
      if (tgTokenInput.trim()) payload.telegram_bot_token = tgTokenInput.trim();
      if (tgChatIdInput.trim()) payload.telegram_chat_id = tgChatIdInput.trim();
      if (dcWebhookInput.trim()) payload.discord_webhook_url = dcWebhookInput.trim();
      payload.telegram_enabled = true;
      payload.discord_enabled = true;
      const updated = await api.updateChannelsConfig(payload);
      setChannelStatus(updated);
      setTgTokenInput("");
      setTgChatIdInput("");
      setDcWebhookInput("");
    } catch {
      // ignore
    } finally {
      setSavingChannel(false);
    }
  }

  async function handleTestChannels() {
    if (!api.testChannels) return;
    setTestingChannels(true);
    setChannelTestResult(null);
    try {
      const res = await api.testChannels("🔔 [Blackboard Test] Discord webhook connectivity verified from Web Cockpit!");
      setChannelTestResult(res?.results || res);
      await refreshChannelStatus();
    } catch (err) {
      setChannelTestResult({ error: err.message });
    } finally {
      setTestingChannels(false);
    }
  }

  async function handleSaveNimConfig(e) {
    e.preventDefault();
    if (!api.updateLlmConfig) return;
    setSavingNim(true);
    try {
      const payload = {
        default_nim_model: nimModelInput,
        prefer_local: preferLocal,
      };
      if (nimKeyInput.trim()) {
        payload.nvidia_nim_api_key = nimKeyInput.trim();
      }
      const updated = await api.updateLlmConfig(payload);
      setLlmStatus(updated);
      setNimKeyInput("");
    } catch {
      // ignore
    } finally {
      setSavingNim(false);
    }
  }

  async function handleClearCooldowns() {
    if (!api.updateLlmConfig) return;
    const updated = await api.updateLlmConfig({ clear_cooldowns: true });
    setLlmStatus(updated);
  }

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
              className={`p-3 rounded-xl text-xs sm:text-sm leading-relaxed ${
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

              {/* Visual Action Receipt Cards */}
              {Array.isArray(m.tools) && m.tools.length > 0 && (
                <div
                  data-testid="concierge-action-receipts"
                  className="mt-3 pt-2.5 border-t border-slate-800/80 space-y-1.5"
                >
                  <div className="flex items-center gap-1.5 text-[10px] uppercase font-bold text-slate-400 tracking-wider">
                    <span>⚡ Executed Action Receipts</span>
                    <span className="px-1.5 py-0.2 rounded-full bg-slate-900 text-indigo-300 border border-slate-700">
                      {m.tools.length}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                    {m.tools.map((rawTc, tIdx) => {
                      const tc =
                        typeof rawTc === "string"
                          ? { tool: rawTc }
                          : rawTc || {};
                      return (
                        <div
                          key={tIdx}
                          className="bg-slate-900/90 border border-emerald-500/30 rounded-lg p-2 text-xs flex flex-col gap-1 shadow-sm"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-mono text-emerald-400 font-semibold flex items-center gap-1 text-[11px]">
                              <span>✓</span>
                              <span>{tc.tool || "action"}</span>
                            </span>
                            <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800">
                              {tc.status || "EXECUTED"}
                            </span>
                          </div>
                          {tc.tool_id && (
                            <div className="text-[11px] text-slate-300 font-mono">
                              Tool: <span className="text-white font-bold">{tc.tool_id}</span>
                              {tc.consumes_type && (
                                <span className="text-slate-400 text-[10px] ml-1">
                                  ({tc.consumes_type} ➔ {tc.produces_type})
                                </span>
                              )}
                            </div>
                          )}
                          {tc.agent_id && (
                            <div className="text-[11px] text-slate-300">
                              Assigned to: <span className="text-indigo-400 font-mono">@{tc.agent_id}</span>
                            </div>
                          )}
                          {tc.workflow_id && (
                            <div className="text-[10px] text-slate-400 font-mono truncate">
                              Pipeline: {tc.workflow_id}
                            </div>
                          )}
                          {tc.key && (
                            <div className="text-[11px] text-amber-300 font-mono">
                              Backlog: {tc.key} — {tc.title}
                            </div>
                          )}
                          {tc.query && (
                            <div className="text-[11px] text-sky-300 font-mono truncate">
                              Query: "{tc.query}"
                            </div>
                          )}
                          {tc.content && (
                            <div className="text-[11px] text-emerald-300 font-mono truncate">
                              Memory: "{tc.content}"
                            </div>
                          )}
                          {tc.agents && (
                            <div className="text-[10px] text-slate-300 font-mono truncate">
                              {tc.count || 0} memories across: {Array.isArray(tc.agents) ? tc.agents.join(", ") : tc.agents}
                            </div>
                          )}
                          {tc.command && (
                            <div className="text-[11px] text-amber-300 font-mono truncate">
                              Cmd: {tc.command}
                            </div>
                          )}
                          {tc.recommendation && (
                            <div className="text-[10px] text-indigo-300 line-clamp-2">
                              {tc.recommendation}
                            </div>
                          )}
                          {tc.policy_type && (
                            <div className="text-[11px] text-purple-300 font-mono truncate">
                              Policy: <span className="text-white font-bold">{tc.policy_type}</span>
                              {tc.pattern && <span className="text-emerald-400 ml-1">({tc.pattern})</span>}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          ))}

          {/* Animated Thinking & Processing Bubble */}
          {loading && (
            <div
              data-testid="concierge-thinking-indicator"
              className="p-3.5 rounded-xl text-xs sm:text-sm leading-relaxed bg-slate-950/90 border border-indigo-500/40 text-slate-200 mr-4 shadow-lg shadow-indigo-950/20"
            >
              <div className="flex items-center gap-2 mb-2 font-semibold text-[11px] uppercase tracking-wider text-indigo-400">
                <span className="relative flex h-2.5 w-2.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-indigo-500"></span>
                </span>
                <span>Concierge Admin</span>
                <span className="text-[10px] lowercase text-slate-400 font-mono">
                  processing
                </span>
              </div>
              <div className="flex items-center gap-2 py-1 text-slate-300">
                <div className="flex items-center gap-1">
                  <span className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-bounce [animation-delay:-0.3s]"></span>
                  <span className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-bounce [animation-delay:-0.15s]"></span>
                  <span className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-bounce"></span>
                </div>
                <span className="text-xs sm:text-sm text-indigo-200/90 italic font-sans">
                  Agent is thinking, consulting models &amp; executing tools...
                </span>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Quick prompt suggestions (one-touch shortcuts for mobile) */}
        <div className="flex items-center gap-1.5 overflow-x-auto py-1.5 no-scrollbar border-t border-slate-800/60">
          <button
            type="button"
            onClick={() => setInput("Permanently approve bash commands starting with git")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            🛡️ Trust Git Commands
          </button>
          <button
            type="button"
            onClick={() => setInput("Create a new tool named 'system_uptime_checker' with description 'Inspect host uptime' using command 'uptime' and assign it to architect.")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            ⚡ Synthesize Tool
          </button>
          <button
            type="button"
            onClick={() => setInput("Do you have memory? What are your stored memories across agents?")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            🧠 Memory & Context
          </button>
          <button
            type="button"
            onClick={() => setInput("Can you search the Web? Tell me what today's date is and if there are any NFL games today")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            🌐 Web Search & NFL
          </button>
          <button
            type="button"
            onClick={() => setInput("We need a bash tool and some web tools. Can you suggest or add them?")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            💻 Bash & Tools Suggestion
          </button>
          <button
            type="button"
            onClick={() => setInput("steer: prioritize strict typing and robust tests")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            ⚡ Steer: Strict Typing
          </button>
          <button
            type="button"
            onClick={() => setInput("list agents and current tools")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            📋 List Agents
          </button>
          <button
            type="button"
            onClick={() => setInput("inspect platform state")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            ⚙️ Platform State
          </button>
          <button
            type="button"
            onClick={() => setInput("prune blackboard entries")}
            className="shrink-0 px-2.5 py-1 rounded-full bg-slate-950 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-300 transition"
          >
            🧹 Prune Board
          </button>
        </div>

        {/* Mobile-Optimized Input Field: Full Width on Mobile, Expandable & Enter to Send */}
        <form
          onSubmit={handleSend}
          className="sticky bottom-0 bg-slate-900 pt-2 border-t border-slate-800 flex items-end gap-2"
        >
          {/* On small mobile screens, hide unused stubs (+ / MIC) to maximize typing box width */}
          <button
            type="button"
            aria-label="Attach artifact context"
            title="Attach artifact context"
            data-testid="concierge-attach-btn"
            onClick={() =>
              setInput((prev) => (prev ? `${prev}\n[context: ` : "[context: "))
            }
            className="hidden sm:flex min-h-[44px] min-w-[44px] shrink-0 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-200 text-xs font-bold items-center justify-center transition"
          >
            +
          </button>
          <button
            type="button"
            aria-label="Voice input"
            title="Voice input"
            data-testid="concierge-mic-btn"
            onClick={() =>
              setInput((prev) => (prev ? `${prev} [voice note]` : "[voice note]"))
            }
            className="hidden sm:flex min-h-[44px] min-w-[44px] shrink-0 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 text-slate-400 hover:text-slate-200 text-xs font-mono items-center justify-center transition"
          >
            MIC
          </button>

          <textarea
            rows={textareaRows}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend(e);
              }
            }}
            placeholder="Ask Concierge or type 'steer: ...'"
            className="min-h-[50px] max-h-[140px] flex-1 resize-none bg-slate-950 border border-slate-800 focus:border-indigo-500 rounded-xl px-3.5 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none transition leading-relaxed"
          />

          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="min-h-[50px] min-w-[72px] shrink-0 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs sm:text-sm font-semibold rounded-xl transition flex items-center justify-center gap-1.5 shadow-md shadow-indigo-900/20"
          >
            {loading ? (
              <span className="flex items-center gap-1 font-mono text-xs">
                <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>
                <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse [animation-delay:0.2s]"></span>
                <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse [animation-delay:0.4s]"></span>
              </span>
            ) : (
              <span>Send</span>
            )}
          </button>
        </form>
      </div>

      {/* Scoped pgvector Memory Hydration Inspector (Collapsible Accordion) */}
      <details className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden group">
        <summary className="p-4 cursor-pointer flex items-center justify-between hover:bg-slate-800/40 select-none">
          <div className="flex items-center gap-2">
            <span className="text-xs text-indigo-400 font-mono">🧠</span>
            <h3 className="text-sm font-semibold text-white">
              Scoped <code>pgvector</code> Memory Inspector
            </h3>
          </div>
          <span className="text-xs text-slate-500 font-mono group-open:rotate-180 transition-transform">
            ▼
          </span>
        </summary>
        <div className="p-4 pt-1 space-y-3 border-t border-slate-800/60">
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
      </details>

      {/* Multi-Tier Local LAN + NVIDIA NIM Router Config (Collapsible Accordion) */}
      <details className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden group">
        <summary className="p-4 cursor-pointer flex items-center justify-between hover:bg-slate-800/40 select-none">
          <div className="flex items-center gap-2">
            <span className="text-xs text-cyan-400 font-mono">🌐</span>
            <div className="text-left">
              <h3 className="text-sm font-semibold text-white">
                Local LAN + NVIDIA NIM Router
              </h3>
              <p className="text-[11px] text-slate-400">
                Free Local Endpoints First &bull; Rate-Limit (429) &amp; Slow Failover
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-1 rounded text-[10px] font-mono border ${
                llmStatus?.nvidia_nim?.api_key_configured
                  ? "bg-emerald-950/60 border-emerald-700 text-emerald-300"
                  : "bg-amber-950/60 border-amber-700 text-amber-300"
              }`}
            >
              {llmStatus?.nvidia_nim?.api_key_configured
                ? `NIM READY (${llmStatus.nvidia_nim.api_key_masked})`
                : "NIM KEY OPTIONAL"}
            </span>
            <span className="text-xs text-slate-500 font-mono group-open:rotate-180 transition-transform">
              ▼
            </span>
          </div>
        </summary>

        <div className="p-4 pt-1 space-y-3 border-t border-slate-800/60">
          <form onSubmit={handleSaveNimConfig} className="space-y-2.5">
            <input
              type="password"
              value={nimKeyInput}
              onChange={(e) => setNimKeyInput(e.target.value)}
              placeholder="Paste NVIDIA NIM API Key (nvapi-...) when ready"
              className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono"
            />
            <div className="flex flex-wrap gap-2">
              <select
                value={nimModelInput}
                onChange={(e) => setNimModelInput(e.target.value)}
                className="min-h-[44px] flex-1 bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-2 text-xs text-slate-200 font-mono"
              >
                {(
                  llmStatus?.nvidia_nim?.free_tier_models || [
                    "meta/llama-3.3-70b-instruct",
                    "qwen/qwen2.5-coder-32b-instruct",
                    "deepseek-ai/deepseek-r1",
                    "mistralai/mistral-nemotron",
                    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
                  ]
                ).map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => setPreferLocal((p) => !p)}
                className="min-h-[44px] min-w-[44px] px-3 py-2 bg-slate-950 border border-slate-700 rounded-lg text-xs font-mono text-slate-200"
              >
                {preferLocal ? "Mode: Local First" : "Mode: NIM First"}
              </button>
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={savingNim}
                className="min-h-[44px] min-w-[44px] flex-1 px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg transition"
              >
                {savingNim ? "Saving..." : "Save Router & NIM Key"}
              </button>
              <button
                type="button"
                onClick={handleClearCooldowns}
                className="min-h-[44px] min-w-[44px] px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono rounded-lg transition"
              >
                Reset Cooldowns
              </button>
            </div>
          </form>
        </div>
      </details>

      {/* External Channels Configuration (Telegram / Discord) (Collapsible Accordion) */}
      <details className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden group">
        <summary className="p-4 cursor-pointer flex items-center justify-between hover:bg-slate-800/40 select-none">
          <div className="flex items-center gap-2">
            <span className="text-xs text-emerald-400 font-mono">📡</span>
            <div className="text-left">
              <h3 className="text-sm font-semibold text-white">
                Telegram &amp; Discord Channels Bridge
              </h3>
              <p className="text-[11px] text-slate-400">
                Steer, trigger pipelines &amp; get alerts via Telegram or Discord
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono border ${
                channelStatus?.telegram?.configured
                  ? "bg-sky-950/60 border-sky-700 text-sky-300"
                  : "bg-slate-950 border-slate-800 text-slate-500"
              }`}
            >
              TG: {channelStatus?.telegram?.configured ? "ACTIVE" : "OFF"}
            </span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono border ${
                channelStatus?.discord?.configured
                  ? "bg-indigo-950/60 border-indigo-700 text-indigo-300"
                  : "bg-slate-950 border-slate-800 text-slate-500"
              }`}
            >
              DC: {channelStatus?.discord?.configured ? "ACTIVE" : "OFF"}
            </span>
            <span className="text-xs text-slate-500 font-mono group-open:rotate-180 transition-transform">
              ▼
            </span>
          </div>
        </summary>

        <div className="p-4 pt-1 space-y-3 border-t border-slate-800/60">
          <form onSubmit={handleSaveChannels} className="space-y-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <input
                type="password"
                value={tgTokenInput}
                onChange={(e) => setTgTokenInput(e.target.value)}
                placeholder="Telegram Bot Token (from @BotFather)"
                className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono"
              />
              <input
                type="text"
                value={tgChatIdInput}
                onChange={(e) => setTgChatIdInput(e.target.value)}
                placeholder="Telegram Chat ID (e.g. 123456789)"
                className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono"
              />
            </div>
            <input
              type="password"
              value={dcWebhookInput}
              onChange={(e) => setDcWebhookInput(e.target.value)}
              placeholder="Discord Webhook URL (https://discord.com/api/webhooks/...)"
              className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-100 font-mono"
            />
            <div className="flex flex-col sm:flex-row gap-2">
              <button
                type="submit"
                disabled={savingChannel}
                className="min-h-[44px] flex-1 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-100 text-xs font-semibold rounded-lg transition"
              >
                {savingChannel ? "Saving Channels..." : "Save External Channels Config"}
              </button>
              <button
                type="button"
                onClick={handleTestChannels}
                disabled={testingChannels}
                className="min-h-[44px] px-4 py-2 bg-indigo-700 hover:bg-indigo-600 text-white text-xs font-semibold rounded-lg transition flex items-center justify-center gap-1.5"
              >
                {testingChannels ? "Pinging..." : "Test Webhook"}
              </button>
            </div>
            {channelTestResult && (
              <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-[11px] font-mono space-y-1">
                <div className="text-slate-400 font-semibold uppercase text-[10px]">Test Broadcast Result:</div>
                {channelTestResult.discord && (
                  <div className={channelTestResult.discord.status === "DELIVERED" ? "text-emerald-400" : "text-amber-400"}>
                    &bull; Discord: {channelTestResult.discord.status} {channelTestResult.discord.http_code ? `(HTTP ${channelTestResult.discord.http_code})` : ""} {channelTestResult.discord.error || channelTestResult.discord.reason || ""}
                  </div>
                )}
                {channelTestResult.telegram && (
                  <div className={channelTestResult.telegram.status === "DELIVERED" ? "text-emerald-400" : "text-slate-400"}>
                    &bull; Telegram: {channelTestResult.telegram.status} {channelTestResult.telegram.http_code ? `(HTTP ${channelTestResult.telegram.http_code})` : ""} {channelTestResult.telegram.error || channelTestResult.telegram.reason || ""}
                  </div>
                )}
                {channelTestResult.error && (
                  <div className="text-rose-400">&bull; Error: {channelTestResult.error}</div>
                )}
              </div>
            )}
          </form>
        </div>
      </details>
    </div>
  );
}
