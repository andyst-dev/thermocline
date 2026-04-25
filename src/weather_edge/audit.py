from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _safe_ts(ts: str) -> str:
    return ts.replace(":", "").replace("+", "Z").replace(".", "-")


def write_json_gz(root: Path, *, kind: str, name: str, payload: Any, fetched_at: str | None = None) -> tuple[str, str]:
    ts = fetched_at or datetime.now(timezone.utc).isoformat()
    day = ts[:10]
    rel_dir = Path("data") / "snapshots" / kind / day
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    rel_path = rel_dir / f"{name}-{_safe_ts(ts)}-{digest[:12]}.json.gz"
    abs_path = root / rel_path
    if not abs_path.exists():
        with gzip.open(abs_path, "wb") as fh:
            fh.write(raw)
    return str(rel_path), digest
