import React, { useState } from "react";
import { api } from "../api";

export default function ToolsRegistry({ tools = [], agents = [], onToolUpdated }) {
  const [selectedServer, setSelectedServer] = useState("all");
  const [testingTool, setTestingTool] = useState(null);
  const [testArguments, setTestArguments] = useState("{}");
  const [testResult, setTestResult] = useState(null);
  const [testingLoading, setTestingLoading] = useState(false);
  const [togglingApproval, setTogglingApproval] = useState({});
  const [pathStart, setPathStart] = useState("search_query");
  const [pathEnd, setPathEnd] = useState("search_results");
  const [resolvedChain, setResolvedChain] = useState(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [registerModalOpen, setRegisterModalOpen] = useState(false);
  const [newTool, setNewTool] = useState({
    id: "",
    name: "",
    description: "",
    server_name: "custom-mcp",
    transport: "builtin",
    consumes_type: "",
    produces_type: "",
    requires_approval: false,
  });
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState("");

  const servers = ["all", ...Array.from(new Set(tools.map((t) => t.server_name).filter(Boolean)))];

  const filteredTools =
    selectedServer === "all"
      ? tools
      : tools.filter((t) => t.server_name === selectedServer);

  // Available unique types for pathfinding
  const allTypes = Array.from(
    new Set(
      tools
        .flatMap((t) => [t.consumes_type, t.produces_type])
        .filter(Boolean)
    )
  );

  async function handleToggleApproval(tool) {
    setTogglingApproval((prev) => ({ ...prev, [tool.id]: true }));
    try {
      const nextVal = !tool.requires_approval;
      await api.updateTool(tool.id, { requires_approval: nextVal });
      if (onToolUpdated) await onToolUpdated();
    } catch (err) {
      console.error("Failed to toggle approval:", err);
    } finally {
      setTogglingApproval((prev) => ({ ...prev, [tool.id]: false }));
    }
  }

  function handleOpenTester(tool) {
    setTestingTool(tool);
    setTestResult(null);

    // Provide sensible default payloads
    if (tool.id === "web_search") {
      setTestArguments(
        JSON.stringify(
          { query: "multi-agent blackboard architecture", num_results: 3 },
          null,
          2
        )
      );
    } else if (tool.id === "repo_search") {
      setTestArguments(
        JSON.stringify({ query: "MCPToolboxGateway", scope: "all" }, null, 2)
      );
    } else if (tool.id === "ast_complexity_analyzer") {
      setTestArguments(
        JSON.stringify(
          { code_snippet: "def compute():\n    for i in range(10):\n        yield i" },
          null,
          2
        )
      );
    } else if (tool.id === "static_security_scanner") {
      setTestArguments(
        JSON.stringify(
          { target: "eval(user_input)", ruleset: "owasp-top-10" },
          null,
          2
        )
      );
    } else if (tool.id === "pytest_sandbox_runner") {
      setTestArguments(
        JSON.stringify({ test_suite: "tests/unit/test_models.py" }, null, 2)
      );
    } else {
      setTestArguments(JSON.stringify(tool.input_schema?.properties || {}, null, 2));
    }
  }

  async function handleExecuteTest() {
    if (!testingTool) return;
    setTestingLoading(true);
    setTestResult(null);
    try {
      let parsedArgs = {};
      try {
        parsedArgs = JSON.parse(testArguments);
      } catch {
        throw new Error("Invalid JSON arguments format.");
      }

      const res = await api.invokeTool({
        agent_id: "operator",
        workflow_id: "code_review_pipeline",
        task_id: `test-${Date.now()}`,
        tool_id: testingTool.id,
        arguments: parsedArgs,
        bypass_approval: true,
      });
      setTestResult(res);
    } catch (err) {
      setTestResult({ status: "ERROR", message: err.message });
    } finally {
      setTestingLoading(false);
    }
  }

  async function handleFindChain() {
    if (!pathStart || !pathEnd) return;
    setChainLoading(true);
    try {
      if (api.resolveToolChain) {
        const res = await api.resolveToolChain(pathStart, pathEnd);
        setResolvedChain(res?.tool_chain || []);
      }
    } catch (err) {
      console.warn("Pathfinding error:", err);
      setResolvedChain([]);
    } finally {
      setChainLoading(false);
    }
  }

  async function handleCreateTool(e) {
    e.preventDefault();
    if (!newTool.id || !newTool.name) {
      setRegisterError("Tool ID and Name are required.");
      return;
    }
    setRegistering(true);
    setRegisterError("");
    try {
      await api.createTool({
        id: newTool.id.trim(),
        name: newTool.name.trim(),
        description: newTool.description.trim() || "Custom MCP Tool",
        server_name: newTool.server_name.trim() || "custom-mcp",
        transport: newTool.transport || "builtin",
        consumes_type: newTool.consumes_type.trim() || null,
        produces_type: newTool.produces_type.trim() || null,
        requires_approval: Boolean(newTool.requires_approval),
        input_schema: { type: "object", properties: {} },
      });
      setRegisterModalOpen(false);
      setNewTool({
        id: "",
        name: "",
        description: "",
        server_name: "custom-mcp",
        transport: "builtin",
        consumes_type: "",
        produces_type: "",
        requires_approval: false,
      });
      if (onToolUpdated) await onToolUpdated();
    } catch (err) {
      setRegisterError(err.message || "Failed to register tool.");
    } finally {
      setRegistering(false);
    }
  }

  return (
    <div
      data-testid="tools-registry-container"
      className="space-y-6 w-full max-w-full overflow-x-hidden"
    >
      {/* Header Banner */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 sm:p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl">🛠️</span>
            <h2 className="text-lg font-bold text-white tracking-tight">
              MCP Tools &amp; Capability Registry
            </h2>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-indigo-950 text-indigo-300 border border-indigo-800">
              {tools.length} Tools Active
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1 max-w-2xl leading-relaxed">
            Centralized Model Context Protocol (MCP) gateway managing stdio, SSE, and builtin tool manifests.
            Enforces per-agent RBAC manifests, typed input/output capability graphs, and human sign-off gates.
          </p>
        </div>

        <button
          type="button"
          data-testid="open-register-tool-btn"
          onClick={() => setRegisterModalOpen(true)}
          className="min-h-[44px] px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-md flex items-center gap-2 transition shrink-0"
        >
          <span>＋</span>
          <span>Register New Tool</span>
        </button>
      </div>

      {/* Visual Tool Capability Flow & Pathfinding Section */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 sm:p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div>
            <h3 className="text-sm font-bold text-white flex items-center gap-2">
              <span>⚡</span>
              <span>Tool Capability Pipeline &amp; Type Pathfinding</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Tools declare <code className="text-cyan-400">consumes_type</code> and <code className="text-emerald-400">produces_type</code>.
              The shortest execution paths are linked deterministically without trial-and-error.
            </p>
          </div>

          {/* Quick Pathfinder Controls */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 text-xs text-slate-300">
              <span className="text-[11px] text-slate-400">From:</span>
              <select
                data-testid="path-start-select"
                value={pathStart}
                onChange={(e) => setPathStart(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-white"
              >
                {allTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex items-center gap-1.5 text-xs text-slate-300">
              <span className="text-[11px] text-slate-400">To:</span>
              <select
                data-testid="path-end-select"
                value={pathEnd}
                onChange={(e) => setPathEnd(e.target.value)}
                className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-white"
              >
                {allTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>

            <button
              type="button"
              data-testid="find-chain-btn"
              onClick={handleFindChain}
              disabled={chainLoading}
              className="min-h-[36px] px-3 py-1 bg-cyan-600 hover:bg-cyan-500 text-white rounded text-xs font-semibold transition"
            >
              {chainLoading ? "Tracing..." : "Resolve Chain"}
            </button>
          </div>
        </div>

        {/* Resolved Path Pill */}
        {resolvedChain !== null && (
          <div
            data-testid="resolved-chain-banner"
            className="p-3 rounded-lg bg-slate-950 border border-cyan-500/40 text-xs flex flex-wrap items-center gap-2"
          >
            <span className="font-semibold text-cyan-400">
              Shortest Path ({pathStart} ➔ {pathEnd}):
            </span>
            {resolvedChain.length === 0 ? (
              <span className="text-slate-400 italic">
                {pathStart === pathEnd
                  ? "Direct match (0 hops needed)."
                  : "No valid composition path exists between these types."}
              </span>
            ) : (
              <div className="flex flex-wrap items-center gap-1.5 font-mono">
                {resolvedChain.map((tid, idx) => (
                  <React.Fragment key={tid}>
                    <span className="px-2 py-0.5 rounded bg-cyan-950 text-cyan-200 border border-cyan-700">
                      {tid}
                    </span>
                    {idx < resolvedChain.length - 1 && (
                      <span className="text-cyan-500">➔</span>
                    )}
                  </React.Fragment>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Live Visual Capability Flow Visualizer */}
        <div className="overflow-x-auto pb-2">
          <div className="min-w-[620px] grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {tools.map((t) => (
              <div
                key={t.id}
                className={`p-3 rounded-xl border transition flex flex-col justify-between gap-2.5 ${
                  resolvedChain?.includes(t.id)
                    ? "bg-cyan-950/30 border-cyan-500 shadow-md shadow-cyan-950/40"
                    : "bg-slate-950 border-slate-800/80 hover:border-slate-700"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="font-mono text-xs font-bold text-white flex items-center gap-1.5">
                    <span className="text-indigo-400">⚙️</span>
                    <span>{t.name}</span>
                  </div>
                  <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-slate-900 border border-slate-700 text-slate-300">
                    {t.server_name}
                  </span>
                </div>

                {/* Input -> Output Type Flow */}
                <div className="flex items-center justify-between text-[11px] font-mono bg-slate-900/80 p-2 rounded-lg border border-slate-800">
                  <div className="flex flex-col">
                    <span className="text-[9px] uppercase tracking-wider text-slate-500">
                      Consumes
                    </span>
                    <span className="text-cyan-400 font-semibold truncate max-w-[95px]">
                      {t.consumes_type || "any"}
                    </span>
                  </div>

                  <span className="text-slate-500 text-base">➔</span>

                  <div className="flex flex-col text-right">
                    <span className="text-[9px] uppercase tracking-wider text-slate-500">
                      Produces
                    </span>
                    <span className="text-emerald-400 font-semibold truncate max-w-[95px]">
                      {t.produces_type || "any"}
                    </span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1 border-t border-slate-800/60 text-xs">
                  <span className="text-[11px] text-slate-400">
                    {t.requires_approval ? "🔒 HITL Approval" : "⚡ Autonomous"}
                  </span>
                  <button
                    type="button"
                    onClick={() => handleOpenTester(t)}
                    className="text-indigo-400 hover:text-indigo-300 text-xs font-semibold flex items-center gap-1"
                  >
                    <span>Test</span>
                    <span>▶</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Server Filter Tabs */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 no-scrollbar">
        {servers.map((s) => (
          <button
            key={s}
            type="button"
            data-testid={`filter-server-${s}`}
            onClick={() => setSelectedServer(s)}
            className={`min-h-[44px] px-3.5 py-1.5 rounded-lg text-xs font-semibold transition shrink-0 ${
              selectedServer === s
                ? "bg-indigo-600 text-white"
                : "bg-slate-900 text-slate-400 hover:text-slate-200 border border-slate-800"
            }`}
          >
            {s === "all" ? "All Servers" : s}
          </button>
        ))}
      </div>

      {/* Tools Roster Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {filteredTools.map((tool) => {
          const isToggling = Boolean(togglingApproval[tool.id]);
          return (
            <div
              key={tool.id}
              data-testid={`tool-card-${tool.id}`}
              className="bg-slate-900 border border-slate-800 hover:border-slate-700 rounded-xl p-4 sm:p-5 flex flex-col justify-between gap-4 transition shadow-sm"
            >
              <div className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h4 className="text-sm font-bold text-white font-mono flex items-center gap-1.5">
                      {tool.id === "web_search" ? "🌐" : "🔧"} {tool.name}
                    </h4>
                    <span className="text-[11px] font-mono text-slate-400">
                      ID: {tool.id}
                    </span>
                  </div>

                  <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded-full bg-slate-950 border border-slate-800 text-slate-300 shrink-0">
                    {tool.transport}
                  </span>
                </div>

                <p className="text-xs text-slate-300 leading-relaxed line-clamp-3">
                  {tool.description}
                </p>

                {/* Capability Type Badges */}
                <div className="flex flex-wrap items-center gap-2 pt-1 font-mono text-[11px]">
                  <span className="px-2 py-0.5 rounded bg-cyan-950/80 text-cyan-300 border border-cyan-800/60">
                    in: {tool.consumes_type || "any"}
                  </span>
                  <span className="text-slate-500">➔</span>
                  <span className="px-2 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-800/60">
                    out: {tool.produces_type || "any"}
                  </span>
                </div>
              </div>

              {/* Action Bar: HITL Approval Toggle & Test Button */}
              <div className="pt-3 border-t border-slate-800/80 flex items-center justify-between gap-2">
                <button
                  type="button"
                  data-testid={`toggle-hitl-${tool.id}`}
                  onClick={() => handleToggleApproval(tool)}
                  disabled={isToggling}
                  className={`min-h-[44px] px-3 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 ${
                    tool.requires_approval
                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40 hover:bg-amber-500/30"
                      : "bg-slate-950 text-slate-400 border border-slate-800 hover:text-slate-200"
                  }`}
                >
                  <span>{tool.requires_approval ? "🔒" : "🔓"}</span>
                  <span>{tool.requires_approval ? "Requires HITL" : "Autonomous"}</span>
                </button>

                <button
                  type="button"
                  data-testid={`test-tool-btn-${tool.id}`}
                  onClick={() => handleOpenTester(tool)}
                  className="min-h-[44px] px-4 py-1.5 bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-200 border border-indigo-500/30 rounded-lg text-xs font-semibold transition flex items-center gap-1"
                >
                  <span>Test Tool</span>
                  <span>▶</span>
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Interactive Tool Tester Modal / Runner */}
      {testingTool && (
        <div
          data-testid="tool-tester-modal"
          className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4"
        >
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-xl w-full p-5 space-y-4 shadow-2xl max-h-[90dvh] overflow-y-auto">
            <div className="flex items-start justify-between border-b border-slate-800 pb-3">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <span>▶</span>
                  <span>Live Tool Execution Tester: {testingTool.name}</span>
                </h3>
                <span className="text-xs font-mono text-slate-400">
                  Tool ID: {testingTool.id} • Server: {testingTool.server_name}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setTestingTool(null)}
                className="min-h-[44px] min-w-[44px] flex items-center justify-center text-slate-400 hover:text-white text-lg"
              >
                ✕
              </button>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-300 block">
                JSON Arguments Payload:
              </label>
              <textarea
                data-testid="tester-arguments-input"
                rows={5}
                value={testArguments}
                onChange={(e) => setTestArguments(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs font-mono text-cyan-200 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setTestingTool(null)}
                className="min-h-[44px] px-4 py-2 bg-slate-800 text-slate-300 rounded-lg text-xs font-semibold hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="execute-test-btn"
                onClick={handleExecuteTest}
                disabled={testingLoading}
                className="min-h-[44px] px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow flex items-center gap-2 disabled:opacity-50"
              >
                {testingLoading ? (
                  <>
                    <span className="animate-spin">⏳</span>
                    <span>Executing Tool...</span>
                  </>
                ) : (
                  <>
                    <span>Execute Tool</span>
                    <span>▶</span>
                  </>
                )}
              </button>
            </div>

            {/* Test Result Output Box */}
            {testResult && (
              <div
                data-testid="tester-result-box"
                className="mt-4 pt-3 border-t border-slate-800 space-y-2"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                    Execution Response
                  </span>
                  <span
                    className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold ${
                      testResult.status === "EXECUTED" || testResult.result
                        ? "bg-emerald-950 text-emerald-300 border border-emerald-800"
                        : "bg-rose-950 text-rose-300 border border-rose-800"
                    }`}
                  >
                    {testResult.status || "COMPLETED"}
                  </span>
                </div>
                <pre className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs font-mono text-slate-200 overflow-x-auto max-h-60 leading-relaxed whitespace-pre-wrap">
                  {JSON.stringify(testResult, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Register Tool Modal */}
      {registerModalOpen && (
        <div
          data-testid="register-tool-modal"
          className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4"
        >
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full p-5 space-y-4 shadow-2xl">
            <div className="flex items-start justify-between border-b border-slate-800 pb-3">
              <h3 className="text-base font-bold text-white flex items-center gap-2">
                <span>＋</span>
                <span>Register New MCP / Built-in Tool</span>
              </h3>
              <button
                type="button"
                onClick={() => setRegisterModalOpen(false)}
                className="min-h-[44px] min-w-[44px] flex items-center justify-center text-slate-400 hover:text-white text-lg"
              >
                ✕
              </button>
            </div>

            {registerError && (
              <div className="p-3 bg-rose-950/60 border border-rose-800 rounded-lg text-xs text-rose-300">
                {registerError}
              </div>
            )}

            <form onSubmit={handleCreateTool} className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-slate-300 block mb-1">
                    Tool ID *
                  </label>
                  <input
                    data-testid="new-tool-id"
                    type="text"
                    required
                    placeholder="e.g. documentation_crawler"
                    value={newTool.id}
                    onChange={(e) =>
                      setNewTool({ ...newTool, id: e.target.value })
                    }
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white"
                  />
                </div>

                <div>
                  <label className="text-xs text-slate-300 block mb-1">
                    Tool Name *
                  </label>
                  <input
                    data-testid="new-tool-name"
                    type="text"
                    required
                    placeholder="e.g. documentation_crawler"
                    value={newTool.name}
                    onChange={(e) =>
                      setNewTool({ ...newTool, name: e.target.value })
                    }
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white"
                  />
                </div>
              </div>

              <div>
                <label className="text-xs text-slate-300 block mb-1">
                  Description
                </label>
                <textarea
                  data-testid="new-tool-description"
                  rows={2}
                  placeholder="Describe what this tool does..."
                  value={newTool.description}
                  onChange={(e) =>
                    setNewTool({ ...newTool, description: e.target.value })
                  }
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-slate-300 block mb-1">
                    Server Name
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. research-mcp"
                    value={newTool.server_name}
                    onChange={(e) =>
                      setNewTool({ ...newTool, server_name: e.target.value })
                    }
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white"
                  />
                </div>

                <div>
                  <label className="text-xs text-slate-300 block mb-1">
                    Transport
                  </label>
                  <select
                    value={newTool.transport}
                    onChange={(e) =>
                      setNewTool({ ...newTool, transport: e.target.value })
                    }
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white"
                  >
                    <option value="builtin">builtin</option>
                    <option value="stdio">stdio</option>
                    <option value="sse">sse</option>
                    <option value="http">http</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-slate-300 block mb-1">
                    Consumes Type
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. search_query"
                    value={newTool.consumes_type}
                    onChange={(e) =>
                      setNewTool({ ...newTool, consumes_type: e.target.value })
                    }
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white"
                  />
                </div>

                <div>
                  <label className="text-xs text-slate-300 block mb-1">
                    Produces Type
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. search_results"
                    value={newTool.produces_type}
                    onChange={(e) =>
                      setNewTool({ ...newTool, produces_type: e.target.value })
                    }
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="req-approval"
                  checked={newTool.requires_approval}
                  onChange={(e) =>
                    setNewTool({
                      ...newTool,
                      requires_approval: e.target.checked,
                    })
                  }
                  className="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0"
                />
                <label
                  htmlFor="req-approval"
                  className="text-xs text-slate-300 cursor-pointer"
                >
                  Require Human-in-the-Loop Approval Before Execution
                </label>
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setRegisterModalOpen(false)}
                  className="min-h-[44px] px-4 py-2 bg-slate-800 text-slate-300 rounded-lg text-xs font-semibold hover:bg-slate-700"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  data-testid="submit-register-tool"
                  disabled={registering}
                  className="min-h-[44px] px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow disabled:opacity-50"
                >
                  {registering ? "Registering..." : "Register Tool"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
