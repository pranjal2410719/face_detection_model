#!/usr/bin/env bash
# ==============================================================================
# Edge Camera Node Station & Centralized Dashboard Launcher
# ==============================================================================

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=== Edge Camera Node Station & Central Dashboard ==="
echo "Project root: $DIR"

# 1. Start Central Server if not already running
if pgrep -f "central_server.py" > /dev/null; then
    echo "[✓] Central Receiver is already running on ws://0.0.0.0:8765"
else
    echo "[*] Launching Central Receiver on ws://0.0.0.0:8765..."
    python3 central_server.py > /dev/null 2>&1 &
    sleep 1
    echo "[✓] Central Receiver started."
fi

# 2. Start Web Camera Node & Centralized Dashboard (Dual HTTP & HTTPS)
echo "[*] Launching Camera Node & Central Dashboard..."
echo "    [HTTP]  Camera Node:      http://localhost:8500  or  http://172.16.18.193:8500"
echo "    [HTTP]  Master Dashboard: http://localhost:8500/dashboard"
echo "    [HTTPS] Mobile Cameras:   https://172.16.18.193:8443 (Secure Mode for Phones)"

python3 -m web_node.server
