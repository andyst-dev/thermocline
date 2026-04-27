# GUIDE — Weather Edge

Guide de reprise pour Laurel/agents.

## But

Scanner des inefficiences sur les weather markets Polymarket en mode local-first, vérifiable et prudent.

Ce projet n’est pas un bot miracle et ne doit pas trader en argent réel tant que le paper trading n’a pas prouvé une edge robuste.

## Chemin

```bash
/root/.openclaw/workspace/projects/weather-edge
```

## État actuel

- Discovery live Gamma Polymarket par pagination profonde.
- Parsing marchés météo température high/low, °C/°F, seuils/ranges.
- Prix CLOB best bid/ask et capacité ask pour fills papier.
- Vérification source/station Weather.com/Wunderground prioritaire, METAR fallback/référence.
- Paper trading en mode observation: nouvelles ouvertures désactivées par `WEATHER_EDGE_DISABLE_PAPER_OPEN=1`.
- PASS gates durcis: 0 PASS au 2026-04-26; same-day/provisional => PAPER; buckets exacts/étroits bloqués avant calibration.
- Cycle système toutes les 30 min via cron + `scripts/paper_cycle.sh`.
- Revue OpenClaw toutes les 12h seulement pour changements matériels.

## Commandes utiles

Depuis le projet:

```bash
cd /root/.openclaw/workspace/projects/weather-edge
PYTHONPATH=src python3 -m weather_edge.main init-db
PYTHONPATH=src WEATHER_EDGE_MARKET_SCAN_PAGES=76 WEATHER_EDGE_REPORT_LIMIT=10 python3 -m weather_edge.main paper-cycle
PYTHONPATH=src python3 -m weather_edge.main paper-report
PYTHONPATH=src python3 -m weather_edge.main verify-candidates
PYTHONPATH=src python3 -m weather_edge.main reconcile-sources
```

Script cron:

```bash
/root/.openclaw/workspace/projects/weather-edge/scripts/paper_cycle.sh
```

## Fichiers à inspecter

- `reports/paper_trades.json`
- `reports/paper_settlement.json`
- `reports/verified_candidates.json`
- `reports/paper_cycle_heartbeat.json`
- `logs/paper_cycle.log`
- `reports/claude_opus_review_2026-04-25.md`
- `reports/source_and_loss_review_2026-04-26.md`
- `reports/hardening_report_2026-04-26.md`
- `reports/source_reconciliation.json`
- `reports/source_reconciliation.md`

## Règles de notification Andy

Notifier seulement si:

- settlement nouveau;
- PnL/stat propre change matériellement;
- erreur répétée ou cycle bloqué;
- risque de résolution/source/fill;
- assez de données pour décider d’un micro-live;
- besoin d’une décision Andy.

Sinon: `NO_REPLY`.

## Garde-fous live-money

Pas de live tant que:

- moins de 20–50 trades clos propres;
- stats non nettoyées des duplicats/fills irréalistes;
- source officielle vs METAR pas claire;
- profondeur carnet non réaliste;
- audit trail insuffisant;
- pas de wallet dédié ni kill switch.

Si live un jour:

- wallet dédié;
- capital initial 50–100 CHF max;
- 1–2$ par trade;
- exposition totale 10–20$ max;
- PASS-only;
- validation manuelle d’abord;
- pas d’auto-trade au début.

## Pièges connus

- Les fills papier peuvent être trop optimistes si on prend seulement le best ask.
- METAR peut ne pas être la source officielle de résolution.
- Les probabilités extrêmes doivent être bornées/guardrailées.
- Les duplicats gonflent artificiellement les stats: vérifier `market_id + side`.
- Certains marchés sont profonds dans la pagination Gamma.

## Prochaines améliorations prioritaires

1. Walk-the-book complet pour taille réaliste.
2. Audit trail enrichi par trade.
3. Source officielle de résolution mieux verrouillée.
4. Stats propres par station/type/horizon.
5. Corrélation/exposition par ville/date/station.
