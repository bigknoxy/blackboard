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
