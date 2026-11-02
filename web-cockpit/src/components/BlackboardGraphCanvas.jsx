import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function BlackboardGraphCanvas({
  workflowId = "code_review_pipeline",
  onSelectNode,
}) {
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], active_lineage: [] });
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState("lineage"); // "lineage" | "graph"
  const [selectedNode, setSelectedNode] = useState(null);
  const [toolChainSource, setToolChainSource] = useState("query_keyword");
  const [toolChainTarget, setToolChainTarget] = useState("security_audit_report");
  const [resolvedChain, setResolvedChain] = useState(null);
  const [resolvingChain, setResolvingChain] = useState(false);

  async function fetchGraph() {
    if (!workflowId || !api.getBlackboardGraph) return;
    setLoading(true);
    try {
      const data = await api.getBlackboardGraph(workflowId);
      setGraphData(data || { nodes: [], edges: [], active_lineage: [] });
      if (data?.active_lineage?.length > 0 && !selectedNode) {
        setSelectedNode(data.active_lineage[data.active_lineage.length - 1]);
      }
    } catch {
      // ignore in tests or offline
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchGraph();
  }, [workflowId]);

  async function handleResolveToolChain(e) {
    e?.preventDefault();
    if (!api.resolveToolChain) return;
    setResolvingChain(true);
    try {
      const res = await api.resolveToolChain(toolChainSource, toolChainTarget);
      setResolvedChain(res);
    } catch {
      setResolvedChain({ resolved: false, tool_chain: null });
    } finally {
      setResolvingChain(false);
    }
  }

  const activeLineageIds = new Set((graphData.active_lineage || []).map((n) => n.id));

  return (
    <div
      data-testid="blackboard-graph-canvas"
      className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col gap-4 text-slate-100"
    >
      {/* Top Header & View Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
        <div>
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse"></span>
            <h2 className="text-sm sm:text-base font-semibold text-white">
              Blackboard DAG &amp; Lineage Pruning
            </h2>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-950 border border-slate-800 text-slate-400">
              {workflowId}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Topological causal ancestry: supersedes dead branches &amp; bounds token context
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Mode Switcher */}
          <div className="flex bg-slate-950 p-1 rounded-lg border border-slate-800 text-xs">
            <button
              type="button"
              onClick={() => setViewMode("lineage")}
              className={`min-h-[36px] px-3 py-1 rounded-md font-medium transition ${
                viewMode === "lineage"
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              Active Lineage ({graphData.active_lineage?.length || 0})
            </button>
            <button
              type="button"
              onClick={() => setViewMode("graph")}
              className={`min-h-[36px] px-3 py-1 rounded-md font-medium transition ${
                viewMode === "graph"
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              All Nodes ({graphData.nodes?.length || 0})
            </button>
          </div>

          <button
            type="button"
            onClick={fetchGraph}
            disabled={loading}
            className="min-h-[36px] px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 transition"
          >
            {loading ? "Refreshing..." : "Refresh DAG"}
          </button>
        </div>
      </div>

      {/* Legend & Stats */}
      <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
        <span className="text-slate-400 font-sans text-xs mr-1">Status:</span>
        <span className="px-2 py-0.5 rounded bg-cyan-950/70 border border-cyan-700/60 text-cyan-300">
          ● Active (In LLM Context)
        </span>
        <span className="px-2 py-0.5 rounded bg-amber-950/60 border border-amber-700/50 text-amber-300 line-through">
          ✕ Superseded (Pruned)
        </span>
        <span className="px-2 py-0.5 rounded bg-rose-950/60 border border-rose-700/50 text-rose-300">
          ▲ Falsified / Critique
        </span>
      </div>

      {/* Main Graph Content Area */}
      {viewMode === "lineage" ? (
        /* 1. TOPOLOGICAL ACTIVE LINEAGE VIEW */
        <div className="space-y-3">
          <div className="text-xs text-slate-300 font-medium">
            Active Ancestor Path (Hydrated into Worker <code className="text-cyan-300">&lt;context_lineage&gt;</code>):
          </div>

          {graphData.active_lineage?.length === 0 ? (
            <div className="p-6 text-center text-xs text-slate-500 bg-slate-950/50 rounded-xl border border-dashed border-slate-800">
              No active nodes in this workflow yet. Nodes will populate as agents contribute.
            </div>
          ) : (
            <div className="space-y-2.5">
              {graphData.active_lineage.map((node, idx) => {
                const isSelected = selectedNode?.id === node.id;
                return (
                  <div
                    key={node.id}
                    onClick={() => {
                      setSelectedNode(node);
                      if (onSelectNode) onSelectNode(node);
                    }}
                    className={`p-3.5 rounded-xl border transition cursor-pointer relative ${
                      isSelected
                        ? "bg-slate-950 border-cyan-500 shadow-md shadow-cyan-950/30"
                        : "bg-slate-950/70 border-slate-800 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <div className="flex items-center gap-2">
                        <span className="w-5 h-5 rounded-full bg-cyan-900/60 border border-cyan-700 text-cyan-300 text-[10px] font-mono font-bold flex items-center justify-center">
                          {idx + 1}
                        </span>
                        <span className="font-semibold text-xs text-white">
                          @{node.author_agent_id}
                        </span>
                        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-cyan-300">
                          #{node.node_type}
                        </span>
                      </div>
                      <span className="text-[10px] font-mono text-emerald-400 bg-emerald-950/50 px-2 py-0.5 rounded border border-emerald-800/60">
                        ACTIVE IN PROMPT
                      </span>
                    </div>

                    <div className="text-xs text-slate-300 font-sans line-clamp-2">
                      {typeof node.payload === "object"
                        ? node.payload.title || node.payload.code || node.payload.summary || JSON.stringify(node.payload)
                        : String(node.payload)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : (
        /* 2. FULL DAG VIEW (ACTIVE + PRUNED NODES & EDGES) */
        <div className="space-y-3">
          <div className="text-xs text-slate-300 font-medium">
            Full Graph Topology ({graphData.nodes?.length || 0} nodes, {graphData.edges?.length || 0} edges):
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {graphData.nodes?.map((node) => {
              const isActive = activeLineageIds.has(node.id);
              const isSuperseded = node.status === "superseded";
              const isSelected = selectedNode?.id === node.id;

              return (
                <div
                  key={node.id}
                  onClick={() => {
                    setSelectedNode(node);
                    if (onSelectNode) onSelectNode(node);
                  }}
                  className={`p-3.5 rounded-xl border transition cursor-pointer ${
                    isSelected
                      ? "bg-slate-950 border-indigo-500 shadow-md"
                      : isSuperseded
                      ? "bg-slate-950/30 border-slate-900/80 opacity-60 hover:opacity-100"
                      : "bg-slate-950 border-slate-800 hover:border-slate-700"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-semibold text-xs text-slate-200">
                        @{node.author_agent_id}
                      </span>
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400">
                        #{node.node_type}
                      </span>
                    </div>
                    <span
                      className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                        isActive
                          ? "bg-cyan-950/80 border-cyan-700 text-cyan-300"
                          : isSuperseded
                          ? "bg-amber-950/60 border-amber-800 text-amber-400 line-through"
                          : "bg-slate-900 border-slate-800 text-slate-500"
                      }`}
                    >
                      {node.status.toUpperCase()}
                    </span>
                  </div>

                  <div className="text-xs text-slate-300 font-mono text-[11px] truncate">
                    {typeof node.payload === "object"
                      ? JSON.stringify(node.payload)
                      : String(node.payload)}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Connected Edges Table */}
          {graphData.edges?.length > 0 && (
            <div className="mt-4 pt-3 border-t border-slate-800">
              <h4 className="text-xs font-semibold text-slate-300 mb-2">
                Directed Edge Relationships
              </h4>
              <div className="space-y-1.5 max-h-40 overflow-y-auto">
                {graphData.edges.map((e, idx) => (
                  <div
                    key={idx}
                    className="flex items-center gap-2 text-[11px] font-mono bg-slate-950 p-2 rounded-lg border border-slate-800/60"
                  >
                    <span className="text-slate-400 truncate max-w-[120px]">
                      {String(e.source_id).slice(0, 8)}...
                    </span>
                    <span className="px-2 py-0.5 rounded bg-indigo-950 border border-indigo-700 text-indigo-300 text-[10px] font-bold">
                      &mdash;[{e.relation_type}]&rarr;
                    </span>
                    <span className="text-slate-400 truncate max-w-[120px]">
                      {String(e.target_id).slice(0, 8)}...
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Tool Capability Chain Resolver Console */}
      <div className="mt-2 pt-4 border-t border-slate-800 bg-slate-950/60 p-3.5 rounded-xl border border-slate-800/80">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div>
            <h4 className="text-xs font-semibold text-white">
              Deterministic Tool Capability Graph Pathfinding
            </h4>
            <p className="text-[11px] text-slate-400">
              Resolve optimal tool pipelines without model trial-and-error
            </p>
          </div>
        </div>

        <form onSubmit={handleResolveToolChain} className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={toolChainSource}
            onChange={(e) => setToolChainSource(e.target.value)}
            placeholder="Source Type (e.g. query_keyword)"
            className="min-h-[40px] flex-1 min-w-[160px] bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs font-mono text-slate-100"
          />
          <span className="text-slate-400 text-xs font-bold">&rarr;</span>
          <input
            type="text"
            value={toolChainTarget}
            onChange={(e) => setToolChainTarget(e.target.value)}
            placeholder="Target Type (e.g. security_audit_report)"
            className="min-h-[40px] flex-1 min-w-[160px] bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs font-mono text-slate-100"
          />
          <button
            type="submit"
            disabled={resolvingChain}
            className="min-h-[40px] px-3.5 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg transition"
          >
            {resolvingChain ? "Resolving..." : "Resolve Tool Chain"}
          </button>
        </form>

        {resolvedChain && (
          <div className="mt-2.5 p-2.5 rounded-lg bg-slate-900 border border-slate-800 text-xs font-mono flex items-center gap-2 flex-wrap">
            <span className="text-slate-400">Result:</span>
            {resolvedChain.resolved && resolvedChain.tool_chain?.length > 0 ? (
              resolvedChain.tool_chain.map((tool, idx) => (
                <span
                  key={idx}
                  className="px-2 py-0.5 rounded bg-indigo-950 border border-indigo-700 text-indigo-300 font-bold"
                >
                  {idx + 1}. {tool}
                </span>
              ))
            ) : resolvedChain.resolved && resolvedChain.tool_chain?.length === 0 ? (
              <span className="text-emerald-400">Source matches target (0 steps needed).</span>
            ) : (
              <span className="text-rose-400">No reachable path between these types.</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
