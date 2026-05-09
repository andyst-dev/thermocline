# Weather Edge — PolyDekos Ladder Strategy Roadmap

**Date:** 2026-05-07
**Repo:** `/home/builder/weather-edge`
**Status:** planning / observation only
**Real money:** **OUT OF SCOPE** until Andy explicitly authorizes it.

## Context

Andy asked whether Weather Edge matches the strategy described by PolyDekos on X:

> Do not bet only the single most likely exact temperature. Temperature is a distribution; a 1°C shift can kill a single-bucket trade. Instead, use a ladder/range strategy: buy adjacent temperature levels around the forecast center when the combined cost/EV is attractive. Scale in over time and size by weather stability / city volatility.

Current Weather Edge state:

- Cron is now clean: **system cron only**, no Hermes/agent cron.
- Active cron file: `/etc/cron.d/weather-edge`
- Cron command: `*/30 * * * * builder cd /home/builder/weather-edge && bash scripts/paper_cycle.sh`
- `scripts/paper_cycle.sh` exports `WEATHER_EDGE_DISABLE_PAPER_OPEN=1`, so automatic paper opening is disabled by default.
- The bot currently behaves mostly like a **single-bucket mispricing scanner**.
- It already calculates useful signals: model probability, market probability, executable EV, orderbook depth, fill simulation, sigma, regime uncertainty, Kelly sizing, station lock, and settlement source checks.
- It does **not yet** evaluate adjacent exact-temperature buckets as one portfolio/ladder.
- Current paper closed PnL is negative and calibration gate has been bad recently (`Brier ~0.86`, `bucket calibration error ~0.94`).

## Objective

Transform Weather Edge from:

```text
single best bucket scanner
```

into:

```text
range / ladder portfolio scanner with scale-in, volatility sizing, event exposure caps, calibration gates, and paper validation
```

The goal is not to force trades. The goal is to produce robust, empirically validated candidate ladders and only later consider real-money execution.

---

## Non-negotiable Safety Rules

1. **No real-money trading** without explicit Andy authorization.
2. **Do not remove** `WEATHER_EDGE_DISABLE_PAPER_OPEN=1` from `scripts/paper_cycle.sh` without explicit Andy authorization.
3. **Do not push to GitHub** without explicit Andy authorization.
4. Keep the system in observation/paper mode until:
   - calibration gate passes,
   - ladder paper PnL is positive on enough samples,
   - settlement quality is validated,
   - drawdown is acceptable,
   - Andy explicitly approves live trading.
5. Never tune constants only to fit recent wins. Validate with fresh post-fix data.

---

## Current Key Files

Core pipeline:

- `src/weather_edge/main.py`
  - CLI commands
  - candidate generation
  - `cmd_paper_open()`
  - `paper-cycle`
- `src/weather_edge/candidates.py`
  - `Candidate`
  - `_top_bucket()`
  - `build_candidate()`
  - `compute_kelly_size()` wrapper
- `src/weather_edge/scanner.py`
  - market scan
  - bucket probabilities
  - orderbook/fill data
- `src/weather_edge/models.py`
  - `WeatherMarket`
  - `BucketProbability`
  - `ScanResult`
- `src/weather_edge/uncertainty.py`
  - regime uncertainty
  - tail hedge plan
- `src/weather_edge/risk.py`
  - position sizing / Kelly
- `src/weather_edge/calibration.py`
  - calibration reports/gate
- `src/weather_edge/db.py`
  - SQLite schema and CRUD

Reports/data:

- `reports/paper_trades.json`
- `reports/paper_settlement.json`
- `reports/paper_cycle_heartbeat.json`
- `reports/calibration_*.txt`
- `data/weather_edge.db`

Cron:

- `/etc/cron.d/weather-edge`
- `/home/builder/weather-edge/scripts/paper_cycle.sh`

---

# Implementation Plan

## Phase 0 — Reconfirm Clean Runtime State

**Goal:** ensure future work is based on a clean, non-agent, non-live setup.

### Task 0.1 — Verify cron state

Run:

```bash
hermes cron status
crontab -l 2>/dev/null || true
cat /etc/cron.d/weather-edge
bash -n /home/builder/weather-edge/scripts/paper_cycle.sh
```

Expected:

```text
Hermes cron: No active jobs
User crontab: empty or no weather-edge entry
/etc/cron.d/weather-edge has the only Weather Edge cron
paper_cycle.sh syntax OK
```

### Task 0.2 — Verify paper-open safety

Run:

```bash
grep -n "WEATHER_EDGE_DISABLE_PAPER_OPEN" /home/builder/weather-edge/scripts/paper_cycle.sh
```

Expected:

```text
export WEATHER_EDGE_DISABLE_PAPER_OPEN=1
```

Do not change this without Andy.

---

## Phase 1 — Fix / Validate Calibration Gate

**Priority:** highest / blocking.
**Why:** A ladder is useless if the probabilities are badly calibrated.

Current warning signs:

```text
Brier score too high: ~0.86 > 0.30
bucket calibration error too high: ~0.94 > 0.25
calibration_gate allowed: false
```

### Task 1.1 — Inspect calibration reports

Files:

- `reports/calibration_2026-05-*.txt`
- `src/weather_edge/calibration.py`

Run:

```bash
cd /home/builder/weather-edge
python -m weather_edge.main calibration-report || true
```

If command name differs, inspect `src/weather_edge/main.py` CLI command mapping.

Questions to answer:

- Is the model systematically overconfident?
- Are exact 1°C buckets the main source of bad Brier?
- Are some cities/stations causing most of the error?
- Are source mismatches / timezone issues polluting `backtest_records`?

### Task 1.2 — Validate `backtest_records` quality

Run SQLite checks:

```bash
cd /home/builder/weather-edge
python3 - <<'PY'
import sqlite3
con = sqlite3.connect('data/weather_edge.db')
con.row_factory = sqlite3.Row
for q in [
    "select count(*) as n from backtest_records",
    "select city, count(*) as n, round(avg(observed_max_c - forecast_max_c),2) as mean_residual from backtest_records group by city order by n desc limit 20",
    "select round(horizon_hours/24)*24 as horizon_bucket, count(*) as n, round(avg(observed_max_c - forecast_max_c),2) as mean_residual from backtest_records group by 1 order by 1"
]:
    print('\n', q)
    for r in con.execute(q):
        print(dict(r))
PY
```

Check for:

- impossible residuals,
- suspicious timezone/geography clusters,
- too few samples,
- weather.com vs METAR divergences.

### Task 1.3 — Recalibrate sigma only after data quality checks

Run:

```bash
cd /home/builder/weather-edge
PYTHONPATH=src python -m weather_edge.main recalibrate-sigma --lookback-days 60
```

Then run a dry scan:

```bash
cd /home/builder/weather-edge
PYTHONPATH=src python -m weather_edge.main verify-candidates
```

Success criteria:

- `calibration_gate.allowed == true`, or if still false, reasons are understood and documented.
- No constants changed blindly.

### Task 1.4 — Tests for calibration gate

Add/verify tests in:

- `tests/test_calibration.py`

Required tests:

- bad Brier blocks,
- bad bucket calibration blocks,
- sufficient clean sample passes,
- insufficient sample blocks or returns conservative state.

Run:

```bash
cd /home/builder/weather-edge
PYTHONPATH=src pytest tests/test_calibration.py -v
```

---

## Phase 2 — Add Time-Based Scale-In

**Priority:** high.
**Tweet alignment:** 48h = small, 24h = medium, final hours = full if stable.

### Desired rule

Initial sizing should be multiplied by horizon factor:

```text
horizon_hours > 48        -> 0.30
24 < horizon_hours <= 48  -> 0.60
8 < horizon_hours <= 24   -> 0.80
horizon_hours <= 8        -> 1.00 only if forecast stability/regime allows it
```

### Task 2.1 — Add horizon scale function

Likely file:

- `src/weather_edge/risk.py` or `src/weather_edge/candidates.py`

Function idea:

```python
def horizon_scale_factor(horizon_hours: float) -> float:
    if horizon_hours > 48:
        return 0.30
    if horizon_hours > 24:
        return 0.60
    if horizon_hours > 8:
        return 0.80
    return 1.00
```

### Task 2.2 — Apply factor to recommended size

Likely files:

- `src/weather_edge/candidates.py`
- `src/weather_edge/risk.py`

The final `recommended_size_usd` should become:

```text
base_kelly_size * horizon_factor * regime_factor * stability_factor
```

Clamp to min/max position size afterwards.

### Task 2.3 — Add tests

Likely file:

- `tests/test_kelly_sizing.py` or `tests/test_risk_sizing.py`

Required tests:

- 72h returns 30% of base size,
- 36h returns 60%,
- 12h returns 80%,
- 4h returns 100% only if no later gate reduces it,
- min/max clamps still work.

Run:

```bash
cd /home/builder/weather-edge
PYTHONPATH=src pytest tests/test_kelly_sizing.py tests/test_risk_sizing.py -v
```

---

## Phase 3 — Turn Regime Uncertainty Into Real Sizing

**Priority:** high.
**Problem:** `regime_uncertainty` is currently mostly a logged signal. It should affect exposure.

### Desired rule

```text
regime.level == "low"      -> 1.00
regime.level == "elevated" -> 0.70
regime.level == "high"     -> 0.40 or full block, depending on candidate type
```

For exact narrow buckets, consider blocking `high` entirely unless it is a deliberately cheap hedge.

### Task 3.1 — Add scale factor to uncertainty module

File:

- `src/weather_edge/uncertainty.py`

Add function/method:

```python
def regime_scale_factor(level: str) -> float:
    ...
```

or method:

```python
class RegimeUncertainty:
    def scale_factor(self) -> float: ...
```

### Task 3.2 — Apply in candidate sizing

File:

- `src/weather_edge/candidates.py`

Combine with horizon factor:

```text
final_size = base_size * horizon_factor * regime_factor
```

### Task 3.3 — Tests

File:

- `tests/test_uncertainty.py`

Required tests:

- low -> 1.0,
- elevated -> 0.7,
- high -> 0.4,
- high uncertainty adds caution/blocker for exact buckets.

Run:

```bash
cd /home/builder/weather-edge
PYTHONPATH=src pytest tests/test_uncertainty.py tests/test_candidates.py -v
```

---

## Phase 4 — Add Event Exposure Caps Before Ladder

**Priority:** high / prerequisite for ladder.

### Problem

`cmd_paper_open()` currently avoids duplicate `market_id`. But a ladder uses multiple adjacent exact-temperature markets with different `market_id`s that belong to the same event/city/date. Without event caps, the bot can over-concentrate.

### Desired rule

For each event bucket group, e.g. `(city, target_date, event_slug/event_id if available)`:

```text
max legs per event: 2 or 3
max total exposure per event: configurable, e.g. $2-$5 in paper; later live much stricter
max global open positions: keep existing cap
```

### Task 4.1 — Define event key

Likely key fields:

- `event_id` if normalized from Polymarket event discovery,
- fallback: `(city, target_date, question family)`.

Files to inspect/modify:

- `src/weather_edge/clients/polymarket.py`
- `src/weather_edge/models.py`
- `src/weather_edge/scanner.py`
- `src/weather_edge/candidates.py`
- `src/weather_edge/db.py`

### Task 4.2 — Persist event key or enough fields

Make sure paper trades store enough data to group by event:

- `city`
- `target_date`
- `event_id` or `event_slug`
- `candidate_json` already contains some context; prefer explicit columns if not too invasive.

Files:

- `src/weather_edge/db.py`
- migrations/schema init logic

### Task 4.3 — Enforce caps in `cmd_paper_open()`

File:

- `src/weather_edge/main.py`

Before opening a candidate/leg:

- compute current exposure for that event,
- reject if max legs reached,
- reject if total cost exceeds cap,
- log reason in report.

### Task 4.4 — Tests

Likely file:

- `tests/test_paper_open.py` or `tests/test_candidates.py`

Required tests:

- second leg can open if within cap,
- third/fourth leg is blocked,
- candidate from different city/date not blocked,
- total exposure cap works even if leg count cap not reached.

---

## Phase 5 — Implement Ladder / Range Portfolio Evaluation

**Priority:** core feature.
**Tweet alignment:** do not bet one number; buy adjacent levels when combined cost is attractive.

### Desired behavior

For a given event/city/date, build candidate ladders around forecast center.

Example:

```text
forecast center: 31°C
candidate ladder: 30°C + 31°C + 32°C
```

For each possible ladder:

```text
cost_total = sum(best_ask_i or fill_cost_i)
prob_hit = sum(model_prob_i)  # exact buckets are mutually exclusive if correctly grouped
expected_payout = prob_hit * 1.00
EV = expected_payout - cost_total
ROI = EV / cost_total
max_loss = cost_total
profit_if_hit = 1.00 - cost_total
```

Only accept if:

```text
cost_total < 1.00
EV > threshold
ROI > threshold
all legs have enough depth
event cap not exceeded
settlement source/station checks pass
calibration gate passes
```

### Task 5.1 — Create ladder data model

Option A: add to `src/weather_edge/models.py`

```python
@dataclass
class LadderLeg:
    market_id: str
    slug: str
    side: str
    lower: float | None
    upper: float | None
    best_ask: float
    fill_cost_usd: float | None
    model_prob: float
    market_prob: float

@dataclass
class LadderCandidate:
    event_key: str
    city: str
    target_date: str
    legs: list[LadderLeg]
    total_cost: float
    prob_hit: float
    ev: float
    roi: float
    max_loss: float
    profit_if_hit: float
    horizon_hours: float
    regime_level: str
```

Option B: keep internal dicts first to reduce migration cost. Prefer dataclasses if implementing seriously.

### Task 5.2 — Build ladder grouping function

Create new file:

- `src/weather_edge/ladder.py`

Suggested functions:

```python
def group_exact_temperature_candidates(candidates: list[Candidate]) -> dict[EventKey, list[Candidate]]: ...

def build_ladder_candidates(group: list[Candidate], forecast_value_c: float, sigma_c: float) -> list[LadderCandidate]: ...

def score_ladder(ladder: LadderCandidate) -> float: ...
```

Grouping should include:

- city,
- target_date,
- event id/slug if available,
- unit/type compatibility,
- exact-temperature buckets only.

### Task 5.3 — Choose adjacent buckets

Possible ladder widths:

```text
±1°C around forecast center for normal sigma
±2°C only if sigma / ensemble spread justifies it and price remains attractive
```

Avoid ladders with missing/ambiguous bucket boundaries.

### Task 5.4 — Compute combined EV correctly

For exact mutually exclusive buckets:

```text
prob_hit = sum(model_prob_i)
market_cost = sum(executable ask/fill cost per $1 payout)
EV = prob_hit - market_cost
```

Caution:

- If markets are not truly mutually exclusive, do not use simple sum.
- For “or higher” / “or below” markets, overlap handling is different. Start with exact °C/°F buckets only.

### Task 5.5 — Integrate ladder into candidate reports

Report should show:

```json
{
  "strategy": "ladder_exact_range",
  "event_key": "Tokyo|2026-05-07|...",
  "legs": [...],
  "total_cost": 0.79,
  "prob_hit": 0.91,
  "ev": 0.12,
  "roi": 0.15,
  "max_loss": 0.79,
  "profit_if_hit": 0.21,
  "horizon_factor": 0.6,
  "regime_factor": 0.7,
  "recommended_size_usd": 1.0
}
```

Likely reports:

- `reports/verified_candidates.json`
- `reports/ladder_candidates.json`

### Task 5.6 — Integrate paper opening for ladder legs

File:

- `src/weather_edge/main.py`

Do not just call existing `insert_paper_trade()` blindly unless each leg is clearly stored and linked to the same ladder/event.

Need at least:

- `strategy = ladder_exact_range` in `candidate_json` or notes,
- shared `ladder_id` or event key,
- per-leg cost and shares,
- total ladder cost tracked in report.

### Task 5.7 — Tests

Create:

- `tests/test_ladder.py`

Required tests:

1. Exact adjacent buckets produce a ladder.
2. Non-adjacent buckets do not produce a ladder.
3. Sum cost >= 1 rejects ladder.
4. EV below threshold rejects ladder.
5. Mutually exclusive exact buckets use `prob_hit = sum(model_prob)`.
6. Overlapping “or higher/or below” markets are excluded initially.
7. Event cap blocks over-concentration.

Run:

```bash
cd /home/builder/weather-edge
PYTHONPATH=src pytest tests/test_ladder.py tests/test_candidates.py -v
```

---

## Phase 6 — Forecast Stability Gate

**Priority:** medium.
**Tweet alignment:** scale in only if forecast remains stable.

### Problem

A final 8h full-size rule is dangerous if the forecast has drifted by 1°C+ since the first entry.

### Desired rule

When a position/ladder exists for an event:

```text
if abs(current_forecast_c - initial_forecast_c) > 1.0:
    do not add exposure
    mark event as unstable
```

Possible thresholds:

```text
<= 0.5°C: stable
0.5°C - 1.0°C: caution / reduced add
> 1.0°C: block add
```

### Task 6.1 — Store initial forecast

Files:

- `src/weather_edge/db.py`
- `src/weather_edge/main.py`

Persist:

- `initial_forecast_c`
- `latest_forecast_c`
- `forecast_drift_c`
- `opened_horizon_hours`

Can be explicit DB columns or inside `candidate_json` initially.

### Task 6.2 — Compare at next cycle

When `cmd_paper_open()` considers adding to an existing event:

- load previous ladder/trade,
- compare forecast,
- block/reduce if drift too high.

### Task 6.3 — Tests

Tests:

- stable forecast allows adding,
- drift 0.8°C reduces or cautions,
- drift 1.2°C blocks adding,
- different event not affected.

---

## Phase 7 — Ladder Backtest

**Priority:** high before any live money.
**Goal:** prove ladder beats single-bucket on clean historical/paper data.

### Task 7.1 — Add ladder backtest report

Files:

- `src/weather_edge/backtest.py`
- `src/weather_edge/calibration.py`
- new report: `reports/ladder_backtest.json`

Compare strategies:

```text
single best bucket
ladder ±1°C
ladder ±2°C
scale-in simulated
regime-scaled sizing
```

Metrics:

- trade count,
- hit rate,
- average cost,
- average payout,
- PnL,
- ROI,
- max drawdown,
- Brier/calibration by bucket,
- performance by city,
- performance by horizon.

### Task 7.2 — Acceptance criteria

Do not move toward live unless:

```text
ladder strategy has positive EV on clean sample
PnL positive after simulated fills
drawdown acceptable
sample size is meaningful
performance not concentrated in 1 lucky win
settlement source quality is acceptable
```

---

## Phase 8 — Paper Validation Protocol

**Priority:** required before live.

### Stage A — Observation only

Keep:

```bash
WEATHER_EDGE_DISABLE_PAPER_OPEN=1
```

Generate ladder reports only.

Success criteria:

- reports generated every 30 min,
- no cron failures,
- ladder candidates look sane,
- no duplicate overexposure.

### Stage B — Paper ladder micro-size

Only with Andy approval, enable paper opening, still no real money.

Suggested:

```text
paper size: $1 per ladder or per leg max
max open ladders: very low
run for several days minimum
```

Success criteria:

- clean settlements,
- PnL positive excluding legacy trades,
- calibration stable,
- no weird city/source failures.

### Stage C — Live micro-size

Only with explicit Andy approval.

Start extremely small:

```text
$1-$5 max total live exposure initially
manual review of each candidate
no fully automated live execution
```

---

# Exact TODO Checklist

## Immediate

- [ ] Verify cron remains system-only and paper-open disabled.
- [ ] Inspect current calibration reports.
- [ ] Validate `backtest_records` quality.
- [ ] Recalibrate sigma only if data quality is acceptable.
- [ ] Confirm whether `calibration_gate.allowed` can pass.
- [ ] Add/verify calibration gate tests.

## Short-term code changes

- [ ] Add `horizon_scale_factor()`.
- [ ] Apply horizon scale to recommended sizing.
- [ ] Add tests for horizon sizing.
- [ ] Add `regime_scale_factor()`.
- [ ] Apply regime scale to recommended sizing.
- [ ] Add tests for regime sizing.
- [ ] Add event key / event exposure grouping.
- [ ] Enforce max legs and max exposure per event.
- [ ] Add tests for event exposure caps.

## Ladder implementation

- [ ] Add ladder data model or dict schema.
- [ ] Group exact-temperature candidates by event/city/date.
- [ ] Build adjacent bucket ladders around forecast center.
- [ ] Compute total cost, prob_hit, EV, ROI, max_loss, profit_if_hit.
- [ ] Reject overlapping non-exact markets initially.
- [ ] Add `reports/ladder_candidates.json`.
- [ ] Integrate ladder paper-open logic with shared ladder/event ID.
- [ ] Add tests for ladder construction and rejection rules.

## Stability / scale-in

- [ ] Store initial forecast at opening.
- [ ] Compare current vs initial forecast each cycle.
- [ ] Reduce/block adding if drift > threshold.
- [ ] Add tests for forecast drift gate.

## Validation

- [ ] Add ladder backtest report.
- [ ] Compare single-bucket vs ladder strategies.
- [ ] Run full tests: `PYTHONPATH=src pytest tests/ -v`.
- [ ] Run dry scan: `PYTHONPATH=src python -m weather_edge.main verify-candidates`.
- [ ] Let cron accumulate observation data.
- [ ] Review paper results after enough samples.

---

# Commands To Run During Work

From repo root:

```bash
cd /home/builder/weather-edge
```

Tests:

```bash
PYTHONPATH=src pytest tests/ -v
```

Specific tests:

```bash
PYTHONPATH=src pytest tests/test_calibration.py -v
PYTHONPATH=src pytest tests/test_kelly_sizing.py tests/test_risk_sizing.py -v
PYTHONPATH=src pytest tests/test_uncertainty.py -v
PYTHONPATH=src pytest tests/test_candidates.py -v
PYTHONPATH=src pytest tests/test_ladder.py -v
```

Dry scan / candidates:

```bash
PYTHONPATH=src python -m weather_edge.main verify-candidates
```

Paper cycle manual dry run, still with paper open disabled:

```bash
WEATHER_EDGE_DISABLE_PAPER_OPEN=1 PYTHONPATH=src python -m weather_edge.main paper-cycle
```

Cron log:

```bash
tail -200 logs/paper_cycle.log
cat reports/paper_cycle_heartbeat.json
```

Paper reports:

```bash
python3 -m json.tool reports/paper_trades.json | head -120
python3 -m json.tool reports/paper_settlement.json | head -120
```

---

# Definition of Done Before Considering Real Money

All must be true:

- [ ] Cron is stable and system-only.
- [ ] Paper-open/live-open safety flags are understood and controlled.
- [ ] Calibration gate passes on clean data.
- [ ] Ladder strategy implemented and tested.
- [ ] Event exposure caps implemented and tested.
- [ ] Horizon scale-in implemented and tested.
- [ ] Regime volatility sizing implemented and tested.
- [ ] Forecast stability gate implemented and tested.
- [ ] Ladder paper trading has enough sample size.
- [ ] Closed paper PnL is positive on post-fix trades, not just legacy/lucky noise.
- [ ] Settlements are reliable by source/city.
- [ ] Andy explicitly approves moving beyond paper.

---

# Summary For Future Agent Starting From Zero

If you are a future agent with no context:

1. Read this file first: `docs/polydekos-ladder-roadmap.md`.
2. Read project rules: `CLAUDE.md`.
3. Do **not** trade real money.
4. Do **not** push to GitHub without Andy.
5. Verify cron and safety flags before changing trading logic.
6. Start with calibration quality, not ladder code.
7. Implement ladder only after event exposure caps exist.
8. Validate through tests and paper results before suggesting live trading.

The strategic direction is correct, but Weather Edge must evolve from single-bucket EV scanning into portfolio-level ladder evaluation before it deserves real capital.
