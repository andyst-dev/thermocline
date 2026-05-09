from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import mean, pstdev
from urllib.error import URLError
from urllib.request import Request, urlopen

GISTEMP_URL = "https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.txt"
GISTEMP_CACHE_PATH = Path("data/cache/gistemp/GLB.Ts+dSST.txt")
GISTEMP_FETCH_TIMEOUT_SECONDS = 10
_GISTEMP_UNAVAILABLE_REASON: str | None = None
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass(frozen=True)
class AnomalyBaseline:
    target_month: int
    mean_c: float
    sigma_c: float
    samples: list[float]
    source_url: str = GISTEMP_URL


def _fetch_table() -> str:
    req = Request(GISTEMP_URL, headers={"User-Agent": "weather-edge/0.1"})
    with urlopen(req, timeout=GISTEMP_FETCH_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def _load_table() -> str:
    """Fetch the NASA GISTEMP table, falling back to the last cached copy.

    Cron must not fail just because NASA's static endpoint is temporarily slow.
    A successful fetch refreshes the cache; a network failure uses the cache if
    present and only raises when no cached table exists yet.
    """
    global _GISTEMP_UNAVAILABLE_REASON
    if _GISTEMP_UNAVAILABLE_REASON and not GISTEMP_CACHE_PATH.exists():
        raise RuntimeError(_GISTEMP_UNAVAILABLE_REASON)
    try:
        text = _fetch_table()
    except (OSError, TimeoutError, URLError) as exc:
        if GISTEMP_CACHE_PATH.exists():
            return GISTEMP_CACHE_PATH.read_text(encoding="utf-8", errors="replace")
        _GISTEMP_UNAVAILABLE_REASON = str(exc)
        raise
    _GISTEMP_UNAVAILABLE_REASON = None
    GISTEMP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GISTEMP_CACHE_PATH.write_text(text, encoding="utf-8")
    return text


def _parse_month_values(text: str, month: int) -> list[tuple[int, float]]:
    values: list[tuple[int, float]] = []
    month_idx = month  # table columns: Year Jan Feb ...
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 13 or not parts[0].isdigit():
            continue
        year = int(parts[0])
        raw = parts[month_idx]
        if raw == "****":
            continue
        try:
            # NASA table is in hundredths of °C.
            values.append((year, float(raw) / 100.0))
        except ValueError:
            continue
    return values


def global_temp_baseline(month: int, lookback_years: int = 25) -> AnomalyBaseline:
    values = _parse_month_values(_load_table(), month)
    if not values:
        raise ValueError(f"No GISTEMP values for month={month}")
    recent = [value for _, value in values[-lookback_years:]]
    sigma = pstdev(recent) if len(recent) > 1 else 0.20
    # Monthly global anomaly uncertainty is dominated by ENSO + unforced variability.
    # A 10-year same-month sample underestimates true forecast uncertainty.
    sigma = max(0.20, sigma)
    return AnomalyBaseline(target_month=month, mean_c=mean(recent), sigma_c=sigma, samples=recent)
