# agents/ev_calculator.py
# Pure EV and fractional Kelly sizing functions.
# No DB, no API calls — import and use from strategy engines.
#
# Usage:
#   from agents.ev_calculator import yes_ev, no_ev, size_recommendation
#
# Terminology:
#   q = your estimated true probability (0–1)
#   p = current market price (0–1), i.e. the implied probability
#   EV > 0 means you have edge; EV < 0 means the market has edge over you

from config import EV_MIN_THRESHOLD, KELLY_FRACTION


def yes_ev(q: float, p: float) -> float:
    """
    Expected value per share for a YES position.
    Returns 0.0 if inputs are degenerate or no edge.
    """
    if not (0.0 < p < 1.0) or not (0.0 < q <= 1.0):
        return 0.0
    return (q - p) / (1.0 - p)


def no_ev(q: float, p: float) -> float:
    """
    Expected value per share for a NO position.
    q = true probability of YES; p = market price of YES.
    """
    # A NO bet at price (1-p) wins when YES doesn't resolve.
    return yes_ev(1.0 - q, 1.0 - p)


def _kelly(q: float, p: float, fraction: float = KELLY_FRACTION) -> float:
    """
    Fractional Kelly bankroll fraction.
    Uses binary-bet form: f* = (q*b - (1-q)) / b, scaled by fraction.
    Returns 0.0 if there is no edge.
    """
    if not (0.0 < p < 1.0) or not (0.0 < q < 1.0):
        return 0.0
    b = (1.0 - p) / p          # decimal odds on a YES bet
    full_kelly = (q * b - (1.0 - q)) / b
    return max(0.0, round(full_kelly * fraction, 4))


def size_recommendation(
    q: float,
    p: float,
    side: str = "yes",          # "yes" or "no"
    fraction: float = KELLY_FRACTION,
) -> dict | None:
    """
    Return an EV + Kelly sizing dict to merge into signal metadata.
    Returns None if EV is below EV_MIN_THRESHOLD (not worth annotating).

    side = "yes"  → you're evaluating a YES position
    side = "no"   → you're evaluating a NO position (q and p are still for YES)
    """
    ev = yes_ev(q, p) if side == "yes" else no_ev(q, p)
    if ev < EV_MIN_THRESHOLD:
        return None

    if side == "yes":
        kelly = _kelly(q, p, fraction)
        trade_price = p
    else:
        kelly = _kelly(1.0 - q, 1.0 - p, fraction)
        trade_price = round(1.0 - p, 4)

    return {
        "ev_side":       side.upper(),
        "estimated_q":   round(q, 4),
        "market_p":      round(p, 4),
        "trade_price":   trade_price,
        "ev":            round(ev, 4),
        "kelly_fraction": kelly,
        "kelly_pct":     f"{kelly * 100:.1f}% of bankroll",
        "sizing_note": (
            f"Bet {side.upper()} @ {trade_price:.3f} | "
            f"EV={ev*100:.1f}% | Kelly={kelly*100:.1f}% bankroll "
            f"(est. q={q:.2f}, market p={p:.2f})"
        ),
    }
