#!/usr/bin/env python3
"""
send_order.py — place a single GTC limit order directly on the Polymarket CLOB.

Uses the same credentials as the bot (POLYGON_PRIVATE_KEY, POLYMARKET_SIGNATURE_TYPE,
POLYMARKET_FUNDER). Run from the bot directory so auth.py is importable.

Usage:
    uv run python send_order.py \\
        --token-id <CLOB_TOKEN_ID> \\
        --price 0.45 \\
        --size 3 \\
        --side BUY \\
        [--neg-risk] \\
        [--dry-run]

Arguments:
    --token-id   CLOB token ID (the long integer string from the orderbook)
    --price      Limit price 0.01–0.99 (must match market tick size)
    --size       Order size in USDC
    --side       BUY or SELL
    --neg-risk   Pass this flag if the market is a neg-risk market
    --dry-run    Print the order args without submitting
"""

import argparse
import os
import sys


def _parse_args():
    p = argparse.ArgumentParser(description="Place a GTC limit order on Polymarket")
    p.add_argument("--token-id", required=True, help="CLOB token ID")
    p.add_argument("--price",    required=True, type=float, help="Limit price (0.01–0.99)")
    p.add_argument("--size",     required=True, type=float, help="Order size in USDC")
    p.add_argument("--side",     required=True, choices=["BUY", "SELL"], help="BUY or SELL")
    p.add_argument("--neg-risk", action="store_true", help="Market uses neg-risk contracts")
    p.add_argument("--dry-run",  action="store_true", help="Print order args without submitting")
    return p.parse_args()


def _validate(args):
    errors = []
    if not (0.01 <= args.price <= 0.99):
        errors.append(f"--price {args.price} out of range (0.01–0.99)")
    if args.size <= 0:
        errors.append(f"--size must be positive")
    if not os.environ.get("POLYGON_PRIVATE_KEY"):
        errors.append("POLYGON_PRIVATE_KEY env var is not set")
    sig_type = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1"))
    funder   = os.environ.get("POLYMARKET_FUNDER", "")
    if sig_type in (1, 3) and not funder:
        errors.append(
            f"POLYMARKET_FUNDER is required when POLYMARKET_SIGNATURE_TYPE={sig_type}"
        )
    return errors


def main():
    args = _parse_args()

    errors = _validate(args)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    from decimal import Decimal, ROUND_DOWN
    from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
    from execution.auth import get_client

    tick_size = None
    client    = get_client()

    # Fetch and validate tick size for this token
    try:
        tick_size = client.get_tick_size(args.token_id)
        tick_dec  = Decimal(str(tick_size))
        price_dec = Decimal(str(args.price)).quantize(tick_dec, rounding=ROUND_DOWN)
        if price_dec != Decimal(str(args.price)):
            print(
                f"WARNING: price {args.price} rounded to {price_dec} "
                f"(tick size {tick_size} for this market)"
            )
    except Exception as e:
        print(f"WARNING: could not fetch tick size ({e}), using price as-is")
        price_dec = Decimal(str(args.price))

    size_dec = Decimal(str(args.size)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

    opts = PartialCreateOrderOptions(neg_risk=True) if args.neg_risk else None

    print()
    print("Order summary")
    print("─────────────────────────────────────────")
    print(f"  Token ID:  {args.token_id}")
    print(f"  Side:      {args.side}")
    print(f"  Price:     {price_dec}")
    print(f"  Size:      {size_dec} USDC")
    print(f"  Type:      GTC (good till cancelled)")
    print(f"  Neg-risk:  {args.neg_risk}")
    print(f"  Tick size: {tick_size or 'unknown'}")
    print()

    if args.dry_run:
        print("DRY RUN — order not submitted.")
        return

    order_args = OrderArgs(
        token_id=args.token_id,
        price=float(price_dec),
        size=float(size_dec),
        side=args.side,
    )

    try:
        signed   = client.create_order(order_args, opts)
        response = client.post_order(signed, OrderType.GTC)
    except Exception as e:
        print(f"ERROR: order submission failed — {e}", file=sys.stderr)
        sys.exit(1)

    order_id = response.get("orderID") or response.get("id", "unknown")
    status   = response.get("status", "unknown")
    print(f"Order placed successfully")
    print(f"  Order ID: {order_id}")
    print(f"  Status:   {status}")
    print()
    print(f"Check status:")
    print(f"  curl -s https://clob.polymarket.com/orders/{order_id} | python3 -m json.tool")


if __name__ == "__main__":
    main()
