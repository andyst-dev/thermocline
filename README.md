# weather-edge

Scanner V1 pour repérer des inefficiences sur les weather markets, en mode sérieux et local-first.

## V1
- récupération des marchés météo Polymarket (Gamma public API)
- parsing des buckets de température
- géocodage de la ville cible
- récupération des prévisions Open-Meteo
- projection d'une distribution simple sur les buckets
- calcul d'edge / EV / ranking
- stockage SQLite local

## Philosophie
Cette V1 n'est **pas** un bot miracle et n'exécute **aucun trade**.
Elle sert à:
- collecter des données propres
- tester une baseline probabiliste
- scorer les opportunités
- préparer l'historisation et le backtest

## Structure
- `src/weather_edge/` : code source
- `data/weather_edge.db` : base SQLite locale
- `reports/` : exports et snapshots d'analyse
- `docs/` : notes de spec

## Commandes
Depuis le dossier du projet:

```bash
PYTHONPATH=src python -m weather_edge.main init-db
PYTHONPATH=src python -m weather_edge.main fetch-markets
PYTHONPATH=src python -m weather_edge.main scan
PYTHONPATH=src python -m weather_edge.main run-once
```

## Limites actuelles
- parsing de marché encore heuristique
- modèle probabiliste baseline seulement
- pas encore de snapshots CLOB détaillés ni de backtest complet
- matching ville/date à améliorer sur les formats exotiques

## Prochaine phase
- historisation régulière des marchés et forecasts
- calibration de sigma par horizon / ville
- enrichissement liquidité / spread / carnet
- backtest et paper trading
