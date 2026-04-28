# Paper trading

Local paper trades are stored in SQLite table `paper_trades`.

Commands:

```bash
PYTHONPATH=src WEATHER_EDGE_MARKET_SCAN_PAGES=76 WEATHER_EDGE_REPORT_LIMIT=10 python3 -m weather_edge.main paper-open --limit 5 --size-usd 1.0
PYTHONPATH=src python3 -m weather_edge.main paper-report
```

Current behavior:
- opens simulated positions from `PASS` candidates only by default
- uses CLOB best ask as simulated entry
- records model probability, executable EV, score, source, station, and theoretical shares
- does not connect to wallet and does not place real orders

Next missing piece:
- settlement/marking command to close paper trades after official resolution or observed station data confirms the bucket.
