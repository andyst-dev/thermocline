from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable

from .parsing import parse_temperature_contract


@dataclass(frozen=True)
class EventExposureLimits:
    max_legs_per_event: int = 2
    max_usd_per_event: float = 5.0
    max_open_events: int | None = None


@dataclass(frozen=True)
class EventKey:
    city: str
    target_date: str
    metric: str
    event_id: str | None = None


def _row_get(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, sqlite3.Row):
        try:
            return row[key]
        except (IndexError, KeyError):
            return default
    return row.get(key, default)


def _extract_event_id(candidate: dict[str, Any]) -> str | None:
    direct = candidate.get("event_id") or candidate.get("eventId")
    if direct:
        return str(direct)
    raw = candidate.get("raw")
    if isinstance(raw, dict):
        raw_direct = raw.get("event_id") or raw.get("eventId") or raw.get("eventID")
        if raw_direct:
            return str(raw_direct)
        events = raw.get("events")
        if isinstance(events, list) and events:
            first = events[0]
            if isinstance(first, dict):
                value = first.get("id") or first.get("event_id") or first.get("eventId")
                if value:
                    return str(value)
    return None


def event_key_for_candidate(candidate: dict[str, Any]) -> EventKey:
    """Group mutually correlated weather contracts into one risk event.

    If a Polymarket event id is available, use it as the canonical grouping key.
    Otherwise fall back to city + target_date + highest/lowest metric parsed from
    the contract question. This groups adjacent temperature buckets together.
    """
    event_id = _extract_event_id(candidate)
    if event_id:
        return EventKey(city="", target_date="", metric="", event_id=event_id)

    question = str(candidate.get("question") or "")
    contract = parse_temperature_contract(question)
    city = str(candidate.get("city") or (contract.city if contract else "")).strip().lower()
    target_date = str(candidate.get("target_date") or (contract.target_date.date().isoformat() if contract else ""))
    metric = str(candidate.get("metric") or (contract.metric if contract else "unknown")).lower()
    return EventKey(city=city, target_date=target_date, metric=metric, event_id=None)


def _candidate_from_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any] | None:
    raw = _row_get(row, "candidate_json")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        candidate = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    if isinstance(candidate, dict):
        return candidate
    return None


def current_exposure(open_rows: Iterable[sqlite3.Row | dict[str, Any]], key: EventKey) -> tuple[int, float]:
    legs = 0
    usd = 0.0
    for row in open_rows:
        if str(_row_get(row, "status", "open")) != "open":
            continue
        candidate = _candidate_from_row(row)
        if candidate is None:
            continue
        if event_key_for_candidate(candidate) != key:
            continue
        legs += 1
        try:
            usd += float(_row_get(row, "size_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return legs, round(usd, 6)


def current_open_event_keys(open_rows: Iterable[sqlite3.Row | dict[str, Any]]) -> set[EventKey]:
    """Return distinct active risk events represented by open paper rows."""
    keys: set[EventKey] = set()
    for row in open_rows:
        if str(_row_get(row, "status", "open")) != "open":
            continue
        candidate = _candidate_from_row(row)
        if candidate is None:
            continue
        keys.add(event_key_for_candidate(candidate))
    return keys


def current_open_event_count(open_rows: Iterable[sqlite3.Row | dict[str, Any]]) -> int:
    """Count distinct active risk events, not individual ladder legs."""
    return len(current_open_event_keys(open_rows))


def check_event_cap(
    candidate: dict[str, Any],
    open_rows: Iterable[sqlite3.Row | dict[str, Any]],
    *,
    proposed_size_usd: float,
    limits: EventExposureLimits | None = None,
) -> tuple[bool, str | None]:
    cfg = limits or EventExposureLimits()
    if str(candidate.get("verdict") or "").upper() == "ARBITRAGE":
        return True, None
    key = event_key_for_candidate(candidate)
    rows = list(open_rows)
    open_event_keys = current_open_event_keys(rows)
    if cfg.max_open_events is not None and cfg.max_open_events >= 0 and key not in open_event_keys and len(open_event_keys) >= cfg.max_open_events:
        return False, f"event exposure cap: max_open_events={cfg.max_open_events} reached before opening {key}"
    legs, usd = current_exposure(rows, key)
    if cfg.max_legs_per_event >= 0 and legs >= cfg.max_legs_per_event:
        return False, f"event exposure cap: max_legs_per_event={cfg.max_legs_per_event} reached for {key}"
    proposed = float(proposed_size_usd or 0.0)
    if cfg.max_usd_per_event >= 0 and usd + proposed > cfg.max_usd_per_event:
        return (
            False,
            "event exposure cap: "
            f"max_usd_per_event={cfg.max_usd_per_event:.2f} would be exceeded "
            f"({usd:.2f} + {proposed:.2f}) for {key}",
        )
    return True, None


def synthetic_open_row(candidate: dict[str, Any], size_usd: float) -> dict[str, Any]:
    """Build a row-like object for exposure accounting after an in-memory open."""
    return {"status": "open", "size_usd": float(size_usd), "candidate_json": json.dumps(candidate)}
