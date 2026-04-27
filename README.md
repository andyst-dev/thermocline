# 🌤️ Weather Edge

> **Systematic weather prediction market making on Polymarket.**
>
> Combines NWP ensemble forecasts (Open-Meteo GFS + ECMWF), Gaussian probability models, and Kelly Criterion position sizing to find mispriced temperature contracts — then learns from every resolved trade to continuously recalibrate forecast uncertainty.

---

## Why Weather Markets?

Polymarket runs hundreds of weather binary contracts daily: *"Will the high in Tokyo on May 3 exceed 22°C?"*. Prices are set by retail sentiment, not meteorology. A 24h GFS forecast is accurate to ~1.3°C. Markets often price a 3°C bucket at 10¢ when the true probability is 40¢.

**Weather Edge closes that gap.**

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Polymarket API │────▶│  Scanner Engine  │────▶│  Probability    │
│  (CLOB + Gamma) │     │  (Python 3.11+)  │     │  Model          │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                        │
        ┌───────────────────────────────────────────────┼──────────────┐
        ▼                                               ▼              ▼
┌───────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ GFS Ensemble  │    │ Gaussian σ       │    │ Kelly Sizing    │
│ (>36h horizon)│    │ (heuristic or    │    │ (fraction 0.25) │
│               │    │  empirical)      │    │                 │
└───────────────┘    └──────────────────┘    └─────────────────┘
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │ Paper Trading   │
                                             │ Engine          │
                                             └─────────────────┘
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │ Auto-Settle     │
                                             │ (METAR + Wunder)│
                                             └─────────────────┘
                                                      │
                                                      ▼
                                             ┌─────────────────┐
                                             │ Sigma Calibration │
                                             │ (Mode B)        │
                                             └─────────────────┘
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **🔭 Multi-Source Forecasts** | Open-Meteo hourly temps + GFS ensemble for >36h horizons |
| **📊 Gaussian + Ensemble Probabilities** | Independent error propagation with capped confidence [5%, 95%] |
| **💰 Kelly Criterion Sizing** | Fractional Kelly (0.25) with dynamic bankroll-aware position sizing |
| **🧪 Paper Trading** | Full simulated CLOB fills, PnL tracking, source reconciliation |
| **🔄 Auto-Recalibration (Mode B)** | Every resolved trade feeds residual analysis; sigma recalibrated daily at 06:00 UTC |
| **📈 Multi-Horizon Backtesting** | Historical forecast vs archive comparison with season-stratified sigma |
| **🛡️ Hardened Ops** | SQLite WAL, DB backups, log rotation, cron locks, heartbeat monitoring |

---

## Quick Start

```bash
# 1. Clone & setup
pip install -e .

# 2. Initialize database
python -m weather_edge init-db

# 3. Run single scan
python -m weather_edge scan

# 4. Verify top candidates
python -m weather_edge verify-candidates

# 5. Open paper positions (PASS-only)
python -m weather_edge paper-open --limit 5

# 6. Settle resolved trades
python -m weather_edge paper-settle

# 7. Manual sigma recalibration
python -m weather_edge recalibrate-sigma --lookback-days 60
```

### Cron Setup (Recommended)

```bash
# Runs every 30 minutes
*/30 * * * * /path/to/weather-edge/scripts/paper_cycle.sh
```

The cycle automatically: scans markets → settles closed positions → opens new paper trades → rotates logs → backups DB daily → recalibrates sigma at 06:00 UTC.

---

## Project Structure

```
weather-edge/
├── src/weather_edge/
│   ├── main.py                 # CLI entry point (12 commands)
│   ├── scanner.py              # Market scan + probability computation
│   ├── candidates.py           # PASS/PAPER/REJECT scoring + Kelly sizing
│   ├── backtest.py             # Historical residual analysis + sigma calibration
│   ├── settlement.py           # METAR + Weather.com resolution
│   ├── ensemble.py             # GFS ensemble member processing
│   ├── db.py                   # SQLite schema + CRUD
│   ├── models.py               # Typed dataclasses
│   └── clients/
│       ├── openmeteo.py        # Forecast API
│       ├── openmeteo_historical.py  # Archive + previous-runs API
│       ├── polymarket.py       # Market fetch
│       ├── clob.py             # Order book simulation
│       ├── aviationweather.py  # METAR observations
│       └── weathercom.py       # Wunderground official records
├── tests/                      # 102 pytest unit tests
├── scripts/paper_cycle.sh      # Production cron wrapper
└── data/weather_edge.db        # SQLite database (WAL mode)
```

---

## Probability Model

### Gaussian Path (short horizons ≤36h)
```
P(bucket) = Φ((upper - μ)/σ) - Φ((lower - μ)/σ)
```
where `μ` = forecast max temp, `σ` = forecast uncertainty

### Ensemble Path (long horizons >36h)
```
P(bucket) = count(ensemble_members ∈ bucket) / total_members
```

### Sigma Sources (hierarchical)
1. **Empirical** — from resolved trade residuals (horizon × season cross-buckets)
2. **Heuristic** — `σ = min(5.0, 1.5 + h/72 × 2.5)` as conservative fallback

---

## Kelly Sizing

```python
f* = (p × (b + 1) - 1) / b   # Full Kelly
size = bankroll × f* × 0.25   # Fractional (quarter Kelly)
```

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Bankroll | $100 | Conservative paper allocation |
| Kelly Fraction | 0.25 | Limits variance, avoids ruin |
| Max Position | $20 | Hard cap regardless of edge |
| Min Position | $1 | Dust-avoidance floor |

---

## Backtesting Framework

```bash
# Historical sigma calibration across 10 cities, 60 days, 5 horizons
python -m weather_edge backtest \
  --start-date 2026-02-01 \
  --end-date 2026-04-20 \
  --cities "New York,London,Tokyo,Seoul,Dallas,Miami" \
  --horizons 24,48,72,96,120
```

Outputs per-bucket statistics:
- `sigma_c` — empirical standard deviation of residuals
- `mean_residual_c` — forecast bias detection
- `by_horizon_season` — spring/summer/autumn/winter stratification

---

## Test Suite

```bash
PYTHONPATH=src pytest tests/ -v
```

**102 tests** covering:
- Bucket parsing (Celsius, Fahrenheit, open tails)
- Probability computation (exact, wide, tail)
- Ensemble aggregation
- Candidate scoring (PASS/PAPER/REJECT logic)
- Kelly sizing (edge cases, caps, floors)
- Backtest aggregation (horizon buckets, season grouping)
- Sigma calibration (load, fallback, recalibration)

---

## Current Status

| Metric | Value |
|--------|-------|
| Paper trades resolved | 42 |
| Active positions | 0 |
| Test coverage | 102/102 passing |
| Cron uptime | Every 30 min |
| Sigma calibration | Pending (Mode B learning) |

**Phase:** Live paper observation. Waiting for empirical sigma calibration from resolved trade residuals.

---

## Roadmap

- [x] Multi-source forecast aggregation
- [x] Gaussian + ensemble probability models
- [x] Paper trading with simulated CLOB fills
- [x] Kelly Criterion position sizing
- [x] Auto-settlement (METAR + Weather.com)
- [x] Backtesting framework (historical API)
- [x] Mode B auto-calibration (residual learning)
- [ ] Live Polymarket API integration (private key)
- [ ] ENSO bias correction (seasonal anomaly adjustment)
- [ ] Multi-city correlation risk model
- [ ] Web dashboard (PnL, open positions, calibration state)

---

## Disclaimer

**This is a research project. All trading is currently paper/simulated.**

- No real money is at risk
- Past performance of the model does not guarantee future results
- Weather markets involve significant uncertainty; the Kelly fraction (0.25) is intentionally conservative
- Always verify official resolution sources before any live trading

---

## License

MIT

---

*Built with Claude Code + Kimi K2.6 · Architecture inspired by ColdMath, 0xbobaaa, and the Polymarket weather trading community.*
