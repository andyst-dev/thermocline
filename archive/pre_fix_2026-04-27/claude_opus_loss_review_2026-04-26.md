# Claude Opus review — Weather Edge losses/source

Date: 2026-04-26 UTC

## Verdict synthétique

- PnL fermé négatif surtout parce que le modèle est beaucoup trop confiant sur des buckets exacts/étroits.
- `model_prob ≈ 90–95%` sur des marchés à 0.1–0.4% implicite ne prouve pas une edge: c’est probablement une erreur de mapping prévision/observation → probabilité de bucket discret.
- Une partie du PnL historique est artefactuelle: des trades fermés sans `fill_avg_price`/`fill_cost_usd` sont comptés comme pertes.
- Le settlement/source n’était pas fiable avant correction: Weather.com était hardcodé en `:9:US`, cassant les stations non-US.

## Points techniques relevés

1. Weather.com non-US cassé:
   - Ancien endpoint: `{ICAO}:9:US` pour toutes les stations.
   - Cause des HTTP 400 sur `ZGSZ`, `OPKC`, `EGLC`, etc.

2. Wunderground/Weather.com vs METAR:
   - Fortes divergences observées sur plusieurs stations.
   - Ne pas assimiler METAR à source officielle de résolution.

3. Timezones:
   - Mapping timezone ICAO incomplet dans settlement.
   - Risque de mauvaise fenêtre journalière pour METAR.

4. Fills historiques:
   - Certains trades clos n’ont pas de fill simulé.
   - Ne pas utiliser ces trades comme stats propres.

## Priorités recommandées

1. Corriger Weather.com country suffix et vérifier les extrêmes officiels.
2. Recalibrer probas de buckets exacts/étroits.
3. Ne compter comme PnL propre que les trades avec fill simulé + settlement Gamma officiel.
4. Compléter timezone/source reconciliation.
5. Pas de live avant 50+ marchés clos réconciliés Gamma / Wunderground / METAR.
