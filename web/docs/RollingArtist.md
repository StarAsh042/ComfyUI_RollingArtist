# Rolling Artist

Randomly draws artists from an artist CSV, assigns each of them a random weight, and outputs a weighted prompt plus a structured JSON result.
The node runs even when its output is not connected to anything downstream (output node).

- **Output 1 `prompt`**: the prompt, e.g. `(artist:xxx:0.8),(artist:yyy:0.6)`
- **Output 2 `artists_json`**: `{"artists": [{"name", "weight", "top"}], "status"}`, `status` is `OK` or `ALL_COMBINATIONS_TESTED`

## Sampling and weights

| Parameter | Type / Range | Description |
|---|---|---|
| `artist_count` | INT 1-10 | Number of artists per run. Set to 1 with an empty Force Include to enter **Exact mode** |
| `artist_top_ratio` | FLOAT 0.01-1.0 | Top pool ratio: the first `ceil(total × ratio)` rows of the CSV (at least 1); earlier rows usually have more works |
| `artist_top_count` | INT 1-10 | Minimum Top-pool artists drawn per run (only when Top Priority is on and the Top pool is not empty) |
| `weight_min` | FLOAT 0.1-2.0 | Lower bound of a single weight; swapped automatically if above the upper bound, negatives clamp to 0 |
| `weight_max` | FLOAT 0.1-2.0 | Upper bound of a single weight |
| `weight_total` | FLOAT 0-20 | Sum of all weights, distributed exactly; clamped to the feasible range (not used in Exact mode) |
| `artists_prefix` | BOOLEAN | When on, outputs `artist:name`; when off, outputs the plain name |
| `seed` | INT | Random seed; the same seed with the same settings is reproducible |

## Sampling preferences, lists and dedup

| Parameter | Type / Range | Description |
|---|---|---|
| `use_top_priority` | BOOLEAN | Draw from the Top pool first; when off, sample uniformly from the whole CSV |
| `sort_by_weight` | BOOLEAN | Reorders artists in the prompt by weight (affects the `full_prompt` dedup key) |
| `exclude_artists` | STRING | Names to exclude, comma separated |
| `force_include` | STRING | Names that must appear, comma separated; takes priority over the exclude list |
| `dedup_mode` | ENUM | Dedup key: `none` / `full_prompt` / `artist_set` / `artist_list` |
| `max_attempts` | INT 1-100 | Maximum retries in dedup mode; if every retry hits a tested combination the last result is accepted with a WARNING |

## Advanced parameters (collapsed by default)

| Parameter | Type | Description |
|---|---|---|
| `csv_column` | STRING | Column selection: `auto` (detect header, artist/character/name/tag preferred), `all` (flatten every column), an index or a column name |
| `custom_csv_path` | STRING | Path to a custom artist CSV; empty uses `danbooru_art_001.csv` in the node directory |
| `tested_csv_path` | STRING | Path of the tested-combinations file; empty uses `tested_combinations.csv` in the node directory. Changing it resets dedup progress |

## Two modes

- **Normal mode**: draws and weights artists randomly, retrying when a combination has already been tested (up to `max_attempts`).
- **Exact mode** (`artist_count=1` with an empty Force Include): enumerates `(artist, weight)` combinations without repetition,
  persisting the pool in `*_remaining.csv`; once exhausted it returns `ALL_COMBINATIONS_TESTED`.
  In this mode `dedup_mode` and `weight_total` have **no effect** (dedup is always `full_prompt`, weights are enumerated on a 0.1 grid).

## Record files

- The default record files live in the node directory: `tested_combinations.csv` and `tested_combinations_remaining.csv`.
- **Both are deleted automatically on every ComfyUI startup** (by `prestartup_script.py`) to keep each run clean.
  To carry dedup progress across sessions, point `tested_csv_path` at your own file (that path is never cleaned).
- Read-tested → generate → record runs inside one process-wide shared lock per `tested_csv_path`,
  so multiple instances or concurrent threads never produce duplicate combinations.
