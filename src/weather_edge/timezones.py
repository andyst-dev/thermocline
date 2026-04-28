from __future__ import annotations


def timezone_hint_for_icao(icao: str | None) -> str:
    """Best-effort timezone for airport/station ICAO codes used by weather markets.

    This is intentionally conservative and explicit: it is only a fallback for
    local-day filtering when the geocoder timezone is unavailable. Official
    Gamma settlement remains the source of truth for realized PnL.
    """
    if not icao:
        return "UTC"
    code = icao.upper()
    if code.startswith("RK"):
        return "Asia/Seoul"
    if code.startswith("RJ"):
        return "Asia/Tokyo"
    if code.startswith(("ZS", "ZG", "ZH", "ZU", "ZB")):
        return "Asia/Shanghai"
    if code.startswith("V"):
        return "Asia/Kolkata"
    if code.startswith("OP"):
        return "Asia/Karachi"
    if code.startswith("WSS"):
        return "Asia/Singapore"
    if code.startswith("EG"):
        return "Europe/London"
    if code.startswith("LF"):
        return "Europe/Paris"
    if code.startswith("LE"):
        return "Europe/Madrid"
    if code.startswith("LI"):
        return "Europe/Rome"
    if code.startswith("ED"):
        return "Europe/Berlin"
    if code.startswith("EH"):
        return "Europe/Amsterdam"
    if code.startswith("EF"):
        return "Europe/Helsinki"
    if code.startswith("LT"):
        return "Europe/Istanbul"
    if code.startswith("OE"):
        return "Asia/Riyadh"
    if code == "KORD":
        return "America/Chicago"
    if code.startswith("K"):
        return "America/New_York"
    if code.startswith("CY"):
        return "America/Toronto"
    if code.startswith("SA"):
        return "America/Argentina/Buenos_Aires"
    if code.startswith("SB"):
        return "America/Sao_Paulo"
    if code.startswith("MP"):
        return "America/Panama"
    return "UTC"
