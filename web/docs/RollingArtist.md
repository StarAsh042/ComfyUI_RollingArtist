# Rolling Artist

Draws artists at random from an artist CSV, assigns random weights, and outputs a weighted prompt plus a
structured JSON result. The node is an output node, so it runs even when nothing consumes its outputs.

- **Output 1 `prompt`**: the prompt, e.g. `(artist:xxx:0.8),(artist:yyy:0.6),`
  - It **always ends with a comma** (even when only one artist was drawn), so you can append the
    character node's multi-line output directly without gluing two tags together. A trailing comma
    has no side effect in prompt syntax
- **Output 2 `artists_json`**: `{"artists": [{"name", "weight", "top"}], "status"}`; `status` is always `OK` for now
- **Output 3 `tested_count`**: number of combinations recorded in the current database (as a **string**, so it can be wired straight into a text node)

## Data source and column

Reads `danbooru_art_001.csv` in the node directory by default (header `artist,trigger,count`) and
**always reads the `trigger` column**: it is already escaped and prompt-ready
(e.g. `hammer_\(sunset_beach\)`). If the CSV has no `trigger` column it falls back to the first
column and logs a WARNING (which may then be an unescaped raw tag).

## Sampling and weights

| Parameter | Type / range | Description |
|---|---|---|
| `artist_count` | INT 1-10 | Artists generated per run; setting it to 1 simply draws one artist at random |
| `artist_top_ratio` | FLOAT 0.01-1.0 | Top pool ratio: the first `ceil(total × ratio)` rows of the CSV (at least 1); earlier rows usually have more works |
| `artist_top_count` | INT 1-10 | Minimum number of Top-pool artists per run (only when Top Priority is on and the Top pool is not empty) |
| `weight_min` | FLOAT 0.1-2.0 | Lower bound of a single artist weight; swapped automatically if above the upper bound, negatives clamped to 0 |
| `weight_max` | FLOAT 0.1-2.0 | Upper bound of a single artist weight |
| `weight_total` | FLOAT 0-20 | Sum of all weights, distributed exactly; clamped to the feasible range |
| `weight_curve` | ENUM | Distribution curve: `flat` (default) / `dominant` (one artist clearly dominates) / `ramp` (descending ladder). Affects only who gets more weight, not the bounds or the total |
| `artists_prefix` | BOOLEAN | On: outputs `artist:name`; off: outputs the plain name |
| `seed` | INT | Random seed; the same seed with the same settings is reproducible |

## Sampling preference, lists and dedup

| Parameter | Type / range | Description |
|---|---|---|
| `use_top_priority` | BOOLEAN | Draw from the Top pool first; off samples uniformly from the whole CSV |
| `sort_by_weight` | BOOLEAN | Reorder artists in the prompt by weight (descending); affects the `full_prompt` dedup key |
| `exclude_artists` | STRING | Exclude list, comma separated |
| `force_include` | STRING | Artists that must appear, comma separated; takes priority over the exclude list |
| `dedup_mode` | ENUM | What counts as a duplicate; four choices, see "Dedup modes in detail" below |
| `max_attempts` | INT 1-100 | Maximum retries in dedup mode; if every retry hits an already-tested combination the last result is accepted with a WARNING |

### Dedup modes in detail

"Dedup" means comparing the combination just drawn against the recorded history and retrying (up to
`max_attempts`) on a hit. The four modes only differ in **what is compared**:

| Mode | Compared | Counts as duplicate when | Typical use |
|---|---|---|---|
| `none` (default) | nothing | — (result is still recorded, just never checked) | Pure randomness; watch progress through `tested_count` |
| `full_prompt` | artist **list + order + weights** | all three are identical | Strictest: never emit the exact same prompt twice |
| `artist_set` | artist **set** (order and weights ignored) | the same group of people is drawn, even with different order or weights | Avoid re-drawing the same artists, but allow a different weight mix |
| `artist_list` | the **sorted name list** (weights ignored) | the same group of people (order-independent) | Basically the same as `artist_set`; differs only in how duplicate names are folded |

Notes:

- Weights are only compared in `full_prompt`; `artist_set` / `artist_list` ignore them, so
  "same people, different weights" still counts as a duplicate there
- `artist_set` folds duplicate names, `artist_list` does not (`["a","a","b"]` equals `["a","b"]` for
  `artist_set`, but not for `artist_list`)
- `sort_by_weight` changes the order inside the prompt and therefore affects `full_prompt` indirectly
- `|` and `:` inside names are escaped before building the key, so `["a:b"]` and `["a","b"]` are never
  mistaken for the same combination
- When retries run out the node **accepts the last result** and logs a WARNING (it never blocks generation)

## Advanced parameters (collapsed by default)

| Parameter | Type | Description |
|---|---|---|
| `custom_csv_path` | STRING | Custom artist CSV path; leave empty for `danbooru_art_001.csv` in the node directory |
| `tested_db_path` | STRING | SQLite records database path; leave empty for `rollingartist.sqlite` in the node directory. Tested records live here, so changing it resets dedup progress |

## Generation flow

The node does one thing: sample artists and assign weights as configured, retry (up to `max_attempts`) when it
hits an already-tested combination, then record the result together with its dedup keys.

> As of 4.1.0 the **"Exact mode" was removed** (the old behaviour that, with `artist_count = 1` and an empty
> Force Include, enumerated `(artist, weight)` combinations without repetition — along with the `mode`
> parameter, the `remaining_count` output and the combination-pool tables). Therefore:

- `artist_count = 1` now simply draws one artist at random; `ALL_COMBINATIONS_TESTED` is never returned
- `dedup_mode` and `weight_total` always apply
- Opening an old database automatically drops the leftover `exact_pool` / `pool_meta` tables

## Records database (SQLite)

- The default database lives in the node directory: `rollingartist.sqlite` (plus `-wal` / `-shm` sidecars).
- **The default database is cleared on every ComfyUI startup** (by `prestartup_script.py`) so each session starts
  clean. To keep dedup progress across sessions, point `tested_db_path` at your own file
  (explicit paths are never touched).
- "Check → generate → record" runs inside a single database transaction, so multiple threads, **multiple ComfyUI
  instances or processes** never produce duplicate combinations.
- Dedup uses an index lookup, so a large history costs no memory; artist names containing `|`, `:` or commas are
  stored safely.
- There is no limit on the number of records and no automatic pruning; the default database is wiped on startup.
- `custom_csv_path` and `tested_db_path` are used verbatim: fine on a single-user machine, but if ComfyUI is exposed
  to a LAN or the internet, anyone could read or write arbitrary files through them — add access control if so.
