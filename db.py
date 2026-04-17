"""
TimescaleDB writer — asyncpg-based.
Provides a pool, insert helper, and query helpers.
"""
import asyncpg
import logging
from typing import Optional
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_pool(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    log.info("DB pool created")


async def close_pool() -> None:
    if _pool:
        await _pool.close()
        log.info("DB pool closed")


async def insert_reading(
    sensor_id: str,
    node_id: str,
    value: float,
    unit: str,
    quality: int,
    time: datetime,
) -> None:
    assert _pool, "DB pool not initialised"
    await _pool.execute(
        """
        INSERT INTO sensor_readings (time, sensor_id, node_id, value, unit, quality)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        time,
        sensor_id,
        node_id,
        value,
        unit,
        quality,
    )


async def query_readings(
    sensor_id: str,
    from_time: datetime,
    to_time: datetime,
    limit: int = 5000,
) -> list[dict]:
    assert _pool, "DB pool not initialised"
    rows = await _pool.fetch(
        """
        SELECT time, sensor_id, node_id, value, unit, quality
        FROM sensor_readings
        WHERE sensor_id = $1
          AND time >= $2
          AND time <= $3
        ORDER BY time ASC
        LIMIT $4
        """,
        sensor_id,
        from_time,
        to_time,
        limit,
    )
    return [dict(r) for r in rows]


async def query_aggregate(
    sensor_id: str,
    bucket: str,
    from_time: datetime,
    to_time: datetime,
) -> list[dict]:
    """Read from the 1-min continuous aggregate view."""
    assert _pool, "DB pool not initialised"
    # Only '1min' supported for now; extend with '1hour' view later
    view = "readings_1min"
    rows = await _pool.fetch(
        f"""
        SELECT bucket, sensor_id, avg_value, min_value, max_value
        FROM {view}
        WHERE sensor_id = $1
          AND bucket >= $2
          AND bucket <= $3
        ORDER BY bucket ASC
        """,
        sensor_id,
        from_time,
        to_time,
    )
    return [dict(r) for r in rows]


async def list_sensors() -> list[str]:
    assert _pool, "DB pool not initialised"
    rows = await _pool.fetch(
        "SELECT DISTINCT sensor_id FROM sensor_readings ORDER BY sensor_id"
    )
    return [r["sensor_id"] for r in rows]


async def health_check() -> bool:
    try:
        assert _pool
        await _pool.fetchval("SELECT 1")
        return True
    except Exception:
        return False
