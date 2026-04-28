# CLAUDE.md — Thermocline / Weather Edge

> Ground-truth for any agent (Claude Code, Kimi, or other) working on this codebase.

## Project
Systematic weather prediction market making on Polymarket. Combines NWP ensemble forecasts, Gaussian probability models, and Kelly Criterion sizing to find mispriced temperature contracts.

**Current phase:** Live paper observation. Zero real money at risk.

## Stack
- Python 3.11+
- SQLite (WAL mode) — `data/weather_edge.db`
- Polymarket CLOB API (read-only for now)
- Open-Meteo (forecasts + historical archive)
- AviationWeather (METAR observations)
- Weather.com / Wunderground (official resolution records)

## Commands
```bash
# Dev
python -m weather_edge scan              # Single market scan
python -m weather_edge verify-candidates # Check top candidates
python -m weather_edge paper-open        # Open paper positions (PASS only)
python -m weather_edge paper-settle      # Settle resolved positions
python -m weather_edge recalibrate-sigma --lookback-days 60

# Test
PYTHONPATH=src pytest tests/ -v

# Cron (production)
*/30 * * * * /path/to/weather-edge/scripts/paper_cycle.sh
```

## Architecture
```
src/weather_edge/
  main.py               # CLI entry point (12 commands)
  scanner.py            # Market scan + probability computation
  candidates.py         # PASS / PAPER / REJECT scoring + Kelly sizing + gates
  models.py             # Typed dataclasses (Candidate, Market, etc.)
  backtest.py           # Historical residual analysis + sigma calibration
  settlement.py         # METAR + Weather.com resolution
  ensemble.py           # GFS ensemble member processing
  db.py                 # SQLite schema + CRUD
  clients/
    openmeteo.py        # Forecast API
    openmeteo_historical.py  # Archive + previous-runs
    polymarket.py       # Market fetch (CLOB + Gamma)
    clob.py             # Order book simulation
    aviationweather.py  # METAR observations + ICAO → lat/lon
    weathercom.py       # Wunderground official records
    nasa_gistemp.py     # Global anomaly sigma (lookback 25y)
```

## Rules
- **NEVER commit secrets.** No `.env`, no API keys, no private keys in git.
- **NEVER push to origin without explicit user authorization.** Ask every time.
- **Git identity is fixed:** `andyst-dev <150129844+andyst-dev@users.noreply.github.com>`. Do not change.
- **Default branch is `master`**, not `main`.
- **Paper trading safety:** `WEATHER_EDGE_DISABLE_PAPER_OPEN=1` is the default. Removing this flag requires explicit user approval.
- **Make minimal changes.** Do not refactor unrelated code when fixing a bug.
- **Run tests after every code change.** `PYTHONPATH=src pytest tests/ -v`
- **Type hints required** on all new functions and methods.
- **Candidate gates are sacred:**
  - Same-day markets (`horizon <= 0`) → REJECT for Forecast (wait for Scalp or day completion).
  - Wunderground gate applies ONLY to future markets (`horizon > 0`).
  - Airport coordinates (ICAO lat/lon) take priority over city-center geocoding for Open-Meteo.
- **Sigma calibration:** GISTEMP lookback is 25 years, sigma floor 0.20°C. Do not reduce without data justification.
- **Kelly sizing:** fraction 0.25, max $20, min $1. Hard caps regardless of edge.

## Workflow
- Ask clarifying questions before starting complex tasks.
- Explain trade-offs when unsure between two approaches.
- Create separate commits per logical change. No 47-file monster commits.
- After any patch, run a dry-run scan to confirm no regression: `python -m weather_edge scan`
- Update tests when changing business logic. 102 tests must pass.
- When adding a new gate or probability path, document the rationale in the commit message.

## Out of Scope
- Live/real-money trading. All execution is paper/simulated until explicitly authorized.
- Web dashboard (roadmap item, do not start without approval).
- ENSO bias correction (roadmap item, do not start without approval).
- Modifying `scripts/paper_cycle.sh` cron logic without discussing with the user.

## Key Metrics (last known)
- Paper trades resolved: 42
- Active positions: 0
- Cron cadence: every 30 min
- Tests: 102/102 passing
