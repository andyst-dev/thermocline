from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import WeatherMarket


def sample_weather_markets() -> list[WeatherMarket]:
    base = datetime.now(timezone.utc).date()
    samples = [
        ("Buenos Aires", base + timedelta(days=2), ["<18", "18-19", "20-21", "22-23", "24-25", "26-27", "28-29", "30+"], [0.05, 0.08, 0.14, 0.2, 0.19, 0.14, 0.1, 0.1]),
        ("Chicago", base + timedelta(days=3), ["<10", "10-11", "12-13", "14-15", "16-17", "18-19", "20-21", "22+"], [0.06, 0.1, 0.16, 0.21, 0.18, 0.13, 0.09, 0.07]),
        ("Tokyo", base + timedelta(days=4), ["<16", "16-17", "18-19", "20-21", "22-23", "24-25", "26-27", "28+"], [0.04, 0.07, 0.13, 0.2, 0.22, 0.16, 0.1, 0.08]),
    ]
    markets: list[WeatherMarket] = []
    for idx, (city, target_date, outcomes, prices) in enumerate(samples, start=1):
        markets.append(
            WeatherMarket(
                market_id=f"sample-{idx}",
                slug=f"highest-temperature-in-{city.lower().replace(' ', '-')}-{target_date.isoformat()}",
                question=f"Highest temperature in {city} on {target_date.strftime('%B')} {target_date.day}, {target_date.year}?",
                end_date=datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=23),
                active=True,
                closed=False,
                liquidity=500.0,
                volume=1500.0,
                outcomes=outcomes,
                outcome_prices=prices,
                raw={"source": "fixture"},
            )
        )
    return markets
