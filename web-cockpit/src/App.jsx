import React, { useCallback, useEffect, useState } from "react";
import { api, WS_URL } from "./api";
import BlackboardFeed from "./components/BlackboardFeed";
import BpmnEditor from "./components/BpmnEditor";
import HITLGates from "./components/HITLGates";
import ConciergeChat from "./components/ConciergeChat";

export default function App() {
  const [mobilePanel, setMobilePanel] = useState("board"); // board | workflows | approvals | concierge
  const [desktopFocus, setDesktopFocus] = useState("split"); // split | board | workflows | approvals | concierge
  const [agents, setAgents] = useState([]);
  const [tools, setTools] = useState([]);
  const [workflows, setWorkflows] = useState([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(
    "code_review_pipeline"
  );
  const [blackboardEntries, setBlackboardEntries] = useState([]);
  const [hitlTickets, setHitlTickets] = useState([]);
  const [liveEvents, setLiveEvents] = useState([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [touchStartX, setTouchStartX] = useState(null);

  const PANEL_ORDER = ["board", "workflows", "approvals", "concierge"];

  const refreshAll = useCallback(async () => {
    try {
      const [agList, tlList, wfList, bbList, hitlList] = await Promise.all([
        api.listAgents(),
        api.listTools(),
        api.listWorkflows(),
        api.listBlackboard(selectedWorkflowId),
        api.listHitlTickets(),
      ]);
      setAgents(agList || []);
      setTools(tlList || []);
      setWorkflows(wfList || []);
      setBlackboardEntries(bbList || []);
      setHitlTickets(hitlList || []);
    } catch (err) {
      console.warn("Initial fetch warning:", err.message);
    }
  }, [selectedWorkflowId]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  // Connect live WebSocket stream to /ws/blackboard
  useEffect(() => {
    let ws;
    let reconnectTimer;

    function connectWs() {
      try {
        ws = new WebSocket(WS_URL);
        ws.onopen = () => setWsConnected(true);
        ws.onmessage = (evt) => {
          try {
            const data = JSON.parse(evt.data);
            setLiveEvents((prev) => [data, ...prev.slice(0, 49)]);
            if (
              data.event &&
              (data.event.startsWith("blackboard.") ||
                data.event.startsWith("worker.") ||
                data.event.startsWith("hitl.") ||
                data.event.startsWith("workflow."))
            ) {
              refreshAll();
            }
          } catch (err) {
            console.warn("WS parse error:", err);
          }
        };
        ws.onclose = () => {
          setWsConnected(false);
          reconnectTimer = setTimeout(connectWs, 3000);
        };
      } catch {
        setWsConnected(false);
      }
    }

    connectWs();
    return () => {
      clearTimeout(reconnectTimer);
      if (ws && ws.close) ws.close();
    };
  }, [refreshAll]);

  // Mobile horizontal swipe gesture handler between panels
  function handleTouchStart(e) {
    if (e.touches && e.touches.length === 1) {
      setTouchStartX(e.touches[0].clientX);
    }
  }

  function handleTouchEnd(e) {
    if (touchStartX === null || !e.changedTouches || !e.changedTouches[0]) {
      return;
    }
    const deltaX = e.changedTouches[0].clientX - touchStartX;
    if (Math.abs(deltaX) > 75) {
      const idx = PANEL_ORDER.indexOf(mobilePanel);
      if (deltaX < 0 && idx < PANEL_ORDER.length - 1) {
        setMobilePanel(PANEL_ORDER[idx + 1]);
      } else if (deltaX > 0 && idx > 0) {
        setMobilePanel(PANEL_ORDER[idx - 1]);
      }
    }
    setTouchStartX(null);
  }

  const currentWorkflow =
    workflows.find((w) => w.id === selectedWorkflowId) || workflows[0];
  const pendingHitlCount = hitlTickets.filter(
    (t) => t.status === "PENDING"
  ).length;

  return (
    <div
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      className="min-h-[100dvh] w-full overflow-x-hidden bg-slate-950 text-slate-100 flex flex-col pb-20 lg:pb-0"
    >
      {/* Top Cockpit Header */}
      <header className="sticky top-0 z-30 border-b border-slate-800 bg-slate-900/95 backdrop-blur px-3 sm:px-6 py-3">
        <div className="max-w-[1600px] mx-auto flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="h-10 w-10 shrink-0 rounded-lg bg-indigo-600 flex items-center justify-center font-bold text-white shadow">
              BB
            </div>
            <div className="truncate">
              <h1 className="text-sm sm:text-base font-bold tracking-tight text-white truncate">
                Multi-Agent Blackboard Cockpit
              </h1>
              <p className="text-[11px] text-slate-400 truncate">
                Dynamic MCP &bull; Scoped <code>pgvector</code> &bull; Redis
                Leases &bull; BPMN 2.0
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${
                wsConnected
                  ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
                  : "bg-amber-500/10 text-amber-300 border-amber-500/30"
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  wsConnected ? "bg-emerald-400 animate-pulse" : "bg-amber-400"
                }`}
              />
              <span className="hidden sm:inline">
                {wsConnected ? "WebSocket Live" : "Reconnecting..."}
              </span>
            </span>

            <select
              aria-label="Select Workflow"
              value={selectedWorkflowId}
              onChange={(e) => setSelectedWorkflowId(e.target.value)}
              className="min-h-[44px] bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 max-w-[190px] sm:max-w-xs truncate"
            >
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.id})
                </option>
              ))}
            </select>

            {/* Desktop layout mode switcher (lg+) */}
            <div className="hidden lg:flex items-center bg-slate-950 border border-slate-800 rounded-lg p-1 gap-1">
              {[
                { id: "split", label: "3-Column Split" },
                { id: "board", label: "Focus Board" },
                { id: "workflows", label: "Focus BPMN" },
                { id: "approvals", label: `Approvals (${pendingHitlCount})` },
                { id: "concierge", label: "Concierge" },
              ].map((mode) => (
                <button
                  key={mode.id}
                  onClick={() => setDesktopFocus(mode.id)}
                  className={`min-h-[36px] px-3 py-1 rounded text-xs font-semibold transition ${
                    desktopFocus === mode.id
                      ? "bg-indigo-600 text-white"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      {/* Main Content Shell */}
      <main className="flex-1 max-w-[1600px] w-full mx-auto p-3 sm:p-5">
        {/* DESKTOP (lg+): Multi-Column Split View (Left: Workflow/BPMN, Center: Live Blackboard Feed, Right: HITL Drawer & Concierge Chat) */}
        <div
          data-testid="desktop-split-shell"
          className="hidden lg:block"
        >
          {desktopFocus === "split" ? (
            <div className="grid grid-cols-12 gap-5 items-start">
              {/* Left Column (4 cols): Workflow / BPMN Modeler */}
              <section className="col-span-4 space-y-4">
                <BpmnEditor
                  workflow={currentWorkflow}
                  onWorkflowUpdated={refreshAll}
                  onWorkflowTriggered={refreshAll}
                  compact
                />
              </section>

              {/* Center Column (5 cols): Live Blackboard Feed */}
              <section className="col-span-5">
                <BlackboardFeed
                  entries={blackboardEntries}
                  selectedWorkflowId={selectedWorkflowId}
                  onRefresh={refreshAll}
                  liveEvents={liveEvents}
                />
              </section>

              {/* Right Column (3 cols): HITL Drawer & Concierge Chat */}
              <section className="col-span-3 space-y-5">
                <HITLGates
                  tickets={hitlTickets}
                  selectedWorkflowId={selectedWorkflowId}
                  onRefresh={refreshAll}
                />
                <ConciergeChat
                  selectedWorkflowId={selectedWorkflowId}
                  agents={agents}
                  tools={tools}
                  onStateMutated={refreshAll}
                />
              </section>
            </div>
          ) : (
            <div>
              {desktopFocus === "board" && (
                <BlackboardFeed
                  entries={blackboardEntries}
                  selectedWorkflowId={selectedWorkflowId}
                  onRefresh={refreshAll}
                  liveEvents={liveEvents}
                />
              )}
              {desktopFocus === "workflows" && (
                <BpmnEditor
                  workflow={currentWorkflow}
                  onWorkflowUpdated={refreshAll}
                  onWorkflowTriggered={refreshAll}
                />
              )}
              {desktopFocus === "approvals" && (
                <HITLGates
                  tickets={hitlTickets}
                  selectedWorkflowId={selectedWorkflowId}
                  onRefresh={refreshAll}
                />
              )}
              {desktopFocus === "concierge" && (
                <ConciergeChat
                  selectedWorkflowId={selectedWorkflowId}
                  agents={agents}
                  tools={tools}
                  onStateMutated={refreshAll}
                />
              )}
            </div>
          )}
        </div>

        {/* MOBILE (<lg): Single-Column Responsive View */}
        <div
          data-testid="mobile-single-column-shell"
          className="block lg:hidden"
        >
          {mobilePanel === "board" && (
            <BlackboardFeed
              entries={blackboardEntries}
              selectedWorkflowId={selectedWorkflowId}
              onRefresh={refreshAll}
              liveEvents={liveEvents}
            />
          )}
          {mobilePanel === "workflows" && (
            <BpmnEditor
              workflow={currentWorkflow}
              onWorkflowUpdated={refreshAll}
              onWorkflowTriggered={() => {
                refreshAll();
                setMobilePanel("board");
              }}
            />
          )}
          {mobilePanel === "approvals" && (
            <HITLGates
              tickets={hitlTickets}
              selectedWorkflowId={selectedWorkflowId}
              onRefresh={refreshAll}
            />
          )}
          {mobilePanel === "concierge" && (
            <ConciergeChat
              selectedWorkflowId={selectedWorkflowId}
              agents={agents}
              tools={tools}
              onStateMutated={refreshAll}
            />
          )}
        </div>
      </main>

      {/* MOBILE (<lg): Persistent Bottom Navigation Bar with >=44x44px Touch Targets */}
      <nav
        data-testid="mobile-bottom-nav"
        aria-label="Mobile Primary Navigation"
        className="lg:hidden fixed bottom-0 inset-x-0 z-40 bg-slate-900/95 backdrop-blur border-t border-slate-800 px-2 py-1.5 grid grid-cols-4 gap-1"
      >
        <button
          data-testid="mobile-nav-board"
          onClick={() => setMobilePanel("board")}
          className={`min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "board"
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span>Board</span>
        </button>

        <button
          data-testid="mobile-nav-workflows"
          onClick={() => setMobilePanel("workflows")}
          className={`min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "workflows"
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span>Workflows</span>
        </button>

        <button
          data-testid="mobile-nav-approvals"
          onClick={() => setMobilePanel("approvals")}
          className={`relative min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "approvals"
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span className="flex items-center gap-1">
            Approvals
            {pendingHitlCount > 0 && (
              <span
                data-testid="approvals-badge"
                className="inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-amber-500 text-slate-950"
              >
                {pendingHitlCount}
              </span>
            )}
          </span>
        </button>

        <button
          data-testid="mobile-nav-concierge"
          onClick={() => setMobilePanel("concierge")}
          className={`min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "concierge"
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span>Concierge</span>
        </button>
      </nav>
    </div>
  );
}
