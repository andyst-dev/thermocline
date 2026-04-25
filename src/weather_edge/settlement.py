from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .clients.aviationweather import observed_extreme_c
from .parsing import parse_temperature_contract


@dataclass(frozen=True)
class SettlementResult:
    can_settle: bool
    outcome_price: float | None
    observed_value_c: float | None
    notes: str


def _icao_from_candidate(candidate: dict[str, Any]) -> str | None:
    loc = candidate.get("resolution_location")
    if isinstance(loc, str) and re.fullmatch(r"[A-Z]{4}", loc):
        return loc
    return None


def settle_candidate(candidate: dict[str, Any], question: str, side: str) -> SettlementResult:
    contract = parse_temperature_contract(question)
    if not contract:
        return SettlementResult(False, None, None, "unsupported question pattern")
    icao = _icao_from_candidate(candidate)
    if not icao:
        return SettlementResult(False, None, None, "no ICAO station lock")
    timezone_name = str(candidate.get("timezone") or "UTC")
    # candidate JSON currently does not expose timezone at top-level; infer common UTC fallback is safe
    # for same-day METAR filtering only when target date is already local in market wording.
    if icao.startswith("RK"):
        timezone_name = "Asia/Seoul"
    elif icao.startswith("EG"):
        timezone_name = "Europe/London"
    elif icao.startswith("LF"):
        timezone_name = "Europe/Paris"
    elif icao.startswith("KL") or icao.startswith("KJ") or icao.startswith("KM"):
        timezone_name = "America/New_York"
    elif icao.startswith("CY"):
        timezone_name = "America/Toronto"
    elif icao.startswith("SA"):
        timezone_name = "America/Argentina/Buenos_Aires"
    elif icao.startswith("SB"):
        timezone_name = "America/Sao_Paulo"

    observed, count = observed_extreme_c(icao, contract.target_date, timezone_name, contract.metric)
    if observed is None:
        return SettlementResult(False, None, None, f"no METAR observations for {icao}")

    day_complete = datetime.now(timezone.utc) > contract.target_date.replace(hour=23, minute=59)
    # Same-day markets can be tentatively settled when the observed value already makes
    # the selected side irreversible for one-sided buckets. Exact buckets remain tentative
    # until day completion.
    yes_wins = False
    irreversible = day_complete
    if contract.lower_c is None and contract.upper_c is not None:
        yes_wins = observed <= contract.upper_c if contract.metric == "lowest" else observed <= contract.upper_c
        if observed > contract.upper_c:
            irreversible = True
    elif contract.lower_c is not None and contract.upper_c is None:
        yes_wins = observed >= contract.lower_c
        if observed >= contract.lower_c:
            irreversible = True
    elif contract.lower_c is not None and contract.upper_c is not None:
        yes_wins = contract.lower_c <= observed <= contract.upper_c

    if not irreversible:
        return SettlementResult(False, None, observed, f"tentative only: {count} METAR obs, day not complete")

    side_wins = yes_wins if side.lower() == "yes" else not yes_wins
    return SettlementResult(True, 1.0 if side_wins else 0.0, observed, f"settled from {count} METAR obs at {icao}")
