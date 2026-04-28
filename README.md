# Weather Edge 🌡️

Bot de trading quantitatif pour les marchés météo de [Polymarket](https://polymarket.com).

> **Statut** : Phase d'observation & calibration — paper trading suspendu.
>
> **Dernier fix critique** : 28 avril 2026 — correction du bug de lecture METAR partielle (voir section "Sécurité").

---

## Philosophie

- **Local-first** : tout tourne sur la machine, rien ne fuite vers des SaaS opaques.
- **Observation avant prédiction** : on accumulate des données propres avant de parier.
- **Kelly 1/4** : sizing conservateur, pas de YOLO.
- **Guard hard** : le paper trading est désactivé par défaut (`WEATHER_EDGE_DISABLE_PAPER_OPEN=1`).

---

## Architecture

```
Polymarket Gamma API  →  Parsing ville/date/bucket  →  Forecast Open-Meteo
                                                        ↓
CLOB Order Book    ←  Scoring / EV / Kelly        ←  Sigma calibration
  ↓
Paper Trading (suspendu)  →  SQLite  →  Reports JSON
```

### Sources de données

| Source | Rôle | Quand utilisée |
|---|---|---|
| **Open-Meteo** | Forecast horaire température | Toujours (jusqu'à fin de journée) |
| **METAR / AviationWeather** | Observation station aéroport | Uniquement quand `day_complete=True` |
| **Weather.com / Wunderground** | Source officielle de résolution Polymarket | Vérification cross-source |
| **Polymarket CLOB** | Prix exécutables, liquidité | Scoring EV et fill simulation |
| **GFS Ensemble** | Spread météo à long terme | Horizons > 36h |

---

## Stratégies

### 1. Forecast Edge
Modèle gaussien sur le bucket de température avec sigma adapté à l'horizon :
- `sigma_c ≈ 1.5°C` à court terme
- `sigma_c ≈ 4.0°C` à long terme (> 72h)

### 2. Near-Expiry Scalp
Quand la journée locale est terminée (`< 2h avant minuit`) et que METAR confirme l'issue :
- Boost de `model_prob` à **0.99**
- Capture de l'écart de prix avant résolution officielle Wunderground

### 3. Intra-Market Arbitrage
Détection automatique si `Yes + No < 0.99` sur un marché liquide (≥ 250$).

---

## Sécurité & Fixes critiques

### Bug racine corrigé le 28 avril 2026
Le bot traitait les **lectures METAR du matin** (température partielle) comme le maximum journalier final. Résultat : probabilités gonflées à 90-95% et pertes systématiques (-42$ en paper trading).

**Fixes appliqués** :
1. `_local_day_complete()` : n'utilise les observations que si la journée locale est dans ses 2 dernières heures.
2. `same-day/provisional observation` : passé de **CAUTION** (PAPER) à **REJECT**.
3. `_effective_sigma_observed()` : `0.63°C` (combinaison instrument + divergence + arrondi).

### Guards actifs
- `WEATHER_EDGE_DISABLE_PAPER_OPEN=1` : tout ouverture de position est bloquée.
- `bucket_width_c <= 1.01` : REJECT (buckets exacts non calibrés).
- `liquidity < 250` : REJECT.
- `executable_ev < 0.15` : REJECT.

---

## Installation

```bash
cd /home/builder/weather-edge
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Commandes

```bash
# Scan rapide (dry-run, 3 pages)
PYTHONPATH=src WEATHER_EDGE_DISABLE_PAPER_OPEN=1 \
  WEATHER_EDGE_MARKET_SCAN_PAGES=3 \
  python3 -m weather_edge.main paper-cycle

# Scan complet
PYTHONPATH=src WEATHER_EDGE_DISABLE_PAPER_OPEN=1 \
  WEATHER_EDGE_MARKET_SCAN_PAGES=76 \
  python3 -m weather_edge.main paper-cycle

# Vérifier l'intégrité des candidats
PYTHONPATH=src python3 -m weather_edge.main verify-candidates

# Check syntaxe
python3 -m compileall -q src/
```

## Cron

Le script `scripts/paper_cycle.sh` tourne toutes les 30 minutes sous l'utilisateur `builder` :

```bash
sudo cat /etc/cron.d/weather-edge
```

---

## Structure

```
.
├── src/weather_edge/          # Code source
│   ├── scanner.py              # Logique de scan & probabilités
│   ├── candidates.py           # Filtrage & scoring
│   ├── models.py               # Dataclasses
│   ├── clients/                # API externes
│   └── ...
├── scripts/paper_cycle.sh     # Entrypoint cron
├── data/weather_edge.db       # SQLite (non versionnée)
├── reports/                   # Snapshots JSON (non versionnés)
├── tests/                     # Tests unitaires
├── NEXT.md                    # Roadmap & notes techniques
└── GUIDE.md                   # Guide d'utilisation
```

---

## Métriques actuelles

- **Marchés scannés** : ~173 par cycle
- **Sigma efficace** : `0.63°C` (observed) / `1.5°C` → `4.0°C` (forecast)
- **Paper trades historiques** : 42 (legacy, -42$)
- **Trades legacy corrigés** : 31 avec fill simulé (-31$)

---

## Roadmap

Voir [`NEXT.md`](NEXT.md) pour le détail.

1. **Observation** : 24-48h de scans propres sans fuite METAR.
2. **Réconciliation** : comparer forecast vs observation finale vs résolution Gamma (50+ marchés).
3. **Recalibration sigma** : ajuster `SIGMA_STATION`, `SIGMA_DIVERGENCE`, `SIGMA_ROUNDING` sur données empiriques.
4. **Scaling temporel** : réduire le sizing selon l'horizon (> 48h → 30%, < 8h → 100%).
5. **Tests unitaires** : `_local_day_complete`, transitions sigma, gates REJECT.
6. **Réactivation paper trading** : critère = 20 marchés scannés sans `model_prob > 0.60` prématuré.

---

## Règles d'or avant live

1. **Pas de live** tant que le paper PnL cumulé sur 50+ trades n'est pas > 0.
2. **Pas de live** sur les buckets exacts (`<= 1.01°C`) tant que la calibration n'est pas validée.
3. **Pas de live** sans table stats Gamma-final : winrate, PnL, drawdown par ville/saison.
4. **Tout trade same-day** doit avoir `observed_authority` + `day_complete=True` + temps local vérifié.

---

*Built with patience. 🦞*
