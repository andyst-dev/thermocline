from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    db_path: Path
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    openmeteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    openmeteo_geocode_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    market_limit: int = 300
    market_scan_pages: int = 60
    min_liquidity: float = 50.0
    report_limit: int = 25
    max_open_positions: int = 25
    use_fixtures: bool = False
    kelly_fraction: float = 0.25
    max_position_size_usd: float = 50.0
    min_position_size_usd: float = 1.0
    kelly_bankroll_usd: float = 100.0
    event_max_legs: int = 2
    event_max_usd: float = 5.0
    event_max_open_events: int | None = None


def get_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    db_path = Path(os.getenv("WEATHER_EDGE_DB", project_root / "data" / "weather_edge.db"))
    market_limit = int(os.getenv("WEATHER_EDGE_MARKET_LIMIT", "500"))
    market_scan_pages = int(os.getenv("WEATHER_EDGE_MARKET_SCAN_PAGES", "120"))
    min_liquidity = float(os.getenv("WEATHER_EDGE_MIN_LIQUIDITY", "50"))
    report_limit = int(os.getenv("WEATHER_EDGE_REPORT_LIMIT", "25"))
    max_open_positions = int(os.getenv("WEATHER_EDGE_MAX_OPEN_POSITIONS", "25"))
    use_fixtures = os.getenv("WEATHER_EDGE_USE_FIXTURES", "0").lower() in {"1", "true", "yes"}
    kelly_fraction = float(os.getenv("WEATHER_EDGE_KELLY_FRACTION", "0.25"))
    max_position_size_usd = float(os.getenv("WEATHER_EDGE_MAX_POSITION_SIZE_USD", "50"))
    min_position_size_usd = float(os.getenv("WEATHER_EDGE_MIN_POSITION_SIZE_USD", "1"))
    kelly_bankroll_usd = float(os.getenv("WEATHER_EDGE_KELLY_BANKROLL_USD", "100"))
    event_max_legs = int(os.getenv("WEATHER_EDGE_EVENT_MAX_LEGS", "2"))
    event_max_usd = float(os.getenv("WEATHER_EDGE_EVENT_MAX_USD", "5.0"))
    event_max_open_events_raw = os.getenv("WEATHER_EDGE_EVENT_MAX_OPEN_EVENTS", "").strip()
    event_max_open_events = int(event_max_open_events_raw) if event_max_open_events_raw else None
    return Settings(
        project_root=project_root,
        db_path=db_path,
        market_limit=market_limit,
        market_scan_pages=market_scan_pages,
        min_liquidity=min_liquidity,
        report_limit=report_limit,
        max_open_positions=max_open_positions,
        use_fixtures=use_fixtures,
        kelly_fraction=kelly_fraction,
        max_position_size_usd=max_position_size_usd,
        min_position_size_usd=min_position_size_usd,
        kelly_bankroll_usd=kelly_bankroll_usd,
        event_max_legs=event_max_legs,
        event_max_usd=event_max_usd,
        event_max_open_events=event_max_open_events,
    )
