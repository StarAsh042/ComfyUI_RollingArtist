# Rolling Character

Draws characters at random from a character CSV (default `danbooru_character_001.csv`) and outputs
**one line per character** in the form `character tag, copyright tag, character description`
(comma + newline separated), plus a structured JSON result and the tested count. The node is an output
node, so it runs even when nothing consumes its outputs.

- **Output 1 `prompt`**: one line per character, e.g.
  ```
  hakurei_reimu,touhou,1girl,brown eyes,long hair,
  ganyu_\(genshin_impact\),genshin_impact,1girl,horns
  ```
  **No weights**: you will never see the `(name:weight)` form here. Multiple characters are separated by
  a comma plus a newline: every line ends with a comma except the last one, so flattening the output into
  a single line never glues two characters' tags together
- **Output 2 `characters_json`**: `{"characters": [{"name","copyright","top","count","core_tags"}], "status": "OK"}`
- **Output 3 `tested_count`**: number of character combinations recorded in the current database (as a **string**, so it can be wired straight into a text node)

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
character tag,copyright tag,character description
```

With "Enable Character Description" turned off it becomes:

```
character tag,copyright tag
```

- **Empty parts are omitted**, so no stray commas appear when a character has no copyright or no description
- The character description (`core_tags`) is **deduplicated within the line** (the source data lists some tags twice)
- Multiple characters are separated by a **comma plus a newline**: every line ends with a comma except the
  last one (a single character carries no trailing comma). The line order is the sampling order

## Parameters

| Parameter | Type / range | Description |
|---|---|---|
| `character_count` | INT 1-10 | Characters generated per run, one line each |
| `character_top_ratio` | FLOAT 0.01-1.0 | Top pool ratio: the first `ceil(candidates × ratio)` rows; candidates = characters left after the work filter |
| `character_top_count` | INT 1-10 | Minimum number of Top-pool characters per run |
| `seed` | INT | Random seed |
| `use_top_priority` | BOOLEAN | Draw from the Top pool first |
| `exclude_characters` | STRING | Exclude list, comma separated |
| `force_include` | STRING | Characters that must appear; names not in the CSV still work but carry no tags |
| `copyright_pick` | COMBO | Work dropdown listing **every work** in the CSV (about 3,460 with the default data, sorted by popularity = the sum of the `count` column, descending); the first entry is `(不限)`. Searchable as you type, case-insensitively |
| `describe_character` | BOOLEAN | Whether to append each character's fixed appearance tags (`core_tags`, i.e. the character description) to its line; on by default |
| `custom_csv_path` | STRING | Custom character CSV path; empty uses `danbooru_character_001.csv` |
| `tested_db_path` | STRING | Records database path; empty uses `rollingcharacter.sqlite` (separate from the artist node) |

This node performs **no weight assignment** (hence no weight parameters), **no exhaustion** (characters
are single tags) and **has no dedup mode parameter** (repeats are avoided with a fixed-rule window, see below).

## Work filter: a single dropdown

- The dropdown lists **every work** in the CSV (about 3,460 with the default data), sorted by
  **popularity**: the `count` column summed over the work's characters, descending. That reflects
  "how popular the work is overall" instead of "how many characters it has"; ties are broken by name
  ascending, so the order is stable and reproducible
- The first entry `(不限)` means no filtering
- It is **searchable as you type**, case-insensitively — typing `a` keeps works starting with a, typing
  `ab` narrows it further
- Both the raw `copyright` form (`fate_(series)`) and the escaped `trigger` form (`fate_\(series\)`) are accepted
- The options come from the **default CSV** (`danbooru_character_001.csv`): pointing `custom_csv_path` at
  another file does not change them
- If nothing is left after filtering the node errors out instead of silently emitting an empty prompt

## Repeat avoidance: no repeat within the last 10 outputs

There is no dedup mode any more; instead a fixed-rule **in-memory window** is used:

- The window holds the **last 10 output records**, where one record = one generation (possibly several characters)
- Before sampling, the window's character names are removed from the candidates, so drawing 1 character ten
  times in a row yields ten different characters; with `character_count=3` it means "no repeat within the
  last 30 characters"
- The window lives **in the node instance's memory**: restarting ComfyUI clears it, and several character
  nodes in one workflow each keep their own
- To clear it from a script, call `node.reset_recent()`; `node.recent_names()` shows the current window
- **When the window covers every candidate** (for example when a very small work is selected), the node
  falls back to allowing repeats and logs a WARNING — it never errors out or gets stuck

## Records database (SQLite)

- The default database lives in the node directory: `rollingcharacter.sqlite`, separate from the artist node
  (records carry a `kind=character` mark, so they never impersonate artist records even in a shared file)
- It only stores the **output history** (for `tested_count` and later inspection); repeat avoidance does
  **not** depend on it, so changing `tested_db_path` does not reset the window
- The default database is cleared on every ComfyUI startup

## Notes

- **About 22% of characters have no character description** (`core_tags` empty); those lines carry only the
  character and copyright tags
- About 10 description tags per character on average, so lines get noticeably longer with many characters
- Character names are unique (no duplicates across 34,416 rows), so a name maps directly to its record
- The weight column in stored records is a `1.0` placeholder (characters carry no weights); it only exists
  to keep record keys distinct
