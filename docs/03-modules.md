# Module Reference — opcua-railway-python

> **Audience:** Developers maintaining or extending the Python service.
> Every module is described: its purpose, public interface, and key design choices.

---

## File layout

```
opcua-railway-python/
├── main.py              Entry point — FastAPI app, routes, lifespan
├── opcua_client.py      OPC UA subscription client (asyncua)
├── sse.py               SSE broker — fan-out to connected HTTP clients
├── db.py                TimescaleDB pool, write, and query helpers
├── config.py            Environment variable loading (pydantic-settings)
├── requirements.txt     Python dependencies
├── Dockerfile           Container image definition
├── docker-compose.yml   Full stack: TimescaleDB + this service + Go service
└── docs/                This documentation
```

---

## `config.py` — Configuration

**Purpose:** Single source of truth for all runtime configuration.
Reads from environment variables (or `.env` file) and validates types.

```python
class Settings(BaseSettings):
    opcua_endpoint:    str   # opc.tcp://hardware:4840
    opcua_node_ids:    str   # comma-separated NodeId string
    opcua_interval_ms: int   # publishing interval in ms
    db_dsn:            str   # postgresql://user:pass@host:port/db
    port:              int   # HTTP server port
```

**Key property:**
```python
settings.node_id_list  # → List[str], parsed from opcua_node_ids
```

**Why pydantic-settings?**
- Same validation model as FastAPI request bodies — consistent across the codebase
- Supports `.env` files for local development and OS env vars in Docker
- Type coercion: `"500"` → `int(500)` automatically

---

## `db.py` — Database Layer

**Purpose:** All TimescaleDB interaction. Owns the asyncpg connection pool.

### Public API

```python
async def init_pool(dsn: str) -> None
    # Creates the asyncpg pool. Called once at startup.

async def close_pool() -> None
    # Closes the pool gracefully. Called at shutdown.

async def insert_reading(
    sensor_id: str, node_id: str, value: float,
    unit: str, quality: int, time: datetime
) -> None
    # Inserts one sensor reading into sensor_readings (hypertable).

async def query_readings(
    sensor_id: str, from_time: datetime, to_time: datetime,
    limit: int = 5000
) -> list[dict]
    # Returns raw readings for a sensor in a time range.

async def query_aggregate(
    sensor_id: str, bucket: str,
    from_time: datetime, to_time: datetime
) -> list[dict]
    # Queries the readings_1min continuous aggregate view.

async def list_sensors() -> list[str]
    # Returns all distinct sensor_id values in the DB.

async def health_check() -> bool
    # Executes SELECT 1 — True if DB is reachable.
```

### Design notes
- Pool size: `min=2, max=10` — sufficient for a few events/second load
- All functions are `async` — they release the event loop while waiting for I/O
- No ORM — raw `asyncpg` SQL for predictable query plans and zero overhead

---

## `sse.py` — SSE Broker

**Purpose:** Decouples the OPC UA ingest path from HTTP response streams.
Implements a publish/subscribe fan-out in memory.

### `Reading` dataclass

```python
@dataclass
class Reading:
    sensor_id: str
    node_id:   str
    value:     float
    unit:      str
    quality:   int
    time:      datetime

    def to_sse(self) -> str:
        # Returns "event: reading\ndata: {...JSON...}\n\n"
```

### `SSEBroker`

```python
class SSEBroker:
    def subscribe(self) -> tuple[str, asyncio.Queue]
        # Register a new client. Returns (client_id, queue).

    def unsubscribe(self, cid: str) -> None
        # Remove a client and discard its queue.

    async def publish(self, reading: Reading) -> None
        # Put reading on every registered client queue.
        # Slow clients: drop oldest, insert newest (maxsize=256).

    @property
    def client_count(self) -> int
```

### `event_generator` coroutine

```python
async def event_generator(broker: SSEBroker, request) -> AsyncGenerator[str, None]
    # Used by FastAPI StreamingResponse.
    # Blocks on queue.get() with a 15-second timeout for keep-alive.
    # Yields raw SSE frames (strings).
    # Calls broker.unsubscribe() in the finally block.
```

### Module-level singleton

```python
broker = SSEBroker()
# Imported by main.py — one broker shared across all requests.
```

---

## `opcua_client.py` — OPC UA Client

**Purpose:** Connects to the hardware OPC UA server, creates a Subscription
with MonitoredItems, and dispatches DataChangeNotifications.

### `_DataChangeHandler`

Subclass of `asyncua.SubHandler`. The `datachange_notification` method is called
synchronously by the asyncua internal loop.

```python
def datachange_notification(self, node, val, data):
    # 1. Resolves node → node_id string (via self._node_to_id dict)
    # 2. Extracts StatusCode → quality int
    # 3. Uses SourceTimestamp (falls back to now())
    # 4. Casts value to float — non-numeric values are dropped with a warning
    # 5. Creates a Reading dataclass
    # 6. Schedules _handle_reading(reading) as an asyncio task
```

### `_handle_reading` coroutine

```python
async def _handle_reading(reading: Reading) -> None:
    await asyncio.gather(
        broker.publish(reading),      # SSE fan-out
        db.insert_reading(...),       # TimescaleDB write
        return_exceptions=True        # one failure doesn't cancel the other
    )
```

Both operations run **concurrently** via `asyncio.gather`.

### `run()` coroutine

```python
async def run() -> None:
    # Outer while loop: reconnect on any exception
    # Inner context: Client → create_subscription → subscribe_data_change
    # Blocks in asyncio.sleep(1) loop while connected
    # Exponential back-off: 2s → 4s → 8s → ... → max 60s
```

### `sensorMeta` dictionary

```python
_SENSOR_META: dict[str, tuple[str, str]] = {
    "ns=2;i=1001": ("rail_temp_1",       "°C"),
    "ns=2;i=1002": ("wheel_vibration_1", "mm/s"),
    "ns=2;i=1003": ("brake_pressure_1",  "bar"),
}
```

**Extend this dictionary** when adding new sensor nodes. In a production system
this information comes from the OPC UA address space (`EUInformation` node)
and can be read automatically at startup via `browse_nodes()`.

---

## `main.py` — Application Entry Point

**Purpose:** Composes all modules, defines HTTP routes, manages lifespan.

### Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool(settings.db_dsn)            # startup
    task = asyncio.create_task(opcua_client.run())  # startup
    yield
    await opcua_client.stop()                       # shutdown
    task.cancel()                                   # shutdown
    await db.close_pool()                           # shutdown
```

FastAPI's `lifespan` replaces the deprecated `on_startup`/`on_shutdown` events.
It ensures clean resource release on `SIGTERM`.

### Routes

| Path | Handler | Notes |
|---|---|---|
| `GET /stream` | `StreamingResponse(event_generator(...))` | `media_type="text/event-stream"`, no cache |
| `GET /readings` | async query via `db.query_readings` | Params: `sensor_id`, `from`, `to`, `limit` |
| `GET /readings/aggregate` | async query via `db.query_aggregate` | Params: `sensor_id`, `bucket`, `from`, `to` |
| `GET /sensors` | `db.list_sensors()` | No params |
| `GET /health` | `db.health_check()` + `broker.client_count` | Returns 503 if DB unreachable |

### OpenAPI docs

FastAPI auto-generates interactive API docs at:
- `http://localhost:8080/docs` — Swagger UI
- `http://localhost:8080/redoc` — ReDoc

---

## `requirements.txt`

```
asyncua==1.1.5          OPC UA client (IEC 62541 stack)
fastapi==0.115.0        HTTP framework + OpenAPI
uvicorn[standard]==0.30.6  ASGI server (uvloop + httptools for performance)
asyncpg==0.29.0         PostgreSQL async driver
pydantic-settings==2.3.4   Env var config with validation
```

No runtime dependency has a transitive dependency on C extensions except
`uvicorn[standard]` (uvloop). The Docker image uses `python:3.12-slim` which
has the required build tools.

---

## Dependency graph

```
main.py
  ├── config.py          (settings singleton)
  ├── db.py              (pool + queries)
  ├── sse.py             (broker singleton + Reading type)
  └── opcua_client.py
        ├── config.py
        ├── db.py
        └── sse.py
```

There are no circular dependencies. `config.py`, `db.py`, and `sse.py` are
leaf modules with no cross-imports between them.
