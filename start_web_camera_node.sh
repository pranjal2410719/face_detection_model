#!/usr/bin/env bash
# ==============================================================================
# Edge Camera Node Station & Centralized Dashboard Launcher
# ==============================================================================

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=============================================================================="
echo "          NETCAM • EDGE FACE RECOGNITION & COMMAND CENTER                     "
echo "=============================================================================="
echo "Project root: $DIR"

# 1. Start Central Receiver if not already running
if pgrep -f "central_server.py" > /dev/null; then
    echo "[✓] Central Receiver is already running on ws://0.0.0.0:8765"
else
    echo "[*] Launching Central Receiver on ws://0.0.0.0:8765..."
    python3 central_server.py > /dev/null 2>&1 &
    sleep 1
    echo "[✓] Central Receiver started."
fi

# 2. Automatically generate/update SSL certificates with all active Wi-Fi & LAN IPs
python3 -c "
from pathlib import Path
from core.network_utils import ensure_ssl_certificates, get_network_info
ensure_ssl_certificates(Path('certs'))
info = get_network_info()
print('')
print('📡 DETECTED NETWORK ADDRESSES (SAME WI-FI):')
for net in info['networks']:
    tag = ' (Primary Wi-Fi/LAN)' if net['is_primary'] else ''
    print(f'   ➜ IP: {net[\"ip\"]}{tag}')
    print(f'      Mobile Cameras:   {net[\"camera_https\"]}')
    print(f'      Dashboard:        {net[\"dashboard_http\"]}')
print('')
print('💻 ON THIS PC:')
print('   Dashboard:        http://localhost:8500/dashboard')
print('   Camera Station:   http://localhost:8500')
print('')
print('📱 ON CONNECTED WI-FI DEVICES (PHONES / CLIENTS):')
print(f'   Mobile Cameras:   {info[\"recommended_camera_url\"]}  (Requires HTTPS)')
print(f'   Client Dashboard: {info[\"recommended_dashboard_url\"]}')
print('==============================================================================')
"

# 3. Start Web Camera Node & Central Dashboard
python3 -m web_node.server
