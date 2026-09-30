# Rolling Character

Draws characters at random from a character CSV (default `danbooru_character_001.csv`) and outputs
**one line per character** in the form `character tag, copyright tag, core_tags` (newline separated),
plus a structured JSON result and the tested count. The node is an output node, so it runs even when
nothing consumes its outputs.

- **Output 1 `prompt`**: one line per character, e.g.
  ```
  hakurei_reimu,touhou,1girl,brown eyes,long hair
  ganyu_\(genshin_impact\),genshin_impact,1girl,horns
  ```
  **No weights**: you will never see the `(name:weight)` form here
- **Output 2 `characters_json`**: `{"characters": [{"name","copyright","top","count","core_tags"}], "status": "OK"}`
- **Output 3 `tested_count`**: number of character combinations recorded in the current database

## Data source and trigger layout

The node reads the `trigger` column by default. Verified across all 34,416 rows, that column is a
**fixed two-segment structure**:

| Segment | Content | Verification |
|---|---|---|
| 1 | Character tag (already escaped, ready to use) | == escaped `character` column: 34,416/34,416 |
| 2 | Copyright tag (e.g. `touhou`) | == escaped `copyright` column: 34,416/34,416 |

The column is fixed to `trigger` (not configurable): it always has the two-segment shape above, so no
column-selection parameter is needed.

## Output format

One line per character, fields joined by a single comma:

```
character tag,copyright tag,core_tags
```

With "Include Appearance Tags" turned off it becomes:

```
character tag,copyright tag
```

- **Empty parts are omitted**, so no stray commas appear when a character has no copyright or no tags
- Appearance tags are **deduplicated within the line** (the source data lists some tags twice)
- Multiple characters are separated by **newlines**; the line order is the sampling order

## Parameters

| Parameter | Type / range | Description |
|---|---|---|
| `character_count` | INT 1-10 | Characters generated per run, one line each |
| `character_top_ratio` | FLOAT 0.01-1.0 | Top pool ratio: the first `ceil(candidates × ratio)` rows; candidates = characters left after Copyright Filter |
| `character_top_count` | INT 1-10 | Minimum number of Top-pool characters per run |
| `seed` | INT | Random seed |
| `use_top_priority` | BOOLEAN | Draw from the Top pool first |
| `exclude_characters` | STRING | Exclude list, comma separated |
| `force_include` | STRING | Characters that must appear; names not in the CSV still work but carry no tags |
| `copyright_filter` | STRING | Only draw from these works (e.g. `touhou,vocaloid`). Accepts both `fate_(series)` and `fate_\(series\)`, case-insensitively; errors out when nothing is left |
| `include_core_tags` | BOOLEAN | Whether to append each character's fixed appearance tags to its line (on by default) |
| `dedup_mode` | ENUM | `none` / `full_prompt` / `artist_set` / `artist_list` |
| `max_attempts` | INT 1-100 | Maximum retries in dedup mode |
| `custom_csv_path` | STRING | Custom character CSV path; empty uses `danbooru_character_001.csv` |
| `tested_db_path` | STRING | Records database path; empty uses `rollingcharacter.sqlite` (separate from the artist node) |

This node performs **no weight assignment** (hence no weight parameters) and **no exhaustion** (characters
are single tags). The output format is fixed; the only format switch is "Include Appearance Tags".

## Notes

- **About 22% of characters have no appearance tags**; those lines carry only the character and copyright tags
- About 10 appearance tags per character on average, so lines get noticeably longer with many characters
- Character names are unique (no duplicates across 34,416 rows), so a name maps directly to its record
- Dedup keys are built from "names + weights", but characters carry no weights, so the key uses `1.0`
  placeholders (irrelevant for `artist_set` / `artist_list`; `full_prompt` then means "identical order")
- Records carry a `kind=character` mark and never impersonate artist records
- The default database is cleared on every ComfyUI startup; set `tested_db_path` explicitly to keep dedup
  progress across sessions
