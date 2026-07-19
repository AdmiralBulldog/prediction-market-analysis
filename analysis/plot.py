"""Render an interactive Plotly chart from a saved DuckDB query.

The .sql files are self-contained (they read the parquet directly), so to just see
rows use the DuckDB CLI instead:  duckdb -c ".read analysis/queries/<file>.sql"
This script is only for plotting. x/y default to the first two result columns.

Example:
    python analysis/plot.py analysis/queries/gap_distribution.sql
    python analysis/plot.py analysis/queries/trades_per_timestamp.sql --chart line
"""

import argparse
from pathlib import Path

import duckdb
import plotly.express as px


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("query", type=Path, help="Path to a .sql file")
    p.add_argument("--x", help="Column for x-axis (default: first result column)")
    p.add_argument("--y", help="Column for y-axis (default: second result column)")
    p.add_argument("--chart", choices=["bar", "line"], default="bar")
    p.add_argument("--out", type=Path, help="Output HTML (default: output/<query>.html)")
    args = p.parse_args()

    df = duckdb.connect().execute(args.query.read_text()).df()

    x = args.x or df.columns[0]
    y = args.y or df.columns[1]
    title = args.query.stem.replace("_", " ")

    fig = (px.bar if args.chart == "bar" else px.line)(df, x=x, y=y, title=title)

    out = args.out or Path("output") / f"{args.query.stem}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(out)
    print(f"{len(df)} rows -> {out}")


if __name__ == "__main__":
    main()
