# Config-Driven Multi-Agent Blackboard System

Production-grade local multi-agent orchestration platform powered by **FastAPI**, **Redis Streams**, **PostgreSQL (`pgvector/pgvector:pg16`)**, **Model Context Protocol (MCP)**, **BPMN 2.0 (`bpmn-js`)**, and **React + Tailwind + Vite** with a **Mobile-First Web Cockpit** and **Popperian Falsification Test Harness**.

> **Zero-Memory Agent Onboarding:** See [AGENTS.md](file:///root/code/blackboard/AGENTS.md) for the complete architectural contract, database schemas, Redis stream/lease protocols, and falsification invariants.

---

## 1. Mobile Phone & Home Network (LAN + UFW) Setup

Both the **Web Cockpit (`0.0.0.0:5173`)** and **Control Plane API/WebSocket (`0.0.0.0:8000`)** bind to all network interfaces (`0.0.0.0`). When you open the Web Cockpit from your phone using your server's LAN IP (e.g. `http://192.168.1.50:5173`), [`web-cockpit/src/api.js`](file:///root/code/blackboard/web-cockpit/src/api.js) automatically detects `window.location.hostname` and routes all REST and WebSocket traffic to `http://<SERVER_LAN_IP>:8000` and `ws://<SERVER_LAN_IP>:8000/ws/blackboard`.

### Allow Mobile Access via UFW Firewall
```bash
chmod +x scripts/setup_lan_ufw.sh
sudo ./scripts/setup_lan_ufw.sh
```
Or run the `ufw` rules directly:
```bash
sudo ufw allow 5173/tcp comment 'Blackboard Web Cockpit (Mobile/LAN)'
sudo ufw allow 8000/tcp comment 'Blackboard FastAPI Control Plane & WebSocket (Mobile/LAN)'
```

---

## 2. Quick Start (32GB Homelab Tuned)

```bash
cp .env.example .env
docker compose up --build -d --scale agent-worker=3
```

---

## 3. Running the Test Suite (`pytest` + `vitest`)

### Backend Unit, Integration & Popperian Falsification Suite
```bash
pytest -v
```
Or inside the container:
```bash
docker compose exec -w /app control-plane pytest -v
```

### Frontend Mobile-First Ergonomics Suite
```bash
cd web-cockpit && npm install && npm test
```

---

## 4. Live LAN LLM Cluster & E2E Verification

This stack is pre-configured to route agent turns across your LAN LLM cluster (`192.168.1.0/24`):
- **Host / Cockpit / API (`192.168.1.100`)**:
  - Mobile & Desktop Web Cockpit: `http://192.168.1.100:5173`
  - FastAPI Control Plane & Swagger UI: `http://192.168.1.100:8000/docs`
- **Ollama Node (`192.168.1.150:11434`)**: `qwen2.5-coder:0.5b`, `qwen2.5-coder:7b`, `qwen3:8b`, `qwen3:1.7b`, `deepseek-coder-v2:16b-lite-instruct-q2_K`, `phi4-mini-reasoning:latest`, `gemma4:e2b`
- **CT118 Node (`192.168.1.180`)**: `:8080/v1` (`groot-19b`), `:8081/v1` (`qwen3-4b`), `:8090/v1` (`lfm3-1.7b`)
- **LAPTOP Node (`192.168.1.149`)**: `:8080/v1` (`mistral-24b`), `:8081/v1` (`qwen3-4b`), `:8090/v1` (`lfm3-1.7b`)

To run the live end-to-end multi-agent verification script against real LAN LLMs:
```bash
python3 scripts/run_live_llm_e2e.py
```

### NVIDIA NIM Free Tier + Local-First Router (`/api/v1/llm`)
Workers prioritize your **free local LAN endpoints** (`CT118`, `Ollama`, `LAPTOP`) and automatically escalate or fail over to **NVIDIA NIM** (`https://integrate.api.nvidia.com/v1`) when slow endpoints time out, or fall back from NVIDIA NIM to local LAN when rate-limited (`HTTP 429` or `NIM_MAX_RPM`).

Inject your NVIDIA NIM API key live at any time (no container restart needed):
```bash
curl -X POST http://192.168.1.100:8000/api/v1/llm/config \
  -H "Content-Type: application/json" \
  -d '{"nvidia_nim_api_key": "nvapi-YOUR_KEY_HERE", "prefer_local": true}'
```
Or paste it directly into the **Local LAN + NVIDIA NIM Router** card in the Web Cockpit (`http://192.168.1.100:5173` -> **Concierge** tab).

---

## 5. Blackboard Graph Engineering & Lineage Pruning (PostgreSQL DAG)

To eliminate context window explosion and LLM hallucinations in complex multi-round agent debates, the system features a topological Adjacency Graph architecture:

- **Adjacency Schema:**
  - `blackboard_nodes`: Typed nodes (`hypothesis`, `critique`, `spec`, `code`, `test_result`, `summary`, etc.) with statuses (`active`, `superseded`, `falsified`).
  - `blackboard_edges`: Directed edges (`references`, `critiques`, `revises`, `verifies`, `derives`) enforcing strict DAG topology (Falsifier 8).
- **Causal Lineage Pruning (`control-plane/app/graph.py`):**
  - Walks active ancestor paths while automatically pruning rejected code and superseded critiques (Falsifier 9).
  - Formats the filtered active ancestor path into structured `<context_lineage>` prompt blocks for worker persona hydration.
- **Tool Capability Graph (`control-plane/app/mcp_gateway.py`):**
  - MCP tools declare typed input/output contracts (`consumes_type`, `produces_type`).
  - `resolve_tool_chain(source_type, target_type)` deterministically calculates shortest execution paths via BFS, allowing smaller models to chain tools without multi-turn trial and error (Falsifier 10).
- **Web Cockpit DAG Canvas (`web-cockpit/src/components/BlackboardGraphCanvas.jsx`):**
  - Interactive DAG visualizer displaying live nodes, relationship edges, active path highlighting, and superseded node dimming.
  - Interactive tool chain resolver UI allowing operators to preview deterministic tool paths directly from the Cockpit.
