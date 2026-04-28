# NEXT — Weather Edge

## État actuel (2026-04-28)

### Ce qui a été fait
- **Pipeline complet** : scan Polymarket → forecast Open-Meteo → scoring → paper trading
- **Source officielle** : Weather.com/Wunderground corrigée (suffixe pays dynamique au lieu de `:9:US` hardcodé)
- **Timezone mapping** : `timezones.py` centralisé pour ICAO → timezone
- **PnL accounting** : distinction trades legacy (sans fill simulé) vs trades propres
- **Fix critique appliqué (2026-04-28)** : `_local_day_complete()` + blocage observation partielle
- **Fix coordonnées aéroport (2026-04-28)** : le forecast Open-Meteo utilise désormais les lat/lon de la station ICAO de résolution (via AviationWeather METAR API) au lieu du centre-ville. Impact potentiel : 1-3°C de correction sur les buckets étroits.

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

## Sources alternatives & fallback

### METAR parsing : python-metar
- Repo : `python-metar/python-metar` (295⭐) — `pip install metar`
- Alternative : `mivek/python-metar-taf-parser` (64⭐) — `pip install metar-taf-parser-mivek`
- **Statut** : pas intégré actuellement. Notre `aviationweather.py` consomme l'API AviationWeather.gov en JSON (temp brute), ce qui est plus direct que parser du texte METAR.
- **Quand l'utiliser** :
  - Si AviationWeather.gov devient instable ou rate-limité
  - Si on veut une source secondaire NOAA texte (`tgftp.nws.noaa.gov/data/observations/metar/stations/<ICAO>.TXT`)
  - Si on diversifie la stratégie vers vent/pression/précip (python-metar a un data model riche)
- **Action** : ne pas ajouter de dépendance tant que l'API JSON fonctionne. Documenté ici pour référence future.

---

## Notes sur les ressources externes

### Tweet Alter Ego (@alterego_eth) — 28 avril 2026
**URL** : https://x.com/alterego_eth/status/2048720881040699419

**Contenu** : Analyse du profit des weather markets sur Polymarket. L'auteur identifie que les marchés météo sont inefficients car les prix ne reflètent pas correctement les probabilités réelles.

**Utile pour nous** :
- ✅ Confirmation que notre niche (weather markets) est mathématiquement exploitable
- ✅ L'idée de l'**oracle lag** (décalage entre données METAR et résolution Wunderground) est exactement notre stratégie de scalping
- ✅ Validation externe que les modèles gaussiens simples peuvent battre le marché si bien calibrés

**Pas utile** :
- 🔴 Pas de code, pas de méthodologie détaillée — du storytelling de trader
- 🔴 Pas de gestion du risque (Kelly, sizing) mentionnée

### Thread AdiX (@adiix_official) — « I gave Claude the keys to my Polymarket account »
**URL** : https://x.com/adiix_official/status/2049055937730609626

**Contenu** : Guide viral pour construire un bot Polymarket avec Claude. 26,880$ de profit en 14 jours sur 120$ de capital. Liste de 13 GitHub repos.

**Utile pour nous** :
- ✅ Insistance sur le **kill switch** et le mode **read-only first** — aligné avec notre `DISABLE_PAPER_OPEN=1`
- ✅ Confirmation que l'API Polymarket (`py-clob-client`) est mature et permissionless
- ✅ Rappel utile : tester avec du petit capital avant de scaler

**Pas utile / dangereux** :
- 🔴 **Survivorship bias massif** — montre le run gagnant, pas les pertes
- 🔴 Approche « donne les clés à Claude » = LLM qui improvise. Nous on veut des règles fixes (EV, Kelly, gates)
- 🔴 Les « 13 GitHub » sont des templates généralistes (py-clob-client, poly-market-maker, agents) — **rien de spécifique météo**
- 🔴 Risque sécurité : beaucoup de repos « gratuits » Polymarket sont des hijacks qui volent les clés API (cf. article Step Security)

**Verdict** : le thread est du **marketing viral**, pas de la science. Notre approche (sigma calibré, EV mathématique, Kelly 1/4) est plus rigoureuse.

---

## Notes

- Architecture reste **local-first, provider-agnostic, vérifiable**
- Cron actif : `scripts/paper_cycle.sh` toutes les 30 min
- Heartbeat : `reports/paper_cycle_heartbeat.json`
