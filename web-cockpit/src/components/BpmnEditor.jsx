import React, { useEffect, useRef, useState } from "react";
import BpmnModeler from "bpmn-js/lib/Modeler";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn-embedded.css";
import { api } from "../api";

export default function BpmnEditor({
  workflow,
  onWorkflowUpdated,
  onWorkflowTriggered,
  compact = false,
}) {
  const containerRef = useRef(null);
  const modelerRef = useRef(null);
  const [viewMode, setViewMode] = useState("auto"); // auto | canvas | timeline | yaml
  const [yamlText, setYamlText] = useState(workflow?.yaml_source || "");
  const [runPrompt, setRunPrompt] = useState(
    "Audit and harden Redis distributed task lease renewal and SQLAlchemy asyncpg pool settings."
  );
  const [statusMsg, setStatusMsg] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!containerRef.current) return;
    let modeler;
    try {
      modeler = new BpmnModeler({
        container: containerRef.current,
      });
      modelerRef.current = modeler;
    } catch (err) {
      console.warn("BPMN modeler init skipped in headless test:", err);
    }
    return () => {
      if (modeler && modeler.destroy) modeler.destroy();
      modelerRef.current = null;
    };
  }, []);

  useEffect(() => {
    setYamlText(workflow?.yaml_source || "");
    if (modelerRef.current && workflow?.bpmn_xml) {
      modelerRef.current
        .importXML(workflow.bpmn_xml)
        .then(() => {
          const canvas = modelerRef.current.get("canvas");
          canvas.zoom("fit-viewport");
        })
        .catch((err) => {
          console.warn("BPMN XML import warning:", err);
        });
    }
  }, [workflow]);

  function handleCanvasZoom(delta) {
    if (!modelerRef.current) return;
    try {
      const canvas = modelerRef.current.get("canvas");
      const current = canvas.zoom();
      canvas.zoom(Math.max(0.3, Math.min(2.5, current + delta)));
    } catch {
      // ignore if canvas not mounted
    }
  }

  async function handleSaveBpmnToYaml() {
    if (!modelerRef.current || !workflow) return;
    setSaving(true);
    setStatusMsg("");
    try {
      const { xml } = await modelerRef.current.saveXML({ format: true });
      const updated = await api.updateWorkflow(workflow.id, {
        bpmn_xml: xml,
      });
      setYamlText(updated.yaml_source || "");
      setStatusMsg("Synced BPMN 2.0 XML -> YAML State Graph.");
      if (onWorkflowUpdated) onWorkflowUpdated(updated);
    } catch (err) {
      setStatusMsg(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveYamlToBpmn() {
    if (!workflow) return;
    setSaving(true);
    setStatusMsg("");
    try {
      const updated = await api.updateWorkflow(workflow.id, {
        yaml_source: yamlText,
      });
      setStatusMsg("Synced YAML State Graph -> BPMN 2.0 XML.");
      if (onWorkflowUpdated) onWorkflowUpdated(updated);
    } catch (err) {
      setStatusMsg(`YAML parse error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleTriggerPipeline() {
    if (!workflow) return;
    setStatusMsg("Triggering workflow run...");
    try {
      const run = await api.triggerWorkflowRun(workflow.id, runPrompt);
      setStatusMsg(
        `Started Run ${run.id.slice(0, 8)} (status: ${run.status})`
      );
      if (onWorkflowTriggered) onWorkflowTriggered(run);
    } catch (err) {
      setStatusMsg(`Trigger failed: ${err.message}`);
    }
  }

  const nodes = workflow?.state_graph?.nodes || [];

  return (
    <div data-no-swipe="true" className="space-y-4 w-full max-w-full overflow-x-hidden">
      {/* Execution Trigger & View Mode Switcher */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-3.5 sm:p-4 space-y-3">
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1">
            Pipeline Goal for{" "}
            <span className="text-indigo-400 font-mono">
              {workflow?.id || "code_review_pipeline"}
            </span>
          </label>
          <input
            type="text"
            value={runPrompt}
            onChange={(e) => setRunPrompt(e.target.value)}
            className="min-h-[44px] w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs sm:text-sm text-slate-100"
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1.5">
            {[
              { id: "auto", label: "Responsive Auto" },
              { id: "timeline", label: "Step-List / Timeline" },
              { id: "canvas", label: "BPMN 2.0 Canvas" },
              { id: "yaml", label: "YAML Source" },
            ].map((mode) => (
              <button
                key={mode.id}
                type="button"
                onClick={() => setViewMode(mode.id)}
                className={`min-h-[44px] min-w-[44px] px-3 py-2 rounded-lg text-xs font-semibold border transition ${
                  viewMode === mode.id
                    ? "bg-indigo-600 text-white border-indigo-500"
                    : "bg-slate-950 text-slate-300 border-slate-800"
                }`}
              >
                {mode.label}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={handleTriggerPipeline}
            className="min-h-[44px] min-w-[44px] px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg shadow transition"
          >
            Run Pipeline
          </button>
        </div>
      </div>

      {statusMsg && (
        <div className="px-4 py-2.5 rounded-lg bg-indigo-950/60 border border-indigo-500/40 text-xs text-indigo-200">
          {statusMsg}
        </div>
      )}

      {/* Fallback Mobile Step-List / Timeline View */}
      <div
        data-testid="mobile-timeline-view"
        className={`${
          viewMode === "timeline"
            ? "block"
            : viewMode === "auto"
            ? "block md:hidden"
            : "hidden"
        } bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3`}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white">
            Step-List / Timeline View ({nodes.length} Stages)
          </h3>
          <span className="text-[11px] text-slate-400">
            Mobile Viewport Fallback
          </span>
        </div>
        <ol className="space-y-2.5">
          {nodes.map((node, index) => (
            <li
              key={node.id}
              className="p-3 rounded-lg bg-slate-950 border border-slate-800 flex items-start gap-3"
            >
              <span className="h-7 w-7 shrink-0 rounded-full bg-indigo-600/30 border border-indigo-500/40 text-indigo-300 text-xs font-bold flex items-center justify-center">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-1">
                  <span className="text-xs font-semibold text-white">
                    {node.name}
                  </span>
                  <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-amber-300">
                    {node.type}
                  </span>
                </div>
                {node.agent_id && (
                  <div className="text-xs text-indigo-300 font-mono mt-0.5">
                    Agent: @{node.agent_id} ({node.entry_type || "spec"})
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>

      {/* Visual BPMN 2.0 Canvas with Touch Pinch-to-Zoom & One-Finger Pan */}
      <div
        className={`${
          viewMode === "canvas"
            ? "block"
            : viewMode === "auto"
            ? "hidden md:block"
            : "hidden"
        } bg-slate-900 border border-slate-800 rounded-xl p-4`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div>
            <h3 className="text-sm font-semibold text-white">
              BPMN 2.0 Modeler (Pinch-to-Zoom & Touch Pan Enabled)
            </h3>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              aria-label="Zoom In"
              onClick={() => handleCanvasZoom(0.2)}
              className="min-h-[44px] min-w-[44px] px-3 rounded-lg bg-slate-800 text-white text-xs font-bold"
            >
              +
            </button>
            <button
              type="button"
              aria-label="Zoom Out"
              onClick={() => handleCanvasZoom(-0.2)}
              className="min-h-[44px] min-w-[44px] px-3 rounded-lg bg-slate-800 text-white text-xs font-bold"
            >
              &minus;
            </button>
            <button
              type="button"
              onClick={handleSaveBpmnToYaml}
              disabled={saving}
              className="min-h-[44px] min-w-[44px] px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg"
            >
              Sync BPMN &rarr; YAML
            </button>
          </div>
        </div>
        <div
          ref={containerRef}
          style={{ touchAction: "pan-x pan-y pinch-zoom" }}
          className={`w-full ${
            compact ? "h-[360px]" : "h-[440px]"
          } rounded-lg border border-slate-700 overflow-hidden`}
        />
      </div>

      {/* Bi-Directional YAML Editor */}
      {viewMode === "yaml" && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-white">
              Bi-Directional YAML State Graph
            </h3>
            <button
              type="button"
              onClick={handleSaveYamlToBpmn}
              disabled={saving}
              className="min-h-[44px] min-w-[44px] px-3 py-2 bg-indigo-600 text-white rounded-lg text-xs font-semibold"
            >
              Sync YAML &rarr; BPMN
            </button>
          </div>
          <textarea
            value={yamlText}
            onChange={(e) => setYamlText(e.target.value)}
            className="min-h-[320px] bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs font-mono text-emerald-300 focus:outline-none"
          />
        </div>
      )}
    </div>
  );
}
