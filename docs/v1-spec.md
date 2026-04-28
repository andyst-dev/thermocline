# Weather Edge V1 - spec

## But
Construire un scanner local qui compare:
- les probabilités implicites des weather markets
- une distribution de température dérivée d'un forecast météo

## Entrées V1
### Marché
- source: Gamma API Polymarket
- champs utiles:
  - id
  - slug
  - question
  - endDate / startDate / active / closed
  - outcomes
  - outcomePrices
  - liquidity / volume

### Forecast
- source: Open-Meteo forecast + geocoding
- champs utiles:
  - latitude / longitude
  - timezone
  - hourly temperature_2m
  - current generation timestamp

## Hypothèses V1
- on cible surtout les marchés du style `Highest temperature in CITY on DATE?`
- on approxime la distribution du maximum journalier avec une loi normale autour du forecast de température max
- sigma est heuristique selon l'horizon jusqu'à la résolution

## Sorties V1
Pour chaque bucket:
- probabilité implicite marché
- probabilité modèle
- edge
- EV brut
- score simple
- flags de confiance

## Hors scope V1
- exécution d'ordres
- sizing live
- carnet CLOB détaillé systématique
- multi-source météo avancée
- backtest propre sans dataset historique suffisant
