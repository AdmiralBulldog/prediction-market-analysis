"""Indexer for Polymarket F1 trades via the Data API (not the blockchain).

Discovers F1 markets through the Gamma `tag_slug=f1` events endpoint, then fetches
trades per market from the Data API (`/trades?market=<conditionId>`). Trades are
written with both their native Data API fields and synthesized
`maker_asset_id`/`taker_asset_id`/`maker_amount`/`taker_amount` columns so the
existing Polymarket calibration analyses run over the output unmodified.
"""

from dataclasses import asdict
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

MARKETS_DIR = Path("data/polymarket/f1/markets")
TRADES_DIR = Path("data/polymarket/f1/trades")
CURSOR_FILE = Path("data/polymarket/f1/.f1_trades_cursor")

BATCH_SIZE = 10000


def discover_f1_markets(http: HttpClient, active_only: bool = False) -> list[Market]:
    """Discover F1 markets via Gamma `GET /events?tag_slug=f1`.

    Paginates events (open and closed), expands the nested `markets[]` of each
    event into `Market` objects, and dedupes by `condition_id`.

    Args:
        http: Shared HTTP client.
        active_only: If True, keep only active, non-closed markets (used by the
            live CLOB recorder in Part 2 to avoid dead books).

    Returns:
        Deduplicated list of F1 `Market` objects.
    """
    markets: dict[str, Market] = {}
    offset = 0
    limit = 100

    while True:
        data: Union[dict, list] = http.get(
            f"{GAMMA_API_URL}/events",
            params={"tag_slug": "f1", "limit": limit, "offset": offset},
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


class PolymarketF1TradesIndexer(Indexer):
    """Fetches F1 trades from the Polymarket Data API into parquet files."""

    def __init__(self):
        super().__init__(
            name="polymarket_f1_trades",
            description="Fetches F1 trades via the Polymarket Data API to parquet files",
        )
        self.http = HttpClient(rate_limit=10)

    def run(self) -> None:
        MARKETS_DIR.mkdir(parents=True, exist_ok=True)
        TRADES_DIR.mkdir(parents=True, exist_ok=True)
        CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: discover F1 markets.
            print("Discovering F1 markets via Gamma...")
            markets = discover_f1_markets(self.http)
            print(f"Found {len(markets)} F1 markets")

            # Step 2: write F1 markets table (resolution source for analyses).
            self._write_markets(markets)

            # Step 3 + 4: fetch trades per market and write in batches.
            self._fetch_trades(markets)
        finally:
            self.http.close()

    def _write_markets(self, markets: list[Market]) -> None:
        fetched_at = datetime.utcnow()
        records = []
        for market in markets:
            record = asdict(market)
            record["_fetched_at"] = fetched_at
            records.append(record)
        path = MARKETS_DIR / "markets_f1.parquet"
        pd.DataFrame(records).to_parquet(path)
        print(f"Saved {len(records)} markets to {path}")

    def _fetch_trades(self, markets: list[Market]) -> None:
        done = self._load_cursor()
        pending = [m for m in markets if m.condition_id not in done]
        if done:
            print(f"Resuming: {len(done)} markets already complete, {len(pending)} remaining")

        all_trades: list[dict] = []
        total_saved = 0
        interrupted = False

        def get_next_chunk_idx() -> int:
            existing = list(TRADES_DIR.glob("trades_*.parquet"))
            indices = []
            for f in existing:
                parts = f.stem.split("_")
                if len(parts) >= 2:
                    try:
                        indices.append(int(parts[1]))
                    except ValueError:
                        pass
            return max(indices) + BATCH_SIZE if indices else 0

        def save_batch(batch: list[dict]) -> None:
            nonlocal total_saved
            if not batch:
                return
            chunk_idx = get_next_chunk_idx()
            path = TRADES_DIR / f"trades_{chunk_idx}_{chunk_idx + BATCH_SIZE}.parquet"
            pd.DataFrame(batch).to_parquet(path)
            total_saved += len(batch)
            tqdm.write(f"Saved {len(batch)} trades to {path.name}")

        try:
            for market in tqdm(pending, desc="Fetching F1 trades", unit=" market"):
                for trade in self._fetch_market_trades(market.condition_id):
                    all_trades.append(trade)

                while len(all_trades) >= BATCH_SIZE:
                    save_batch(all_trades[:BATCH_SIZE])
                    all_trades = all_trades[BATCH_SIZE:]

                self._append_cursor(market.condition_id)
        except KeyboardInterrupt:
            interrupted = True
            print("\nInterrupted. Progress saved.")

        if all_trades:
            save_batch(all_trades)

        if not interrupted and CURSOR_FILE.exists():
            CURSOR_FILE.unlink()

        print(f"\nDone: {total_saved} F1 trades saved")

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

    @staticmethod
    def _load_cursor() -> set[str]:
        if not CURSOR_FILE.exists():
            return set()
        return {line.strip() for line in CURSOR_FILE.read_text().splitlines() if line.strip()}

    @staticmethod
    def _append_cursor(condition_id: str) -> None:
        with CURSOR_FILE.open("a") as f:
            f.write(f"{condition_id}\n")
