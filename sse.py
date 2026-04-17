"""
SSE broker — fan-out in-process events to all connected HTTP clients.

Each call to subscribe() returns an asyncio.Queue.
publish() puts a message on every registered queue.
"""
import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import AsyncGenerator, Dict

log = logging.getLogger(__name__)


@dataclass
class Reading:
    sensor_id: str
    node_id: str
    value: float
    unit: str
    quality: int
    time: datetime

    def to_sse(self) -> str:
        payload = {
            "sensor_id": self.sensor_id,
            "node_id": self.node_id,
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality,
            "time": self.time.isoformat(),
        }
        return f"event: reading\ndata: {json.dumps(payload)}\n\n"


class SSEBroker:
    def __init__(self) -> None:
        self._clients: Dict[str, asyncio.Queue] = {}

    def subscribe(self) -> tuple[str, asyncio.Queue]:
        cid = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._clients[cid] = q
        log.debug("SSE client connected: %s  (total: %d)", cid, len(self._clients))
        return cid, q

    def unsubscribe(self, cid: str) -> None:
        self._clients.pop(cid, None)
        log.debug("SSE client disconnected: %s  (total: %d)", cid, len(self._clients))

    async def publish(self, reading: Reading) -> None:
        dead = []
        for cid, q in self._clients.items():
            try:
                q.put_nowait(reading)
            except asyncio.QueueFull:
                # Slow consumer — drop oldest, insert newest
                try:
                    q.get_nowait()
                    q.put_nowait(reading)
                except asyncio.QueueEmpty:
                    pass
            except Exception:
                dead.append(cid)
        for cid in dead:
            self.unsubscribe(cid)

    @property
    def client_count(self) -> int:
        return len(self._clients)


async def event_generator(broker: SSEBroker, request) -> AsyncGenerator[str, None]:
    """
    Async generator consumed by FastAPI StreamingResponse.
    Yields SSE frames until the client disconnects.
    """
    cid, q = broker.subscribe()
    try:
        # Send a comment immediately so the browser knows the stream is alive
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                reading: Reading = await asyncio.wait_for(q.get(), timeout=15.0)
                yield reading.to_sse()
            except asyncio.TimeoutError:
                # Keep-alive comment every 15 s
                yield ": keep-alive\n\n"
    finally:
        broker.unsubscribe(cid)


broker = SSEBroker()
