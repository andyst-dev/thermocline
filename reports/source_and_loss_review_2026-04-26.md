# Weather Edge — source officielle & pertes

Date: 2026-04-26 UTC

## Résumé

Le paper bot ne doit pas passer en live. Les pertes viennent surtout d’un modèle trop confiant sur des buckets exacts et de trades ouverts sur des prix poussière, plus quelques artefacts d’accounting historiques.

## Corrections appliquées

### Source officielle Weather.com/Wunderground

Bug trouvé: `clients/weathercom.py` appelait toujours:

```text
/LOCATION/<ICAO>:9:US/observations/historical.json
```

Cela marche pour les stations US, mais renvoie HTTP 400 pour les stations non-US (`ZGSZ`, `OPKC`, `EGLC`, etc.).

Correction:

- extraction du pays depuis l’URL Wunderground (`/history/daily/<country>/.../<ICAO>`);
- appel Weather.com avec `<ICAO>:9:<COUNTRY>`;
- exemple: `OPKC:9:PK`, `ZGSZ:9:CN`, `EGLC:9:GB`;
- utilisation de `max_temp` / `min_temp` quand présent, pas seulement `temp`, pour éviter de manquer les extrêmes journaliers.

Smoke tests réussis:

- KLAX US: OK
- ZGGG CN: OK
- OPKC PK: OK
- EGLC GB: OK
- RKSI KR lowest: OK

Après correction, `paper-settle` ne retourne plus `official_unavailable` pour les 24 positions ouvertes: elles sont toutes `official_wunderground_provisional`.

### Paper cycle sécurisé

Le cron système continue settlement/reporting, mais n’ouvre plus de nouvelles positions pendant l’enquête:

```bash
WEATHER_EDGE_DISABLE_PAPER_OPEN=1
```

### Accounting PnL

`paper-report` distingue maintenant:

- `closed_pnl_usd`: historique total, inclut legacy trades;
- `closed_pnl_with_simulated_fill_usd`: PnL plus propre, uniquement trades clos avec fill simulé;
- `closed_without_simulated_fill`: trades historiques à ne pas surinterpréter.

État après correction:

- trades: 42
- closed: 18
- open: 24
- duplicates excluded: 3
- closed with simulated fill: 12
- closed without simulated fill: 6
- closed PnL historique: -18$
- closed PnL avec fill simulé: -12$

## Pourquoi ça perd

### 1. Probabilités trop confiantes

Le modèle donne souvent 90–95% à des buckets exacts ou très étroits parce que l’observation/prévision tombe près du centre du bucket. C’est trop agressif.

Un bucket exact météo n’est pas “presque certain” simplement parce que la valeur courante/projetée est proche. Le max/min peut encore évoluer, la station/source peut différer, l’arrondi peut changer, et les marchés exacts sont fragiles.

### 2. Ouverture sur marchés trop tardifs / quasi-résolus

Beaucoup de positions sont ouvertes quand la journée est déjà très avancée ou quand l’observation provisoire donne une impression de certitude. C’est utile pour paper-test, mais dangereux si le marché ferme/résout contre nous ou si la source officielle diffère.

### 3. Source officielle vs METAR

Même après correction Weather.com, on observe encore des divergences entre Wunderground/weather.com et METAR sur plusieurs stations. Le bot doit traiter ça comme un risque majeur, pas comme une edge.

### 4. Legacy fills

Une partie des pertes fermées vient de trades historiques sans `fill_avg_price`/`fill_cost_usd`. Elles restent en historique, mais ne doivent pas être utilisées comme stats propres.

## Décision

- Pas de live trading.
- Paper opening suspendu temporairement.
- Continuer settlement/report pour apprendre des positions ouvertes.

## Prochaines priorités

1. Recalibrer les probabilités des buckets exacts/étroits.
2. Ajouter un filtre “no open if target day is already active unless condition is truly irreversible and source official agrees”.
3. Ajouter un flag source authority dans les candidats: Weather.com/Wunderground vs METAR vs forecast.
4. Exclure les legacy no-fill des stats de décision live.
5. Construire un rapport de reconciliation Gamma vs Wunderground vs METAR sur au moins 50 marchés clos.
