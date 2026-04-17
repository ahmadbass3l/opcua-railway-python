"""
OPC UA subscription client.

Connects to the hardware's OPC UA server, creates a Subscription with
MonitoredItems for each configured NodeId, and pushes DataChangeNotifications
to the SSE broker and DB writer.
"""
import asyncio
import logging
from datetime import datetime, timezone

from asyncua import Client, ua
from asyncua.common.subscription import SubHandler

import db
from config import settings
from sse import broker, Reading

log = logging.getLogger(__name__)

# Map node_id string → human-readable sensor_id and unit
# In a real deployment these come from the OPC UA address space (BrowseName +
# EUInformation). For the scaffold we derive them from the NodeId index.
_SENSOR_META: dict[str, tuple[str, str]] = {
    "ns=2;i=1001": ("rail_temp_1",        "°C"),
    "ns=2;i=1002": ("wheel_vibration_1",  "mm/s"),
    "ns=2;i=1003": ("brake_pressure_1",   "bar"),
}


def _meta(node_id_str: str) -> tuple[str, str]:
    return _SENSOR_META.get(node_id_str, (node_id_str, ""))


class _DataChangeHandler(SubHandler):
    """Called by asyncua on every DataChangeNotification from the server."""

    def __init__(self, node_to_id: dict) -> None:
        # Maps asyncua Node objects → node_id strings
        self._node_to_id = node_to_id

    def datachange_notification(self, node, val, data):
        """
        This callback is invoked synchronously in the asyncua event loop.
        We schedule the async work as a task to avoid blocking.
        """
        node_id_str = self._node_to_id.get(node)
        if node_id_str is None:
            return

        status = data.monitored_item.Value.StatusCode
        quality = 0 if status.is_good() else int(status.value)

        # Use the server timestamp if available, fall back to now
        source_ts = data.monitored_item.Value.SourceTimestamp
        if source_ts:
            ts = source_ts.replace(tzinfo=timezone.utc)
        else:
            ts = datetime.now(tz=timezone.utc)

        sensor_id, unit = _meta(node_id_str)

        try:
            float_val = float(val)
        except (TypeError, ValueError):
            log.warning("Non-numeric value from %s: %r", node_id_str, val)
            return

        reading = Reading(
            sensor_id=sensor_id,
            node_id=node_id_str,
            value=float_val,
            unit=unit,
            quality=quality,
            time=ts,
        )

        loop = asyncio.get_event_loop()
        loop.create_task(_handle_reading(reading))


async def _handle_reading(reading: Reading) -> None:
    """Fan out to SSE broker and DB concurrently."""
    await asyncio.gather(
        broker.publish(reading),
        db.insert_reading(
            sensor_id=reading.sensor_id,
            node_id=reading.node_id,
            value=reading.value,
            unit=reading.unit,
            quality=reading.quality,
            time=reading.time,
        ),
        return_exceptions=True,
    )


_running = False


async def run() -> None:
    """
    Main loop: connect → subscribe → keep alive.
    Reconnects automatically on any error.
    """
    global _running
    _running = True
    backoff = 2.0

    while _running:
        try:
            async with Client(url=settings.opcua_endpoint) as client:
                log.info("OPC UA connected: %s", settings.opcua_endpoint)
                backoff = 2.0

                node_to_id: dict = {}
                nodes = []
                for nid_str in settings.node_id_list:
                    node = client.get_node(nid_str)
                    node_to_id[node] = nid_str
                    nodes.append(node)

                handler = _DataChangeHandler(node_to_id)
                sub = await client.create_subscription(
                    settings.opcua_interval_ms, handler
                )
                await sub.subscribe_data_change(nodes)
                log.info(
                    "Subscribed to %d nodes @ %d ms",
                    len(nodes),
                    settings.opcua_interval_ms,
                )

                # Block until the task is cancelled or connection drops
                while _running:
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("OPC UA error: %s — reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    log.info("OPC UA client stopped")


async def stop() -> None:
    global _running
    _running = False
