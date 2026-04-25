from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .clients.aviationweather import observed_extreme_c
from .clients.weathercom import official_extreme_c
from .parsing import TemperatureContract, parse_temperature_contract


@dataclass(frozen=True)
class SettlementResult:
    can_settle: bool
    outcome_price: float | None
    observed_value_c: float | None
    notes: str
    authority: str = "none"
    observation_count: int = 0


def _icao_from_candidate(candidate: dict[str, Any]) -> str | None:
    loc = candidate.get("resolution_location")
    if isinstance(loc, str) and re.fullmatch(r"[A-Z]{4}", loc):
        return loc
    return None


def _evaluate_contract(contract: TemperatureContract, observed: float, day_complete: bool) -> tuple[bool, bool]:
    yes_wins = False
    irreversible = day_complete
    if contract.lower_c is None and contract.upper_c is not None:
        yes_wins = observed <= contract.upper_c
        if observed > contract.upper_c:
            irreversible = True
    elif contract.lower_c is not None and contract.upper_c is None:
        yes_wins = observed >= contract.lower_c
        if observed >= contract.lower_c:
            irreversible = True
    elif contract.lower_c is not None and contract.upper_c is not None:
        yes_wins = contract.lower_c <= observed <= contract.upper_c
    return yes_wins, irreversible


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
    elif icao == "KORD":
        timezone_name = "America/Chicago"
    elif icao.startswith("K"):
        timezone_name = "America/New_York"
    elif icao.startswith("CY"):
        timezone_name = "America/Toronto"
    elif icao.startswith("SA"):
        timezone_name = "America/Argentina/Buenos_Aires"
    elif icao.startswith("SB"):
        timezone_name = "America/Sao_Paulo"

    day_complete = datetime.now(timezone.utc) > contract.target_date.replace(hour=23, minute=59)
    resolution_source = str(candidate.get("resolution_source") or "")

    official_observed = None
    official_count = 0
    official_note = ""
    if "wunderground.com" in resolution_source.lower() or "weather.com" in resolution_source.lower():
        official_observed, official_count, official_note = official_extreme_c(resolution_source, contract.target_date, contract.metric)
        if official_observed is None:
            metar_observed, metar_count = observed_extreme_c(icao, contract.target_date, timezone_name, contract.metric)
            hint = f"; METAR reference {metar_observed}°C from {metar_count} obs at {icao}" if metar_observed is not None else ""
            return SettlementResult(False, None, metar_observed, f"official source unavailable: {official_note}{hint}", "official_unavailable", metar_count)

        _yes_wins, irreversible = _evaluate_contract(contract, official_observed, day_complete)
        metar_observed, metar_count = observed_extreme_c(icao, contract.target_date, timezone_name, contract.metric)
        compare = ""
        if metar_observed is not None and abs(metar_observed - official_observed) > 0.6:
            compare = f"; METAR differs ({metar_observed}°C from {metar_count} obs)"
        state = "irreversible by source observation" if irreversible else "tentative/day not complete"
        return SettlementResult(
            False,
            None,
            official_observed,
            f"provisional Wunderground/weather.com observation only ({state}): {official_count} obs at {icao}{compare}; wait for Polymarket Gamma closed outcome before counting PnL",
            "official_wunderground_provisional",
            official_count,
        )

    observed, count = observed_extreme_c(icao, contract.target_date, timezone_name, contract.metric)
    if observed is None:
        return SettlementResult(False, None, None, f"no METAR observations for {icao}", "metar", 0)

    _yes_wins, irreversible = _evaluate_contract(contract, observed, day_complete)
    state = "irreversible by METAR observation" if irreversible else "tentative/day not complete"
    return SettlementResult(
        False,
        None,
        observed,
        f"provisional METAR fallback only ({state}): {count} obs at {icao}; wait for Polymarket Gamma closed outcome before counting PnL",
        "metar_provisional",
        count,
    )
