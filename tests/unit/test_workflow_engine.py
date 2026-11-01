from pathlib import Path
from app.workflow_engine import workflow_engine


def test_yaml_to_bpmn_and_back_roundtrip():
    yaml_path = (
        Path(__file__).resolve().parent.parent.parent
        / "workflows"
        / "code_review_pipeline.yaml"
    )
    raw_yaml = yaml_path.read_text(encoding="utf-8")
    parsed = workflow_engine.parse_yaml_to_graph(raw_yaml)

    assert parsed["id"] == "code_review_pipeline"
    node_types = {n["type"] for n in parsed["nodes"]}
    assert "ServiceTask" in node_types
    assert "ParallelGateway" in node_types
    assert "ExclusiveGateway" in node_types
    assert "UserTask" in node_types

    bpmn_xml = workflow_engine.graph_to_bpmn_xml(
        parsed["id"],
        parsed["name"],
        {"nodes": parsed["nodes"], "edges": parsed["edges"]},
    )
    assert "<bpmn:serviceTask" in bpmn_xml
    assert "<bpmn:parallelGateway" in bpmn_xml
    assert "<bpmn:exclusiveGateway" in bpmn_xml
    assert "<bpmn:userTask" in bpmn_xml
    assert "<bpmndi:BPMNDiagram" in bpmn_xml

    roundtrip_graph = workflow_engine.bpmn_xml_to_graph(bpmn_xml)
    assert len(roundtrip_graph["nodes"]) == len(parsed["nodes"])
    assert len(roundtrip_graph["edges"]) == len(parsed["edges"])

    # Verify custom agent_id and entry_type attributes survived roundtrip
    rt_by_id = {n["id"]: n for n in roundtrip_graph["nodes"]}
    assert rt_by_id["stage_research"]["agent_id"] == "architect"
    assert rt_by_id["stage_research"]["entry_type"] == "hypothesis"
    assert rt_by_id["stage_code"]["agent_id"] == "coder"
    assert rt_by_id["stage_code"]["entry_type"] == "code"
