# Paper cycle

Command:

```bash
PYTHONPATH=src WEATHER_EDGE_MARKET_SCAN_PAGES=76 WEATHER_EDGE_REPORT_LIMIT=10 python3 -m weather_edge.main paper-cycle
```

Cycle steps:
1. refresh verified candidates
2. open up to 5 PASS-only paper trades at $1 each
3. settle irreversible paper trades from METAR observations
4. write paper report

Cron is configured outside the project to run this every 30 minutes.
