"""Tag-scoped Polymarket trade fetching from the Polygon blockchain.

`OrderFilled` events do not index the asset/token IDs, so the RPC cannot filter
by market — this indexer scans the full chain (both CTF exchanges) and keeps only
trades whose maker/taker asset ID belongs to a market of the target Gamma tag.
Compared to the Data API indexer it is far slower (a whole-chain scan, requires a
`POLYGON_RPC` archive endpoint) but returns complete history with no per-market cap.

Output is written to the same `data/polymarket/<tag>/trades` location and schema
as the blockchain `polymarket_trades` indexer, so the existing calibration
analyses run over it unmodified. Use one fetch method per tag; if switching
methods, delete the tag's `trades/` directory first (the two methods write
different column sets).

To scope a new tag, add a thin subclass of `TagFilteredChainTradesIndexer` (see
`PolymarketF1ChainTradesIndexer`).
"""

import json
from abc import abstractmethod
from dataclasses import asdict
from datetime import datetime

import pandas as pd
from tqdm import tqdm

from src.common.client import HttpClient
from src.common.indexer import Indexer
from src.indexers.polymarket.blockchain import (
    CTF_EXCHANGE,
    NEGRISK_CTF_EXCHANGE,
    POLYMARKET_START_BLOCK,
    PolygonClient,
)
from src.indexers.polymarket.f1_data_api import discover_markets, tag_data_dir, write_markets

BATCH_SIZE = 10000
CHUNK_SIZE = 1000


class TagFilteredChainTradesIndexer(Indexer):
    """Base indexer: scan the Polygon blockchain, keep only a tag's trades.

    Concrete subclasses set the `tag_slug` class attribute (which makes them
    non-abstract and therefore discoverable by the `index` menu).
    """

    @property
    @abstractmethod
    def tag_slug(self) -> str:
        """Gamma tag slug this indexer scopes to."""

    def __init__(self, name: str, description: str):
        super().__init__(name=name, description=description)
        base = tag_data_dir(self.tag_slug)
        self.markets_dir = base / "markets"
        self.trades_dir = base / "trades"
        self.cursor_file = base / ".chain_trades_block_cursor"

    def run(self) -> None:
        self.trades_dir.mkdir(parents=True, exist_ok=True)
        self.cursor_file.parent.mkdir(parents=True, exist_ok=True)

        # Resolve the allowed token-id set from the tag's markets (and persist
        # the markets table the analyses use for resolution).
        with HttpClient(rate_limit=10) as http:
            print(f"Discovering '{self.tag_slug}' markets via Gamma...")
            markets = discover_markets(http, self.tag_slug)
        print(f"Found {len(markets)} markets")
        write_markets(markets, self.markets_dir)

        allowed = self._token_id_set(markets)
        if not allowed:
            print(f"No token IDs found for tag '{self.tag_slug}'; nothing to fetch.")
            return
        print(f"Filtering chain trades to {len(allowed)} token IDs")

        client = PolygonClient()
        to_block = client.get_block_number()
        from_block = self._resume_block()
        print(f"Scanning blocks {from_block:,} -> {to_block:,}")

        ranges = []
        current = from_block
        while current <= to_block:
            end = min(current + CHUNK_SIZE - 1, to_block)
            ranges.append((current, end))
            current = end + 1

        contracts = [("CTF Exchange", CTF_EXCHANGE), ("NegRisk CTF Exchange", NEGRISK_CTF_EXCHANGE)]
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
            tqdm.write(f"Saved {len(batch)} {self.tag_slug} trades to {path.name}")

        pbar = tqdm(total=len(ranges), desc="Scanning", unit=" chunk")
        try:
            for start, end in ranges:
                fetched_at = datetime.utcnow()
                for contract_name, contract_address in contracts:
                    trades, _, _ = client._fetch_chunk(start, end, contract_address)
                    for trade in trades:
                        if str(trade.maker_asset_id) in allowed or str(trade.taker_asset_id) in allowed:
                            row = asdict(trade)
                            row["maker_asset_id"] = str(row["maker_asset_id"])
                            row["taker_asset_id"] = str(row["taker_asset_id"])
                            row["_fetched_at"] = fetched_at
                            row["_contract"] = contract_name
                            all_trades.append(row)

                pbar.update(1)
                pbar.set_postfix(block=end, buffer=len(all_trades), saved=total_saved)

                while len(all_trades) >= BATCH_SIZE:
                    save_batch(all_trades[:BATCH_SIZE])
                    all_trades = all_trades[BATCH_SIZE:]

                self.cursor_file.write_text(str(end))
        except KeyboardInterrupt:
            interrupted = True
            print("\nInterrupted. Progress saved.")
        finally:
            pbar.close()

        if all_trades:
            save_batch(all_trades)

        if not interrupted and self.cursor_file.exists():
            self.cursor_file.unlink()

        print(f"\nDone: {total_saved} {self.tag_slug} trades saved")

    @staticmethod
    def _token_id_set(markets) -> set[str]:
        allowed: set[str] = set()
        for market in markets:
            try:
                token_ids = json.loads(market.clob_token_ids) if market.clob_token_ids else []
            except (json.JSONDecodeError, TypeError):
                continue
            for tid in token_ids:
                if tid:
                    allowed.add(str(tid))
        return allowed

    def _resume_block(self) -> int:
        if self.cursor_file.exists():
            try:
                block = int(self.cursor_file.read_text().strip())
                print(f"Resuming from block {block}")
                return block
            except (ValueError, TypeError):
                pass
        return POLYMARKET_START_BLOCK

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


class PolymarketF1ChainTradesIndexer(TagFilteredChainTradesIndexer):
    """Fetches complete F1 trade history from the Polygon blockchain (filtered)."""

    @property
    def tag_slug(self) -> str:
        return "f1"

    def __init__(self):
        super().__init__(
            name="polymarket_f1_chain_trades",
            description="Fetches complete F1 trade history from Polygon (full-chain scan, filtered)",
        )
