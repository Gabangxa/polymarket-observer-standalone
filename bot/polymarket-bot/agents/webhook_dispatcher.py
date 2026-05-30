import logging
from datetime import datetime, timezone

import httpx

from config import SIGNAL_WEBHOOK_URL

logger = logging.getLogger(__name__)

# Validate once at import time so misconfiguration is obvious in startup logs.
_WEBHOOK_ENABLED = SIGNAL_WEBHOOK_URL.startswith(("http://", "https://"))
if SIGNAL_WEBHOOK_URL and not _WEBHOOK_ENABLED:
    logger.warning(
        "SIGNAL_WEBHOOK_URL is set but missing a protocol prefix — "
        "webhooks are disabled. Prepend 'https://' to the value in Railway. "
        f"Current value: {SIGNAL_WEBHOOK_URL!r}"
    )


def fire_signal(strategy: str, market_id: str, signal_data: dict) -> None:
    """POST a signal event to the configured webhook URL. Silently logs on failure."""
    if not _WEBHOOK_ENABLED:
        return

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "market": market_id,
        "action": "EXECUTE",
        "data": signal_data,
    }

    try:
        with httpx.Client(timeout=2.0) as client:
            client.post(SIGNAL_WEBHOOK_URL, json=payload)
    except Exception as e:
        logger.warning(f"Webhook dispatch failed for {strategy}/{market_id}: {e}")
