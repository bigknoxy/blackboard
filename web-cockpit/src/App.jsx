import React, { useCallback, useEffect, useState } from "react";
import { api, WS_URL } from "./api";
import BlackboardFeed from "./components/BlackboardFeed";
import BpmnEditor from "./components/BpmnEditor";
import HITLGates from "./components/HITLGates";
import ConciergeChat from "./components/ConciergeChat";
import CockpitHome from "./components/CockpitHome";
import AgentManager from "./components/AgentManager";
import ToolsRegistry from "./components/ToolsRegistry";
import UserGuide from "./components/UserGuide";
import BacklogModal from "./components/BacklogModal";
import {
  NAVIGATION_TABS,
  MORE_DRAWER_ITEMS,
  DESKTOP_MODES,
} from "./constants/navigation";

export default function App() {
  const [mobilePanel, setMobilePanel] = useState("concierge"); // Defaults to Conversational Cockpit Home
  const [desktopFocus, setDesktopFocus] = useState("home"); // Defaults to Conversational Cockpit Home
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
  const [touchStartY, setTouchStartY] = useState(null);
  const [touchStartTime, setTouchStartTime] = useState(null);
  const [swipeEnabled, setSwipeEnabled] = useState(true);
  const [backlogOpen, setBacklogOpen] = useState(false);
  const [backlogCount, setBacklogCount] = useState(0);
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);

  const PANEL_ORDER = [
    "concierge",
    "board",
    "approvals",
    "agents",
    "workflows",
    "tools",
    "guide",
  ];

  const refreshAll = useCallback(async () => {
    try {
      const [agList, tlList, wfList, bbList, hitlList, bList] = await Promise.all([
        api.listAgents(),
        api.listTools(),
        api.listWorkflows(),
        api.listBlackboard(selectedWorkflowId),
        api.listHitlTickets(),
        api.listBacklog ? api.listBacklog() : Promise.resolve([]),
      ]);
      setAgents(agList || []);
      setTools(tlList || []);
      setWorkflows(wfList || []);
      setBlackboardEntries(bbList || []);
      setHitlTickets(hitlList || []);
      if (Array.isArray(bList)) setBacklogCount(bList.length);
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

  // Helper to detect if touch originates inside an interactive element, diagram, code block, or scrollable area
  function isSwipeIgnoredTarget(target) {
    if (!target || typeof target.closest !== "function") return false;
    const ignored = target.closest(
      [
        "[data-no-swipe]",
        ".djs-container",
        ".bjs-container",
        ".djs-overlay-container",
        "svg",
        "canvas",
        "pre",
        "code",
        "textarea",
        "input",
        "select",
        "button",
        '[role="button"]',
        ".overflow-x-auto",
        ".overflow-x-scroll",
      ].join(", ")
    );
    return Boolean(ignored);
  }

  // Hardened Mobile horizontal swipe gesture handler
  function handleTouchStart(e) {
    if (!swipeEnabled) return;
    if (e.touches && e.touches.length === 1) {
      if (isSwipeIgnoredTarget(e.target)) {
        setTouchStartX(null);
        setTouchStartY(null);
        return;
      }
      setTouchStartX(e.touches[0].clientX);
      setTouchStartY(e.touches[0].clientY);
      setTouchStartTime(Date.now());
    }
  }

  function handleTouchEnd(e) {
    if (
      !swipeEnabled ||
      touchStartX === null ||
      touchStartY === null ||
      !e.changedTouches ||
      !e.changedTouches[0]
    ) {
      setTouchStartX(null);
      setTouchStartY(null);
      return;
    }

    const touch = e.changedTouches[0];
    const deltaX = touch.clientX - touchStartX;
    const deltaY = touch.clientY - touchStartY;
    const elapsed = Date.now() - (touchStartTime || 0);

    // Intentional horizontal gesture filtering:
    // 1. Must be a rapid swipe (< 650ms), preventing drag/pinch/inspection misfires
    // 2. Horizontal distance must be substantial (>= 115px instead of 75px)
    // 3. Vertical drift must be small (< 60px) to prevent vertical scrolling from flipping tabs
    // 4. Horizontal travel must be at least 2.2x vertical travel to ensure horizontal intent
    const isHorizontalSwipe =
      elapsed < 650 &&
      Math.abs(deltaX) >= 115 &&
      Math.abs(deltaY) < 60 &&
      Math.abs(deltaX) > Math.abs(deltaY) * 2.2;

    if (isHorizontalSwipe) {
      const idx = PANEL_ORDER.indexOf(mobilePanel);
      if (deltaX < 0 && idx < PANEL_ORDER.length - 1) {
        setMobilePanel(PANEL_ORDER[idx + 1]);
      } else if (deltaX > 0 && idx > 0) {
        setMobilePanel(PANEL_ORDER[idx - 1]);
      }
    }
    setTouchStartX(null);
    setTouchStartY(null);
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

            {/* Backlog & Ideas Quick Launcher */}
            <button
              type="button"
              onClick={() => setBacklogOpen(true)}
              className="min-h-[44px] px-3 py-1.5 rounded-lg bg-indigo-950/80 hover:bg-indigo-900 border border-indigo-700/80 text-indigo-200 text-xs font-semibold flex items-center gap-1.5 transition"
              title="Open Backlog, Ideas & Feature Requests"
            >
              <span>💡</span>
              <span className="hidden sm:inline">Backlog</span>
              <span className="px-1.5 py-0.5 rounded-full bg-indigo-800 text-[10px] font-mono">
                {backlogCount}
              </span>
            </button>

            {/* Swipe Lock / Sensitivity Toggle */}
            <button
              type="button"
              onClick={() => setSwipeEnabled(!swipeEnabled)}
              className={`min-h-[44px] min-w-[44px] px-2.5 py-1.5 rounded-lg text-xs font-mono font-semibold border transition flex items-center gap-1 ${
                swipeEnabled
                  ? "bg-slate-900 border-slate-700 text-slate-300 hover:text-white"
                  : "bg-amber-950/70 border-amber-700 text-amber-300"
              }`}
              title={
                swipeEnabled
                  ? "Mobile swipe navigation active (Tap to lock)"
                  : "Mobile swipe navigation locked (Tap to enable)"
              }
            >
              <span>{swipeEnabled ? "👆" : "🔒"}</span>
              <span className="hidden md:inline">
                {swipeEnabled ? "Swipe" : "Locked"}
              </span>
            </button>

            {/* Desktop layout mode switcher (lg+) */}
            <div className="hidden lg:flex items-center bg-slate-950 border border-slate-800 rounded-lg p-1 gap-1">
              {DESKTOP_MODES.map((mode) => {
                let dynamicLabel = mode.label;
                if (mode.dynamicCount === "agents") dynamicLabel = `Agents (${agents.length})`;
                if (mode.dynamicCount === "tools") dynamicLabel = `Tools (${tools.length})`;
                if (mode.dynamicCount === "approvals") dynamicLabel = `Approvals (${pendingHitlCount})`;

                return (
                  <button
                    key={mode.id}
                    onClick={() => setDesktopFocus(mode.id)}
                    className={`min-h-[36px] px-3 py-1 rounded text-xs font-semibold transition ${
                      desktopFocus === mode.id
                        ? "bg-indigo-600 text-white"
                        : "text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    {dynamicLabel}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Global Live Stream Activity Ribbon */}
        {liveEvents.length > 0 && (
          <div
            data-testid="global-live-stream-ribbon"
            className="border-t border-slate-800/80 bg-slate-950/70 px-4 py-1.5 flex items-center justify-between text-xs overflow-x-auto no-scrollbar gap-3"
          >
            <div className="flex items-center gap-2 truncate">
              <span className="relative flex h-2 w-2 shrink-0">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              <span className="text-[10px] font-mono text-emerald-400 font-bold uppercase tracking-wider shrink-0">
                Live Activity:
              </span>
              <span className="text-slate-300 font-mono text-[11px] truncate">
                {liveEvents[0].event ? `[${liveEvents[0].event}]` : ""}{" "}
                {liveEvents[0].agent_id ? `@${liveEvents[0].agent_id}` : ""}{" "}
                {liveEvents[0].entry_type ? `#${liveEvents[0].entry_type}` : ""}{" "}
                {liveEvents[0].content ? `— ${liveEvents[0].content}` : ""}
              </span>
            </div>
            <button
              type="button"
              onClick={() => {
                setMobilePanel("board");
                setDesktopFocus("board");
              }}
              className="text-[11px] text-indigo-400 hover:text-indigo-300 font-semibold shrink-0 underline"
            >
              Inspect ➔
            </button>
          </div>
        )}
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
              {desktopFocus === "home" && (
                <CockpitHome
                  selectedWorkflowId={selectedWorkflowId}
                  currentWorkflow={currentWorkflow}
                  agents={agents}
                  tools={tools}
                  blackboardEntries={blackboardEntries}
                  hitlTickets={hitlTickets}
                  liveEvents={liveEvents}
                  wsConnected={wsConnected}
                  onNavigate={(panel) => {
                    setDesktopFocus(panel);
                    setMobilePanel(panel);
                  }}
                  onRefresh={refreshAll}
                />
              )}
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
              {desktopFocus === "agents" && (
                <AgentManager
                  agents={agents}
                  tools={tools}
                  onAgentUpdated={refreshAll}
                />
              )}
              {desktopFocus === "tools" && (
                <ToolsRegistry
                  tools={tools}
                  agents={agents}
                  onToolUpdated={refreshAll}
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
              {desktopFocus === "guide" && <UserGuide />}
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
          {mobilePanel === "agents" && (
            <AgentManager
              agents={agents}
              tools={tools}
              onAgentUpdated={refreshAll}
            />
          )}
          {mobilePanel === "tools" && (
            <ToolsRegistry
              tools={tools}
              agents={agents}
              onToolUpdated={refreshAll}
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
            <CockpitHome
              selectedWorkflowId={selectedWorkflowId}
              currentWorkflow={currentWorkflow}
              agents={agents}
              tools={tools}
              blackboardEntries={blackboardEntries}
              hitlTickets={hitlTickets}
              liveEvents={liveEvents}
              wsConnected={wsConnected}
              onNavigate={(panel) => setMobilePanel(panel)}
              onRefresh={refreshAll}
            />
          )}
          {mobilePanel === "guide" && <UserGuide />}
        </div>
      </main>

      {/* MOBILE (<lg): Ergonomic 5-Slot Bottom Navigation Bar with >=44x44px Touch Targets */}
      <nav
        data-testid="mobile-bottom-nav"
        aria-label="Mobile Primary Navigation"
        className="lg:hidden fixed bottom-0 inset-x-0 z-40 bg-slate-900/95 backdrop-blur border-t border-slate-800 px-2 py-1.5 grid grid-cols-5 gap-1.5"
      >
        <button
          data-testid="mobile-nav-concierge"
          onClick={() => {
            setMobilePanel("concierge");
            setMobileMoreOpen(false);
          }}
          className={`min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "concierge" && !mobileMoreOpen
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span className="text-sm">💬</span>
          <span>Concierge</span>
        </button>

        <button
          data-testid="mobile-nav-board"
          onClick={() => {
            setMobilePanel("board");
            setMobileMoreOpen(false);
          }}
          className={`min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "board" && !mobileMoreOpen
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span className="text-sm">📋</span>
          <span>Board</span>
        </button>

        <button
          data-testid="mobile-nav-approvals"
          onClick={() => {
            setMobilePanel("approvals");
            setMobileMoreOpen(false);
          }}
          className={`relative min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "approvals" && !mobileMoreOpen
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span className="text-sm">🔒</span>
          <span className="flex items-center gap-1">
            Approvals
            {pendingHitlCount > 0 && (
              <span
                data-testid="approvals-badge"
                className="inline-flex items-center justify-center px-1.5 py-0.2 text-[10px] font-bold rounded-full bg-amber-500 text-slate-950 shadow"
              >
                {pendingHitlCount}
              </span>
            )}
          </span>
        </button>

        <button
          data-testid="mobile-nav-agents"
          onClick={() => {
            setMobilePanel("agents");
            setMobileMoreOpen(false);
          }}
          className={`min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobilePanel === "agents" && !mobileMoreOpen
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span className="text-sm">🤖</span>
          <span>Agents</span>
        </button>

        <button
          data-testid="mobile-nav-more"
          onClick={() => setMobileMoreOpen(!mobileMoreOpen)}
          className={`min-h-[44px] min-w-[44px] flex flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
            mobileMoreOpen || ["workflows", "tools", "guide"].includes(mobilePanel)
              ? "bg-indigo-600 text-white"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <span className="text-sm">•••</span>
          <span>More</span>
        </button>
      </nav>

      {/* MOBILE More Drawer Bottom Sheet */}
      {mobileMoreOpen && (
        <div
          data-testid="mobile-more-drawer"
          className="lg:hidden fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex flex-col justify-end"
          onClick={() => setMobileMoreOpen(false)}
        >
          <div
            className="bg-slate-900 border-t border-slate-800 rounded-t-2xl p-5 space-y-3 shadow-2xl max-h-[80dvh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <span className="text-base">🚀</span>
                <h3 className="text-sm font-bold text-white">Platform Navigation &amp; Tools</h3>
              </div>
              <button
                type="button"
                data-testid="close-more-drawer"
                onClick={() => setMobileMoreOpen(false)}
                className="min-h-[44px] min-w-[44px] flex items-center justify-center text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2.5">
              <button
                type="button"
                data-testid="more-item-workflows"
                onClick={() => {
                  setMobilePanel("workflows");
                  setMobileMoreOpen(false);
                }}
                className={`min-h-[52px] p-3 rounded-xl border text-left flex flex-col justify-center transition ${
                  mobilePanel === "workflows"
                    ? "bg-indigo-600/30 border-indigo-500 text-white"
                    : "bg-slate-950 border-slate-800 text-slate-300 hover:border-slate-700"
                }`}
              >
                <span className="text-xs font-bold flex items-center gap-1.5">
                  <span>📐</span> Workflows &amp; BPMN
                </span>
                <span className="text-[10px] text-slate-400 mt-0.5">Dual graph &amp; timeline</span>
              </button>

              <button
                type="button"
                data-testid="more-item-tools"
                onClick={() => {
                  setMobilePanel("tools");
                  setMobileMoreOpen(false);
                }}
                className={`min-h-[52px] p-3 rounded-xl border text-left flex flex-col justify-center transition ${
                  mobilePanel === "tools"
                    ? "bg-indigo-600/30 border-indigo-500 text-white"
                    : "bg-slate-950 border-slate-800 text-slate-300 hover:border-slate-700"
                }`}
              >
                <span className="text-xs font-bold flex items-center gap-1.5">
                  <span>🛠️</span> Skills &amp; Tools ({tools.length})
                </span>
                <span className="text-[10px] text-slate-400 mt-0.5">MCP registry &amp; tester</span>
              </button>

              <button
                type="button"
                data-testid="more-item-backlog"
                onClick={() => {
                  setMobileMoreOpen(false);
                  setBacklogOpen(true);
                }}
                className="min-h-[52px] p-3 rounded-xl border border-slate-800 bg-slate-950 text-slate-300 hover:border-slate-700 text-left flex flex-col justify-center transition"
              >
                <span className="text-xs font-bold flex items-center gap-1.5">
                  <span>💡</span> Idea Backlog ({backlogCount})
                </span>
                <span className="text-[10px] text-slate-400 mt-0.5">Feature capture &amp; intake</span>
              </button>

              <button
                type="button"
                data-testid="more-item-guide"
                onClick={() => {
                  setMobilePanel("guide");
                  setMobileMoreOpen(false);
                }}
                className={`min-h-[52px] p-3 rounded-xl border text-left flex flex-col justify-center transition ${
                  mobilePanel === "guide"
                    ? "bg-indigo-600/30 border-indigo-500 text-white"
                    : "bg-slate-950 border-slate-800 text-slate-300 hover:border-slate-700"
                }`}
              >
                <span className="text-xs font-bold flex items-center gap-1.5">
                  <span>📖</span> System Guide
                </span>
                <span className="text-[10px] text-slate-400 mt-0.5">Architecture &amp; shortcuts</span>
              </button>
            </div>

            <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] text-slate-400 font-mono">
              <span>Nodes: Ollama • CT118 • NIM</span>
              <span className={wsConnected ? "text-emerald-400" : "text-amber-400"}>
                {wsConnected ? "● Live WebSocket" : "○ Disconnected"}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Persistent Backlog, Ideas & Intake Modal */}
      <BacklogModal
        isOpen={backlogOpen}
        onClose={() => setBacklogOpen(false)}
        onIntakeTriggered={(info) => {
          setBacklogOpen(false);
          refreshAll();
          if (info?.workflow_id) {
            setSelectedWorkflowId(info.workflow_id);
          } else {
            setSelectedWorkflowId("intake_pipeline");
          }
        }}
      />
    </div>
  );
}
