"""F1-scoped win rate by price calibration analysis.

Thin subclass of `PolymarketWinRateByPriceAnalysis` that defaults to the F1 data
dirs. The legacy/FPMM dir does not exist for F1 CTF markets, so that branch is
skipped cleanly. Dir overrides are still accepted (e.g. for tests).
"""

from __future__ import annotations

from pathlib import Path

from src.analysis.polymarket.polymarket_win_rate_by_price import PolymarketWinRateByPriceAnalysis


class F1WinRateByPriceAnalysis(PolymarketWinRateByPriceAnalysis):
    """Win rate vs price calibration over F1 trades."""

    def __init__(
        self,
        trades_dir: Path | str | None = None,
        legacy_trades_dir: Path | str | None = None,
        markets_dir: Path | str | None = None,
        collateral_lookup_path: Path | str | None = None,
    ):
        f1 = Path(__file__).parent.parent.parent.parent / "data" / "polymarket" / "f1"
        super().__init__(
            trades_dir=trades_dir or f1 / "trades",
            legacy_trades_dir=legacy_trades_dir or f1 / "legacy_trades",
            markets_dir=markets_dir or f1 / "markets",
            collateral_lookup_path=collateral_lookup_path,
        )
        self.name = "f1_win_rate_by_price"
        self.description = "F1 win rate vs price market calibration analysis"
