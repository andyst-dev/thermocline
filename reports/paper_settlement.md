# Paper settlement

Command:

```bash
PYTHONPATH=src python3 -m weather_edge.main paper-settle
```

Current settlement behavior:
- Uses METAR observations for ICAO-locked station markets.
- Closes paper trades only when the observed value makes the selected side irreversible, or the local day is complete.
- Keeps exact-bucket same-day markets pending until day completion.
- This is paper accounting only, not live trading.

Latest output: `reports/paper_settlement.json`.
