#!/usr/bin/env bash
set -euo pipefail
cd /root/.openclaw/workspace/projects/weather-edge
mkdir -p logs reports
LOCK_FILE=/tmp/weather_edge_paper_cycle.lock
HEARTBEAT=reports/paper_cycle_heartbeat.json
{
  echo "=== $(date -u --iso-8601=seconds) Weather Edge paper cycle ==="
  set +e
  flock -n "$LOCK_FILE" bash -c 'PYTHONPATH=src WEATHER_EDGE_MARKET_SCAN_PAGES=76 WEATHER_EDGE_REPORT_LIMIT=10 python3 -m weather_edge.main paper-cycle'
  code=$?
  set -e
  if [ "$code" -ne 0 ]; then
    python3 - <<PY
import json
from datetime import datetime, timezone
from pathlib import Path
Path("$HEARTBEAT").write_text(json.dumps({
    "finished_at": datetime.now(timezone.utc).isoformat(),
    "exit_code": $code,
    "status": "skipped_or_failed",
    "reason": "lock held or command failed",
}, indent=2))
PY
    exit "$code"
  fi
  python3 - <<PY
import json
from datetime import datetime, timezone
from pathlib import Path
Path("$HEARTBEAT").write_text(json.dumps({
    "finished_at": datetime.now(timezone.utc).isoformat(),
    "exit_code": 0,
    "status": "ok",
}, indent=2))
PY
  echo
} >> logs/paper_cycle.log 2>&1
