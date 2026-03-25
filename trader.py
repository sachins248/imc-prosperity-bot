import json
from datamodel import OrderDepth, TradingState, Order
from typing import List

# ── Position limits (tutorial round) ──────────────────────────────────────────
LIMITS = {"EMERALDS": 80, "TOMATOES": 80}

# ── EMERALDS: stable asset, fair value never moves ────────────────────────────
EMERALD_FV    = 10000   # Confirmed from data: wall-mid always = 10000
EMERALD_EDGE  = 2       # Quote at FV ± 2 (inside market-maker walls at ±8)
EMERALD_SOFT  = 50      # Start clearing position above this threshold
EMERALD_SKEW  = 0.05    # Ticks of quote-skew per unit of position

# ── TOMATOES: drifting asset, fair value = EMA of wall-mid ────────────────────
TOMATO_ALPHA  = 0.6     # Fast EMA: tracks drift while smoothing 1-tick noise
TOMATO_EDGE   = 5       # Quote at FV ± 5 (inside market-maker walls at ±8)
TOMATO_SOFT   = 50      # Clear position threshold
TOMATO_SKEW   = 2       # Max ticks of quote-skew (scales with position %)


class Trader:
    """
    Tutorial-round bot: EMERALDS + TOMATOES.

    Strategy per product each timestep:
      1. TAKE  – lift cheap asks / hit rich bids vs. fair value
      2. CLEAR – place 0-EV orders at fair value to free capacity when near limit
      3. MAKE  – passive quotes inside the market-maker's ±8-tick walls,
                 skewed toward the side that reduces inventory
    """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _wall_mid(self, od: OrderDepth) -> float | None:
        """
        Midpoint of the highest-volume bid and ask levels.
        This is the fair value used by IMC's internal PnL calculation.
        Verified against mid_price column for every row in historical data.
        """
        if not od.buy_orders or not od.sell_orders:
            return None
        wall_bid = max(od.buy_orders, key=lambda p: od.buy_orders[p])
        wall_ask = max(od.sell_orders, key=lambda p: abs(od.sell_orders[p]))
        return (wall_bid + wall_ask) / 2.0

    # ── Per-product strategies ────────────────────────────────────────────────

    def _trade_emeralds(self, od: OrderDepth, pos: int, limit: int) -> List[Order]:
        orders: List[Order] = []
        fv        = EMERALD_FV
        buy_cap   = limit - pos
        sell_cap  = limit + pos

        # ── 1. TAKE ──────────────────────────────────────────────────────────
        # Lift any ask strictly below fair value (guaranteed +EV)
        for ask in sorted(od.sell_orders):
            if ask >= fv or buy_cap <= 0:
                break
            qty = min(abs(od.sell_orders[ask]), buy_cap)
            orders.append(Order("EMERALDS", ask, qty))
            buy_cap -= qty

        # Hit any bid strictly above fair value (guaranteed +EV)
        for bid in sorted(od.buy_orders, reverse=True):
            if bid <= fv or sell_cap <= 0:
                break
            qty = min(od.buy_orders[bid], sell_cap)
            orders.append(Order("EMERALDS", bid, -qty))
            sell_cap -= qty

        # ── 2. CLEAR ─────────────────────────────────────────────────────────
        # 0-EV trades at fair value to free capacity before hitting the hard limit
        if pos > EMERALD_SOFT and sell_cap > 0:
            qty = min(pos - EMERALD_SOFT, sell_cap)
            orders.append(Order("EMERALDS", fv, -qty))
            sell_cap -= qty
        elif pos < -EMERALD_SOFT and buy_cap > 0:
            qty = min(-pos - EMERALD_SOFT, buy_cap)
            orders.append(Order("EMERALDS", fv, qty))
            buy_cap -= qty

        # ── 3. MAKE ──────────────────────────────────────────────────────────
        # Position skew: tilt quotes toward reducing inventory
        skew      = -round(pos * EMERALD_SKEW)   # e.g. pos=+40 → skew=-2

        best_bid  = max(od.buy_orders)  if od.buy_orders  else fv - 10
        best_ask  = min(od.sell_orders) if od.sell_orders else fv + 10

        # Overbid the book by 1 tick, or use target—whichever gives better price
        target_bid = fv - EMERALD_EDGE + skew
        if best_bid < fv:
            make_bid = max(target_bid, best_bid + 1)
        else:
            make_bid = fv - 1
        make_bid = min(make_bid, fv - 1)   # never bid at or above fair value

        # Undercut the book by 1 tick, or use target—whichever gives better price
        target_ask = fv + EMERALD_EDGE + skew
        if best_ask > fv:
            make_ask = min(target_ask, best_ask - 1)
        else:
            make_ask = fv + 1
        make_ask = max(make_ask, fv + 1)   # never ask at or below fair value

        if buy_cap > 0:
            orders.append(Order("EMERALDS", make_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order("EMERALDS", make_ask, -sell_cap))

        return orders

    def _trade_tomatoes(
        self, od: OrderDepth, pos: int, limit: int, data: dict
    ) -> List[Order]:
        orders: List[Order] = []

        wm = self._wall_mid(od)
        if wm is None:
            return orders

        # EMA of wall-mid as fair value (tracks drift, smooths noise)
        prev_ema  = data.get("tomato_ema", wm)
        ema       = TOMATO_ALPHA * wm + (1 - TOMATO_ALPHA) * prev_ema
        data["tomato_ema"] = ema
        fv        = ema

        buy_cap   = limit - pos
        sell_cap  = limit + pos

        # ── 1. TAKE ──────────────────────────────────────────────────────────
        # 1-tick buffer on each side avoids noise-trading near fair value
        for ask in sorted(od.sell_orders):
            if ask >= fv - 1 or buy_cap <= 0:
                break
            qty = min(abs(od.sell_orders[ask]), buy_cap)
            orders.append(Order("TOMATOES", ask, qty))
            buy_cap -= qty

        for bid in sorted(od.buy_orders, reverse=True):
            if bid <= fv + 1 or sell_cap <= 0:
                break
            qty = min(od.buy_orders[bid], sell_cap)
            orders.append(Order("TOMATOES", bid, -qty))
            sell_cap -= qty

        # ── 2. CLEAR ─────────────────────────────────────────────────────────
        fv_int = int(fv)
        if pos > TOMATO_SOFT and sell_cap > 0:
            qty = min(pos - TOMATO_SOFT, sell_cap)
            orders.append(Order("TOMATOES", fv_int, -qty))
            sell_cap -= qty
        elif pos < -TOMATO_SOFT and buy_cap > 0:
            qty = min(-pos - TOMATO_SOFT, buy_cap)
            orders.append(Order("TOMATOES", fv_int, qty))
            buy_cap -= qty

        # ── 3. MAKE ──────────────────────────────────────────────────────────
        pos_pct   = pos / limit                          # ranges −1 to +1
        skew      = -round(pos_pct * TOMATO_SKEW)       # up to ±2 ticks

        best_bid  = max(od.buy_orders)  if od.buy_orders  else fv - 10
        best_ask  = min(od.sell_orders) if od.sell_orders else fv + 10

        target_bid = fv_int - TOMATO_EDGE + skew
        if best_bid < fv:
            make_bid = max(target_bid, best_bid + 1)
        else:
            make_bid = fv_int - 1
        make_bid = min(make_bid, fv_int - 1)

        target_ask = fv_int + TOMATO_EDGE + skew
        if best_ask > fv:
            make_ask = min(target_ask, best_ask - 1)
        else:
            make_ask = fv_int + 1
        make_ask = max(make_ask, fv_int + 1)

        if buy_cap > 0:
            orders.append(Order("TOMATOES", make_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order("TOMATOES", make_ask, -sell_cap))

        return orders

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self, state: TradingState):
        # Restore persistent state (rolling EMA values, etc.)
        data: dict = json.loads(state.traderData) if state.traderData else {}

        result = {}
        for product, od in state.order_depths.items():
            pos   = state.position.get(product, 0)
            limit = LIMITS.get(product, 20)

            if product == "EMERALDS":
                result[product] = self._trade_emeralds(od, pos, limit)
            elif product == "TOMATOES":
                result[product] = self._trade_tomatoes(od, pos, limit, data)
            else:
                result[product] = []

        conversions  = 0
        traderData   = json.dumps(data)
        return result, conversions, traderData
