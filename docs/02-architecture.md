# Architecture — Railway OPC UA Microservice

> **Audience:** Developers (Ahmad, Andy, and contributors).
> This document explains the architecture, design decisions, and data flow.

---

## System overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Railway Field Hardware                           │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Rail Temp   │  │ Wheel Vibration  │  │  Brake Pressure Sensor   │   │
│  │ Sensor      │  │ Sensor           │  │                          │   │
│  └──────┬──────┘  └────────┬─────────┘  └────────────┬─────────────┘   │
│         └─────────────────┬┘                          │                 │
│                    ┌──────▼──────────────────────────┐                 │
│                    │     OPC UA Server               │                 │
│                    │  opc.tcp://hardware:4840        │                 │
│                    └──────────────┬─────────────────-┘                 │
└──────────────────────────────────-│──────────────────────────────────--┘
                                    │  OPC UA TCP Binary
                                    │  Subscription + DataChangeNotification
                                    ▼
┌───────────────────────────────────────────────────────────────────────-─┐
│                  opcua-railway-python (this service)                     │
│                                                                          │
│  ┌──────────────────────┐        ┌──────────────────────┐               │
│  │   opcua_client.py    │        │       db.py           │               │
│  │  asyncua subscription│──────▶ │  asyncpg pool        │               │
│  │  DataChangeHandler   │  write │  INSERT sensor_readings│              │
│  └──────────┬───────────┘        └──────────────────────┘               │
│             │ publish                                                     │
│             ▼                           ┌───────────────────────────┐    │
│  ┌──────────────────────┐               │      TimescaleDB          │    │
│  │       sse.py         │◀─────────────▶│  sensor_readings          │    │
│  │  SSEBroker           │  async query  │  readings_1min (view)     │    │
│  │  asyncio.Queue×N     │               └───────────────────────────┘    │
│  └──────────┬───────────┘                                                │
│             │                                                             │
│  ┌──────────▼───────────────────────────────────────────────────────┐    │
│  │                        main.py (FastAPI)                          │    │
│  │  GET /stream          → StreamingResponse (SSE)                   │    │
│  │  GET /readings        → historical raw query                      │    │
│  │  GET /readings/aggregate → 1-min rollup query                     │    │
│  │  GET /sensors         → sensor list                               │    │
│  │  GET /health          → DB + broker status                        │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────-──┘
                                    │  HTTP / SSE
              ┌─────────────────────┼─────────────────────────┐
              ▼                     ▼                         ▼
    ┌──────────────────┐  ┌──────────────────┐   ┌──────────────────────┐
    │  Browser / SPA   │  │  Grafana          │   │  Other microservices │
    │  EventSource     │  │  (TimescaleDB     │   │  REST consumers      │
    │  Chart.js        │  │   data source)    │   │                      │
    └──────────────────┘  └──────────────────┘   └──────────────────────┘
```

---

## Design decisions

### Why FastAPI?

| Concern | Decision | Rationale |
|---|---|---|
| Async runtime | `asyncio` (single-threaded event loop) | OPC UA callbacks + DB writes + SSE fan-out are all I/O-bound — asyncio handles thousands of concurrent SSE clients without threads |
| HTTP framework | FastAPI | Native async, automatic OpenAPI docs at `/docs`, Pydantic validation, `StreamingResponse` for SSE |
| OPC UA client | `asyncua` | Only mature async-native OPC UA Python library; integrates directly with asyncio |
| DB driver | `asyncpg` | Fastest async PostgreSQL driver for Python; native binary protocol |
| Settings | `pydantic-settings` | Env var loading with type validation; same pattern as FastAPI models |

### Why SSE over WebSockets?

Sensor data flows **one direction only**: hardware → service → browser.

| Criterion | SSE | WebSocket |
|---|---|---|
| Direction | Server → Client (unidirectional) | Bidirectional |
| Protocol | Plain HTTP/1.1 | Upgrade handshake |
| Auto-reconnect | Built into browser `EventSource` | Manual implementation |
| Proxy support | Works through any HTTP proxy | Requires proxy upgrade support |
| Complexity | Minimal | Higher |
| Use case fit | ✅ Sensor feed | ❌ Overkill |

### Why TimescaleDB?

| Concern | TimescaleDB | MongoDB |
|---|---|---|
| Data shape | `(time, sensor_id, value)` — perfectly tabular | Flexible documents — unnecessary here |
| Time-range queries | Chunk pruning — only scans relevant time partitions | Full collection scan without manual sharding |
| Aggregations | Continuous aggregates — pre-computed, near-zero query cost | Aggregation pipeline — computed on-demand |
| Compression | ~90% columnar compression on append-only data | No comparable compression |
| SQL | Full SQL — `WHERE time > NOW() - INTERVAL '1 hour'` | MongoDB Query Language |
| Ecosystem | Grafana native data source, pgAdmin, psql | Separate toolchain |
| When MongoDB wins | Variable/nested/unstructured event schemas | — |

---

## Runtime concurrency model

```
asyncio event loop (single thread)
│
├── Task: opcua_client.run()
│     └── asyncua internal tasks (TCP keepalive, Publish loop)
│           └── on DataChangeNotification:
│                 loop.create_task(_handle_reading(reading))
│                       ├── broker.publish(reading)   [puts on each SSE queue]
│                       └── db.insert_reading(...)    [asyncpg execute]
│
├── Task: uvicorn HTTP server
│     ├── GET /stream → event_generator()
│     │     └── asyncio.wait_for(queue.get(), timeout=15)  [yields SSE frames]
│     ├── GET /readings → db.query_readings()
│     └── GET /health   → db.health_check()
```

**No threads.** All I/O is async. The event loop multiplexes between OPC UA
callbacks, DB writes, and SSE fan-out without blocking.

### Slow SSE consumer protection

Each SSE client queue has `maxsize=256`. If a client is slow:
1. The oldest reading is dropped (`queue.get_nowait()`)
2. The newest is inserted (`queue.put_nowait()`)

This prevents one stuck browser tab from back-pressuring the OPC UA ingest path.

---

## Reconnection strategy

The OPC UA client reconnects automatically on any connection error:

```
attempt 1 → wait 2s
attempt 2 → wait 4s
attempt 3 → wait 8s
...
max wait  → 60s
```

On reconnection, the subscription and all MonitoredItems are re-created from scratch.
The SSE broker and DB writer remain active — SSE clients see a brief gap and then
resume receiving data.

---

## Database schema design

```sql
-- Hypertable: automatically partitioned by time (default 7-day chunks)
sensor_readings (
    time        TIMESTAMPTZ    -- partition key
    sensor_id   TEXT           -- which sensor
    node_id     TEXT           -- OPC UA NodeId string
    value       DOUBLE PRECISION
    unit        TEXT
    quality     SMALLINT       -- 0 = Good, non-zero = Bad/Uncertain
)

-- Index: optimised for "give me all readings for sensor X, newest first"
INDEX (sensor_id, time DESC)

-- Continuous aggregate: 1-minute rollups, refreshed every minute
readings_1min (
    bucket      TIMESTAMPTZ    -- time_bucket('1 minute', time)
    sensor_id   TEXT
    avg_value   DOUBLE PRECISION
    min_value   DOUBLE PRECISION
    max_value   DOUBLE PRECISION
    sample_count BIGINT
)
-- WHERE quality = 0 — only Good readings included in rollups
```

---

## Deployment topology

```
docker-compose.yml
├── timescaledb        port 5432   TimescaleDB + auto-init via init.sql
├── python-service     port 8080   This service
└── go-service         port 8081   Go implementation (same API)
```

Both services write to the **same** TimescaleDB instance and expose the **same**
REST/SSE API — they are interchangeable. Run both for comparison, or pick one
for production.

---

## Roadmap

| Priority | Feature | Notes |
|---|---|---|
| High | OPC UA `SignAndEncrypt` | See `04-security.md` |
| High | Username/password or X.509 auth on OPC UA session | |
| Medium | Alerting via SSE `event: alert` | Threshold-based |
| Medium | TimescaleDB retention policy (auto-drop old chunks) | Uncomment in `init.sql` |
| Medium | TimescaleDB compression policy | Uncomment in `init.sql` |
| Low | Grafana dashboard | Native TimescaleDB data source |
| Low | Prometheus metrics endpoint | `GET /metrics` |
| Low | Multi-server support | Multiple OPC UA endpoints per service instance |
