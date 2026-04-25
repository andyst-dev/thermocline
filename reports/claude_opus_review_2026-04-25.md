# Claude Code Opus review — 2026-04-25

Prompt: senior trading infra/risk review for Weather Edge readiness.

## Verdict
- Continue paper trading: GO conditionnel.
- Micro-live manual $1: NO-GO as repo support; PARTIAL only if manually guided with clear explain output.
- Automated live: NO-GO.

## Main blockers identified
1. Paper PnL is still likely inflated by dust/fill assumptions.
2. Need walk-the-book fill simulation, not just best ask / capacity.
3. Process-level CLOB/METAR caches need TTL or removal.
4. METAR settlement is not identical to official Wunderground resolution source.
5. Sigma/model probabilities are too confident; cap model probabilities until calibrated.
6. Need snapshot/replay audit trail for books/forecasts.
7. Need real CLOB signing/order infrastructure + kill switch before live.

## Minimum before real money
- 30+ days paper with realistic fills.
- Official-source settlement reconciliation on 50+ markets.
- Calibrated sigma and bounded model probabilities.
- Dedicated hot wallet, hard caps, kill switch, append-only logs.

## Immediate action accepted
Cap model probabilities away from 0/1 until calibration exists.
