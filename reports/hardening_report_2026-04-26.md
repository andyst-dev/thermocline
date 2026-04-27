# Weather Edge hardening report

Date: 2026-04-26 UTC

## Objectif

Rendre le paper bot conservateur, vérifiable et impossible à confondre avec une edge tant que les preuves ne sont pas solides.

## Changements appliqués

### 1. Weather.com/Wunderground

- Correction du suffix pays Weather.com: plus de `:9:US` hardcodé.
- Extraction du pays depuis l’URL Wunderground.
- Utilisation de `max_temp` / `min_temp` quand disponible.
- Correction du comptage d’observations: on compte maintenant les lignes d’observation contenant au moins une température, pas le nombre de champs lus.

### 2. Timezones

- Ajout de `src/weather_edge/timezones.py`.
- Centralisation du mapping ICAO -> timezone.
- Settlement et reconciliation utilisent la même logique.
- Ajouts notables: `LT*`, `OP*`, `ZG/ZH/ZS/ZU`, `OE*`, Europe, US/Canada, AmSud.

### 3. PASS gates durcis

Résultat actuel: **0 PASS**.

Nouveaux garde-fous:

- buckets exacts/étroits `<= 1.01°C` bloqués avant calibration;
- observations same-day/provisionnelles => `PAPER`, pas `PASS`;
- source officielle Wunderground/weather.com absente => `REJECT`;
- paper opening suspendu par cron avec `WEATHER_EDGE_DISABLE_PAPER_OPEN=1`.

### 4. Audit trail CLOB

`verify-candidates` écrit maintenant un snapshot brut du carnet CLOB pour chaque candidat affiché avec token CLOB.

Objectif: pouvoir auditer un prix/fill même sans ouvrir de nouvelle position papier.

### 5. Reconciliation sources

Nouvelle commande:

```bash
PYTHONPATH=src python3 -m weather_edge.main reconcile-sources
```

Sorties:

- `reports/source_reconciliation.json`
- `reports/source_reconciliation.md`

Résumé actuel:

- rows: 42
- gamma_closed: 18
- official_source_missing: 0
- official/METAR diff > 1°C: 3
- legacy no simulated fill: 11

### 6. Accounting PnL

`paper-report` distingue maintenant:

- PnL historique total;
- PnL des trades clos avec fill simulé;
- trades legacy sans fill simulé.

Résumé actuel:

- trades: 42
- closed: 18
- open: 24
- duplicates excluded: 3
- closed with simulated fill: 12
- closed without simulated fill: 6
- closed PnL historique: -18$
- closed PnL avec fill simulé: -12$

## Test final exécuté

```bash
PYTHONPATH=src WEATHER_EDGE_MARKET_SCAN_PAGES=76 WEATHER_EDGE_REPORT_LIMIT=20 WEATHER_EDGE_DISABLE_PAPER_OPEN=1 python3 -m weather_edge.main paper-cycle
PYTHONPATH=src python3 -m weather_edge.main reconcile-sources
python3 -m compileall -q src
```

Résultat:

- verified: 0 PASS, 19 PAPER, 516 REJECT, 103 skipped
- paper opening: désactivé
- settlement: 0 nouveau settlement, 24 pending
- reconciliation: OK
- top candidate snapshot CLOB: présent

## Blockers restants avant live

1. Recalibrer mathématiquement les probabilités des buckets exacts/étroits.
2. Construire une vraie stats table Gamma-final uniquement: winrate, PnL, drawdown, source diffs.
3. Écrire tests unitaires ciblés pour parsing, Weather.com country, observation count, timezone, gates PASS/PAPER/REJECT.
4. Décider si les positions papier legacy sans fill doivent être exclues des rapports live-readiness.
5. Continuer settlement passif des 24 positions ouvertes sans ouvrir de nouvelles positions.

## Verdict

Weather Edge est maintenant beaucoup plus sûr en mode paper/research.

Mais il n’est pas prêt pour live trading.

Le système doit rester en observation jusqu’à calibration + réconciliation Gamma sur un échantillon beaucoup plus grand.
