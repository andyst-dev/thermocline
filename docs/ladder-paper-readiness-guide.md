# Weather Edge — Guide avant ladders paper

Statut: guide opérationnel read-only avant toute ouverture de ladders paper.

Principe: les ladders exact-range peuvent être analysés, scorés et reportés, mais ne doivent pas être ouverts en paper tant que toute la checklist ci-dessous n'est pas verte et qu'Andy n'a pas donné son accord explicite.

---

## 0. Invariants de sécurité

À conserver tant que le protocole n'est pas validé:

```bash
WEATHER_EDGE_DISABLE_PAPER_OPEN=1
```

Interdits avant validation:

- pas de live trading;
- pas d'activation cron qui ouvre des trades;
- pas de branchement de `paper-open` sur `ladders`;
- pas de sizing dollar automatique pour ladders;
- pas de contournement des caps par event;
- pas de commit/push GitHub sans autorisation explicite d'Andy.

Les reports ladder doivent rester read-only:

- `reports/verified_candidates.json` peut contenir `ladders`;
- `paper-open` ne doit pas consommer ces ladders;
- le report doit exposer explicitement `ladder_exact_range_read_only=true` et `ladder_excludes_paper_open=true`.

---

## 1. Ce qui existe déjà

### Event exposure caps

Avant toute logique ladder paper, Weather Edge a maintenant des caps par event:

- nombre max de legs par event;
- exposition USD max par event;
- option de nombre max d'events ouverts;
- synthetic open rows intra-run pour éviter de double-compter une ouverture décidée dans la même boucle.

Env vars:

```bash
WEATHER_EDGE_EVENT_MAX_LEGS=2
WEATHER_EDGE_EVENT_MAX_USD=5.0
WEATHER_EDGE_EVENT_MAX_OPEN_EVENTS=
```

### Ladder read-only

Le module `src/weather_edge/ladder.py` construit des ladders exact-range uniquement:

- side `Yes` uniquement;
- buckets exact/narrow `<= 1.01°C`;
- buckets adjacents par bornes continues, pas seulement par centre;
- coût total `< 1.0`;
- EV/ROI positifs selon seuils;
- forecast dans la fenêtre du ladder;
- pas de `recommended_size_usd`.

### Étape 4 — probabilité CDF union

Le scoring ladder ne dépend plus seulement de `sum(model_prob)`.

Règle actuelle:

- `model_prob_sum` reste visible en diagnostic;
- `prob_hit` utilise `bucket_probability(lower_union, upper_union, forecast, sigma)` quand sigma est valide;
- fallback `sum_leg_model_probs_fallback` uniquement si sigma manque/invalide;
- le report expose `prob_method` et `prob_source_sigma_c`.

Pourquoi c'est important: additionner les probabilités de legs peut dépasser 1.0 ou surestimer l'EV. Avant paper, la probabilité doit représenter la probabilité de toucher l'union du range.

---

## 2. Ce qu'il faut encore faire AVANT ladders paper

### A. Qualité calibration / données

À vérifier:

- calibration gate passe sur données propres;
- sigma post-fix stable, pas basé sur legacy bug;
- closed trades audit compris, surtout mismatch restant;
- settlement source fiable par city/source;
- timeout/source externe NASA GISTEMP maîtrisé pour scans reproductibles.

Critères minimum:

- audit read-only sans erreur;
- sample post-fix suffisant;
- pas de source/city qui pollue systématiquement les observations;
- calibration non concentrée sur quelques trades legacy.

### B. Backtest ladder

Créer un vrai backtest ladder avant paper:

- single best bucket;
- ladder ±1°C;
- ladder ±2°C;
- scale-in simulé;
- sizing réduit par horizon/régime;
- fills simulés par leg.

Métriques minimales:

- trade count;
- hit rate;
- average cost;
- payout moyen;
- PnL;
- ROI;
- max drawdown;
- Brier/calibration;
- performance par city;
- performance par horizon;
- performance hors legacy trades.

No-go si:

- PnL négatif après fills simulés;
- edge concentré dans un seul lucky win;
- drawdown excessif;
- sample trop faible;
- settlement source douteuse.

### C. Fill simulation par leg

Avant paper, chaque ladder doit simuler son coût réel:

- refresh order book de chaque leg;
- fill moyen par leg à taille réelle;
- coût total ladder après slippage;
- capacité suffisante;
- snapshot book/hash conservé;
- refus si un leg ne fill pas proprement.

À ne pas faire: prendre `best_ask` statique comme coût final d'un ladder paper.

### D. Sizing dollar ladder

Il faut une politique explicite, séparée du scoring:

- taille max par ladder;
- taille max par leg;
- taille max par event;
- horizon scale factor;
- regime scale factor;
- éventuel Kelly ladder basé sur `prob_hit` et `total_cost`, mais plafonné très bas;
- aucune taille recommandée si calibration gate KO.

Départ conseillé si Andy approuve paper:

```text
$1 max par ladder ou $1 max par leg
max open ladders très bas
plusieurs jours minimum d'observation
```

### E. Forecast stability gate

Pour scale-in / ajout d'exposition sur un event existant:

```text
if abs(current_forecast_c - initial_forecast_c) > 1.0:
    block add
elif drift in [0.5, 1.0]:
    caution / reduce size
else:
    stable
```

À stocker au minimum:

- `initial_forecast_c`;
- `latest_forecast_c`;
- `forecast_drift_c`;
- `opened_horizon_hours`;
- event key / ladder id.

### F. Stockage et traçabilité ladder

Avant d'ouvrir un ladder paper, chaque leg doit être liée à un même ladder:

- `strategy = ladder_exact_range`;
- `ladder_id` stable;
- event key stable;
- per-leg cost/shares;
- total ladder cost;
- `prob_hit`, `model_prob_sum`, `prob_method`, `prob_source_sigma_c`;
- forecast initial et horizon initial;
- report d'ouverture unique pour le ladder.

À ne pas faire: insérer chaque leg comme trade indépendant sans lien ladder/event.

### G. Tests obligatoires

Avant paper ladder:

```bash
cd /home/builder/weather-edge
PYTHONPATH=src pytest tests/test_ladder.py tests/test_event_exposure.py tests/test_risk_sizing.py tests/test_candidates.py -q
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m weather_edge.main audit-closed-trades --include-observations --output reports/closed_trades_audit_with_observations.json
```

Tests à ajouter avant branchement paper:

- ladder rejected si un leg ne fill pas;
- ladder total cost utilise fills réels;
- ladder cap event appliqué au ladder global;
- drift forecast bloque ajout;
- `paper-open` n'ouvre pas de ladder si flag disable actif;
- `paper-open` stocke `ladder_id` commun;
- settlement/PnL ladder agrège les legs correctement.

---

## 3. Protocole recommandé

### Stage A — Observation only

Garder `WEATHER_EDGE_DISABLE_PAPER_OPEN=1`.

Objectif:

- générer les reports ladder régulièrement;
- vérifier que les ladders proposés sont cohérents;
- surveiller prob_method/fallback;
- détecter duplicates/overexposure;
- accumuler données post-fix.

Succès:

- zéro ouverture paper inattendue;
- reports sans crash pendant plusieurs jours;
- peu ou pas de fallback `sum_leg_model_probs_fallback`;
- candidates alignées avec forecast et CDF.

### Stage B — Paper ladder micro-size

Seulement après accord explicite Andy.

Paramètres initiaux recommandés:

```text
paper size: $1 max par ladder ou par leg
max open ladders: 1-2
max legs/event: 2 au début
max USD/event: très bas
no live
```

Succès:

- settlements propres;
- PnL positif hors legacy;
- calibration stable;
- pas de source/city bizarre;
- aucune surconcentration event.

### Stage C — Live micro-size

Seulement après nouvelle approbation explicite Andy.

Départ:

```text
$1-$5 max total live exposure
review manuel de chaque candidate
pas d'automatisation live complète
```

---

## 4. Checklist go/no-go avant paper ladder

- [ ] `WEATHER_EDGE_DISABLE_PAPER_OPEN=1` vérifié pendant observation.
- [ ] Full suite tests verte.
- [ ] Audit closed trades read-only vert.
- [ ] Ladder report généré sans ouvrir de trade.
- [ ] `prob_hit` CDF union utilisé; fallback rare et surveillé.
- [ ] Calibration gate passe sur données propres.
- [ ] Backtest ladder positif après fills simulés.
- [ ] Fill simulation par leg implémentée/testée.
- [ ] Sizing ladder micro-size implémenté/testé.
- [ ] Event caps appliqués au ladder global.
- [ ] Forecast stability gate implémentée/testée.
- [ ] `ladder_id` / event key stockés pour toutes les legs.
- [ ] Settlement/PnL ladder agrégé correctement.
- [ ] Cron stable et non ouvrant.
- [ ] Andy approuve explicitement Stage B.

Si une case est rouge: pas de ladders paper.
