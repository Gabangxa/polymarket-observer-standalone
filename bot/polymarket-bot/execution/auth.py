# execution/auth.py — Polymarket CLOB authenticated client
#
# Wraps py-clob-client ClobClient initialisation.
# L1 private key is read once from POLYGON_PRIVATE_KEY env var.
# L2 API key is derived from the wallet on first call and cached.
# The client instance is a module-level singleton — import get_client()
# wherever an authenticated CLOB call is needed.
#
# Required env vars:
#   POLYGON_PRIVATE_KEY  — hex private key (with or without 0x prefix)
#   POLYMARKET_CHAIN_ID  — 137 for Polygon mainnet (default), 80002 for Amoy testnet

import logging
import os
import threading

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()

CHAIN_ID = int(os.environ.get("POLYMARKET_CHAIN_ID", "137"))
CLOB_HOST = "https://clob.polymarket.com"


def get_client():
    """
    Return the shared authenticated ClobClient, initialising it on first call.
    Raises RuntimeError if POLYGON_PRIVATE_KEY is not set.
    Thread-safe via double-checked locking.
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        key = os.environ.get("POLYGON_PRIVATE_KEY", "")
        if not key:
            raise RuntimeError(
                "POLYGON_PRIVATE_KEY env var is not set. "
                "Set it in Railway service variables — never in code."
            )

        try:
            from py_clob_client.client import ClobClient
        except ImportError as e:
            raise RuntimeError(
                "py-clob-client is not installed. "
                "Add it to requirements.txt: py-clob-client>=0.17.0"
            ) from e

        logger.info(f"Initialising ClobClient (chain_id={CHAIN_ID})")
        client = ClobClient(
            host=CLOB_HOST,
            chain_id=CHAIN_ID,
            key=key,
        )

        # Derive L2 API credentials from the wallet. This call signs a message
        # on-chain — it will fail if the key is invalid or the network is unreachable.
        try:
            client.set_api_creds(client.create_or_derive_api_creds())
            logger.info("ClobClient L2 credentials derived successfully")
        except Exception as e:
            raise RuntimeError(f"Failed to derive L2 API credentials: {e}") from e

        _client = client
        return _client
