# NEXT

## Projet
Weather Edge V1

## Etat actuel
- squelette projet créé
- SQLite locale OK
- pipeline V1 OK
- Open-Meteo OK
- Polymarket accessible depuis le VPS Hetzner Allemagne
- discovery live Gamma par pagination offset en place
- marchés live trouvés actuellement : anomalie température globale NASA GISTEMP avril 2026
- fallback fixtures disponible via `WEATHER_EDGE_USE_FIXTURES=1`

## Ce que fait déjà la V1
- récupération des marchés météo via provider Polymarket live
- parsing ville + date + buckets, plus marchés globaux GISTEMP
- géocodage de la ville
- récupération du forecast Open-Meteo ou baseline NASA GISTEMP
- projection probabiliste baseline sur les buckets
- calcul d’edge / EV
- stockage forecasts + scans en SQLite
- export d’un rapport JSON

## Commandes utiles
```bash
cd /home/laurel/openclaw/projects/weather-edge
PYTHONPATH=src python -m weather_edge.main init-db
PYTHONPATH=src python -m weather_edge.main fetch-markets
PYTHONPATH=src python -m weather_edge.main scan
PYTHONPATH=src python -m weather_edge.main run-once
```

## Prochaine étape recommandée
1. rendre le market provider proprement interchangeable par interface explicite
2. historiser snapshots marchés + forecasts / baselines
3. brancher prix CLOB/best bid-ask et spread
4. préparer le backtest baseline

## Notes importantes
- garder une architecture provider-agnostic
- ne pas lancer de trading live avant historique + backtest + paper trading
- le moteur doit rester local-first, sobre et vérifiable
