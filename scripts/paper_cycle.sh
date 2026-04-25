#!/usr/bin/env bash
set -euo pipefail
cd /root/.openclaw/workspace/projects/weather-edge
mkdir -p logs
{
  echo "=== $(date -u --iso-8601=seconds) Weather Edge paper cycle ==="
  PYTHONPATH=src WEATHER_EDGE_MARKET_SCAN_PAGES=76 WEATHER_EDGE_REPORT_LIMIT=10 python3 -m weather_edge.main paper-cycle
  echo
} >> logs/paper_cycle.log 2>&1
