# nats_bus.py — lightweight NATS publish bus for sync code
#
# Runs a daemon thread with its own asyncio loop and a single persistent NATS
# connection. Sync callers just call publish() — it's non-blocking and a no-op
# if NATS_URL is not set.
#
# Subjects:
#   pm.snapshots.{market_id}              — fresh snapshot per market
#   pm.signals.{strategy}.{market_id}    — signal emitted by an engine
#   pm.execution.queue                   — signals cleared for bot execution

import atexit
import asyncio
import json
import logging
import os
import queue
import threading

logger = logging.getLogger(__name__)

NATS_URL = os.environ.get("NATS_URL", "")

_q: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None
_started = False
_lock = threading.Lock()

_DRAIN_TIMEOUT = 5  # seconds to wait for queue flush on shutdown


def _worker() -> None:
    asyncio.run(_async_worker())


async def _async_worker() -> None:
    if not NATS_URL:
        return
    try:
        import nats as nats_client
        nc = await nats_client.connect(NATS_URL)
        logger.info(f"NATS bus connected → {NATS_URL}")
    except Exception as e:
        logger.warning(f"NATS bus could not connect: {e}")
        return

    try:
        while True:
            try:
                subject, payload = _q.get(timeout=0.1)
            except queue.Empty:
                continue
            if subject is None:
                break
            try:
                await nc.publish(subject, payload)
            except Exception as e:
                logger.warning(f"NATS publish error [{subject}]: {e}")
    finally:
        try:
            await nc.drain()
        except Exception:
            pass
        logger.info("NATS bus disconnected")


def _ensure_started() -> None:
    global _thread, _started
    if _started or not NATS_URL:
        return
    with _lock:
        if _started:
            return
        _started = True
        _thread = threading.Thread(target=_worker, daemon=True, name="nats-bus")
        _thread.start()


def shutdown() -> None:
    """Best-effort drain on process exit — sends sentinel and waits briefly."""
    if not _started or _thread is None:
        return
    _q.put_nowait((None, None))
    _thread.join(timeout=_DRAIN_TIMEOUT)


atexit.register(shutdown)


def publish(subject: str, data: dict) -> None:
    """Enqueue a NATS message. Non-blocking. No-op if NATS_URL is unset."""
    if not NATS_URL:
        return
    _ensure_started()
    try:
        payload = json.dumps(data, default=str).encode()
        _q.put_nowait((subject, payload))
    except Exception as e:
        logger.warning(f"NATS enqueue failed [{subject}]: {e}")
