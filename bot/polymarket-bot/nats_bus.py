# nats_bus.py — lightweight NATS publish/subscribe bus for sync code
#
# Runs a daemon thread with its own asyncio loop and a single persistent NATS
# connection. Sync callers just call publish() — it's non-blocking and a no-op
# if NATS_URL is not set. Subscriptions are registered before the thread starts
# and fire their callbacks on every matching message.
#
# Subjects:
#   pm.snapshots.{market_id}                         — fresh snapshot per market
#   pm.signals.{strategy}.{market_id}               — signal emitted by an engine
#   pm.execution.placed.{strategy}.{market_id}      — order placed on CLOB
#   pm.execution.filled.{strategy}.{market_id}      — order fully filled
#   pm.execution.rejected.{strategy}.{market_id}    — order rejected after retries
#   pm.execution.repriced.{strategy}.{market_id}    — GTD-expired order repriced
#   pm.heartbeat.executor                            — executor liveness tick

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

# Subscriptions are registered via subscribe() before the worker starts.
# Each entry is (subject_pattern, sync_callback(subject, data)).
_subscriptions: list[tuple[str, callable]] = []
_subscription_lock = threading.Lock()

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

    loop = asyncio.get_running_loop()

    # Register subscriptions — callbacks are sync; invoke directly from async handler.
    with _subscription_lock:
        subs = list(_subscriptions)
    for subject, callback in subs:
        cb = callback  # capture for closure

        async def _handler(msg, _cb=cb):
            try:
                data = json.loads(msg.data.decode())
            except Exception:
                data = {}
            try:
                _cb(msg.subject, data)
            except Exception as exc:
                logger.warning(f"NATS subscription callback error [{msg.subject}]: {exc}")

        try:
            await nc.subscribe(subject, cb=_handler)
            logger.info(f"NATS subscribed to {subject}")
        except Exception as e:
            logger.warning(f"NATS subscribe failed [{subject}]: {e}")

    try:
        while True:
            try:
                # run_in_executor yields control back to the asyncio event loop
                # while waiting on the sync queue — subscription callbacks can fire.
                subject, payload = await loop.run_in_executor(
                    None, lambda: _q.get(timeout=0.1)
                )
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


def subscribe(subject: str, callback) -> None:
    """
    Register a sync callback for a NATS subject pattern.
    callback(subject: str, data: dict) is called on each matching message.
    Must be called before or at the same time as the first publish() — the
    worker registers all subscriptions once after connecting.
    No-op if NATS_URL is unset.
    """
    if not NATS_URL:
        return
    with _subscription_lock:
        _subscriptions.append((subject, callback))
    _ensure_started()


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
