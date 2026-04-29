# opcua-railway-python

Railway sensor telemetry microservice — **Python** implementation.

Connects to an OPC UA server on railway hardware, subscribes to sensor nodes, streams live data to browsers via **Server-Sent Events**, and persists readings to **TimescaleDB**.

> See the companion [Go implementation](https://github.com/ahmadbass3l/opcua-railway-go) for an identical API in Go.

---

## Stack

| Layer | Library |
|---|---|
| OPC UA client | [`asyncua`](https://github.com/FreeOpcUa/opcua-asyncio) |
| HTTP framework | FastAPI + uvicorn |
| SSE | `StreamingResponse` + `asyncio.Queue` fan-out |
| DB driver | `asyncpg` |
| Database | TimescaleDB (PostgreSQL) |

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/stream` | SSE live feed — `event: reading` per sensor change |
| `GET` | `/readings?sensor_id=&from=&to=&limit=` | Historical raw readings |
| `GET` | `/readings/aggregate?sensor_id=&bucket=1min&from=&to=` | 1-min pre-aggregated averages |
| `GET` | `/sensors` | List all known sensor IDs |
| `GET` | `/health` | OPC UA + DB health probe |

### SSE event format

```
event: reading
data: {"sensor_id":"rail_temp_1","node_id":"ns=2;i=1001","value":42.3,"unit":"°C","quality":0,"time":"2026-04-17T19:00:00+00:00"}

```

---

## Quick start

### Docker Compose (recommended)

```bash
git clone https://github.com/ahmadbass3l/opcua-railway-python.git
cd opcua-railway-python
cp .env.example .env          # edit OPCUA_ENDPOINT and OPCUA_NODE_IDS
docker compose up
```

The compose file starts TimescaleDB and this service together.

### Run locally

```bash
pip install -r requirements.txt
export OPCUA_ENDPOINT=opc.tcp://192.168.1.100:4840
export OPCUA_NODE_IDS="ns=2;i=1001,ns=2;i=1002"
export DB_DSN=postgresql://railway:railway@localhost:5432/railway
python main.py
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OPCUA_ENDPOINT` | `opc.tcp://localhost:4840` | OPC UA server on the hardware |
| `OPCUA_NODE_IDS` | `ns=2;i=1001,...` | Comma-separated NodeIds to subscribe to |
| `OPCUA_INTERVAL_MS` | `500` | Subscription publishing interval (ms) |
| `DB_DSN` | `postgresql://railway:railway@localhost:5432/railway` | TimescaleDB connection string |
| `PORT` | `8080` | HTTP server port |

---

## File layout

```
opcua-railway-python/
├── main.py           # FastAPI app, routes, lifespan
├── opcua_client.py   # asyncua connect + subscribe + DataChangeHandler
├── sse.py            # SSEBroker: fan-out asyncio.Queue per client
├── db.py             # asyncpg pool, insert_reading, query helpers
├── config.py         # env var loading via pydantic-settings
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .github/
    └── workflows/
        └── ci.yml
```

---

## How it works

1. **FastAPI lifespan** starts the asyncpg pool and launches `opcua_client.run()` as a background asyncio task.
2. `opcua_client.run()` connects to `opc.tcp://hardware:4840`, creates an OPC UA **Subscription** (publishing interval = `OPCUA_INTERVAL_MS` ms), and registers one **MonitoredItem** per configured NodeId.
3. On each `DataChangeNotification`, the handler concurrently:
   - Pushes a `Reading` into every SSE client queue via `SSEBroker.publish()`
   - Inserts the reading into TimescaleDB via `asyncpg`
4. `GET /stream` returns a `StreamingResponse` over an `async` generator that blocks on a per-client `asyncio.Queue` and yields `event: reading\ndata: ...\n\n` frames.
5. Slow SSE clients are handled with a drop-oldest policy (bounded queue, size 256) — one stuck client cannot stall the ingest path.

---

## OPC UA connection notes

- Default security mode: **None** (no encryption) — suitable for a trusted local network.
- To enable `Sign` or `SignAndEncrypt`, set `OPCUA_SECURITY_MODE` and provide client cert/key via `OPCUA_CLIENT_CERT` / `OPCUA_CLIENT_KEY` (roadmap item).
- The service auto-reconnects with exponential back-off (2 s → 60 s) if the hardware connection drops.

---

## Database schema

See [`db/init.sql`](https://github.com/ahmadbass3l/opcua-railway-python/blob/main/db/init.sql) — creates the `sensor_readings` hypertable and `readings_1min` continuous aggregate.

---

## License

MIT

---

## Based on open standards and open-source

This project is a **general-purpose template** implementing the publicly available
**OPC UA standard (IEC 62541)**. It is built entirely from open-source libraries
and public documentation — no proprietary data, internal systems, or employer
resources were used.

It is intended as a starting point that can be adapted for specific use cases
before deployment in production. It is not affiliated with, derived from, or
endorsed by any company or commercial product.

See [NOTICE.md](./NOTICE.md) for the full list of standards, libraries, public
documentation sources, and an explicit disclaimer of affiliation.

