"""Tag-scoped Polymarket trade fetching via the Data API (not the blockchain).

Markets for a Gamma tag (e.g. `f1`) are discovered via the events endpoint, then
trades are fetched per market from the Data API (`/trades?market=<conditionId>`).
Trades are written with both their native Data API fields and synthesized
`maker_asset_id`/`taker_asset_id`/`maker_amount`/`taker_amount` columns so the
existing Polymarket calibration analyses run over the output unmodified.

`discover_markets` is shared with the blockchain-based filtered indexer
(`chain_filtered_trades.py`) and the Part 2 CLOB recorder.

To scope a new tag, add a thin subclass of `TagDataApiTradesIndexer` (see
`PolymarketF1TradesIndexer`).
"""

from abc import abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Union

import pandas as pd
from tqdm import tqdm

from src.common.client import HttpClient
from src.common.indexer import Indexer
from src.indexers.polymarket.models import Market

GAMMA_API_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"

BATCH_SIZE = 10000

# The Data API rejects `offset` beyond this value with a 400
# ("max historical activity offset of 3000 exceeded") and ignores time-range
# params, so each market is limited to roughly the most recent MAX_OFFSET +
# page-size trades. Use the blockchain indexer for complete history.
MAX_OFFSET = 3000


def tag_data_dir(tag_slug: str) -> Path:
    """Root data directory for a tag, e.g. `data/polymarket/f1`."""
    return Path("data/polymarket") / tag_slug


def discover_markets(http: HttpClient, tag_slug: str, active_only: bool = False) -> list[Market]:
    """Discover markets for a Gamma tag via `GET /events?tag_slug=<slug>`.

    Paginates events (open and closed), expands the nested `markets[]` of each
    event into `Market` objects, and dedupes by `condition_id`.

    Args:
        http: Shared HTTP client.
        tag_slug: Gamma tag slug, e.g. `f1` or `elections`.
        active_only: If True, keep only active, non-closed markets (used by the
            live CLOB recorder in Part 2 to avoid dead books).

    Returns:
        Deduplicated list of `Market` objects for the tag.
    """
    markets: dict[str, Market] = {}
    offset = 0
    limit = 100

    while True:
        data: Union[dict, list] = http.get(
            f"{GAMMA_API_URL}/events",
            params={"tag_slug": tag_slug, "limit": limit, "offset": offset},
        )
        events = data if isinstance(data, list) else data.get("data", data)
        if not events:
            break

        for event in events:
            for market_dict in event.get("markets", []) or []:
                market = Market.from_dict(market_dict)
                if not market.condition_id:
                    continue
                if active_only and (market.closed or not market.active):
                    continue
                markets[market.condition_id] = market

        if len(events) < limit:
            break
        offset += len(events)

    return list(markets.values())


def discover_f1_markets(http: HttpClient, active_only: bool = False) -> list[Market]:
    """Convenience wrapper for `discover_markets(http, "f1", ...)`."""
    return discover_markets(http, "f1", active_only)


def write_markets(markets: list[Market], markets_dir: Path) -> None:
    """Write the tag's markets table (resolution source for the analyses)."""
    from dataclasses import asdict

    markets_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.utcnow()
    records = []
    for market in markets:
        record = asdict(market)
        record["_fetched_at"] = fetched_at
        records.append(record)
    path = markets_dir / "markets.parquet"
    pd.DataFrame(records).to_parquet(path)
    print(f"Saved {len(records)} markets to {path}")


class TagDataApiTradesIndexer(Indexer):
    """Base indexer: fetch a tag's trades from the Polymarket Data API.

    Concrete subclasses set the `tag_slug` class attribute (which makes them
    non-abstract and therefore discoverable by the `index` menu).
    """

    @property
    @abstractmethod
    def tag_slug(self) -> str:
        """Gamma tag slug this indexer scopes to."""

    def __init__(self, name: str, description: str):
        super().__init__(name=name, description=description)
        self.http = HttpClient(rate_limit=10)
        base = tag_data_dir(self.tag_slug)
        self.markets_dir = base / "markets"
        self.trades_dir = base / "trades"
        self.cursor_file = base / ".data_api_trades_cursor"

    def run(self) -> None:
        self.trades_dir.mkdir(parents=True, exist_ok=True)
        self.cursor_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            print(f"Discovering '{self.tag_slug}' markets via Gamma...")
            markets = discover_markets(self.http, self.tag_slug)
            print(f"Found {len(markets)} markets")

            write_markets(markets, self.markets_dir)
            self._fetch_trades(markets)
        finally:
            self.http.close()

    def _fetch_trades(self, markets: list[Market]) -> None:
        done = self._load_cursor()
        pending = [m for m in markets if m.condition_id not in done]
        if done:
            print(f"Resuming: {len(done)} markets already complete, {len(pending)} remaining")

        all_trades: list[dict] = []
        total_saved = 0
        interrupted = False

        def save_batch(batch: list[dict]) -> None:
            nonlocal total_saved
            if not batch:
                return
            chunk_idx = self._next_chunk_idx()
            path = self.trades_dir / f"trades_{chunk_idx}_{chunk_idx + BATCH_SIZE}.parquet"
            pd.DataFrame(batch).to_parquet(path)
            total_saved += len(batch)
            tqdm.write(f"Saved {len(batch)} trades to {path.name}")

        try:
            for market in tqdm(pending, desc=f"Fetching {self.tag_slug} trades", unit=" market"):
                all_trades.extend(self._fetch_market_trades(market.condition_id))

                while len(all_trades) >= BATCH_SIZE:
                    save_batch(all_trades[:BATCH_SIZE])
                    all_trades = all_trades[BATCH_SIZE:]

                self._append_cursor(market.condition_id)
        except KeyboardInterrupt:
            interrupted = True
            print("\nInterrupted. Progress saved.")

        if all_trades:
            save_batch(all_trades)

        if not interrupted and self.cursor_file.exists():
            self.cursor_file.unlink()

        print(f"\nDone: {total_saved} {self.tag_slug} trades saved")

    def _fetch_market_trades(self, condition_id: str) -> list[dict]:
        fetched_at = datetime.utcnow()
        rows: list[dict] = []
        offset = 0
        limit = 500

        while True:
            data: Union[dict, list] = self.http.get(
                f"{DATA_API_URL}/trades",
                params={"market": condition_id, "limit": limit, "offset": offset},
            )
            trades = data if isinstance(data, list) else data.get("data", data)
            if not trades:
                break

            for t in trades:
                rows.append(self._build_row(t, fetched_at))

            if len(trades) < limit:
                break

            offset += len(trades)
            if offset > MAX_OFFSET:
                tqdm.write(
                    f"Truncated {condition_id} at {len(rows)} trades (Data API offset cap of {MAX_OFFSET} reached)"
                )
                break

        return rows

    @staticmethod
    def _build_row(t: dict, fetched_at: datetime) -> dict:
        size = float(t.get("size", 0) or 0)
        price = float(t.get("price", 0) or 0)
        asset = t.get("asset", "")
        return {
            # Native Data API fields (kept for reference / future analyses).
            "proxyWallet": t.get("proxyWallet"),
            "side": t.get("side"),
            "asset": asset,
            "conditionId": t.get("conditionId"),
            "size": size,
            "price": price,
            "timestamp": t.get("timestamp"),
            "outcome": t.get("outcome"),
            "outcomeIndex": t.get("outcomeIndex"),
            "slug": t.get("slug"),
            "eventSlug": t.get("eventSlug"),
            "title": t.get("title"),
            "transactionHash": t.get("transactionHash"),
            # Synthesized fields matching the blockchain trades schema so the
            # existing calibration analyses run unmodified. Modeled as a taker
            # buying outcome tokens with USDC: maker provides USDC (asset 0).
            "maker_asset_id": "0",
            "taker_asset_id": str(asset),
            "taker_amount": int(round(size * 1e6)),
            "maker_amount": int(round(price * size * 1e6)),
            "_fetched_at": fetched_at,
        }

    def _next_chunk_idx(self) -> int:
        existing = list(self.trades_dir.glob("trades_*.parquet"))
        indices = []
        for f in existing:
            parts = f.stem.split("_")
            if len(parts) >= 2:
                try:
                    indices.append(int(parts[1]))
                except ValueError:
                    pass
        return max(indices) + BATCH_SIZE if indices else 0

    def _load_cursor(self) -> set[str]:
        if not self.cursor_file.exists():
            return set()
        return {line.strip() for line in self.cursor_file.read_text().splitlines() if line.strip()}

    def _append_cursor(self, condition_id: str) -> None:
        with self.cursor_file.open("a") as f:
            f.write(f"{condition_id}\n")


class PolymarketF1TradesIndexer(TagDataApiTradesIndexer):
    """Fetches F1 trades from the Polymarket Data API into parquet files."""

    @property
    def tag_slug(self) -> str:
        return "f1"

    def __init__(self):
        super().__init__(
            name="polymarket_f1_trades",
            description="Fetches F1 trades via the Polymarket Data API to parquet files",
        )
