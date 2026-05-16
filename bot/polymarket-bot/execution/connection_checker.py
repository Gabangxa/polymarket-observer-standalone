import json
import logging
from datetime import datetime, timezone

import db

logger = logging.getLogger(__name__)

CONNECTION_STATUS_KEY = "connection_status"


def run_check(client=None) -> dict:
    """
    Two-probe connectivity check written to bot_config on every call.

    Probe 1 — CLOB reachable:
        GET /ok (unauthenticated). Confirms the Polymarket CLOB API is up and
        the bot can reach it from this network.

    Probe 2 — Wallet authenticated with Polymarket:
        GET /auth/api-key (requires valid L2 credentials). Confirms the private
        key is loaded and Polymarket recognises the wallet's API credentials.
        Only attempted if probe 1 succeeds.

    Writes the JSON result to bot_config['connection_status'] so the API server
    can surface it to the dashboard.
    """
    clob_ok   = False
    wallet_ok = False
    error     = None

    try:
        if client is None:
            from execution.auth import get_client
            client = get_client()

        # ── Probe 1: CLOB reachable ───────────────────────────────────────────
        try:
            result  = client.get_ok()
            clob_ok = result is not None
        except Exception as e:
            error = f"CLOB unreachable: {e}"
            logger.warning(f"[connection_checker] CLOB probe failed: {e}")

        # ── Probe 2: Wallet authenticated ─────────────────────────────────────
        if clob_ok:
            try:
                keys      = client.get_api_keys()
                wallet_ok = keys is not None
            except Exception as e:
                error = f"Wallet auth failed: {e}"
                logger.warning(f"[connection_checker] Wallet probe failed: {e}")

    except RuntimeError as e:
        # Private key missing or client init failed
        error = str(e)
        logger.warning(f"[connection_checker] Client init failed: {e}")

    status = {
        "clobReachable":       clob_ok,
        "walletAuthenticated": wallet_ok,
        "checkedAt":           datetime.now(timezone.utc).isoformat(),
        "error":               error,
    }

    try:
        db.set_config(CONNECTION_STATUS_KEY, json.dumps(status))
    except Exception as e:
        logger.error(f"[connection_checker] Failed to persist status: {e}")

    logger.info(
        f"[connection_checker] clob={clob_ok} wallet={wallet_ok}"
        + (f" error={error}" if error else "")
    )
    return status
