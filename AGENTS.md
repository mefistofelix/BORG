# AGENTS.md

## Project Context

Repository: `BORG`  
Last confirmed local root: `C:\Users\Michele\Desktop\BORG`  
GitHub: `mefistofelix/BORG` (private)  
VCS workflow: prefer `jj` (Jujutsu) with colocated Git backend. Default bookmark/branch: `main`.

Main Python package: `borg/`.

Current work is primarily a cleanup/port of older JS utilities into minimal Python equivalents, especially:

- `borg/x.py` — common utilities + EventBus
- `borg/jrpc.py` — generic async WebSocket RPC transport
- `borg/cdp.py` — Chrome DevTools Protocol layer
- `borg/tg.py` — Telegram Bot API + TDLib wrapper
- `borg/xdb.py` — generic-ish DB helper, ported from `doc/lib/xdb.js`
- `borg/borg.py` / `borg/__main__.py` — application bootstrap

Reference/original code lives mainly under `doc/` and `borg/BAK/`.

---

## Coding Style

Optimize for **cognitive simplicity first, line count second**.

- Keep code minimal but readable. No code golf.
- One line should represent one clear idea.
- Do not combine unrelated initializations/operations just to save lines.
- Prefer direct language/runtime/library constructs over wrappers and abstractions.
- Do not add helpers, classes, validation layers, type annotations, comments, docstrings, or defensive checks unless they solve a real problem.
- Prefer native library validation/errors/defaults over reimplementing them.
- Avoid fake abstractions: a few duplicated lines are preferable to an abstraction that hides semantics.
- Do not create short aliases for methods/modules/attributes merely to reduce repetition.
  - Example to avoid: `q = self.escape_name`.
- Keep origin visible when useful:
  - prefer `import pathlib` + `pathlib.Path(...)`
  - exception: avoid redundant constructions such as `jrpc.jrpc`; importing the primary same-named symbol is fine.
- Do not hide ordinary public attributes behind `_attr` + trivial properties.
- Prefer guard clauses for empty/error/exceptional cases:
  ```python
  ret = call()
  if not ret:
      return
  return parse(ret)
  ```
  rather than nesting the successful path.
- Do not wrap synchronous/blocking APIs in `async` unless real concurrency is required.
- If a blocking API must coexist with async code, make that explicit at the caller boundary, e.g. `asyncio.to_thread`.
- Keep abstraction boundaries strict:
  - lower layers expose generic representations;
  - higher layers translate domain/protocol-specific concepts.
- Generic formatters/parsers must not branch on concrete drivers unless the syntax genuinely belongs to that layer.
- At protocol boundaries, prefer a generic structured argument over continuously extending low-level function signatures.
- Assume known internal data contracts instead of handling every theoretical malformed input.
- Prefer structured data transformations over clever string manipulation.

---

## EventBus (`borg/x.py`)

Desired semantics:

- `on`, `once`, `off`
- whitespace-separated multiple events
- listener registration order preserved
- synchronous callbacks execute inline
- async callbacks use `asyncio.create_task`
- futures are one-shot listeners
- `once_future(events)` resolves to `(event, data)`
- no event queue/storage
- no unnecessary error-handling machinery

`go_main(coro)` should remain a thin wrapper around `asyncio.run(coro)`.

Application lifecycle must be controlled by the application coroutine, not by a generic “wait forever” option.

---

## JRPC (`borg/jrpc.py`)

`jrpc` inherits `EventBus`.

It is intentionally a **generic RPC transport**, not CDP-specific and not rigid JSON-RPC validation.

- `call(req)` takes a dict.
- Add an auto-incremented `id` only when `req` has no `id`.
- A custom caller-supplied ID must not increment the internal counter.
- Incoming messages with `id` emit `rpc_<id>`.
- Incoming notifications emit:
  - `notify`
  - their method name
- Pending calls also listen for `close`.
- If connection closes while waiting, return `None`.
- RPC errors raise ordinary `Exception`; attach:
  ```python
  exc.cause = {"req": req, "ret": ret}
  ```
- Do not introduce a custom JRPCError class unless actually necessary.
- `connect()` creates the websocket receive-loop task.
- Do not start a second `loop()` elsewhere.
- `close()` closes task/websocket/session cleanly.

---

## CDP (`borg/cdp.py`)

`cdp` inherits `jrpc`.

CDP-specific translation belongs here, not in `jrpc`.

`call(method, params=None)` converts CDP calls into the generic request dict.

Target/session handling:

- `_targetId` is a higher-level convenience consumed by `cdp.call`.
- translate it to `sessionId` using the target map.
- `cdp_page.call()` injects its target ID.

Naming:

- use `on_notify`, not `_onnotify`.
- Do not add underscore-private naming without a concrete reason.

`jrpc.connect()` already starts the receive-loop task.

### Lifecycle

The CDP websocket receive task is the natural process lifetime condition.

Application pattern:

```python
c = cdp()

try:
    await c.launch(chrome_udd)
    print("start")
    await c.task
finally:
    await c.close()
```

The program stays alive while the CDP websocket is alive.

When Chrome/CDP closes:

1. websocket loop ends;
2. `c.task` completes;
3. cleanup runs;
4. Python process exits.

This was tested by sending real CDP `Browser.close`; `uv run -m borg` exited cleanly with code `0` and without aiohttp unclosed-session warnings.

---

## Telegram (`borg/tg.py`)

Contains:

- `bot` — Telegram Bot API
- `td` — TDLib wrapper

`td.loadlib(path)` should stay explicit.

Accept a DLL path or directory and resolve it, but do not add arbitrary filesystem inference.

`td.receive()` may use:

```python
await asyncio.to_thread(...)
```

because TDLib receive is a genuinely blocking native operation exposed into an async/event-driven layer.

Prefer early-return style:

```python
ret = self.lib.td_execute(...)
if not ret:
    return
return json.loads(ret)
```

Same principle for `receive()`.

Never expose or commit Telegram credentials.

---

## XDB (`borg/xdb.py`)

Python port of `doc/lib/xdb.js`.

Current supported drivers:

- `sqlite3`
- `duckdb`
- `mariadb` via dynamic import

No fake SQL Server support. PostgreSQL has been discussed but is not implemented.

The module is intentionally synchronous. Do not re-add fake `async` wrappers. Concurrency belongs at a separate caller layer.

### Connection

Use the driver module directly:

```python
driver = self.conf["driver"]
module = importlib.import_module(driver)
conn = module.connect(*args, **kwargs)
```

No driver whitelist or aliases unless needed.

Let unsupported modules naturally raise import/driver errors.

Do not force connector settings such as autocommit/isolation unless BORG explicitly needs different semantics.

### Row normalization

Normalize DB-API tuple rows centrally in `query()` using `cursor.description`.

Expected fetch modes:

- `cell` → first column of one row
- `col` → list of first-column values
- `row` → one dict
- `res` → list of dicts

Do not use driver-specific row factories/dictionary cursors when central normalization suffices.

### Identifier escaping

Current requirement:

- SQLite/MariaDB: backticks
- DuckDB: double quotes

DuckDB was tested and rejects backticks.

Do not alias `self.escape_name` to a one-letter local function.

### `dyn_sql`

Keep it driver-independent.

Current placeholders:

- `?NAME`
- `?COLNAMES`
- `?IN`
- `?SET`
- `?SET_COLVAL`
- `?SET_VALUES`
- `?SET_EXCLUDED`
- `?W_AND`

`.key` addressing is supported.

Each placeholder consumes its explicit argument. Do not restore implicit memory/coupling between placeholders.

Important behavior:

- `?IN` empty list → `IN(NULL)`
- `?SET_VALUES` generates MariaDB-style `VALUES(col)`
- `?SET_EXCLUDED` generates SQLite/DuckDB/PostgreSQL-style `excluded.col`
- dialect selection belongs in callers such as `upsert()`, not inside `dyn_sql`.

### Upsert dialects

SQLite/DuckDB/PostgreSQL-style:

```sql
ON CONFLICT (...) DO UPDATE SET col = excluded.col
```

MariaDB:

```sql
ON DUPLICATE KEY UPDATE col = VALUES(col)
```

A branch in `upsert()` is therefore legitimate and should not be abstracted away artificially.

Likewise `insert_ignore()` legitimately differs by dialect.

### RETURNING

Supported API concept:

```python
insert(table, row, returning=None)
update(table, row, where, returning=None)
upsert(table, row, conflict_cols=None, returning=None)
```

`returning` is an explicit iterable/list of column names.

- insert/upsert returning → one dict
- update returning → list of dicts
- falsy `returning` → old behavior

Do not silently broaden it to `"*"` unless requested.

SQLite and DuckDB RETURNING paths were tested successfully.

MariaDB:
- INSERT/UPSERT RETURNING supported by target syntax
- UPDATE RETURNING is not supported; do not emulate it in the wrapper.

### Automatic schema management

`row_to_table_schema()` infers SQL types from real row values.

`table_create_or_add_cols()`:

1. inspect table;
2. create table if absent;
3. add only missing columns;
4. do nothing when already aligned.

CREATE + ALTER + no-op behavior was tested successfully on SQLite and DuckDB.

### Type inference

Keep straightforward inference for:

- bool/int/bigint
- float/double
- Decimal
- bytes/binary
- date/datetime
- numeric/date/datetime strings
- valid JSON object/array strings
- long text
- fallback varchar

Do not over-engineer inference.

---

## Trading / Meme-Token Anomaly Detection

Separate project/workstream: detect unusually interesting meme tokens among the currently trending set, primarily on very short horizons (seconds → minutes → ~1 hour).

### Goal

Rank trending tokens by how **anomalous / interesting** their current behavior is, rather than trying to predict price directly from the start.

Examples of interesting patterns:

- very new token with unusually many watchers;
- watchers exploding while market cap/price has not moved yet;
- holders accumulating while price is still flat;
- old token suddenly receiving abnormal attention;
- unusual combinations of age, watchers, holders, price and market cap;
- rapid price/market-cap acceleration relative both to the token's own recent history and to the other currently trending tokens.

The system only sees a small changing subset of the market (roughly the top 10–20 trending tokens), not continuous data for every token.

### Input Data

Per-token time series, sampled approximately every 5–30 seconds:

- timestamp;
- token age / time since creation;
- watchers / current viewers;
- market cap;
- price;
- holders count;
- additional metrics when useful.

Also keep slower/static features such as:

- creation timestamp;
- creator statistics;
- other token metadata.

History is naturally variable-length:

- a token may have only 30 seconds of data;
- another may have 5–60 minutes;
- tokens can disappear from trending and later reappear;
- some tokens can already be days old when first observed.

Never assume a complete fixed-size history exists.

### Absolute + Relative Features

Preserve **both**:

1. absolute values;
2. relative values versus the current market/trending population.

Examples:

- raw watchers = 800;
- raw market cap = 20k;
- watcher percentile among current trending tokens;
- market-cap percentile;
- holder-growth percentile;
- momentum/acceleration percentile.

Relative features matter because the market regime changes over time. Absolute values still matter and must not be discarded.

Include global/current-market context where useful so that the same raw movement can be interpreted differently under different liquidity/attention regimes.

### Time-Series Features

Useful derived features over several windows, e.g.:

- 30 s;
- 1 min;
- 5 min;
- 10 min;
- up to ~1 h.

Possible features:

- absolute change;
- percentage/log change;
- slope;
- acceleration;
- volatility;
- watcher growth;
- holder growth;
- price/market-cap growth;
- disagreement/divergence between metrics;
- ratios such as attention relative to market cap;
- token age.

Timestamps/time deltas must remain available because observations may be intermittent and non-uniform.


### Canonical Model Input JSON

Use a structured token-centric payload. Keep raw observations and derived features conceptually separate.

Canonical shape:

```jsonc
{
  "snapshot_ts": 1786226400.0,

  "market": {
    // Global context at this snapshot.
    "trending_count": 20,

    // Cross-sectional market context. Keep this extensible.
    "watchers": {
      "median": 42,
      "p90": 180,
      "p99": 620
    },
    "market_cap": {
      "median": 85000,
      "p90": 420000,
      "p99": 1800000
    },
    "holders": {
      "median": 210,
      "p90": 1800,
      "p99": 7200
    }
  },

  "tokens": [
    {
      "id": "TOKEN_CA_OR_STABLE_ID",

      "static": {
        "created_ts": 1786226120.0,
        "creator_token_count": 7
      },

      "current": {
        "ts": 1786226400.0,
        "age_s": 280.0,

        // Absolute values.
        "watchers": 812,
        "market_cap": 94000.0,
        "price": 0.000094,
        "holders": 362,

        // Same snapshot expressed relative to the current trending set.
        "relative": {
          "watchers_pct": 0.995,
          "market_cap_pct": 0.61,
          "price_pct": 0.54,
          "holders_pct": 0.73,
          "age_pct": 0.18
        }
      },

      "history": [
        {
          "ts": 1786226280.0,
          "age_s": 160.0,
          "watchers": 120,
          "market_cap": 71000.0,
          "price": 0.000071,
          "holders": 240
        },
        {
          "ts": 1786226310.0,
          "age_s": 190.0,
          "watchers": 210,
          "market_cap": 76000.0,
          "price": 0.000076,
          "holders": 267
        },
        {
          "ts": 1786226400.0,
          "age_s": 280.0,
          "watchers": 812,
          "market_cap": 94000.0,
          "price": 0.000094,
          "holders": 362
        }
      ],

      "windows": {
        "30s": {
          "coverage_s": 30.0,
          "samples": 2,

          "watchers": {
            "delta": 270.0,
            "pct_change": 0.50,
            "slope_per_s": 9.0
          },
          "market_cap": {
            "delta": 7000.0,
            "pct_change": 0.0805,
            "slope_per_s": 233.33
          },
          "price": {
            "pct_change": 0.0805
          },
          "holders": {
            "delta": 28.0,
            "pct_change": 0.0838
          }
        },

        "5m": {
          // Actual available coverage can be shorter than the nominal window.
          "coverage_s": 280.0,
          "samples": 11,

          "watchers": {
            "delta": 752.0,
            "pct_change": 12.53,
            "slope_per_s": 2.69,
            "acceleration": 0.018
          },
          "market_cap": {
            "delta": 51000.0,
            "pct_change": 1.186,
            "slope_per_s": 182.14,
            "volatility": 0.092
          },
          "price": {
            "pct_change": 1.186,
            "volatility": 0.095
          },
          "holders": {
            "delta": 211.0,
            "pct_change": 1.397,
            "slope_per_s": 0.754
          },

          // Cross-metric relationships are first-class features.
          "relations": {
            "watchers_vs_market_cap": 8.3,
            "holders_vs_price": 1.18,
            "attention_without_price_move": 0.74
          },

          // Relative ranking of derived behavior among current candidates.
          "relative": {
            "watcher_growth_pct": 0.999,
            "market_cap_growth_pct": 0.91,
            "holder_growth_pct": 0.97,
            "volatility_pct": 0.82
          }
        },

        "10m": {
          "coverage_s": 280.0,
          "samples": 11
        },

        "1h": {
          "coverage_s": 280.0,
          "samples": 11
        }
      }
    }
  ]
}
```

### JSON Contract Rules

- `tokens` is the current candidate/trending set, normally around 10–20 tokens.
- `history` is **variable length** and chronological, oldest → newest.
- Never pad missing historical observations at the beginning or end with fake measurements.
- A newly created token simply has less history.
- Keep `coverage_s` and `samples` for every derived window so the model can distinguish a real 5-minute history from only 30 seconds of available data.
- Every raw observation carries `ts`; do not assume perfectly regular sampling.
- `age_s` should be explicit even though it can be derived from timestamps.
- Preserve raw absolute values. Relative/percentile features supplement them; they do not replace them.
- Relative values should normally be normalized to `[0, 1]` percentiles/ranks across the current comparison population.
- The current snapshot may also appear as the last `history` item; this is acceptable and keeps the sequence self-contained.
- Nominal windows such as `30s`, `5m`, `10m`, `1h` may contain less actual history. `coverage_s` makes this explicit.
- Do not invent missing samples to force equal-length sequences.
- For a first classical model or small MLP, use the fixed-size `current` + `windows` feature representation.
- Keep `history` available for future sequence models and for recomputing features.
- Prefer explicit numeric `null`/absence handling at feature-building time rather than magic sentinel numbers.
- Market context belongs at the top level when it applies to every token in the same snapshot; do not duplicate identical market-wide values into every raw history sample.
- Add new metrics consistently to raw observations first, then derive their window and relative features only when useful.

### Canonical Model Output

A practical inference result should preserve the token identifier and expose separate scores:

```jsonc
{
  "snapshot_ts": 1786226400.0,
  "results": [
    {
      "id": "TOKEN_CA_OR_STABLE_ID",

      // Primary unsupervised score.
      "anomaly_score": 0.94,

      // Optional later supervised score.
      "trade_score": null,

      // Rank within this snapshot's candidate set.
      "rank": 1,

      // Small explainability payload for UI/debugging.
      "signals": [
        "watcher_growth_extreme",
        "attention_high_vs_market_cap",
        "holders_rising_before_price"
      ]
    }
  ]
}
```

Keep `anomaly_score` and the later supervised `trade_score` separate. They answer different questions.

### Learning Strategy

Start with **unsupervised anomaly detection**.

There are initially no trustworthy labels, so the first model should learn what is normal among observed/trending tokens and assign an anomaly score to unusual combinations.

Do not start with a neural network just because one is available. Begin with a simple classical anomaly model if it solves the problem more clearly.

Candidate approaches include:

- Isolation Forest;
- Local Outlier Factor where appropriate;
- robust/statistical distance methods;
- later, possibly an autoencoder or small PyTorch model.

Inference can run continuously.

Training/re-fitting should happen periodically, not every few minutes. The model needs to adapt to market drift without constantly retraining on every snapshot.

### Supervised Signal Later

Later add user feedback such as thumbs-up / thumbs-down after enough future price history exists.

Keep this supervised signal conceptually separate from the unsupervised anomaly detector.

Preferred architecture:

- unsupervised model → “how unusual is this token now?”
- supervised model/head → “historically, do patterns like this tend to become desirable trades?”

Do not force both tasks into one model unless there is a concrete advantage.

The system should remain usable in:

- unsupervised-only mode;
- supervised-enhanced mode.

### Neural Network

A small PyTorch neural network may be added later, CPU-friendly and trainable locally.

It should not receive raw variable-length histories blindly unless there is a clear architecture for them.

Prefer first producing a stable feature representation from multiple time windows and feeding that into a small network.

If sequence modeling later proves useful, evaluate it separately rather than immediately introducing Transformers/RNN complexity.

The neural network is not the definition of the system; the anomaly/trading objective and feature semantics come first.

### Output

For every current trending token produce at least:

- anomaly/interest score;
- ranking among current candidates;
- useful contributing signals/explanation;
- token metadata needed by the UI.

The primary practical output is a ranked shortlist of tokens worth inspecting quickly.

Avoid reducing the output to a binary buy/not-buy decision in the initial unsupervised phase.

---

## Running / Testing

Primary command:

```text
uv run -m borg
```

For ad-hoc Python tests through MrMCP, prefer:

```text
uv run -
```

with Python passed through stdin.

Do not use shell/Python to read/search/edit repository files when structured MrMCP tools cover the operation.

For repo edits prefer:

- `read_file` / `read_files`
- `grep`
- `glob`
- `edit`
- `replace`

Use `exec` only for actual program execution/tests/VCS tooling.

---

## MrMCP Workflow

When available:

1. create/reuse a MrMCP context;
2. call `context_info`;
3. read root `AGENTS.md`;
4. preserve the exact returned `context_handle`;
5. use structured file tools for repository operations.

The last confirmed project root was:

```text
C:\Users\Michele\Desktop\BORG
```

Do not assume this if a new session can query `context_info`; query it first.

---

## Version Control

Prefer `jj` over raw Git for normal workflow.

Repository was initialized as a colocated Jujutsu/Git repository.

Remote:

```text
https://github.com/mefistofelix/BORG
```

GitHub repository is private.

Default bookmark/branch:

```text
main
```

Initial commit description:

```text
Initial import
```

Use meaningful commit descriptions, not release/version-only messages.

---

## Repository Hygiene

`.gitignore` excludes local/runtime artifacts such as:

- `.venv`
- Python `__pycache__`
- Chrome user-data directory
- local executables/DLLs such as `uv.exe` / `tdjson.dll`

Two historical Telegram source files were intentionally not included in the initial GitHub snapshot because they contained an old hardcoded Telegram credential:

- `borg/BAK/telegram.js`
- `doc/lib/telegram.js`

Do not commit those files until credentials have been removed/redacted.

Never reproduce secrets in logs, commits, documentation, or chat output.

---

## Working Preference

When a requested change is clear:

- inspect the relevant code;
- make the smallest coherent edit;
- run a concrete test;
- report the result briefly.

Do not spend long responses explaining obvious edits before making them.

When something looks like a code smell, determine whether it has a real semantic purpose. Remove ceremony that does not buy anything, but preserve genuine protocol/driver/runtime differences.
