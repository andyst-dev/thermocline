from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from urllib.request import Request, urlopen

GISTEMP_URL = "https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.txt"
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
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


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


def global_temp_baseline(month: int, lookback_years: int = 10) -> AnomalyBaseline:
    values = _parse_month_values(_fetch_table(), month)
    if not values:
        raise ValueError(f"No GISTEMP values for month={month}")
    recent = [value for _, value in values[-lookback_years:]]
    sigma = pstdev(recent) if len(recent) > 1 else 0.08
    # Avoid fake overconfidence: GISTEMP monthly anomaly moves a lot year-to-year.
    sigma = max(0.06, sigma)
    return AnomalyBaseline(target_month=month, mean_c=mean(recent), sigma_c=sigma, samples=recent)
