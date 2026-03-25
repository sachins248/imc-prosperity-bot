import json
import math
from typing import Dict, List, Tuple
from datamodel import Order, OrderDepth, TradingState

LIMITS = {"EMERALDS": 80, "TOMATOES": 80}

# ─── EMERALDS PARAMS ──────────────────────────────────────────────────────────
EMERALDS_FAIR_VALUE = 10000.0
EMERALDS_QUOTE_EDGE = 1.5
EMERALDS_TAKE_EDGE = 1.0
EMERALDS_POSITION_SKEW = 0.08

# ─── TOMATOES PARAMS ──────────────────────────────────────────────────────────
TOMATO_ALPHA = 0.45
TOMATO_QUOTE_EDGE = 1.5
TOMATO_TAKE_EDGE = 1.0
TOMATO_POSITION_SKEW = 0.12

# ─── UNIVERSAL PARAMS ─────────────────────────────────────────────────────────
# Stop quoting into the direction of our inventory when near the limit
SOFT_POSITION_LIMIT = 76

class Trader:
    def run(self, state: TradingState):
        data = json.loads(state.traderData) if state.traderData else {}
        if state.timestamp == 0:
            data = {}

        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            if product not in LIMITS:
                continue

            position = state.position.get(product, 0)
            orders: List[Order] = []

            if product == "EMERALDS":
                fair_value = EMERALDS_FAIR_VALUE
                quote_edge = EMERALDS_QUOTE_EDGE
                take_edge = EMERALDS_TAKE_EDGE
                position_skew = EMERALDS_POSITION_SKEW
            else:
                fair_value = self._update_tomato_fair_value(depth, data)
                quote_edge = TOMATO_QUOTE_EDGE
                take_edge = TOMATO_TAKE_EDGE
                position_skew = TOMATO_POSITION_SKEW

            best_bid, best_ask = self._best_prices(depth)
            if best_bid is None or best_ask is None:
                result[product] = orders
                continue

            remaining_buy = LIMITS[product] - position
            remaining_sell = LIMITS[product] + position

            # --- OPTIMIZATION 1: Skew fair value BEFORE taking stale quotes ---
            # Prevents us from aggressively taking liquidity when our inventory is already heavy
            taking_fair = fair_value - (position_skew * position)

            take_orders, remaining_buy, remaining_sell, expected_position = self._take_stale_quotes(
                product=product,
                depth=depth,
                fair_value=taking_fair,
                take_edge=take_edge,
                position=position,
                remaining_buy=remaining_buy,
                remaining_sell=remaining_sell,
            )
            orders.extend(take_orders)

            # --- OPTIMIZATION 2: Re-skew quoting fair value based on expected position ---
            quoting_fair = fair_value - (position_skew * expected_position)
            bid_quote, ask_quote = self._quote_prices(
                best_bid=best_bid,
                best_ask=best_ask,
                adjusted_fair=quoting_fair,
                quote_edge=quote_edge,
            )

            # --- OPTIMIZATION 3: Maximize passive volume sizes ---
            # We quote our entire remaining capacity instead of artificially limiting to 12/20 lots
            passive_buy_size = max(0, remaining_buy)
            passive_sell_size = max(0, remaining_sell)

            # Safety valve: Stop providing liquidity in the direction of heavy inventory
            if expected_position > SOFT_POSITION_LIMIT:
                passive_buy_size = 0
            elif expected_position < -SOFT_POSITION_LIMIT:
                passive_sell_size = 0

            if passive_buy_size > 0:
                orders.append(Order(product, bid_quote, passive_buy_size))
            if passive_sell_size > 0:
                orders.append(Order(product, ask_quote, -passive_sell_size))

            result[product] = orders

        trader_data = json.dumps(data, separators=(",", ":"))
        return result, 0, trader_data

    def _update_tomato_fair_value(self, depth: OrderDepth, data: dict) -> float:
        """
        Calculates TOMATOES fair value using Wall Midpoint and Order Imbalance (OIB).
        This prevents getting run over by short-term micro-trends compared to just microprice.
        """
        wall_bid = max(depth.buy_orders.items(), key=lambda level: level[1])[0]
        wall_ask = min(depth.sell_orders.items(), key=lambda level: abs(level[1]))[0]
        wall_mid = (wall_bid + wall_ask) / 2.0

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        vol_bid = depth.buy_orders[best_bid]
        vol_ask = abs(depth.sell_orders[best_ask])
        
        # OIB ranges from -1 to 1 based on buy vs sell pressure
        oib = (vol_bid - vol_ask) / (vol_bid + vol_ask)
        
        # Blend the wall mid with the order book pressure
        raw_signal = wall_mid + (oib * 1.5)
        
        previous = data.get("tomatoes_ema")
        fair_value = raw_signal if previous is None else TOMATO_ALPHA * raw_signal + (1.0 - TOMATO_ALPHA) * previous
        data["tomatoes_ema"] = fair_value
        
        return fair_value

    def _take_stale_quotes(
        self, product: str, depth: OrderDepth, fair_value: float, take_edge: float, 
        position: int, remaining_buy: int, remaining_sell: int
    ) -> Tuple[List[Order], int, int, int]:
        orders: List[Order] = []
        expected_position = position

        # Take cheap asks
        for ask_price in sorted(depth.sell_orders):
            if remaining_buy <= 0:
                break
            ask_volume = -depth.sell_orders[ask_price]
            if ask_price > fair_value - take_edge:
                break
            quantity = min(remaining_buy, ask_volume)
            if quantity > 0:
                orders.append(Order(product, ask_price, quantity))
                remaining_buy -= quantity
                expected_position += quantity

        # Hit rich bids
        for bid_price in sorted(depth.buy_orders, reverse=True):
            if remaining_sell <= 0:
                break
            bid_volume = depth.buy_orders[bid_price]
            if bid_price < fair_value + take_edge:
                break
            quantity = min(remaining_sell, bid_volume)
            if quantity > 0:
                orders.append(Order(product, bid_price, -quantity))
                remaining_sell -= quantity
                expected_position -= quantity

        return orders, remaining_buy, remaining_sell, expected_position

    def _quote_prices(self, best_bid: int, best_ask: int, adjusted_fair: float, quote_edge: float) -> Tuple[int, int]:
        # Quote exactly at our required edge from fair value
        bid_quote = math.floor(adjusted_fair - quote_edge)
        ask_quote = math.ceil(adjusted_fair + quote_edge)

        # Safety: Never quote worse than the current book (don't get buried)
        # and never accidentally cross the spread (which would trigger a taker fee/bad fill)
        if best_bid is not None:
            bid_quote = max(bid_quote, best_bid)
        if best_ask is not None:
            ask_quote = min(ask_quote, best_ask)

        # Final sanity check: ensure quotes don't cross each other
        if bid_quote >= ask_quote:
            bid_quote = ask_quote - 1

        return bid_quote, ask_quote

    def _best_prices(self, depth: OrderDepth) -> Tuple[int, int]:
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        return best_bid, best_ask
