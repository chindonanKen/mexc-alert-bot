"""AD Desk Machine — simulated decision loop. live_orders_allowed is always false."""

LIVE_ORDERS_ALLOWED = False
# PARKED: resting exchange sell limits after live buys. Do not turn on.
LIVE_RESTING_SELLS = False

__all__ = ["LIVE_ORDERS_ALLOWED", "LIVE_RESTING_SELLS"]
