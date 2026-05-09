# 🌡️ Thermocline / Weather Edge

> **Weather-market intelligence for Polymarket.**
>
> Thermocline scans weather contracts, turns forecasts into calibrated probabilities, simulates execution quality on the CLOB, and observes PolyDekos-style adjacent-bucket ladders before any capital is put at risk.

It is built to answer one practical question:

> *Is this weather market actually mispriced after forecast uncertainty, liquidity, calibration, and event exposure are accounted for?*

The current release is a **production observation build**: cron-ready, tested, snapshotting live market conditions, but deliberately **not live trading**.

---

## Current Status

| Area | Status |
|---|---|
| Runtime | System cron every 30 min from `/home/builder/weather-edge` |
| Latest validation | `PYTHONPATH=src pytest tests/ -q` → **194 passed** |
| Trading mode | **No live trading** |
| Paper opening | Disabled in cron with `WEATHER_EDGE_DISABLE_PAPER_OPEN=1` |
| Calibration gate | Currently blocking; paper/live should stay off |
| PolyDekos ladder | Implemented for read-only observation, fill simulation, and reporting |
| Ladder readiness | Not tradable yet: missing historical fill-level replay, realized PnL/ROI/drawdown, and ladder-level calibration |

**Bottom line:** the cron may run to collect data and snapshots. Do not enable paper/live openings until calibration and ladder readiness gates are explicitly green and the operator authorizes it.

---

## Safety Rules

1. **Cron keeps paper opening disabled:**

   ```bash
   export WEATHER_EDGE_DISABLE_PAPER_OPEN=1
   ```

2. **Calibration gate blocks risk-taking** when Brier score / bucket calibration are outside thresholds.
3. **Ladder fill simulation is read-only:** it calls order-book simulation, writes snapshots, and places no orders.
4. **No secrets belong in the repo or README.** Keep credentials in the runtime environment only.
5. **Do not commit runtime artifacts:** DBs, logs, reports, snapshots, backups, locks, and generated datasets are ignored.

---

## What the System Does

Thermocline targets weather binary markets such as:

> “Will the high in Tokyo be 22°C or higher on May 3?”

The pipeline:

1. discovers Polymarket weather markets;
2. parses temperature buckets / thresholds;
3. fetches weather forecasts and context;
4. computes probabilities with uncertainty;
5. rejects unsafe or poorly calibrated opportunities;
6. tracks paper accounting and settlement;
7. records order-book snapshots and fill simulations;
8. produces reports for calibration, risk, and ladder readiness.

---

## Architecture

```text
Polymarket Gamma/CLOB
        │
        ▼
Market discovery + parsing
        │
        ▼
Forecast/context layer
  - Open-Meteo forecasts
  - ensemble / horizon features
  - Weather.com / METAR settlement sources
  - NASA GISTEMP baseline with cache/fallback for global-temperature markets
        │
        ▼
Scanner + probability model
  - Gaussian bucket probability
  - uncertainty / horizon / regime adjustments
  - candidate scoring
        │
        ├───────────────┐
        ▼               ▼
Single-bucket       PolyDekos-style ladder observation
candidate flow      - adjacent buckets
                    - deterministic ladder_id
                    - parent_ladder_id per leg
                    - token_id per leg
                    - read-only fill simulation
                    - ladder order-book snapshots
        │               │
        ▼               ▼
Risk and gates       Ladder backtest report
  - calibration gate  - qualitative hit-rate only for now
  - event exposure    - no historical fill/PnL yet
  - sizing caps
        │
        ▼
Reports + paper accounting + settlement audit
        │
        ▼
Cron heartbeat + DB backup + runtime snapshots
```

---

## Key Modules

```text
src/weather_edge/
├── main.py                    # CLI entry point and paper-cycle orchestration
├── scanner.py                 # Market scan and probability computation
├── candidates.py              # PASS/PAPER/REJECT scoring
├── calibration.py             # Calibration reports/gates
├── risk.py                    # Risk sizing logic
├── event_exposure.py          # Event-level exposure caps
├── uncertainty.py             # Horizon/regime uncertainty adjustments
├── weather_features.py        # Forecast/weather feature extraction
├── ladder.py                  # PolyDekos-style adjacent-bucket ladders
├── ladder_fill.py             # Read-only per-leg fill simulation + snapshots
├── ladder_backtest.py         # Qualitative ladder-vs-single report
├── closed_trades_audit.py     # Settlement/accounting audit
├── research_dataset.py        # Export research dataset artifacts
├── settlement.py              # Resolution via weather sources
├── db.py                      # SQLite schema and persistence
└── clients/
    ├── clob.py                # CLOB order-book / fill simulation helpers
    ├── polymarket.py          # Market discovery
    ├── weathercom.py          # Weather.com / Wunderground observations
    ├── aviationweather.py     # METAR observations
    ├── openmeteo.py           # Forecast API
    └── nasa_gistemp.py        # GISTEMP baseline with timeout/cache/fallback
```

Docs:

```text
docs/polydekos-ladder-roadmap.md       # Ladder strategy roadmap
docs/ladder-paper-readiness-guide.md   # Go/no-go checklist before paper ladder
docs/v1-spec.md                        # Earlier system spec
```

Runtime artifacts are under `data/`, `reports/`, and `logs/` and are intentionally ignored by Git.

---

## Common Commands

### Install / setup

```bash
pip install -e .
PYTHONPATH=src python3 -m weather_edge.main init-db
```

### Run tests

```bash
PYTHONPATH=src pytest tests/ -q
```

Targeted safety/ladder checks:

```bash
PYTHONPATH=src pytest \
  tests/test_ladder.py \
  tests/test_ladder_fill.py \
  tests/test_ladder_backtest.py \
  tests/test_nasa_gistemp.py \
  tests/test_scanner_global.py \
  -q
```

### Safe candidate verification

```bash
PYTHONPATH=src \
WEATHER_EDGE_DISABLE_PAPER_OPEN=1 \
python3 -m weather_edge.main verify-candidates
```

Outputs include `reports/verified_candidates.json` and policy flags such as:

```json
{
  "ladder_fill_simulation_read_only": true,
  "ladder_fill_simulation_places_orders": false
}
```

### Ladder readiness report

```bash
PYTHONPATH=src \
WEATHER_EDGE_DISABLE_PAPER_OPEN=1 \
python3 -m weather_edge.main ladder-backtest-report \
  --output /tmp/weather_edge_ladder_backtest_report.json
```

Current interpretation: useful for qualitative comparison, **not** sufficient for trading because historical fill-level replay and realized ladder PnL are not available yet.

### Full paper cycle wrapper

```bash
bash scripts/paper_cycle.sh
```

Production cron currently runs:

```cron
*/30 * * * * builder cd /home/builder/weather-edge && bash scripts/paper_cycle.sh
```

The wrapper uses a project-local lock:

```text
data/run/weather_edge_paper_cycle.lock
```

and writes heartbeat state to:

```text
reports/paper_cycle_heartbeat.json
```

---

## CLI Commands

Current CLI commands include:

```text
init-db
fetch-markets
scan
verify-candidates
paper-open
paper-report
paper-settle
reconcile-sources
paper-cycle
run-once
calibration-report
calibration-snapshot
audit-closed-trades
risk-sizing-report
export-research-dataset
ladder-backtest-report
recalibrate-sigma
backtest
```

`paper-open` exists for controlled experiments, but should not be used automatically while `WEATHER_EDGE_DISABLE_PAPER_OPEN=1` and calibration gates are blocking.

---

## Probability and Risk Model

Single-bucket probabilities use Gaussian bucket integration:

```text
P(bucket) = Φ((upper - μ) / σ) - Φ((lower - μ) / σ)
```

where:

- `μ` = forecast temperature estimate;
- `σ` = forecast uncertainty;
- uncertainty is adjusted by horizon/regime/calibration context.

Risk controls include:

- calibration gate;
- event exposure caps;
- conservative sizing / caps;
- CLOB fill simulation;
- rejection of candidates with insufficient liquidity or unstable inputs.

---

## PolyDekos / Adjacent-Bucket Ladder

The ladder path is designed around a range/ladder thesis rather than betting only the exact most likely bucket.

Implemented now:

- deterministic `ladder_id`;
- `parent_ladder_id` on every leg;
- `token_id` propagation for CLOB simulation;
- adjacent buckets / narrow ranges;
- read-only per-leg fill simulation via `ladder_fill.py`;
- gzip snapshots for ladder books;
- qualitative report comparing:
  - `single_best_bucket`,
  - `ladder_pm_1c`,
  - `ladder_pm_2c`.

Not ready yet:

- historical fill-level replay by `ladder_id`;
- realized ladder cost / payout / PnL / ROI / max drawdown;
- ladder-level calibration gate;
- enough resolved ladder observations to justify paper/live.

---

## Go / No-Go Checklist Before Enabling Paper or Live

Do **not** enable automatic paper/live openings until all are true:

- [ ] calibration gate allowed;
- [ ] candidate reports show stable non-zero accepted candidates;
- [ ] ladder fill snapshots have accumulated across many cycles;
- [ ] historical ladder replay works by `event_key + ladder_id`;
- [ ] ladder settlement maps each leg to realized outcomes;
- [ ] PnL / ROI / drawdown are computed from real historical snapshots;
- [ ] ladder-level calibration metrics are acceptable;
- [ ] cron heartbeat remains stable;
- [ ] explicit operator approval is given.

---

## Git Hygiene

Useful code/docs/tests should be committed intentionally. Generated runtime artifacts should not.

Ignored by design:

- `data/backups/`
- `data/cache/`
- `data/run/`
- `data/snapshots/`
- `data/*.db*`
- `data/research_dataset*.jsonl`
- `data/sigma_calibration.json`
- `reports/*` except placeholders
- `logs/*` except placeholders
- `.hermes/`

Before any commit:

```bash
git status --short
PYTHONPATH=src pytest tests/ -q
```

Commit/push only after explicit operator approval.

---

## Disclaimer

This is a research and observation system for prediction-market strategy development. It is not financial advice. Live trading should remain disabled until the system has proven calibration, execution quality, and risk behavior on resolved historical observations.
