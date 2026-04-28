#!/usr/bin/env bash
set -euo pipefail
export WEATHER_EDGE_DISABLE_PAPER_OPEN=1
cd /home/builder/weather-edge
mkdir -p logs reports data/backups data/run
LOCK_FILE=data/run/weather_edge_paper_cycle.lock
HEARTBEAT=reports/paper_cycle_heartbeat.json
DB=data/weather_edge.db
TODAY=$(date -u +%F)
BACKUP=data/backups/weather_edge-${TODAY}.db.gz
{
  echo "=== $(date -u --iso-8601=seconds) Weather Edge paper cycle ==="
  if [ -f "$DB" ] && [ ! -f "$BACKUP" ]; then
    python3 - <<PY
import gzip
import shutil
import sqlite3
from pathlib import Path
src = Path("$DB")
tmp = Path(f"/tmp/weather_edge-${TODAY}.db")
dst = Path("$BACKUP")
with sqlite3.connect(src) as source:
    status = source.execute("PRAGMA integrity_check").fetchone()[0]
    if status != "ok":
        raise SystemExit(f"SQLite integrity_check failed: {status}")
    with sqlite3.connect(tmp) as target:
        source.backup(target)
with tmp.open("rb") as raw, gzip.open(dst, "wb") as gz:
    shutil.copyfileobj(raw, gz)
tmp.unlink(missing_ok=True)
PY
  fi
  find data/backups -name 'weather_edge-*.db.gz' -mtime +30 -delete 2>/dev/null || true
  find data/snapshots -type f -mtime +30 -delete 2>/dev/null || true
  if [ -f logs/paper_cycle.log ] && [ "$(stat -c%s logs/paper_cycle.log)" -gt 10485760 ]; then
    mv logs/paper_cycle.log "logs/paper_cycle-${TODAY}.log"
    gzip -f "logs/paper_cycle-${TODAY}.log"
  fi
  set +e
  flock -n "$LOCK_FILE" bash -c 'PYTHONPATH=src WEATHER_EDGE_DISABLE_PAPER_OPEN=1 WEATHER_EDGE_MARKET_SCAN_PAGES=76 WEATHER_EDGE_REPORT_LIMIT=10 WEATHER_EDGE_MAX_OPEN_POSITIONS=5 python3 -m weather_edge.main paper-cycle'
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
summary = {}
for name in ("paper_trades", "paper_settlement"):
    path = Path("reports") / f"{name}.json"
    if path.exists():
        try:
            summary[name] = json.loads(path.read_text()).get("summary")
        except Exception as exc:
            summary[name] = {"error": str(exc)}
Path("$HEARTBEAT").write_text(json.dumps({
    "finished_at": datetime.now(timezone.utc).isoformat(),
    "exit_code": 0,
    "status": "ok",
    "backup": "$BACKUP" if Path("$BACKUP").exists() else None,
    "summary": summary,
}, indent=2))
PY
  echo
} >> logs/paper_cycle.log 2>&1
