# NEXT — Weather Edge

## État actuel (2026-04-28)

### Ce qui a été fait
- **Pipeline complet** : scan Polymarket → forecast Open-Meteo → scoring → paper trading
- **Source officielle** : Weather.com/Wunderground corrigée (suffixe pays dynamique au lieu de `:9:US` hardcodé)
- **Timezone mapping** : `timezones.py` centralisé pour ICAO → timezone
- **PnL accounting** : distinction trades legacy (sans fill simulé) vs trades propres
- **Fix critique appliqué (2026-04-28)** : `_local_day_complete()` + blocage observation partielle

### Paper trading
- Trades fermés : **42**
- PnL total historique : **-$42**
- PnL avec fill simulé : **-$31** (31 trades)
- **Paper opening suspendu** : `WEATHER_EDGE_DISABLE_PAPER_OPEN=1`

---

## Bug racine corrigé le 28 avril

Le bot traitait les **lectures METAR du matin** comme le maximum journalier final.

**Exemple** : Trade #29 (Karachi, 00:04 UTC = 05:04 local). La station METAR retournait ~24°C (matinal). Le bot remplaçait le forecast par cette valeur, appliquait `sigma = 0.30°C`, et calculait `model_prob = 0.9044` que la journée se terminerait exactement à 28°C. L'après-midi a fait plus chaud → perte.

**Fixes appliqués** :
1. `_local_day_complete()` : n'utiliser les observations que si la journée locale est dans les 2 dernières heures avant minuit
2. `_effective_sigma_observed()` : docstring corrigée (`0.63°C` au lieu de `0.82°C`)
3. `candidates.py` : `same-day/provisional observation` passé de **caution** (PAPER) à **blocker** (REJECT)

---

## Étapes avant réactivation du paper trading

### 1. Observation 24–48h (en cours)
- Laisser le cron tourner avec `DISABLE_PAPER_OPEN=1`
- Vérifier que `calibration_snapshot.json` montre `sigma_c >= 1.5` sur tous les marchés same-day
- Confirmer qu'aucun marché ne génère `model_prob > 0.60` sur bucket étroit avant la fin de journée locale

### 2. Réconciliation Gamma (attendre fin avril)
- Collecter les résolutions officielles des marchés du 28 avril sur Polymarket
- Comparer : forecast NWP vs observation finale vs résolution Gamma
- Cibler : **50+ marchés clos** avec données propres (fill simulé + source officielle)

### 3. Recalibration sigma (après 50 marchés)
- Ajuster `SIGMA_STATION`, `SIGMA_DIVERGENCE`, `SIGMA_ROUNDING` sur données empiriques
- Introduire `sigma_city` si certains endroits (HK, Seoul au printemps) sont plus volatiles
- Remplacer le blocage dur `bucket_width_c <= 1.01` par un ratio `width / sigma_eff`

### 4. Scaling temporel (inspiré du tweet Alter Ego)
- Réduire la taille de position selon l'horizon :
  - `> 48h` → 30% du stake max
  - `24–48h` → 60%
  - `< 8h` → 100%
- Implémenter dans `compute_kelly_size()` ou comme post-multiplicateur

### 5. Tests unitaires
- `_local_day_complete()` pour toutes les timezones cibles (Asia/Karachi, Asia/Tokyo, Europe/Paris, America/New_York)
- `bucket_probability()` : vérifier la transition sigma 0.30 → 0.63 → 1.5
- `build_candidate()` : s'assurer que same-day + observed_authority = REJECT

### 6. Réactiver le paper trading
- Critère : au moins 20 marchés scannés sans générer de `model_prob > 0.60` prématuré
- Activer `WEATHER_EDGE_DISABLE_PAPER_OPEN=0` temporairement
- Surveiller les premiers trades pendant 48h

---

## Règles d'or avant live

1. **Pas de live** tant que le paper PnL cumulé sur 50+ trades n'est pas > 0
2. **Pas de live** sur les buckets exacts (`<= 1.01°C`) tant que la calibration n'est pas validée
3. **Pas de live** sans table stats Gamma-final : winrate, PnL, drawdown par ville/saison
4. **Tout trade same-day** doit avoir `observed_authority` + `_local_day_complete = True` + `horizon_hours >= 0` en temps local (pas UTC)

---

## Commandes utiles

```bash
# Scan rapide (dry-run, 3 pages)
cd /home/builder/weather-edge
PYTHONPATH=src WEATHER_EDGE_DISABLE_PAPER_OPEN=1 WEATHER_EDGE_MARKET_SCAN_PAGES=3 \
  python3 -m weather_edge.main paper-cycle

# Scan complet
PYTHONPATH=src WEATHER_EDGE_DISABLE_PAPER_OPEN=1 WEATHER_EDGE_MARKET_SCAN_PAGES=76 \
  python3 -m weather_edge.main paper-cycle

# Réconciliation sources
PYTHONPATH=src python3 -m weather_edge.main reconcile-sources

# Vérifier qu'aucun same-day ne fuit
PYTHONPATH=src python3 -m weather_edge.main verify-candidates

# Compile check
python3 -m compileall -q src/
```

---

## Notes

- Architecture reste **local-first, provider-agnostic, vérifiable**
- Cron actif : `scripts/paper_cycle.sh` toutes les 30 min
- Heartbeat : `reports/paper_cycle_heartbeat.json`
