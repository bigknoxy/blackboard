#!/usr/bin/env python3
"""
End-to-End Live LAN LLM Verification Script.
Configures the 4 agents to use the live LAN LLM cluster:
  - architect   -> CT118  192.168.1.180:8080/v1 (groot-19b)
  - coder       -> Ollama 192.168.1.150:11434   (qwen2.5-coder:7b)
  - qa-tester   -> LAPTOP 192.168.1.149:8090/v1 (lfm3-1.7b)
  - sec-auditor -> CT118  192.168.1.180:8081/v1 (qwen3-4b)
Triggers `code_review_pipeline` and verifies real LLM completions on the Blackboard.
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def api_call(method: str, path: str, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("1. Updating dynamic agents in PostgreSQL/Redis with live LAN model endpoints...")
    live_configs = {
        "architect": {
            "name": "Principal Systems Architect (CT118 qwen3-4b)",
            "inference_config": {
                "provider": "openai",
                "model_name": "qwen3-4b",
                "base_url": "http://192.168.1.180:8081/v1",
                "api_key": None,
                "temperature": 0.2,
            },
        },
        "coder": {
            "name": "Staff Implementation Engineer (Ollama qwen2.5-coder:0.5b)",
            "inference_config": {
                "provider": "ollama",
                "model_name": "qwen2.5-coder:0.5b",
                "base_url": "http://192.168.1.150:11434",
                "api_key": None,
                "temperature": 0.15,
            },
        },
        "qa-tester": {
            "name": "Autonomous Verification Engineer (CT118 lfm3-1.7b)",
            "inference_config": {
                "provider": "openai",
                "model_name": "lfm3-1.7b",
                "base_url": "http://192.168.1.180:8090/v1",
                "api_key": None,
                "temperature": 0.1,
            },
        },
        "sec-auditor": {
            "name": "Application Security Auditor (CT118 lfm3-1.7b)",
            "inference_config": {
                "provider": "openai",
                "model_name": "lfm3-1.7b",
                "base_url": "http://192.168.1.180:8090/v1",
                "api_key": None,
                "temperature": 0.1,
            },
        },
    }

    for agent_id, update_body in live_configs.items():
        updated = api_call("PUT", f"/agents/{agent_id}", update_body)
        cfg = updated["inference_config"]
        print(
            f"   -> @{agent_id}: provider={cfg['provider']} model={cfg['model_name']} url={cfg['base_url']}"
        )

    print("\n2. Injecting live operator steering onto the Blackboard...")
    steer = api_call(
        "POST",
        "/blackboard/steer",
        {
            "workflow_id": "code_review_pipeline",
            "content": "Keep all code and review responses under 120 words and use async/await.",
        },
    )
    print(f"   -> Steering entry id={steer['id']}")

    print("\n3. Triggering `code_review_pipeline` run across stateless worker pool...")
    run = api_call(
        "POST",
        "/workflows/code_review_pipeline/run",
        {
            "initial_prompt": "Write a compact async Python token-bucket rate limiter function for FastAPI."
        },
    )
    run_id = run["id"]
    print(f"   -> Started workflow run_id={run_id} status={run['status']}")

    print("\n4. Polling Blackboard as workers execute real LAN LLM calls...")
    seen_ids = set()
    for _ in range(45):
        entries = api_call("GET", "/blackboard?workflow_id=code_review_pipeline&limit=30")
        for e in reversed(entries):
            meta = e.get("metadata") or e.get("metadata_") or {}
            if meta.get("run_id") == run_id and e["id"] not in seen_ids:
                seen_ids.add(e["id"])
                llm_src = meta.get("llm_source", "operator_seed")
                preview = e["content"].replace("\n", " ")[:160]
                print(
                    f"   + [{e['entry_type'].upper()}] by @{e['agent_id']} ({llm_src}): {preview}..."
                )

        runs = api_call("GET", "/workflows/code_review_pipeline/runs")
        current_run = next((r for r in runs if r["id"] == run_id), None)
        if current_run and current_run["status"] in ("AWAITING_APPROVAL", "COMPLETED"):
            print(
                f"\n==> Workflow run reached terminal/gate state: {current_run['status']} "
                f"(completed_nodes={current_run['completed_nodes']}, current_nodes={current_run['current_nodes']})"
            )
            break
        time.sleep(3)


if __name__ == "__main__":
    main()
