-- Trade count per timestamp, ordered in time.
-- Volume-over-time view: how many trades share each 1-second Unix timestamp.
SELECT
    to_timestamp(timestamp) AS ts,
    count(*) AS n_trades
FROM read_parquet('data/polymarket/f1/trades/*.parquet')
GROUP BY timestamp
ORDER BY timestamp
