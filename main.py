"""
Railway OPC UA microservice — Python implementation.

Endpoints
---------
GET /stream                              SSE live feed
GET /readings?sensor_id=&from=&to=&limit=  Historical raw readings
GET /readings/aggregate?sensor_id=&bucket=1min&from=&to=  Aggregated view
GET /sensors                             List known sensor IDs
GET /health                              Health probe
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

import db
import opcua_client
from config import settings
from sse import broker, event_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db.init_pool(settings.db_dsn)
    task = asyncio.create_task(opcua_client.run(), name="opcua-client")
    log.info("Service started on port %d", settings.port)
    yield
    # Shutdown
    await opcua_client.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await db.close_pool()
    log.info("Service stopped")


app = FastAPI(
    title="Railway OPC UA Service",
    description="Live OPC UA sensor data over SSE + TimescaleDB persistence",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/stream", summary="SSE live sensor feed")
async def stream(request: Request):
    """
    Server-Sent Events endpoint. Each event has type `reading`.

        event: reading
        data: {"sensor_id":"rail_temp_1","value":42.3,"unit":"°C","time":"..."}
    """
    return StreamingResponse(
        event_generator(broker, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable Nginx buffering
        },
    )


@app.get("/readings", summary="Historical raw readings")
async def readings(
    sensor_id: str = Query(..., description="Sensor ID to query"),
    from_: datetime = Query(
        default=None,
        alias="from",
        description="Start time (ISO-8601, UTC). Defaults to 1 hour ago.",
    ),
    to: datetime = Query(
        default=None,
        description="End time (ISO-8601, UTC). Defaults to now.",
    ),
    limit: int = Query(default=5000, ge=1, le=50000),
):
    now = datetime.now(tz=timezone.utc)
    from_time = from_ or (now - timedelta(hours=1))
    to_time = to or now
    # Ensure timezone-aware
    if from_time.tzinfo is None:
        from_time = from_time.replace(tzinfo=timezone.utc)
    if to_time.tzinfo is None:
        to_time = to_time.replace(tzinfo=timezone.utc)

    rows = await db.query_readings(sensor_id, from_time, to_time, limit)
    # Serialise datetime objects
    for r in rows:
        if isinstance(r.get("time"), datetime):
            r["time"] = r["time"].isoformat()
    return JSONResponse({
        "sensor_id": sensor_id,
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "count": len(rows),
        "readings": rows,
    })


@app.get("/readings/aggregate", summary="Pre-aggregated readings (1-min buckets)")
async def readings_aggregate(
    sensor_id: str = Query(...),
    bucket: str = Query(default="1min", description="Aggregation bucket. Currently: '1min'"),
    from_: datetime = Query(default=None, alias="from"),
    to: datetime = Query(default=None),
):
    if bucket not in ("1min",):
        raise HTTPException(status_code=400, detail="Supported buckets: 1min")

    now = datetime.now(tz=timezone.utc)
    from_time = from_ or (now - timedelta(hours=1))
    to_time = to or now
    if from_time.tzinfo is None:
        from_time = from_time.replace(tzinfo=timezone.utc)
    if to_time.tzinfo is None:
        to_time = to_time.replace(tzinfo=timezone.utc)

    rows = await db.query_aggregate(sensor_id, bucket, from_time, to_time)
    for r in rows:
        if isinstance(r.get("bucket"), datetime):
            r["bucket"] = r["bucket"].isoformat()
    return JSONResponse({
        "sensor_id": sensor_id,
        "bucket": bucket,
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "count": len(rows),
        "data": rows,
    })


@app.get("/sensors", summary="List known sensor IDs")
async def sensors():
    ids = await db.list_sensors()
    return JSONResponse({"sensors": ids})


@app.get("/health", summary="Health probe")
async def health():
    db_ok = await db.health_check()
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ok" if db_ok else "degraded",
            "db": db_ok,
            "sse_clients": broker.client_count,
        },
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
