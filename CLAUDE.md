# CLAUDE.md — Thermocline / Weather Edge

> Ground-truth for any agent (Claude Code, Kimi, or other) working on this codebase.

## Project
Systematic weather prediction market making on Polymarket. Combines NWP ensemble forecasts, Gaussian probability models, and Kelly Criterion sizing to find mispriced temperature contracts.

**Current phase:** Production observation/readiness. Cron is allowed to collect reports, book snapshots, and ladder fill simulations. Paper/live opening stays disabled with `WEATHER_EDGE_DISABLE_PAPER_OPEN=1` until explicit operator approval and green calibration/readiness gates.

## Stack
- Python 3.11+
- SQLite (WAL mode) — `data/weather_edge.db`
- Polymarket CLOB API (read-only for now)
- Open-Meteo (forecasts + historical archive)
- AviationWeather (METAR observations)
- Weather.com / Wunderground (official resolution records)

## Commands
```bash
# Dev / observation
PYTHONPATH=src python3 -m weather_edge.main scan
PYTHONPATH=src WEATHER_EDGE_DISABLE_PAPER_OPEN=1 python3 -m weather_edge.main verify-candidates
PYTHONPATH=src WEATHER_EDGE_DISABLE_PAPER_OPEN=1 python3 -m weather_edge.main ladder-backtest-report
PYTHONPATH=src python3 -m weather_edge.main paper-settle
PYTHONPATH=src python3 -m weather_edge.main calibration-snapshot

# Test
PYTHONPATH=src pytest tests/ -q

# Cron (production)
*/30 * * * * builder cd /home/builder/weather-edge && bash scripts/paper_cycle.sh
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

## Key Metrics (current clean-slate baseline)
- Fresh runtime DB initialized after archiving legacy data: `paper_trades=0`, `backtest_records=0`
- Cron cadence: every 30 min
- Tests: 194/194 passing
- Latest mode: observation/readiness, no live orders, no automatic paper opens
