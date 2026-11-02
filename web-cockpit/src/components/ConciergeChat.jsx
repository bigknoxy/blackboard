import React, { useEffect, useState } from "react";
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

      {/* Multi-Tier Local LAN + NVIDIA NIM Router Config */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-white">
              Local LAN + NVIDIA NIM Router
            </h3>
            <p className="text-[11px] text-slate-400">
              Free Local Endpoints First &bull; Rate-Limit (429) &amp; Slow Failover
            </p>
          </div>
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
        </div>

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

      {/* External Channels Configuration (Telegram / Discord) */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-white">
              Telegram &amp; Discord Channels Bridge
            </h3>
            <p className="text-[11px] text-slate-400">
              Steer, trigger pipelines &amp; get alerts via Telegram or Discord
            </p>
          </div>
          <div className="flex gap-1.5">
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
          </div>
        </div>

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
    </div>
  );
}
