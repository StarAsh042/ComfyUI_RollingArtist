# Rolling Artist

Draws artists at random from an artist CSV, assigns random weights, and outputs a weighted prompt plus a
structured JSON result. The node is an output node, so it runs even when nothing consumes its outputs.

- **Output 1 `prompt`**: the prompt, e.g. `(artist:xxx:0.8),(artist:yyy:0.6)`
- **Output 2 `artists_json`**: `{"artists": [{"name", "weight", "top"}], "status"}`; `status` is `OK` or `ALL_COMBINATIONS_TESTED`
- **Output 3 `tested_count`**: number of combinations recorded in the current database
- **Output 4 `remaining_count`**: combinations left in the pool in Exact mode; always `0` outside Exact mode

## Data source and column

Reads `danbooru_art_001.csv` in the node directory by default (header `artist,trigger,count`) and
**always reads the `trigger` column**: it is already escaped and prompt-ready
(e.g. `hammer_\(sunset_beach\)`). If the CSV has no `trigger` column it falls back to the first
column and logs a WARNING (which may then be an unescaped raw tag).

## Sampling and weights

| Parameter | Type / range | Description |
|---|---|---|
| `artist_count` | INT 1-10 | Artists generated per run. With `Mode = auto`, setting it to 1 while Force Include is empty enters **Exact mode** |
| `artist_top_ratio` | FLOAT 0.01-1.0 | Top pool ratio: the first `ceil(total × ratio)` rows of the CSV (at least 1); earlier rows usually have more works |
| `artist_top_count` | INT 1-10 | Minimum number of Top-pool artists per run (only when Top Priority is on and the Top pool is not empty) |
| `weight_min` | FLOAT 0.1-2.0 | Lower bound of a single artist weight; swapped automatically if above the upper bound, negatives clamped to 0 |
| `weight_max` | FLOAT 0.1-2.0 | Upper bound of a single artist weight |
| `weight_total` | FLOAT 0-20 | Sum of all weights, distributed exactly; clamped to the feasible range (not used in Exact mode) |
| `weight_curve` | ENUM | Distribution curve: `flat` (default) / `dominant` (one artist clearly dominates) / `ramp` (descending ladder). Affects only who gets more weight, not the bounds or the total |
| `artists_prefix` | BOOLEAN | On: outputs `artist:name`; off: outputs the plain name |
| `seed` | INT | Random seed; the same seed with the same settings is reproducible |

## Generation mode

| `mode` | Exhausts? | Condition |
|---|---|---|
| `auto` (default) | Yes when `artist_count = 1` and Force Include is empty | Same as 3.2.x |
| `random` | Never | Use this to draw a single artist without exhausting |
| `exact` | Always | Requires `artist_count = 1` and an empty Force Include, otherwise the node **errors out** |

## Sampling preference, lists and dedup

| Parameter | Type / range | Description |
|---|---|---|
| `use_top_priority` | BOOLEAN | Draw from the Top pool first; off samples uniformly from the whole CSV |
| `sort_by_weight` | BOOLEAN | Reorder artists in the prompt by weight (descending); affects the `full_prompt` dedup key |
| `exclude_artists` | STRING | Exclude list, comma separated |
| `force_include` | STRING | Artists that must appear, comma separated; takes priority over the exclude list |
| `dedup_mode` | ENUM | Dedup key: `none` / `full_prompt` / `artist_set` / `artist_list` |
| `max_attempts` | INT 1-100 | Maximum retries in dedup mode; if every retry hits an already-tested combination the last result is accepted with a WARNING |

## Advanced parameters (collapsed by default)

| Parameter | Type | Description |
|---|---|---|
| `custom_csv_path` | STRING | Custom artist CSV path; leave empty for `danbooru_art_001.csv` in the node directory |
| `tested_db_path` | STRING | SQLite records database path; leave empty for `rollingartist.sqlite` in the node directory. Tested records and the Exact-mode pool both live here, so changing it resets dedup progress |

## The two working modes

- **Normal mode**: samples and assigns weights as configured, retrying (up to `max_attempts`) when it hits an
  already-tested combination.
- **Exact mode** (`artist_count = 1` with an empty Force Include, or `Mode = exact`): enumerates `(artist, weight)`
  combinations without repetition; the pool is persisted in the database. It returns `ALL_COMBINATIONS_TESTED` once
  every combination has been used. `dedup_mode` and `weight_total` do **not** apply there (dedup is always
  `full_prompt`, weights are enumerated on a 0.1 grid). When the universe exceeds 1,000,000 combinations no pool is
  built and the node falls back to random sampling with dedup (`remaining_count` is then always 0).

## Records database (SQLite)

- The default database lives in the node directory: `rollingartist.sqlite` (plus `-wal` / `-shm` sidecars).
- **The default database is cleared on every ComfyUI startup** (by `prestartup_script.py`) so each session starts
  clean. To keep dedup / exhaustion progress across sessions, point `tested_db_path` at your own file
  (explicit paths are never touched).
- "Check → generate → record" runs inside a single database transaction, so multiple threads, **multiple ComfyUI
  instances or processes** never produce duplicate combinations.
- Dedup uses an index lookup, so a large history costs no memory; artist names containing `|`, `:` or commas are
  stored safely.
- There is no limit on the number of records and no automatic pruning; the default database is wiped on startup.
- `custom_csv_path` and `tested_db_path` are used verbatim: fine on a single-user machine, but if ComfyUI is exposed
  to a LAN or the internet, anyone could read or write arbitrary files through them — add access control if so.
