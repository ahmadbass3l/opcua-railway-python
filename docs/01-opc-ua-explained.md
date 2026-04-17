# OPC UA — In-Depth Technical Reference

> **Audience:** Developers integrating with railway sensor hardware.
> This document explains what OPC UA is, how it works at the protocol level,
> and how this service uses it.

---

## What is OPC UA?

**OPC UA** (Open Platform Communications Unified Architecture) is an open,
platform-independent industrial communication standard published as **IEC 62541**.
It is the successor to the older OPC Classic (COM/DCOM-based) family of protocols
and is widely adopted in:

- Railway infrastructure monitoring
- Factory automation (Industry 4.0)
- Energy grid management
- Building automation

Unlike REST or MQTT, OPC UA provides a **structured address space** — a tree of
typed nodes describing the physical equipment — not just a raw data stream.

---

## Protocol stack

```
┌───────────────────────────────────────────┐
│  Application Layer  (Services, NodeIds)   │
├───────────────────────────────────────────┤
│  Session Layer      (Sessions, Security)  │
├───────────────────────────────────────────┤
│  Secure Channel     (Signing/Encryption)  │
├───────────────────────────────────────────┤
│  Transport Layer    (TCP Binary / HTTPS)  │
└───────────────────────────────────────────┘
```

| Layer | Detail |
|---|---|
| Transport | `opc.tcp://` — binary TCP (port 4840). More efficient than SOAP/HTTPS. |
| Secure Channel | Optional TLS-like layer: `None`, `Sign`, `SignAndEncrypt` |
| Session | Authenticated context (anonymous, username/password, X.509 certificate) |
| Application | Services: Read, Write, Browse, Subscribe, Call, etc. |

---

## The Address Space

Every OPC UA server exposes a hierarchical **address space** — a directed graph
of **Nodes**. Every sensor, actuator, method, and data type is a node.

### Node types relevant to sensors

| NodeClass | Description |
|---|---|
| `Object` | A logical grouping (e.g. `RailSection_A`) |
| `Variable` | Holds a value — this is where sensor readings live |
| `ObjectType` | Type definition (like a class) |
| `DataType` | Defines the data type of a Variable (Float, Int32, …) |

### NodeId — the unique address of a node

Every node has a **NodeId** with three components:

```
ns=2;i=1001
│      └── Identifier (integer in this case)
└── Namespace index (0 = OPC UA standard, 1+ = vendor-defined)
```

NodeId forms:
| Form | Example | Description |
|---|---|---|
| Numeric | `ns=2;i=1001` | Most common in hardware |
| String | `ns=2;s=RailTemp1` | Human-readable |
| GUID | `ns=2;g=...` | UUID-based |

> **To find your hardware's NodeIds:** use [UaExpert](https://www.unified-automation.com/products/development-tools/uaexpert.html)
> (free OPC UA browser) to connect to `opc.tcp://<hardware-ip>:4840` and browse the address space visually.

---

## The Subscription Model

This is the core mechanism this service uses — fundamentally different from polling.

### How polling works (what we do NOT do)

```
Client → Server: "What is the value of ns=2;i=1001?"  (every N ms)
Server → Client: "42.3°C"
Client → Server: "What is the value of ns=2;i=1001?"
Server → Client: "42.3°C"   ← same value, wasted network round-trip
```

Problems: latency = polling interval, unnecessary traffic, missed spikes.

### How OPC UA Subscriptions work (what we DO)

```
1. Client → Server: CreateSubscription (publishingInterval=500ms)
   ← Server assigns a SubscriptionId

2. Client → Server: CreateMonitoredItems (list of NodeIds to watch)
   ← Server confirms each as a MonitoredItem

3. Server → Client: Publish (only when a value changes, or on keepalive)
   DataChangeNotification {
     MonitoredItem { clientHandle=1, value=42.3, sourceTimestamp=... }
     MonitoredItem { clientHandle=2, value=1.21, sourceTimestamp=... }
   }

4. Client → Server: Publish acknowledgement
```

### Key parameters

| Parameter | This service | Meaning |
|---|---|---|
| `PublishingInterval` | `OPCUA_INTERVAL_MS` (default 500ms) | How often the server checks for changes |
| `SamplingInterval` | Same as publishing interval | How often the server samples each node |
| `QueueSize` | 1 (default) | How many undelivered notifications to buffer per item |
| `MonitoringMode` | `Reporting` | Changes are reported immediately |

### DataChangeNotification fields we use

| Field | Used for |
|---|---|
| `Value.Value` | The sensor reading (cast to float64) |
| `Value.SourceTimestamp` | When the hardware measured the value |
| `Value.StatusCode` | OPC UA quality code (0 = Good) |
| `ClientHandle` | Maps back to our NodeId string |

---

## StatusCode / Quality

Every OPC UA value carries a `StatusCode` indicating data quality:

| Code | Meaning | Our `quality` field |
|---|---|---|
| `0x00000000` | Good | `0` |
| `0x40000000` | Uncertain | non-zero |
| `0x80000000` | Bad | non-zero |

The continuous aggregate view (`readings_1min`) filters `WHERE quality = 0`
to exclude degraded readings from rollups.

---

## Security Modes

| Mode | Wire protection | Use case |
|---|---|---|
| `None` | No encryption or signing | Trusted private LAN only |
| `Sign` | Message signing (integrity, no confidentiality) | Local network with integrity requirement |
| `SignAndEncrypt` | Full TLS-equivalent | Any untrusted or routed network |

See [`04-security.md`](./04-security.md) for step-by-step setup of `SignAndEncrypt`.

---

## Namespace mapping for this service

The default node IDs are placeholders. Replace them with the actual NodeIds
from your hardware's address space:

| Environment variable | Default | Meaning |
|---|---|---|
| `OPCUA_NODE_IDS` | `ns=2;i=1001,ns=2;i=1002,ns=2;i=1003` | Comma-separated list of Variable nodes to monitor |

The `sensorMeta` map in `opcua_client.py` (Python) / `opcua/client.go` (Go)
translates NodeId strings to human-readable sensor names and units.
Update this map to match your hardware's address space.

---

## Further reading

- [OPC Foundation official specification](https://opcfoundation.org/developer-tools/specifications-unified-architecture)
- [asyncua (Python) documentation](https://python-opcua.readthedocs.io)
- [gopcua (Go) repository](https://github.com/gopcua/opcua)
- [UaExpert — free OPC UA browser](https://www.unified-automation.com/products/development-tools/uaexpert.html)
