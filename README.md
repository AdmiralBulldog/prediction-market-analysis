# Prediction Market Analysis

A framework for analyzing prediction market data, including the largest publicly available dataset of Polymarket and Kalshi market and trade data. Provides tools for data collection, storage, and running analysis scripts that generate figures and statistics.

## Overview

This project enables research and analysis of prediction markets by providing:
- Pre-collected datasets from Polymarket and Kalshi
- Data collection indexers for gathering new data
- Analysis framework for generating figures and statistics

Currently supported features:
- Market metadata collection (Kalshi & Polymarket)
- Trade history collection via API and blockchain
- Parquet-based storage with automatic progress saving
- Extensible analysis script framework

## Installation & Usage

Requires Python 3.9+. Install dependencies with [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

Download and extract the pre-collected dataset (36GiB compressed):

```bash
make setup
```

This downloads `data.tar.zst` from [Cloudflare R2 Storage](https://s3.jbecker.dev/data.tar.zst) and extracts it to `data/`.

### Data Collection

Collect market and trade data from prediction market APIs:

```bash
make index
```

This opens an interactive menu to select which indexer to run. Data is saved to `data/kalshi/` and `data/polymarket/` directories. Progress is saved automatically, so you can interrupt and resume collection.

#### Tag-scoped trades (e.g. Formula 1)

You can collect trades for a single category of markets (identified by a Polymarket
[Gamma](https://docs.polymarket.com/) tag, such as `f1`) instead of the whole market.
Both methods discover the tag's markets via `GET /events?tag_slug=<tag>` and write to
`data/polymarket/<tag>/` (`markets/` plus `trades/`), so the same analyses work on the
output. There are two methods with different trade-offs:

| | **Data API** (`polymarket_f1_trades`) | **Blockchain** (`polymarket_f1_chain_trades`) |
|---|---|---|
| Speed | Fast (direct per-market queries) | Slow (full-chain scan of every `OrderFilled` event) |
| Requirements | None | `POLYGON_RPC` archive endpoint (set in `.env`) |
| Completeness | Most recent ~3500 trades per market (Data API offset cap) | Complete history, no per-market cap |
| Best for | Quick/recent data; markets under ~3500 trades | Full history of high-volume markets |

> The blockchain has to scan everything because the `OrderFilled` event does not index
> the market/token ID, so the RPC cannot filter by market — the indexer pulls all trades
> and keeps only those belonging to the tag's markets. Prefer the Data API unless you
> specifically need complete history.

**Method 1 — Data API:**

```bash
make index   # then select "Polymarket F1 Trades"
```

Resumable: completed markets are tracked in `data/polymarket/f1/.data_api_trades_cursor`,
so you can interrupt with `Ctrl+C` and rerun to continue.

**Method 2 — Blockchain (complete history):**

```bash
make index   # then select "Polymarket F1 Chain Trades"
```

Resumable by block via `data/polymarket/f1/.chain_trades_block_cursor`. Requires a
`POLYGON_RPC` archive node URL in your `.env`.

> **Use one method per tag.** Both write to `data/polymarket/<tag>/trades/` but with
> different column sets, so if you switch methods, delete that `trades/` directory first.

See [Data Schemas](docs/SCHEMAS.md#polymarket-f1-data-api-trades) for the column details.
Once data is collected, run the F1-scoped calibration analyses:

```bash
make run f1_win_rate_by_price
make run f1_calibration_by_bucket
```

#### Adding another filter (e.g. "elections")

Each method is a thin subclass that only sets a `tag_slug`. To scope a new tag, add the
indexer subclass(es) you want — using any valid Gamma tag slug:

```python
# src/indexers/polymarket/elections_trades.py
from src.indexers.polymarket.f1_data_api import TagDataApiTradesIndexer
from src.indexers.polymarket.chain_filtered_trades import TagFilteredChainTradesIndexer


class ElectionsDataApiTradesIndexer(TagDataApiTradesIndexer):
    @property
    def tag_slug(self) -> str:
        return "elections"

    def __init__(self):
        super().__init__(name="polymarket_elections_trades", description="Elections trades via Data API")


class ElectionsChainTradesIndexer(TagFilteredChainTradesIndexer):
    @property
    def tag_slug(self) -> str:
        return "elections"

    def __init__(self):
        super().__init__(name="polymarket_elections_chain_trades", description="Elections trades via blockchain")
```

They are auto-discovered by `make index` and write to `data/polymarket/elections/`. To run
the calibration analyses on the new tag, add matching analysis subclasses (copy
`src/analysis/polymarket/f1_win_rate_by_price.py` and `f1_calibration_by_bucket.py`,
pointing their dirs at `data/polymarket/elections`).

### Running Analyses

```bash
make analyze
```

This opens an interactive menu to select which analysis to run. You can run all analyses or select a specific one. Output files (PNG, PDF, CSV, JSON) are saved to `output/`.

### Packaging Data

To compress the data directory for storage/distribution:

```bash
make package
```

This creates a zstd-compressed tar archive (`data.tar.zst`) and removes the `data/` directory.

## Project Structure

```
├── src/
│   ├── analysis/           # Analysis scripts
│   │   ├── kalshi/         # Kalshi-specific analyses
│   │   └── polymarket/     # Polymarket-specific analyses
│   ├── indexers/           # Data collection indexers
│   │   ├── kalshi/         # Kalshi API client and indexers
│   │   └── polymarket/     # Polymarket API/blockchain indexers
│   └── common/             # Shared utilities and interfaces
├── data/                   # Data directory (extracted from data.tar.zst)
│   ├── kalshi/
│   │   ├── markets/
│   │   └── trades/
│   └── polymarket/
│       ├── blocks/
│       ├── markets/
│       ├── trades/
│       └── f1/            # F1 trades fetched via the Data API
│           ├── markets/
│           └── trades/
├── docs/                   # Documentation
└── output/                 # Analysis outputs (figures, CSVs)
```

## Documentation

- [Data Schemas](docs/SCHEMAS.md) - Parquet file schemas for markets and trades
- [Writing Analyses](docs/ANALYSIS.md) - Guide for writing custom analysis scripts

## Contributing

If you'd like to contribute to this project, please open a pull-request with your changes, as well as detailed information on what is changed, added, or improved.

For more information, see the [contributing guide](CONTRIBUTING.md).

## Issues

If you've found an issue or have a question, please open an issue [here](https://github.com/jon-becker/prediction-market-analysis/issues).

## Research & Citations

- Becker, J. (2026). _The Microstructure of Wealth Transfer in Prediction Markets_. Jbecker. https://jbecker.dev/research/prediction-market-microstructure
- Le, N. A. (2026). _Decomposing Crowd Wisdom: Domain-Specific Calibration Dynamics in Prediction Markets_. arXiv. https://arxiv.org/abs/2602.19520
- Akey P., Gregoire, V., Harvie, N., Martineau, C. (2026). _Who Wins and Who Loses In Prediction Markets? Evidence from Polymarket_. SSRN. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6443103
- Vedova, J. (2026). _Who Profits from Prediction Markets? Execution, not Information_. SSRN. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6191618

If you have used or plan to use this dataset in your research, please reach out via [email](mailto:jonathan@jbecker.dev) or [Twitter](https://x.com/BeckerrJon) -- i'd love to hear about what you're using the data for! Additionally, feel free to open a PR and update this section with a link to your paper.
