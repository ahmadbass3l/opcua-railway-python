# Frontend API Contract

> **Document type:** API Integration Specification
> **Service:** opcua-railway-python (identical contract applies to opcua-railway-go)
> **Protocol:** HTTP/1.1
> **Base URL:** `http://<host>:8080`
> **Audience:** Frontend developers integrating the live sensor stream and
> historical data query into a browser application.

---

## Overview

This service exposes two modes of data access:

| Mode | Mechanism | Use case |
|---|---|---|
| **Live stream** | Server-Sent Events (SSE) | Real-time chart updates, live dashboard |
| **Historical query** | REST GET | Loading past data on page load, time-range explorer |

No authentication is required in the current (development) configuration.
See `04-security.md` for production hardening.

---

## 1. Live Stream — `GET /stream`

### Protocol: Server-Sent Events (SSE)

Connect using the browser's native `EventSource` API.
The connection stays open indefinitely. The server pushes a new event whenever
a sensor value changes.

### Request

```
GET /stream HTTP/1.1
Accept: text/event-stream
Cache-Control: no-cache
```

No query parameters. No request body.

### Response headers

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

### Event stream format

```
: connected

event: reading
data: {"sensor_id":"rail_temp_1","node_id":"ns=2;i=1001","value":42.3,"unit":"°C","quality":0,"time":"2026-04-17T19:00:00+00:00"}

event: reading
data: {"sensor_id":"wheel_vibration_1","node_id":"ns=2;i=1002","value":1.21,"unit":"mm/s","quality":0,"time":"2026-04-17T19:00:00.500+00:00"}

: keep-alive

```

**Lines:**
- `: ...` — comment lines (keep-alive ping, ignored by EventSource)
- `event: reading` — named event type
- `data: {...}` — JSON payload
- Blank line — terminates one event

### Event payload schema

| Field | Type | Description |
|---|---|---|
| `sensor_id` | `string` | Human-readable sensor name (e.g. `rail_temp_1`) |
| `node_id` | `string` | OPC UA NodeId (e.g. `ns=2;i=1001`) |
| `value` | `number` (float) | Measured value |
| `unit` | `string` | Engineering unit (e.g. `°C`, `mm/s`, `bar`) |
| `quality` | `integer` | OPC UA StatusCode — `0` = Good, non-zero = degraded |
| `time` | `string` (ISO-8601) | Source timestamp from the hardware sensor |

### Keep-alive

The server sends a comment line every 15 seconds when no readings arrive.
`EventSource` treats comment lines as no-ops. This prevents proxies and load
balancers from closing idle connections.

### Browser integration example

```javascript
const source = new EventSource('/stream');

source.addEventListener('reading', (e) => {
  const reading = JSON.parse(e.data);
  console.log(`${reading.sensor_id}: ${reading.value} ${reading.unit}`);
  updateChart(reading);
});

source.onerror = (e) => {
  console.warn('SSE connection lost, browser will auto-reconnect');
};

// To stop: source.close();
```

### Auto-reconnect

`EventSource` reconnects automatically on disconnection (browser built-in).
The service handles new connections transparently — no session state is lost.

---

## 2. Historical Data — `GET /readings`

Returns raw sensor readings from TimescaleDB for a given sensor and time range.

### Request

```
GET /readings?sensor_id=rail_temp_1&from=2026-04-17T18:00:00Z&to=2026-04-17T19:00:00Z&limit=5000
```

### Query parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `sensor_id` | string | ✅ Yes | — | Sensor to query (from `GET /sensors`) |
| `from` | ISO-8601 datetime | No | `now() - 1 hour` | Start of time range (inclusive) |
| `to` | ISO-8601 datetime | No | `now()` | End of time range (inclusive) |
| `limit` | integer | No | `5000` | Max rows returned (1–50000) |

### Response `200 OK`

```json
{
  "sensor_id": "rail_temp_1",
  "from": "2026-04-17T18:00:00+00:00",
  "to": "2026-04-17T19:00:00+00:00",
  "count": 2,
  "readings": [
    {
      "time": "2026-04-17T18:00:00.123000+00:00",
      "sensor_id": "rail_temp_1",
      "node_id": "ns=2;i=1001",
      "value": 41.9,
      "unit": "°C",
      "quality": 0
    },
    {
      "time": "2026-04-17T18:00:00.623000+00:00",
      "sensor_id": "rail_temp_1",
      "node_id": "ns=2;i=1001",
      "value": 42.1,
      "unit": "°C",
      "quality": 0
    }
  ]
}
```

### Response schema

| Field | Type | Description |
|---|---|---|
| `sensor_id` | string | The queried sensor |
| `from` / `to` | ISO-8601 | Actual time range applied |
| `count` | integer | Number of rows in `readings` |
| `readings` | array | Ordered by `time ASC` |

---

## 3. Aggregated Data — `GET /readings/aggregate`

Returns pre-computed 1-minute average/min/max from the TimescaleDB continuous
aggregate view. Use this for longer time ranges where raw data density is too
high for a chart.

### Request

```
GET /readings/aggregate?sensor_id=rail_temp_1&bucket=1min&from=2026-04-17T17:00:00Z&to=2026-04-17T19:00:00Z
```

### Query parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `sensor_id` | string | ✅ Yes | — | Sensor to query |
| `bucket` | string | No | `1min` | Aggregation window. Currently: `1min` |
| `from` | ISO-8601 datetime | No | `now() - 1 hour` | Start of range |
| `to` | ISO-8601 datetime | No | `now()` | End of range |

### Response `200 OK`

```json
{
  "sensor_id": "rail_temp_1",
  "bucket": "1min",
  "from": "2026-04-17T17:00:00+00:00",
  "to": "2026-04-17T19:00:00+00:00",
  "count": 2,
  "data": [
    {
      "bucket": "2026-04-17T17:00:00+00:00",
      "sensor_id": "rail_temp_1",
      "avg_value": 41.85,
      "min_value": 41.2,
      "max_value": 42.5
    },
    {
      "bucket": "2026-04-17T17:01:00+00:00",
      "sensor_id": "rail_temp_1",
      "avg_value": 42.10,
      "min_value": 41.8,
      "max_value": 42.7
    }
  ]
}
```

---

## 4. Sensor List — `GET /sensors`

Returns all sensor IDs that have data in the database.

### Request

```
GET /sensors
```

### Response `200 OK`

```json
{
  "sensors": ["brake_pressure_1", "rail_temp_1", "wheel_vibration_1"]
}
```

Use this to populate a sensor selector in the UI before making a `/readings` query.

---

## 5. Health Probe — `GET /health`

For monitoring and load balancer health checks.

### Response `200 OK` (healthy)

```json
{
  "status": "ok",
  "db": true,
  "sse_clients": 3
}
```

### Response `503 Service Unavailable` (DB unreachable)

```json
{
  "status": "degraded",
  "db": false,
  "sse_clients": 0
}
```

---

## Error responses

All error responses follow this format:

```json
{ "detail": "sensor_id is required" }
```

| HTTP status | Cause |
|---|---|
| `400 Bad Request` | Missing required parameter or invalid bucket value |
| `422 Unprocessable Entity` | Parameter type mismatch (e.g. non-integer `limit`) |
| `503 Service Unavailable` | Database unreachable (from `/health`) |

---

## Complete frontend integration example

```html
<!DOCTYPE html>
<html>
<head>
  <title>Railway Sensor Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
  <select id="sensorSelect"></select>
  <canvas id="liveChart"></canvas>
  <canvas id="historyChart"></canvas>

<script>
const API = '';  // same origin; or 'http://localhost:8080'
const MAX_LIVE_POINTS = 200;

// ── 1. Load sensor list ────────────────────────────────────────────────────
async function loadSensors() {
  const res = await fetch(`${API}/sensors`);
  const { sensors } = await res.json();
  const sel = document.getElementById('sensorSelect');
  sensors.forEach(s => {
    const opt = document.createElement('option');
    opt.value = opt.textContent = s;
    sel.appendChild(opt);
  });
  sel.addEventListener('change', () => loadHistory(sel.value));
  if (sensors.length) loadHistory(sensors[0]);
}

// ── 2. Load historical data ────────────────────────────────────────────────
async function loadHistory(sensorId) {
  const to = new Date().toISOString();
  const from = new Date(Date.now() - 3600_000).toISOString();
  const res = await fetch(
    `${API}/readings/aggregate?sensor_id=${sensorId}&bucket=1min&from=${from}&to=${to}`
  );
  const { data } = await res.json();
  historyChart.data.labels = data.map(r => new Date(r.bucket).toLocaleTimeString());
  historyChart.data.datasets[0].data = data.map(r => r.avg_value);
  historyChart.data.datasets[0].label = `${sensorId} avg (1 min)`;
  historyChart.update();
}

// ── 3. Live stream ─────────────────────────────────────────────────────────
const source = new EventSource(`${API}/stream`);
source.addEventListener('reading', (e) => {
  const r = JSON.parse(e.data);
  const now = new Date(r.time).toLocaleTimeString();

  liveChart.data.labels.push(now);
  liveChart.data.datasets[0].data.push(r.value);
  liveChart.data.datasets[0].label = `${r.sensor_id} (${r.unit})`;

  if (liveChart.data.labels.length > MAX_LIVE_POINTS) {
    liveChart.data.labels.shift();
    liveChart.data.datasets[0].data.shift();
  }
  liveChart.update('quiet');
});

// ── 4. Chart.js setup ─────────────────────────────────────────────────────
const liveChart = new Chart(document.getElementById('liveChart'), {
  type: 'line',
  data: { labels: [], datasets: [{ data: [], borderColor: '#f8786a', tension: 0.3, pointRadius: 0 }] },
  options: { animation: false, scales: { x: { ticks: { maxTicksLimit: 10 } } } }
});

const historyChart = new Chart(document.getElementById('historyChart'), {
  type: 'line',
  data: { labels: [], datasets: [{ data: [], borderColor: '#a8a0f8', tension: 0.3, pointRadius: 0 }] },
  options: { scales: { x: { ticks: { maxTicksLimit: 10 } } } }
});

loadSensors();
</script>
</body>
</html>
```

---

## CORS

By default FastAPI does not add CORS headers. If the frontend is served from a
different origin than the API, add the CORS middleware in `main.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend.example.com"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
```

For the SSE endpoint, the browser's `EventSource` follows CORS rules — the
`Access-Control-Allow-Origin` header must be present for cross-origin streams.
