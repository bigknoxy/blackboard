# AGENTS.md — Zero-Memory Agent Onboarding & System Contract

> **Purpose:** Any AI agent, developer, or automation script entering this repository with **zero prior memory** MUST read this document first to understand the complete architecture, invariants, database schemas, Redis stream contracts, mobile-first ergonomics, and Popperian falsification tests.

---

## 1. Executive Summary

This repository implements a **Config-Driven Multi-Agent Blackboard System** with:
1. **Zero Hardcoded Agent Personas:** Every agent (`architect`, `coder`, `qa-tester`, `sec-auditor`, or custom operator-created agents) is stored in PostgreSQL (`agents` table) and cached in Redis (`agent:config:{id}`).
2. **Scoped Persistent Vector Memory (`pgvector/pgvector:pg16`):** Each agent has isolated episodic, semantic, and reflection memories (`agent_memories` table, `vector(768)` compatible with Ollama `nomic-embed-text`). Top-3 nearest memories are injected into `<prior_experience>` during worker hydration.
3. **Centralized MCP Toolbox Gateway (`control-plane/app/mcp_gateway.py`):** Wraps stdio/SSE/HTTP/builtin Model Context Protocol (MCP) tools, enforces strict per-agent RBAC (`allowed_tools`), and halts execution when `requires_approval: true` by queueing a ticket in `hitl_tickets`.
4. **Shared Blackboard System (Redis Streams + PostgreSQL):**
   - **Active Plane (Redis):** `stream:tasks` (`XREADGROUP` consumer group `agent_worker_pool`), atomic distributed task leases (`SET task:{id}:lease {worker_id} NX EX 60`), and live Pub/Sub (`channel:blackboard:events`).
   - **Audit Plane (PostgreSQL):** `blackboard_entries` (`hypothesis`, `critique`, `spec`, `code`, `test_result`, `steering`, `summary`) with automated debate pruning (`build_bounded_context` & `prune_and_summarize_blackboard`).
5. **Dual Workflow Engine (`control-plane/app/workflow_engine.py`):** Bi-directional conversion between YAML/JSON state graphs and BPMN 2.0 XML (`ServiceTask`, `ParallelGateway`, `ExclusiveGateway`, `UserTask`).
6. **Mobile-First Web Cockpit (`web-cockpit/`):** Touch-friendly ($\ge 44\times 44\text{px}$) React + Tailwind + Vite UI with LAN auto-discovery (`window.location.hostname`), `100dvh` mobile layout, bottom nav bar, windowed pagination, `bpmn-js` pinch-to-zoom + timeline fallback, and HITL confirmation modal.

---

## 2. Repository Map

```text
/root/code/blackboard/
├── AGENTS.md                        # Zero-memory agent onboarding & invariant specification
├── README.md                        # Quick start, LAN/UFW mobile setup, and API reference
├── docker-compose.yml               # 32GB homelab-tuned stack (0.0.0.0 LAN bindings)
├── .env.example                     # Environment template (LOCAL_OLLAMA_URL, DB pools, Redis)
├── pytest.ini                       # Pytest configuration
├── scripts/
│   ├── setup_lan_ufw.sh             # UFW firewall rules & LAN IP helper for mobile testing
│   └── run_live_llm_e2e.py          # Live multi-node LAN LLM E2E verification harness
├── workflows/
│   └── code_review_pipeline.yaml    # Sample multi-agent pipeline (research->spec->code->test/review->HITL)
├── control-plane/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                  # FastAPI REST endpoints & WebSocket /ws/blackboard bridge
│       ├── database.py              # Async SQLAlchemy engine, pgvector init, Redis pool
│       ├── models.py                # SQLAlchemy ORM models
│       ├── schemas.py               # Pydantic v2 request/response schemas
│       ├── mcp_gateway.py           # MCP multiplexer, RBAC enforcement & HITL ticket router
│       ├── workflow_engine.py       # YAML <-> BPMN 2.0 converter, state machine, pruning & zombie recovery
│       └── concierge.py             # Concierge Admin Agent (/api/v1/chat) & embedding helper
├── agent-worker/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── worker.py                # Redis Stream consumer & SET NX EX 60 lease loop
│       ├── memory.py                # Scoped pgvector retrieval & <prior_experience> formatter
│       └── runner.py                # Stateless persona hydration, LLM client & MCP tool caller
├── web-cockpit/
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx                  # Responsive shell (Desktop 3-col split / Mobile bottom nav)
│       ├── api.js                   # Dynamic LAN-aware REST & WebSocket client
│       ├── components/
│       │   ├── BlackboardFeed.jsx   # Sticky filter tags, >=44px targets, 20-item windowed pagination
│       │   ├── BpmnEditor.jsx       # bpmn-js touch canvas + mobile Step-List/Timeline fallback
│       │   ├── HITLGates.jsx        # Thumb-accessible Approve/Reject drawer + confirmation modal
│       │   └── ConciergeChat.jsx    # dvh keyboard-aware chat + scoped pgvector inspector
│       └── tests/
│           ├── BlackboardFeed.test.jsx
│           ├── MobileNavigation.test.jsx
│           └── HITLApprovalDrawer.test.jsx
└── tests/
    ├── conftest.py                  # InMemoryAsyncRedis emulator & test fixtures
    ├── unit/
    │   ├── test_models.py
    │   ├── test_mcp_gateway.py
    │   └── test_workflow_engine.py
    ├── integration/
    │   ├── test_blackboard_api.py
    │   └── test_worker_loop.py
    └── falsifications/              # Popperian edge-case falsifiers
        ├── test_concurrent_leasing.py
        ├── test_memory_isolation.py
        ├── test_context_overflow.py
        ├── test_tool_rbac_bypass.py
        ├── test_zombie_lease_recovery.py
        └── test_llm_router_resilience.py
```

---

## 3. Core Architectural Invariants (DO NOT VIOLATE)

1. **Lease Exclusivity (Falsifier 1):** Every worker MUST acquire `SET task:{task_id}:lease {worker_id} NX EX 60` before hydrating an agent or calling an LLM.
2. **Memory Isolation (Falsifier 2):** Every vector search against `agent_memories` MUST bind `WHERE agent_memories.agent_id = :agent_id`. Cross-agent memory bleed is strictly forbidden.
3. **Bounded Debate Context (Falsifier 3):** Before hydrating an agent turn, `WorkflowEngine.build_bounded_context` / `prune_and_summarize_blackboard` MUST keep the context within the token budget while preserving the original `StartEvent` problem statement and latest `critique`.
4. **Strict MCP RBAC (Falsifier 4):** `MCPToolboxGateway.enforce_rbac_and_invoke` MUST verify `tool_id in agent.allowed_tools`. Unauthorized calls raise `UnauthorizedToolException`, log a security critique to the Blackboard, and never reach the MCP transport.
5. **Zombie Lease Recovery (Falsifier 5):** Abandoned tasks whose Redis lease TTL expires MUST be transitioned to `RETRY_QUEUED` by `WorkflowEngine.recover_abandoned_task` so healthy workers can re-claim and complete them.
6. **Mobile-First Touch & LAN Compatibility:** All interactive buttons in `web-cockpit/` MUST maintain `min-h-[44px] min-w-[44px]` touch targets, use `dvh` viewport units for chat/drawers, and resolve API/WS URLs dynamically via `window.location.hostname` so mobile phones on the home Wi-Fi connect seamlessly.

---

## 4. Home LAN LLM Cluster & Default Agent Routing

The control plane (`192.168.1.100`) orchestrates stateless workers across three LAN inference nodes:

| Node | IP / Port | Provider | Models Available | Default Assigned Agent(s) |
| :--- | :--- | :--- | :--- | :--- |
| **Ollama Server** | `http://192.168.1.150:11434` | `ollama` | `qwen2.5-coder:7b`, `qwen2.5-coder:0.5b`, `qwen3:8b`, `qwen3:1.7b`, `deepseek-coder-v2:16b-lite-instruct-q2_K`, `phi4-mini-reasoning:latest`, `gemma4:e2b` | `coder` (`qwen2.5-coder:0.5b` / `qwen2.5-coder:7b`) |
| **CT118 (`llama.cpp`)** | `http://192.168.1.180:8080/v1`<br>`http://192.168.1.180:8081/v1`<br>`http://192.168.1.180:8090/v1` | `openai` | `:8080` -> `groot-19b`<br>`:8081` -> `qwen3-4b`<br>`:8090` -> `lfm3-1.7b` | `architect` (`qwen3-4b` on `:8081`)<br>`qa-tester` (`lfm3-1.7b` on `:8090`)<br>`sec-auditor` (`lfm3-1.7b` on `:8090`) |
| **LAPTOP (`llama.cpp`)** | `http://192.168.1.149:8080/v1`<br>`http://192.168.1.149:8081/v1`<br>`http://192.168.1.149:8090/v1` | `openai` | `:8080` -> `mistral-24b`<br>`:8081` -> `qwen3-4b`<br>`:8090` -> `lfm3-1.7b` | Hot-swappable via `PUT /api/v1/agents/{id}` or Web Cockpit |
| **NVIDIA NIM Free Cloud** | `https://integrate.api.nvidia.com/v1` | `nvidia_nim` | `meta/llama-3.3-70b-instruct`, `qwen/qwen2.5-coder-32b-instruct`, `deepseek-ai/deepseek-r1`, `mistralai/mistral-nemotron`, `nvidia/llama-3.1-nemotron-ultra-253b-v1` | Automatic escalation / fallback tier (`POST /api/v1/llm/config`, `llm:cooldown:*`, `llm:nim:rpm:*`) |
