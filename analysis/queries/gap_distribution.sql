-- Gap (in seconds) between consecutive distinct trade timestamps.
-- Directly answers "are trades on a ~2s cadence?": a spike at gap=2 (and its
-- even multiples) means timestamps track Polygon's ~2s block time.
WITH ts AS (
    SELECT DISTINCT timestamp
    FROM read_parquet('data/polymarket/f1/trades/*.parquet')
),
gaps AS (
    SELECT timestamp - lag(timestamp) OVER (ORDER BY timestamp) AS gap_seconds
    FROM ts
)
SELECT gap_seconds, count(*) AS n_occurrences
FROM gaps
WHERE gap_seconds IS NOT NULL
GROUP BY gap_seconds
ORDER BY gap_seconds
