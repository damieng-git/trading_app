#!/usr/bin/env bash
set -euo pipefail
cd /root/damiverse_apps/trading_app/main
git pull origin main
systemctl restart trading-dashboard
TRADING_APP_ROOT=/root/damiverse_apps/trading_app/main \
  python3 -m trading_dashboard dashboard rebuild-ui
echo "Prod deployed. Check: http://46.224.149.54/"
