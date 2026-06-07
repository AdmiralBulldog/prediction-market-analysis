# Plan: Polymarket F1 Data API fetcher + CLOB WebSocket recorder

## Context

The repo currently fetches Polymarket trades by decoding `OrderFilled` events from the
Polygon blockchain (`src/indexers/polymarket/blockchain.py` + `trades.py`). That pulls
*all* trades and is heavy. The user wants two new, F1-scoped capabilities, kept in **this**
repo so the existing analysis tooling can be reused:

1. **Fetch F1 trades via the Polymarket Data API** (`https://data-api.polymarket.com/trades`)
   instead of the blockchain, producing parquet that the existing calibration analyses can read.
2. **Record high-frequency CLOB data live** via the WebSocket
   (`wss://ws-subscriptions-clob.polymarket.com/ws/`) for F1 markets and save it.

**Order (per user): Part 1 first, then Part 2. Stay in this repo.**

### Key feasibility findings (verified live during planning)

- `GET /trades?market=<conditionId>` filters per market and returns:
  `proxyWallet, side, asset (CLOB token id), conditionId, size, price, timestamp,
  title, slug, eventSlug, outcome, outcomeIndex, transactionHash`. Supports `limit`/`offset`.
- F1 markets are discoverable via Gamma `GET /events?tag_slug=f1` (tag `f1`, id `100389`).
  Each event nests `markets[]` with `conditionId` + `clobTokenIds` + `outcomePrices` + `closed`.
- **Schema compatibility is the crux and it works:** the two calibration analyses
  (`polymarket_win_rate_by_price.py`, `polymarket_calibration_by_bucket.py`) derive price
  purely from `maker_asset_id`/`taker_asset_id`/`maker_amount`/`taker_amount` joined to the
  markets resolution table — **they never read `block_number` or the blocks dir**. So we can
  synthesize those four columns from the Data API's `price`/`size`/`asset` and run the
  existing analyses **unmodified**.
- The two **time-series** analyses (`polymarket_volume_over_time`, `polymarket_trades_over_time`)
  *do* need `block_number` + the blocks join, so they will **not** apply to Data API output.
  This is the accepted partial-reuse tradeoff the user signed off on. (The Data API gives a real
  `timestamp` per trade, so bespoke F1 time-series could be added later if wanted.)

### Effort / difficulty assessment

- **Part 1: Easy / fast (~half a day).** Pure HTTP + parquet, reuses `HttpClient`,
  the `Indexer` framework, and `Market.from_dict`. Low risk.
- **Part 2: Medium (~1 day).** Needs a new dependency (`websocket-client`), a long-running
  connection with heartbeat + reconnect, and produces a *different* data shape (heterogeneous
  order-book/trade messages) that the existing analyses do not consume. More moving parts and
  must be tested live.

---

## Part 1 — F1 trades via Data API (do first)

### New file: `src/indexers/polymarket/f1_data_api.py`

Contains a thin client + an `Indexer` subclass (auto-discovered by the `make index` menu via
`Indexer.load()`).

**Reuse:**
- `HttpClient` (`src/common/client.py`) for both Gamma and Data API calls (`rate_limit=10`).
- `Market.from_dict` (`src/indexers/polymarket/models.py`) to parse nested event markets.
- `Indexer` base (`src/common/indexer.py`).
- Batch-write + cursor pattern copied from `markets.py` / `trades.py`.

**Constants / output dirs:**
- `GAMMA_API_URL = "https://gamma-api.polymarket.com"`, `DATA_API_URL = "https://data-api.polymarket.com"`
- `MARKETS_DIR = data/polymarket/f1/markets`, `TRADES_DIR = data/polymarket/f1/trades`
- `CURSOR_FILE = data/polymarket/f1/.f1_trades_cursor` (newline-delimited completed conditionIds, for resume)

**`PolymarketF1TradesIndexer.run()`:**
1. **Discover F1 markets:** paginate Gamma `GET /events?tag_slug=f1&limit=100&offset=N`
   (include both open and closed). For each event, for each `market` dict → `Market.from_dict`.
   Dedup by `condition_id`; collect `(condition_id, clob_token_ids)`.
2. **Write F1 markets** to `MARKETS_DIR/markets_*.parquet` (`asdict(market)` + `_fetched_at`),
   identical format to `markets.py`. This is what the analyses' resolution table is built from.
3. **Fetch trades per market:** for each `condition_id` not already in the cursor, paginate
   `GET /trades?market=<cid>&limit=500&offset=N` until an empty page. Build each row with:
   - **Native fields** (kept for reference/future analyses): `proxyWallet, side, asset,
     conditionId, size, price, timestamp, outcome, outcomeIndex, slug, eventSlug, title,
     transactionHash, _fetched_at`.
   - **Synthesized fields for analysis compatibility** (match existing trades schema exactly):
     - `maker_asset_id = "0"` (USDC, string)
     - `taker_asset_id = str(asset)` (string token id — matches `clob_token_ids` entries)
     - `taker_amount = int(round(size * 1e6))` (int)
     - `maker_amount = int(round(price * size * 1e6))` (int)

     This makes the analysis compute `100 * maker_amount / taker_amount == 100 * price` and join
     `taker_asset_id == token_id`, exactly as for blockchain trades. (Note: blockchain trades
     keep `maker_amount`/`taker_amount` as ints and only the asset ids as strings — we mirror that
     so DuckDB's `taker_amount > 0` numeric comparison stays correct.)
4. Accumulate and write `TRADES_DIR/trades_{idx}_{idx+10000}.parquet` (BATCH_SIZE 10000),
   appending the conditionId to the cursor after each market completes. Delete cursor on clean
   finish; preserve on `KeyboardInterrupt` (same pattern as `trades.py`).

### New files: thin F1 analysis subclasses (make `make analyze` work on F1 data)

The existing analyses default to `data/polymarket/{trades,markets}` but accept dir overrides in
`__init__`. Add two ~10-line subclasses, auto-discovered by the analyze menu:

- `src/analysis/polymarket/f1_win_rate_by_price.py` → subclass of
  `PolymarketWinRateByPriceAnalysis`, name `f1_win_rate_by_price`, passing
  `trades_dir=data/polymarket/f1/trades`, `markets_dir=data/polymarket/f1/markets`,
  `legacy_trades_dir=data/polymarket/f1/legacy_trades` (non-existent → skipped cleanly).
- `src/analysis/polymarket/f1_calibration_by_bucket.py` → subclass of
  `PolymarketCalibrationByBucketAnalysis`, name `f1_calibration_by_bucket`, same dir overrides.

(FPMM/legacy resolution is empty for F1 CTF markets, and the legacy dir won't exist, so those
branches are skipped — confirmed by the guards in both analysis files.)

### Docs
- Add an "F1 Data API trades" section to `docs/SCHEMAS.md` documenting the synthesized + native
  columns and the partial-reuse note (time-series analyses N/A).

---

## Part 2 — CLOB WebSocket high-frequency recorder (do second)

### Dependency
Add `websocket-client>=1.7.0` to `pyproject.toml` (synchronous, matches the repo's all-sync style;
no asyncio elsewhere).

### New file: `src/indexers/polymarket/clob_ws_recorder.py`

An `Indexer` subclass `PolymarketF1ClobRecorder` (`name="polymarket_f1_clob_recorder"`) whose
`run()` is a long-running live capture (stop with Ctrl+C), shown in the `make index` menu.

**Flow:**
1. **Resolve F1 token ids** to subscribe: reuse the Gamma `tag_slug=f1` discovery from Part 1
   (extract `clob_token_ids` from F1 markets; default to **active/non-closed** markets to avoid
   dead books). Factor the discovery helper out of `f1_data_api.py` so both parts share it.
2. **Connect** to `wss://ws-subscriptions-clob.polymarket.com/ws/market` and send the subscription
   `{"assets_ids": [<token ids>], "type": "market"}`.
3. **Record both book + trades** (user choice): persist every incoming message of type
   `book`, `price_change`, `last_trade_price`, `tick_size_change`.
4. **Storage — JSONL (lossless, append-friendly):** write one JSON object per line
   `{"recv_ts": <epoch ms>, "msg": <raw message>}` to hourly-rotated files
   `data/polymarket/f1/clob_ws/ws_YYYYMMDD_HH.jsonl`. Rationale: the message types are
   heterogeneous (book snapshots vs price deltas vs trade ticks), so a single parquet schema is
   awkward; JSONL is crash-safe for a streaming HF feed and is directly queryable by DuckDB
   (`read_json_auto`). These messages are not consumed by the existing batch analyses anyway.
5. **Heartbeat + resilience:** send a `PING` on the documented interval (~10s) to keep the
   connection alive; on disconnect, reconnect with exponential backoff and re-subscribe.
   Flush and close cleanly on `KeyboardInterrupt`.

### Docs
- Add a "CLOB WebSocket recordings" section to `docs/SCHEMAS.md` describing the JSONL line format
  and message types.

---

## Critical files

| File | Action |
|------|--------|
| `src/indexers/polymarket/f1_data_api.py` | **new** — Gamma F1 discovery + Data API trade fetch indexer |
| `src/analysis/polymarket/f1_win_rate_by_price.py` | **new** — thin subclass w/ F1 dir defaults |
| `src/analysis/polymarket/f1_calibration_by_bucket.py` | **new** — thin subclass w/ F1 dir defaults |
| `src/indexers/polymarket/clob_ws_recorder.py` | **new** (Part 2) — live WS recorder indexer |
| `pyproject.toml` | add `websocket-client` (Part 2) |
| `docs/SCHEMAS.md` | document F1 trades schema + WS JSONL format |

**Reused as-is:** `src/common/client.py` (`HttpClient`), `src/common/indexer.py` (`Indexer`),
`src/indexers/polymarket/models.py` (`Market.from_dict`), and both existing Polymarket
calibration analyses.

---

## Verification

**Part 1**
1. `uv run main.py index` → select `polymarket_f1_trades` (or run the indexer directly). Confirm
   parquet appears under `data/polymarket/f1/markets/` and `data/polymarket/f1/trades/`.
2. Sanity-check synthesized columns with DuckDB, e.g.
   `SELECT maker_asset_id, taker_asset_id, maker_amount, taker_amount, price FROM 'data/polymarket/f1/trades/*.parquet' LIMIT 5;`
   and verify `100.0*maker_amount/taker_amount ≈ price*100`.
3. `uv run main.py analyze f1_win_rate_by_price` and `... analyze f1_calibration_by_bucket` →
   confirm a figure/output is produced over F1 trades (resolved markets only).
4. `make test` (or `pytest tests/test_compile.py`) → new indexer/analyses are discovered and
   instantiate without error.

**Part 2**
1. Run `polymarket_f1_clob_recorder` for ~60s against active F1 markets. Confirm
   `data/polymarket/f1/clob_ws/ws_*.jsonl` is created and contains lines with `book`,
   `price_change`, and/or `last_trade_price` message types.
2. Verify it survives a brief network blip (reconnects + re-subscribes) and exits cleanly on
   Ctrl+C with the buffer flushed.
3. Spot-query with DuckDB `read_json_auto` to confirm the JSONL is parseable.
