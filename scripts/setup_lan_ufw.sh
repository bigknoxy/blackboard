#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Configure UFW Firewall Rules & Display Home LAN URLs for Mobile Phone Testing
# ==============================================================================

echo "==> Detecting local LAN IP address..."
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || echo "127.0.0.1")

echo "==> Configuring UFW firewall rules for Mobile Web Cockpit (5173/tcp) and Control Plane API/WS (8000/tcp)..."
if command -v ufw >/dev/null 2>&1; then
  ufw allow 5173/tcp comment 'Blackboard Web Cockpit (Mobile/LAN)' || true
  ufw allow 8000/tcp comment 'Blackboard FastAPI Control Plane & WebSocket (Mobile/LAN)' || true
  ufw status numbered || true
else
  echo "WARN: ufw binary not found on host; skipping ufw command."
fi

echo ""
echo "=================================================================="
echo "  Multi-Agent Blackboard System — Mobile & Home LAN Access Ready"
echo "=================================================================="
echo "  Open on your phone (connected to your home Wi-Fi):"
echo "    Web Cockpit UI:     http://${LAN_IP}:5173"
echo "    Control Plane API:  http://${LAN_IP}:8000/docs"
echo "    Health Endpoint:    http://${LAN_IP}:8000/health"
echo "=================================================================="
