# execution/auth.py — Polymarket CLOB authenticated client
#
# Wraps py-clob-client-v2 ClobClient initialisation.
# L1 private key is read once from POLYGON_PRIVATE_KEY env var.
# L2 API key is derived from the wallet on first call and cached.
# The client instance is a module-level singleton — import get_client()
# wherever an authenticated CLOB call is needed.
#
# Required env vars:
#   POLYGON_PRIVATE_KEY        — hex private key (with or without 0x prefix)
#   POLYMARKET_CHAIN_ID        — 137 for Polygon mainnet (default), 80002 for Amoy testnet
#   POLYMARKET_SIGNATURE_TYPE  — 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE, 3=POLY_1271
#   POLYMARKET_FUNDER          — proxy/safe/deposit-wallet address (required for types 1, 2, 3)
#
# Signature type guide (py-clob-client-v2 supports 0, 1, 2, 3):
#   0 (EOA)              — direct EOA signing; maker = signer = EOA; no smart wallet
#   1 (POLY_PROXY)       — legacy Polymarket Proxy wallet (older accounts)
#   2 (POLY_GNOSIS_SAFE) — EOA owns a Gnosis Safe; funds in the safe
#   3 (POLY_1271)        — Deposit Wallet; used by all new polymarket.com accounts.
#                          POLYMARKET_FUNDER must be the deposit wallet address.
#
# For types 1, 2, 3 the POLYMARKET_FUNDER address is the maker on every order.
# It is the deposit wallet / proxy / safe address — not your EOA address.

import logging
import os
import threading

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()

CHAIN_ID      = int(os.environ.get("POLYMARKET_CHAIN_ID", "137"))
CLOB_HOST     = "https://clob.polymarket.com"
SIG_TYPE      = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1"))
FUNDER        = os.environ.get("POLYMARKET_FUNDER", "")


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
            from py_clob_client_v2.client import ClobClient
        except ImportError as e:
            raise RuntimeError(
                "py-clob-client-v2 is not installed. "
                "Add it to requirements.txt: py-clob-client-v2>=1.0.1"
            ) from e

        if SIG_TYPE not in (0, 1, 2, 3):
            raise RuntimeError(
                f"POLYMARKET_SIGNATURE_TYPE={SIG_TYPE} is not a valid value. "
                "Valid values: 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE, 3=POLY_1271 (Deposit Wallet)."
            )

        if SIG_TYPE in (1, 2, 3) and not FUNDER:
            raise RuntimeError(
                f"POLYMARKET_FUNDER env var is required when POLYMARKET_SIGNATURE_TYPE={SIG_TYPE}. "
                "Set it to your Polymarket deposit wallet / proxy / safe address in Railway service variables."
            )

        logger.info(f"Initialising ClobClient (chain_id={CHAIN_ID}, sig_type={SIG_TYPE}, funder={FUNDER or 'EOA'})")
        client = ClobClient(
            host=CLOB_HOST,
            chain_id=CHAIN_ID,
            key=key,
            signature_type=SIG_TYPE,
            funder=FUNDER if FUNDER else None,
        )

        # Derive L2 API credentials from the wallet. This call signs a message
        # on-chain — it will fail if the key is invalid or the network is unreachable.
        try:
            client.set_api_creds(client.create_or_derive_api_key())
            logger.info("ClobClient L2 credentials derived successfully")
        except Exception as e:
            raise RuntimeError(f"Failed to derive L2 API credentials: {e}") from e

        _client = client
        return _client
