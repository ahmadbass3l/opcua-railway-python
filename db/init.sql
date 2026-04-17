-- TimescaleDB schema for the railway OPC UA sensor service
-- Mounted as /docker-entrypoint-initdb.d/init.sql in the TimescaleDB container

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── Main hypertable ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sensor_readings (
    time        TIMESTAMPTZ        NOT NULL,
    sensor_id   TEXT               NOT NULL,
    node_id     TEXT               NOT NULL,
    value       DOUBLE PRECISION   NOT NULL,
    unit        TEXT,
    -- OPC UA StatusCode: 0 = Good, non-zero = Bad/Uncertain
    quality     SMALLINT           NOT NULL DEFAULT 0
);

SELECT create_hypertable('sensor_readings', 'time', if_not_exists => TRUE);

-- Most common query shape: one sensor, most recent first
CREATE INDEX IF NOT EXISTS idx_sensor_time
    ON sensor_readings (sensor_id, time DESC);

-- ── Continuous aggregate: 1-minute rollups ────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS readings_1min
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', time) AS bucket,
    sensor_id,
    AVG(value)   AS avg_value,
    MIN(value)   AS min_value,
    MAX(value)   AS max_value,
    COUNT(*)     AS sample_count
FROM sensor_readings
WHERE quality = 0          -- only Good-quality readings in the rollup
GROUP BY bucket, sensor_id
WITH NO DATA;

-- Refresh policy: keep last 24 h of buckets up to date, run every minute
SELECT add_continuous_aggregate_policy('readings_1min',
    start_offset     => INTERVAL '1 day',
    end_offset       => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists    => TRUE
);

-- ── Compression (enable after data is a few days old) ─────────────────────────
-- Uncomment when you are ready to enable columnar compression:
-- ALTER TABLE sensor_readings SET (
--     timescaledb.compress,
--     timescaledb.compress_segmentby = 'sensor_id'
-- );
-- SELECT add_compression_policy('sensor_readings', INTERVAL '7 days', if_not_exists => TRUE);

-- ── Retention (optional — drop chunks older than 90 days) ─────────────────────
-- SELECT add_retention_policy('sensor_readings', INTERVAL '90 days', if_not_exists => TRUE);
