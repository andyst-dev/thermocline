from __future__ import annotations

from datetime import datetime, timezone
from urllib.error import URLError

import pytest

from weather_edge.config import Settings
from weather_edge.models import WeatherMarket
from weather_edge.scanner import ScanSkip, scan_market


def _global_market() -> WeatherMarket:
    return WeatherMarket(
        market_id="global-1",
        slug="global-temp-may-2026",
        question="Will the global temperature increase by more than 1.0°C in May 2026?",
        end_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
        active=True,
        closed=False,
        liquidity=1000.0,
        volume=100.0,
        outcomes=["Yes", "No"],
        outcome_prices=[0.5, 0.5],
        raw={},
    )


def test_global_temperature_market_is_skipped_when_gistemp_baseline_unavailable(tmp_path, monkeypatch):
    settings = Settings(project_root=tmp_path, db_path=tmp_path / "weather_edge.db")

    def fail_baseline(month: int):
        raise URLError("timed out")

    monkeypatch.setattr("weather_edge.scanner.global_temp_baseline", fail_baseline)

    with pytest.raises(ScanSkip, match="GISTEMP baseline unavailable"):
        scan_market(settings, _global_market())
