# scripts/

Maintenance helpers that are **not** part of the shipped `haid` package.

- **`build_metric_baselines.py`** — regenerates `src/haid/data/metric_baselines.json`
  (the per-scope baselines `haid metrics` places rates against) from a corpus of real
  sessions. Run it when the baseline corpus or a metric rule changes; commit the refreshed
  JSON. Stdlib-only, like the rest of the repo.

The earlier `viz_extract.py` / `viz_assemble.py` prototype feeders moved to the
`archive/experiments` branch along with the `viz/` wireframes.
