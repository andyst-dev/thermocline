from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class TemperatureContract:
    metric: str
    city: str
    target_date: datetime
    lower_c: float | None
    upper_c: float | None
    label: str


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


def parse_metric_city_and_date(question: str) -> tuple[str, str, datetime] | None:
    match = re.search(
        r"(?P<metric>highest|lowest) temperature in\s+(?P<city>.+?)\s+on\s+"
        r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:,?\s*(?P<year>\d{4}))?",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    month = MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    year = int(match.group("year") or datetime.now(timezone.utc).year)
    target = datetime(year, month, int(match.group("day")), tzinfo=timezone.utc)
    return match.group("metric").lower(), match.group("city").strip(" ?"), target


def _to_celsius(value: float, unit: str) -> float:
    if unit.upper() == "F":
        return (value - 32.0) * 5.0 / 9.0
    return value


def parse_temperature_contract(question: str) -> TemperatureContract | None:
    match = re.search(
        r"(?:Will\s+the\s+)?(?P<metric>highest|lowest) temperature in\s+"
        r"(?P<city>.+?)\s+be\s+(?P<bucket>.+?)\s+on\s+"
        r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:,?\s*(?P<year>\d{4}))?",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    month = MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    year = int(match.group("year") or datetime.now(timezone.utc).year)
    bucket = match.group("bucket").strip(" ?")
    unit_match = re.search(r"°?([CF])", bucket, flags=re.IGNORECASE)
    unit = unit_match.group(1).upper() if unit_match else "C"
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", bucket)]
    if not numbers:
        return None

    bucket_lc = bucket.lower()
    lower: float | None
    upper: float | None
    if "below" in bucket_lc or "or less" in bucket_lc or "or lower" in bucket_lc:
        lower, upper = None, _to_celsius(numbers[0], unit)
    elif "higher" in bucket_lc or "or more" in bucket_lc or "or above" in bucket_lc:
        lower, upper = _to_celsius(numbers[0], unit), None
    elif "between" in bucket_lc and len(numbers) >= 2:
        lower, upper = _to_celsius(numbers[0], unit), _to_celsius(numbers[1], unit)
    elif len(numbers) == 1:
        value = _to_celsius(numbers[0], unit)
        # Single-degree Celsius buckets resolve to exact integer °C; Fahrenheit
        # markets usually use 2°F buckets, handled by "between" above.
        lower, upper = value - 0.5, value + 0.5
    else:
        lower, upper = _to_celsius(numbers[0], unit), _to_celsius(numbers[-1], unit)

    return TemperatureContract(
        metric=match.group("metric").lower(),
        city=match.group("city").strip(" ?"),
        target_date=datetime(year, month, int(match.group("day")), tzinfo=timezone.utc),
        lower_c=lower,
        upper_c=upper,
        label=bucket,
    )


def parse_global_temperature_market(question: str) -> tuple[int, int, float | None, float | None] | None:
    match = re.search(
        r"global temperature increase by (?P<range>.+?) in (?P<month>[A-Za-z]+) (?P<year>\d{4})",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    month = MONTHS.get(match.group("month").lower())
    year = int(match.group("year"))
    if month is None:
        return None
    text = match.group("range").replace("º", "").replace("°", "")
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    text_lc = text.lower()
    if "less than" in text_lc and numbers:
        return year, month, None, numbers[0]
    if "more than" in text_lc and numbers:
        return year, month, numbers[0], None
    if "between" in text_lc and len(numbers) >= 2:
        return year, month, numbers[0], numbers[1]
    return None


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
