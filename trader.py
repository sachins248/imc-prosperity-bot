import json
from datamodel import OrderDepth, TradingState, Order
from typing import List

# ── Position limits ────────────────────────────────────────────────────────────
LIMITS = {"EMERALDS": 80, "TOMATOES": 80}

# ── EMERALDS: stable, FV = 10000, market-maker walls at 9992/10008 (±8) ───────
EMERALD_FV    = 10000
EMERALD_EDGE  = 7       # Quote 1 tick inside the walls (9993 bid / 10007 ask)
EMERALD_SOFT  = 30      # Start clearing at ±30 to free capacity early
EMERALD_SKEW  = 0.1     # pos × 0.1 ticks; at pos=80 → skew=-8 → bid falls below wall

# ── TOMATOES: drifting, FV = EMA of wall-mid, walls ±8 from wall-mid ──────────
TOMATO_ALPHA  = 0.6     # Fast EMA — minimal lag vs current wall mid
TOMATO_EDGE   = 7       # Quote 1 tick inside walls (same logic as EMERALDS)
TOMATO_SOFT   = 25      # Clear early; prevents large inventory drawdowns
TOMATO_SKEW   = 7       # Skew equals full edge → one-sided quoting at position limits


class Trader:
    """
    Optimized tutorial-round bot: EMERALDS + TOMATOES.

    Core insight: market-maker walls sit at ±8 from fair value.
    Quoting at ±7 (1 tick inside) gives queue priority over the market maker
    while earning 3.5× more per fill than the original ±2 edge.

    Position management: skew = -(pos / limit) × EDGE ticks.
    At full long (pos=limit):  bid drops 14 below FV (behind wall → no buys),
                               ask drops to FV+1 (very competitive → fast sell).
    At neutral (pos=0):        symmetric ±7 quotes, both sides ahead of market maker.
    At full short (pos=-limit): mirror of full long.

    Steps each tick: TAKE → CLEAR → MAKE
    """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _wall_mid(self, od: OrderDepth) -> float | None:
        """
        Midpoint of the highest-volume bid/ask levels.
        Matches IMC's internal PnL fair-value — verified against every data row.
        """
        if not od.buy_orders or not od.sell_orders:
            return None
        wall_bid = max(od.buy_orders,  key=lambda p: od.buy_orders[p])
        wall_ask = max(od.sell_orders, key=lambda p: abs(od.sell_orders[p]))
        return (wall_bid + wall_ask) / 2.0

    # ── EMERALDS ──────────────────────────────────────────────────────────────

    def _trade_emeralds(self, od: OrderDepth, pos: int, limit: int) -> List[Order]:
        orders: List[Order] = []
        fv       = EMERALD_FV
        buy_cap  = limit - pos
        sell_cap = limit + pos

        # 1. TAKE — guaranteed +EV: lift any ask < FV, hit any bid > FV
        for ask in sorted(od.sell_orders):
            if ask >= fv or buy_cap <= 0:
                break
            qty = min(abs(od.sell_orders[ask]), buy_cap)
            orders.append(Order("EMERALDS", ask, qty))
            buy_cap -= qty

        for bid in sorted(od.buy_orders, reverse=True):
            if bid <= fv or sell_cap <= 0:
                break
            qty = min(od.buy_orders[bid], sell_cap)
            orders.append(Order("EMERALDS", bid, -qty))
            sell_cap -= qty

        # 2. CLEAR — 0-EV at FV to free capacity before hitting the hard limit
        if pos > EMERALD_SOFT and sell_cap > 0:
            qty = min(pos - EMERALD_SOFT, sell_cap)
            orders.append(Order("EMERALDS", fv, -qty))
            sell_cap -= qty
        elif pos < -EMERALD_SOFT and buy_cap > 0:
            qty = min(-pos - EMERALD_SOFT, buy_cap)
            orders.append(Order("EMERALDS", fv, qty))
            buy_cap -= qty

        # 3. MAKE — position-skewed quotes; no best_bid+1 override so skew acts correctly
        # At pos=0:   bid=9993, ask=10007  (1 tick inside ±8 walls → queue priority)
        # At pos=+80: bid=9985 (below wall → not filled), ask=10001 (very competitive)
        skew     = -round(pos * EMERALD_SKEW)
        make_bid = min(fv - EMERALD_EDGE + skew, fv - 1)
        make_ask = max(fv + EMERALD_EDGE + skew, fv + 1)

        if buy_cap > 0:
            orders.append(Order("EMERALDS", make_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order("EMERALDS", make_ask, -sell_cap))

        return orders

    # ── TOMATOES ──────────────────────────────────────────────────────────────

    def _trade_tomatoes(
        self, od: OrderDepth, pos: int, limit: int, data: dict
    ) -> List[Order]:
        orders: List[Order] = []

        wm = self._wall_mid(od)
        if wm is None:
            return orders

        # EMA fair-value: fast enough to track drift, smooths 1-tick noise
        ema = TOMATO_ALPHA * wm + (1 - TOMATO_ALPHA) * data.get("tomato_ema", wm)
        data["tomato_ema"] = ema
        fv     = ema
        fv_int = int(fv)

        buy_cap  = limit - pos
        sell_cap = limit + pos

        # 1. TAKE — hit any genuinely mispriced orders (1-tick buffer avoids noise)
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

        # 2. CLEAR — 0-EV unwind when position approaches soft limit
        if pos > TOMATO_SOFT and sell_cap > 0:
            qty = min(pos - TOMATO_SOFT, sell_cap)
            orders.append(Order("TOMATOES", fv_int, -qty))
            sell_cap -= qty
        elif pos < -TOMATO_SOFT and buy_cap > 0:
            qty = min(-pos - TOMATO_SOFT, buy_cap)
            orders.append(Order("TOMATOES", fv_int, qty))
            buy_cap -= qty

        # 3. MAKE — full-range position skew; no best_bid+1 override
        # pos_pct ∈ [-1, +1]; skew ∈ [-7, +7]
        # At pos=0:       bid=fv-7, ask=fv+7  (both 1 tick inside ±8 walls)
        # At pos=+limit:  bid=fv-14 (silent), ask=fv+0 → capped fv+1 (fast unwind)
        # At pos=-limit:  bid=fv+0 → capped fv-1 (fast cover), ask=fv+14 (silent)
        pos_pct  = pos / limit
        skew     = -round(pos_pct * TOMATO_SKEW)
        
        make_bid = min(fv_int - TOMATO_EDGE + skew, fv_int - 1)
        make_ask = max(fv_int + TOMATO_EDGE + skew, fv_int + 1)

        if buy_cap > 0:
            orders.append(Order("TOMATOES", make_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order("TOMATOES", make_ask, -sell_cap))

        return orders

    # ── Entry point ───────────────────────────────────────────────────────────

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

        return result, 0, json.dumps(data)
