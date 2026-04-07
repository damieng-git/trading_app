#!/usr/bin/env bash
set -euo pipefail
cd /root/damiverse_apps/trading_app_test
git pull origin staging
systemctl restart trading-dashboard-test
echo "Staging deployed. Check: http://46.224.149.54/test/"
