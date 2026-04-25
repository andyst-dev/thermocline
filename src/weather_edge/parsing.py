from __future__ import annotations

import math
import re
from datetime import datetime, timezone

MONTHS = {
    name.lower(): idx
    for idx, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}


def parse_city_and_date(question: str) -> tuple[str, datetime] | None:
    match = re.search(
        r"highest temperature in\s+(?P<city>.+?)\s+on\s+(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:,?\s*(?P<year>\d{4}))?",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    city = match.group("city").strip(" ?")
    month = MONTHS.get(match.group("month").lower())
    day = int(match.group("day"))
    year = int(match.group("year") or datetime.now(timezone.utc).year)
    if month is None:
        return None
    target = datetime(year, month, day, tzinfo=timezone.utc)
    return city, target


def parse_bucket(label: str) -> tuple[float | None, float | None]:
    normalized = label.strip().replace("°", "").replace("F", "").replace("C", "").replace("–", "-")
    normalized = normalized.replace("to", "-")

    m = re.match(r"^(?:<|under\s+)(\d+(?:\.\d+)?)$", normalized, flags=re.IGNORECASE)
    if m:
        return None, float(m.group(1))

    m = re.match(r"^(?:>|over\s+|above\s+)(\d+(?:\.\d+)?)$", normalized, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)), None

    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:or above|\+)$", normalized, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)), None

    m = re.match(r"^(\d+(?:\.\d+)?)\s*[-]\s*(\d+(?:\.\d+)?)$", normalized)
    if m:
        return float(m.group(1)), float(m.group(2))

    if normalized.isdigit():
        value = float(normalized)
        return value, value

    return None, None


def normal_cdf(x: float, mean: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / (sigma * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def bucket_probability(lower: float | None, upper: float | None, mean: float, sigma: float) -> float:
    if lower is None and upper is None:
        return 0.0
    if lower is None:
        return max(0.0, min(1.0, normal_cdf(upper, mean, sigma)))
    if upper is None:
        return max(0.0, min(1.0, 1 - normal_cdf(lower, mean, sigma)))
    if lower == upper:
        half_width = 0.5
        return max(0.0, min(1.0, normal_cdf(upper + half_width, mean, sigma) - normal_cdf(lower - half_width, mean, sigma)))
    return max(0.0, min(1.0, normal_cdf(upper, mean, sigma) - normal_cdf(lower, mean, sigma)))
