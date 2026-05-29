import json
import logging
from datetime import datetime, timezone

import httpx

import alerts
import db

logger = logging.getLogger(__name__)

CONNECTION_STATUS_KEY = "connection_status"
GEOBLOCK_STATUS_KEY   = "geoblock_status"

# Public, unauthenticated endpoint that geolocates the *requesting* IP and
# reports whether Polymarket will accept orders from it. Reads/auth are never
# geoblocked — only order placement — so a passing run_check() can coexist with
# a hard trading block. This probe makes the deploy region's trading
# eligibility explicit instead of leaving it buried in per-order 403s.
GEOBLOCK_URL = "https://polymarket.com/api/geoblock"


def check_geoblock() -> dict:
    """Probe Polymarket's geoblock endpoint from this container's egress IP.

    Persists the result to bot_config['geoblock_status'] and, when blocked,
    logs at ERROR and fires a Discord alert. Network/parse failures are
    non-fatal: they are logged and recorded as error, never raised, so a
    transient probe failure can't take down the executor.
    """
    status = {
        "blocked":   None,
        "country":   None,
        "region":    None,
        "ip":        None,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "error":     None,
    }
    try:
        resp = httpx.get(GEOBLOCK_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        status["blocked"] = bool(data.get("blocked"))
        status["country"] = data.get("country")
        status["region"]  = data.get("region")
        status["ip"]      = data.get("ip")
    except Exception as e:
        status["error"] = str(e)
        logger.warning(f"[connection_checker] Geoblock probe failed: {e}")

    if status["blocked"]:
        logger.error(
            f"[connection_checker] GEOBLOCKED — order placement will 403. "
            f"country={status['country']} region={status['region']} ip={status['ip']}"
        )
        alerts.geoblock_detected(status)
    elif status["error"] is None:
        logger.info(
            f"[connection_checker] Geoblock check: NOT blocked "
            f"(country={status['country']} region={status['region']} ip={status['ip']})"
        )

    try:
        db.set_config(GEOBLOCK_STATUS_KEY, json.dumps(status))
    except Exception as e:
        logger.error(f"[connection_checker] Failed to persist geoblock status: {e}")

    return status


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
