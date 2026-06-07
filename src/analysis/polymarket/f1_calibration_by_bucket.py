"""F1-scoped calibration-by-bucket analysis.

Thin subclass of `PolymarketCalibrationByBucketAnalysis` that points at the F1
Data API output dirs. The legacy/FPMM dir does not exist for F1 CTF markets, so
that branch is skipped cleanly.
"""

from __future__ import annotations

from pathlib import Path

from src.analysis.polymarket.polymarket_calibration_by_bucket import PolymarketCalibrationByBucketAnalysis


class F1CalibrationByBucketAnalysis(PolymarketCalibrationByBucketAnalysis):
    """Calibration curve by decile probability bucket over F1 Data API trades."""

    def __init__(self):
        base_dir = Path(__file__).parent.parent.parent.parent
        f1 = base_dir / "data" / "polymarket" / "f1"
        super().__init__(
            trades_dir=f1 / "trades",
            legacy_trades_dir=f1 / "legacy_trades",
            markets_dir=f1 / "markets",
        )
        self.name = "f1_calibration_by_bucket"
        self.description = "F1 calibration curve grouped by decile probability buckets"
