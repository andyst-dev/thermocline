# NEXT

## Projet
Weather Edge V1

## Etat actuel
- squelette projet créé
- SQLite locale OK
- pipeline V1 OK
- Open-Meteo OK
- Polymarket inaccessible depuis la Suisse ici, géorestriction probable
- fallback fixtures en place pour continuer le développement sans bloquer l’architecture

## Ce que fait déjà la V1
- récupération des marchés météo via provider Polymarket avec fallback local
- parsing ville + date + buckets
- géocodage de la ville
- récupération du forecast Open-Meteo
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
1. rendre le market provider proprement interchangeable
2. brancher une ingestion marché live depuis le VPS irlandais
3. historiser snapshots marchés + forecasts
4. préparer le backtest baseline

## Notes importantes
- garder une architecture provider-agnostic
- ne pas lancer de trading live avant historique + backtest + paper trading
- le moteur doit rester local-first, sobre et vérifiable
