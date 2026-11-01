import React, { useState } from "react";

export default function UserGuide({ apiBaseUrl = "http://192.168.8.109:8000/api/v1" }) {
  const [activeSection, setActiveSection] = useState("quickstart");
  const [copiedCmd, setCopiedCmd] = useState(null);

  function copy(text, key) {
    navigator.clipboard?.writeText(text);
    setCopiedCmd(key);
    setTimeout(() => setCopiedCmd(null), 2000);
  }

  return (
    <div className="space-y-6 w-full max-w-full overflow-x-hidden text-slate-200">
      {/* Header Banner */}
      <div className="bg-gradient-to-r from-indigo-950/80 via-slate-900 to-slate-950 border border-indigo-800/40 rounded-2xl p-5 shadow-xl">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <span className="text-[10px] font-mono uppercase tracking-widest text-indigo-400 bg-indigo-950/80 px-2.5 py-1 rounded-full border border-indigo-700/50">
              Interactive Documentation &amp; Testing Runbook
            </span>
            <h1 className="text-xl sm:text-2xl font-bold text-white mt-2">
              Blackboard Multi-Agent Cockpit Guide
            </h1>
            <p className="text-xs sm:text-sm text-slate-300 mt-1 max-w-2xl">
              Learn how to trigger autonomous pipelines, steer agents on the fly, route tasks across local LAN nodes &amp; NVIDIA NIM, and connect Telegram / Discord channels.
            </p>
          </div>
          <div className="flex gap-2">
            <a
              href={`${apiBaseUrl}/docs`}
              target="_blank"
              rel="noreferrer"
              className="min-h-[44px] min-w-[44px] inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold shadow transition"
            >
              Open Swagger Docs &rarr;
            </a>
          </div>
        </div>

        {/* Tab Selector */}
        <div className="flex flex-wrap gap-2 mt-5 pt-4 border-t border-slate-800">
          {[
            { id: "quickstart", label: "Quick Start & Testing" },
            { id: "steering", label: "Steering & HITL" },
            { id: "llm_routing", label: "Local LAN & NVIDIA NIM" },
            { id: "channels", label: "Telegram & Discord Channels" },
            { id: "curl_cheatsheet", label: "cURL & API Cheatsheet" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveSection(tab.id)}
              className={`min-h-[44px] min-w-[44px] px-3.5 py-2 rounded-lg text-xs font-semibold transition ${
                activeSection === tab.id
                  ? "bg-indigo-600 text-white shadow-md shadow-indigo-600/30"
                  : "bg-slate-950/80 text-slate-400 hover:text-slate-200 border border-slate-800"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* SECTION 1: QUICKSTART */}
      {activeSection === "quickstart" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
            <div className="flex items-center gap-2 text-indigo-400 font-semibold text-sm">
              <span className="w-6 h-6 rounded-full bg-indigo-900/60 flex items-center justify-center text-xs font-bold border border-indigo-700">1</span>
              How to Trigger an Autonomous Pipeline
            </div>
            <p className="text-xs text-slate-300 leading-relaxed">
              In the <strong>Workflows</strong> tab (or the left panel on Desktop), type a goal like <code className="text-indigo-300">"Build a compact async Python token-bucket rate limiter"</code> and tap <strong>Trigger Pipeline Run</strong>.
            </p>
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800 text-[11px] font-mono text-slate-300 space-y-1">
              <div>&bull; <code>architect</code> (CT118 <code>qwen3-4b</code>) generates technical specification.</div>
              <div>&bull; <code>coder</code> (NIM / Local) synthesizes candidate code patch.</div>
              <div>&bull; <code>qa-tester</code> &amp; <code>sec-auditor</code> execute pytest &amp; security review in parallel.</div>
              <div>&bull; Halts at <code>stage_hitl_signoff</code> for your one-click approval.</div>
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
            <div className="flex items-center gap-2 text-emerald-400 font-semibold text-sm">
              <span className="w-6 h-6 rounded-full bg-emerald-900/60 flex items-center justify-center text-xs font-bold border border-emerald-700">2</span>
              Testing from your Mobile Phone on Wi-Fi
            </div>
            <p className="text-xs text-slate-300 leading-relaxed">
              Open your phone browser and go to <code className="text-emerald-300 font-bold">http://192.168.8.109:5173</code>.
            </p>
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800 text-[11px] font-mono text-slate-300 space-y-1">
              <div>&bull; Min 44&times;44px touch targets on all buttons.</div>
              <div>&bull; Bottom navigation bar: <code>[Board]</code>, <code>[Workflows]</code>, <code>[Approvals]</code>, <code>[Concierge]</code>.</div>
              <div>&bull; Uses dynamic LAN hostname resolution so no proxy or VPN is needed.</div>
            </div>
          </div>
        </div>
      )}

      {/* SECTION 2: STEERING & HITL */}
      {activeSection === "steering" && (
        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
            <h3 className="text-sm font-semibold text-white">Live Operator Steering Directives</h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              You can steer running agents at any time without restarting the pipeline. Any steering directive is written with <code className="text-indigo-300">entry_type="steering"</code> on the Blackboard and injected into the agent context window under <code className="text-indigo-300">&lt;operator_steering&gt;</code> on their very next turn!
            </p>
            <div className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs font-mono text-indigo-300">
              # Example Steering Formats:<br />
              steer: Keep all code under 100 lines and use strict type hints.<br />
              steer: Target agent coder only: enforce zero external dependencies.
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
            <h3 className="text-sm font-semibold text-white">Human-In-The-Loop (HITL) Gatekeeper</h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              When a workflow reaches a <code>UserTask</code> release gate, or an agent calls a sensitive tool marked with <code>requires_approval: true</code> (e.g. <code>git_branch_commit</code> or <code>production_release_gate</code>), execution pauses and queues a ticket in the <strong>Approvals</strong> drawer.
            </p>
            <div className="flex gap-2">
              <span className="px-3 py-1.5 rounded-lg bg-emerald-950 border border-emerald-700 text-emerald-300 text-xs font-mono font-bold">
                [Approve] &rarr; Executes tool / advances workflow
              </span>
              <span className="px-3 py-1.5 rounded-lg bg-rose-950 border border-rose-700 text-rose-300 text-xs font-mono font-bold">
                [Reject with Note] &rarr; Feeds critique back to Blackboard
              </span>
            </div>
          </div>
        </div>
      )}

      {/* SECTION 3: LLM ROUTING */}
      {activeSection === "llm_routing" && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">Local LAN First + NVIDIA NIM Cloud Tier</h3>
          <p className="text-xs text-slate-300 leading-relaxed">
            The system implements an automated 3-tier inference router designed for home lab resilience:
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            <div className="p-3.5 bg-slate-950 border border-slate-800 rounded-xl space-y-2">
              <div className="font-semibold text-indigo-400">Tier 1: Free Local LAN</div>
              <p className="text-[11px] text-slate-400">
                Direct calls to CT118 (<code>192.168.8.180:8081</code>) and Ollama (<code>192.168.8.136:11434</code>). Zero cloud costs.
              </p>
            </div>
            <div className="p-3.5 bg-slate-950 border border-slate-800 rounded-xl space-y-2">
              <div className="font-semibold text-emerald-400">Tier 2: NVIDIA NIM Cloud</div>
              <p className="text-[11px] text-slate-400">
                Escalates to NVIDIA NIM when local models are slow or for complex code synthesis using models like <code>poolside/laguna-xs-2.1</code>.
              </p>
            </div>
            <div className="p-3.5 bg-slate-950 border border-slate-800 rounded-xl space-y-2">
              <div className="font-semibold text-amber-400">Tier 3: 429 &amp; Rate-Limit Failover</div>
              <p className="text-[11px] text-slate-400">
                If NIM returns HTTP 429 or hits the 35 RPM cap, it automatically falls back to local LAN with 60s cooldown.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* SECTION 4: TELEGRAM & DISCORD */}
      {activeSection === "channels" && (
        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
            <h3 className="text-sm font-semibold text-white">Connect External Chat Channels</h3>
            <p className="text-xs text-slate-300 leading-relaxed">
              Interact with the multi-agent system from anywhere using Telegram, Discord, or an HTTP webhook.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Telegram Box */}
              <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-sky-400">Telegram Bot</span>
                  <span className="text-[10px] bg-slate-900 px-2 py-0.5 rounded text-slate-400">Webhook &amp; Polling</span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  1. Message <code className="text-sky-300">@BotFather</code> on Telegram &rarr; <code className="text-sky-300">/newbot</code>.<br />
                  2. Copy your bot token and send a message to the bot.<br />
                  3. Configure it via the API or Concierge chat!
                </p>
                <div className="bg-slate-900 p-2.5 rounded font-mono text-[10px] text-slate-300 break-all">
                  curl -X POST {apiBaseUrl}/channels/config \<br />
                  &nbsp;&nbsp;-H "Content-Type: application/json" \<br />
                  &nbsp;&nbsp;-d &#39;&#123;"telegram_bot_token": "YOUR_BOT_TOKEN", "telegram_chat_id": "YOUR_CHAT_ID", "telegram_enabled": true&#125;&#39;
                </div>
              </div>

              {/* Discord Box */}
              <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-indigo-400">Discord Webhook</span>
                  <span className="text-[10px] bg-slate-900 px-2 py-0.5 rounded text-slate-400">Outbound Alerts</span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  1. In Discord Server Settings &rarr; Integrations &rarr; Webhooks.<br />
                  2. Create Webhook &rarr; Copy Webhook URL.<br />
                  3. Blackboard events &amp; approvals are sent straight to your channel!
                </p>
                <div className="bg-slate-900 p-2.5 rounded font-mono text-[10px] text-slate-300 break-all">
                  curl -X POST {apiBaseUrl}/channels/config \<br />
                  &nbsp;&nbsp;-H "Content-Type: application/json" \<br />
                  &nbsp;&nbsp;-d &#39;&#123;"discord_webhook_url": "YOUR_DISCORD_WEBHOOK_URL", "discord_enabled": true&#125;&#39;
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* SECTION 5: CURL CHEATSHEET */}
      {activeSection === "curl_cheatsheet" && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">One-Liner Terminal Testing Cheatsheet</h3>
          <div className="space-y-3">
            {[
              {
                title: "1. Trigger an Autonomous Pipeline Run",
                cmd: `curl -X POST ${apiBaseUrl}/workflows/code_review_pipeline/run \\\n  -H "Content-Type: application/json" \\\n  -d '{"initial_prompt": "Implement async token-bucket rate limiter for FastAPI"}'`,
              },
              {
                title: "2. Chat with Concierge Admin Agent",
                cmd: `curl -X POST ${apiBaseUrl}/chat \\\n  -H "Content-Type: application/json" \\\n  -d '{"message": "Show platform health and active agents"}'`,
              },
              {
                title: "3. Inject Live Operator Steering Directive",
                cmd: `curl -X POST ${apiBaseUrl}/blackboard/steer \\\n  -H "Content-Type: application/json" \\\n  -d '{"content": "Keep all code under 80 words and verify type annotations."}'`,
              },
              {
                title: "4. Check Router Health & Active Cooldowns",
                cmd: `curl -s ${apiBaseUrl}/llm/status | python3 -m json.tool`,
              },
            ].map((item, idx) => (
              <div key={idx} className="bg-slate-950 border border-slate-800 rounded-xl p-3.5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-300">{item.title}</span>
                  <button
                    onClick={() => copy(item.cmd, idx)}
                    className="min-h-[32px] px-2.5 py-1 text-[11px] font-mono rounded bg-slate-800 hover:bg-slate-700 text-indigo-300 transition"
                  >
                    {copiedCmd === idx ? "Copied!" : "Copy"}
                  </button>
                </div>
                <pre className="text-[11px] font-mono text-slate-400 bg-slate-900/90 p-2.5 rounded-lg overflow-x-auto whitespace-pre">
                  {item.cmd}
                </pre>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
