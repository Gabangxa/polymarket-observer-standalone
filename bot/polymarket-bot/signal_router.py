# signal_router.py — subscribe to pm.signals.> and route to pm.execution.queue
#
# Run as a separate process alongside scheduler.py:
#   uv run python signal_router.py
#
# Env vars:
#   NATS_URL              — required
#   EXECUTION_MIN_SCORE   — minimum signal_score to forward (default 0.75)

import asyncio
import json
import logging
import os
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("signal_router")

MIN_SCORE = float(os.environ.get("EXECUTION_MIN_SCORE", "0.75"))


async def main() -> None:
    nats_url = os.environ.get("NATS_URL", "")
    if not nats_url:
        logger.error("NATS_URL not set — signal_router cannot start")
        sys.exit(1)

    import nats as nats_client

    logger.info(f"Connecting to NATS at {nats_url}")
    nc = await nats_client.connect(nats_url)
    logger.info(f"Connected. Listening on pm.signals.> (execution threshold: score >= {MIN_SCORE})")

    async def on_signal(msg) -> None:
        try:
            data = json.loads(msg.data.decode())
        except Exception:
            return

        score    = float(data.get("signal_score") or 0)
        strategy = data.get("strategy", "unknown")
        market_id = (data.get("market_id") or "")[:24]

        logger.info(f"Signal | {strategy:<22} | {market_id} | score={score:.3f}")

        if score >= MIN_SCORE:
            try:
                await nc.publish("pm.execution.queue", json.dumps(data).encode())
                logger.info(f"  -> pm.execution.queue")
            except Exception as e:
                logger.warning(f"  -> execution route failed: {e}")

    await nc.subscribe("pm.signals.>", cb=on_signal)
    logger.info("Signal router running. Ctrl-C to stop.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    await nc.drain()
    logger.info("Signal router stopped.")


if __name__ == "__main__":
    asyncio.run(main())
