# NOTICE

## Open Standard

This project implements the **OPC UA** (OPC Unified Architecture) protocol,
an open international standard published as **IEC 62541** by the
OPC Foundation (https://opcfoundation.org). OPC UA is a publicly available,
vendor-neutral standard used across many industrial domains. No proprietary
protocol specification was used in the creation of this software.

## Open-Source Libraries

This codebase is built exclusively on publicly available, open-source libraries
and frameworks. All references are to their official public repositories:

| Library | Language | Licence | Source |
|---|---|---|---|
| asyncua (opcua-asyncio) | Python | LGPL-3.0 | https://github.com/FreeOpcUa/opcua-asyncio |
| FastAPI | Python | MIT | https://github.com/tiangolo/fastapi |
| asyncpg | Python | Apache-2.0 | https://github.com/MagicStack/asyncpg |
| pydantic-settings | Python | MIT | https://github.com/pydantic/pydantic-settings |
| uvicorn | Python | BSD-3-Clause | https://github.com/encode/uvicorn |
| TimescaleDB | Database | TSL / Apache-2.0 | https://github.com/timescale/timescaledb |

## Public Documentation Used

All design and implementation decisions are based solely on publicly available
documentation and specifications:

| Resource | URL |
|---|---|
| OPC UA Specification (IEC 62541) | https://opcfoundation.org/developer-tools/specifications-unified-architecture |
| asyncua documentation | https://python-opcua.readthedocs.io |
| FastAPI documentation | https://fastapi.tiangolo.com |
| TimescaleDB documentation | https://docs.timescale.com |
| asyncpg documentation | https://magicstack.github.io/asyncpg |
| Server-Sent Events (SSE) specification | https://html.spec.whatwg.org/multipage/server-sent-events.html |
| PostgreSQL documentation | https://www.postgresql.org/docs |

## Purpose and Scope

This repository is a **general-purpose template** for connecting any OPC UA
server to a web-accessible data stream and time-series database. It is not
built for, derived from, or affiliated with any specific company, product,
or proprietary system. It is intended as a starting point for developers
working with the OPC UA standard in any industrial domain and should be
adapted for specific use cases before deployment in production.

## No Affiliation

This project is **not affiliated with, endorsed by, or derived from any
employer, client, or commercial entity**. No proprietary data, internal
documentation, confidential information, or employer resources of any kind
were used in the creation of this software. This is an independent work
created outside of any employment or contractual obligation.

## Licence

This project is released under the MIT Licence. See [LICENSE](./LICENSE) for details.
