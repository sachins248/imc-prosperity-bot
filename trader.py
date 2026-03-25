import json
import math
from typing import Dict, List, Tuple

from datamodel import Order, OrderDepth, TradingState


LIMITS = {"EMERALDS": 80, "TOMATOES": 80}

EMERALDS_FAIR_VALUE = 10000.0
EMERALDS_QUOTE_EDGE = 3.0
EMERALDS_TAKE_EDGE = 1.0
EMERALDS_POSITION_SKEW = 0.10
EMERALDS_PASSIVE_SIZE = 20

TOMATO_ALPHA = 0.35
TOMATO_QUOTE_EDGE = 2.0
TOMATO_TAKE_EDGE = 1.0
TOMATO_POSITION_SKEW = 0.12
TOMATO_PASSIVE_SIZE = 12

SOFT_POSITION_LIMIT = 50
HARD_POSITION_LIMIT = 65


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
                passive_size = EMERALDS_PASSIVE_SIZE
            else:
                fair_value = self._update_tomato_fair_value(depth, data)
                quote_edge = TOMATO_QUOTE_EDGE
                take_edge = TOMATO_TAKE_EDGE
                position_skew = TOMATO_POSITION_SKEW
                passive_size = TOMATO_PASSIVE_SIZE

            best_bid, best_ask = self._best_prices(depth)
            if best_bid is None or best_ask is None:
                result[product] = orders
                continue

            remaining_buy = LIMITS[product] - position
            remaining_sell = LIMITS[product] + position

            take_orders, remaining_buy, remaining_sell, expected_position = self._take_stale_quotes(
                product=product,
                depth=depth,
                fair_value=fair_value,
                take_edge=take_edge,
                position=position,
                remaining_buy=remaining_buy,
                remaining_sell=remaining_sell,
            )
            orders.extend(take_orders)

            clear_orders, remaining_buy, remaining_sell, expected_position = self._inventory_clear(
                product=product,
                position=expected_position,
                best_bid=best_bid,
                best_ask=best_ask,
                remaining_buy=remaining_buy,
                remaining_sell=remaining_sell,
            )
            orders.extend(clear_orders)

            adjusted_fair = fair_value - position_skew * expected_position
            bid_quote, ask_quote = self._quote_prices(
                best_bid=best_bid,
                best_ask=best_ask,
                adjusted_fair=adjusted_fair,
                quote_edge=quote_edge,
            )

            passive_buy_size = min(passive_size, max(0, remaining_buy))
            passive_sell_size = min(passive_size, max(0, remaining_sell))

            if position > SOFT_POSITION_LIMIT:
                passive_buy_size = 0
            elif position < -SOFT_POSITION_LIMIT:
                passive_sell_size = 0

            if passive_buy_size > 0:
                orders.append(Order(product, bid_quote, passive_buy_size))
            if passive_sell_size > 0:
                orders.append(Order(product, ask_quote, -passive_sell_size))

            result[product] = orders

        trader_data = json.dumps(data, separators=(",", ":"))
        return result, 0, trader_data

    def _update_tomato_fair_value(self, depth: OrderDepth, data: dict) -> float:
        best_bid, best_ask = self._best_prices(depth)
        wall_mid = self._wall_mid(depth)
        microprice = self._microprice(depth)
        mid = (best_bid + best_ask) / 2.0

        raw_signal = mid + 0.8 * (wall_mid - mid) + 0.9 * (microprice - mid)
        previous = data.get("tomatoes_ema")
        fair_value = raw_signal if previous is None else TOMATO_ALPHA * raw_signal + (1.0 - TOMATO_ALPHA) * previous
        data["tomatoes_ema"] = fair_value
        return fair_value

    def _take_stale_quotes(
        self,
        product: str,
        depth: OrderDepth,
        fair_value: float,
        take_edge: float,
        position: int,
        remaining_buy: int,
        remaining_sell: int,
    ) -> Tuple[List[Order], int, int, int]:
        orders: List[Order] = []
        expected_position = position

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

    def _inventory_clear(
        self,
        product: str,
        position: int,
        best_bid: int,
        best_ask: int,
        remaining_buy: int,
        remaining_sell: int,
    ) -> Tuple[List[Order], int, int, int]:
        orders: List[Order] = []
        expected_position = position

        if position > HARD_POSITION_LIMIT and remaining_sell > 0:
            quantity = min(position - SOFT_POSITION_LIMIT, remaining_sell)
            if quantity > 0:
                orders.append(Order(product, best_bid, -quantity))
                remaining_sell -= quantity
                expected_position -= quantity
        elif position < -HARD_POSITION_LIMIT and remaining_buy > 0:
            quantity = min((-position) - SOFT_POSITION_LIMIT, remaining_buy)
            if quantity > 0:
                orders.append(Order(product, best_ask, quantity))
                remaining_buy -= quantity
                expected_position += quantity

        return orders, remaining_buy, remaining_sell, expected_position

    def _quote_prices(self, best_bid: int, best_ask: int, adjusted_fair: float, quote_edge: float) -> Tuple[int, int]:
        bid_quote = min(best_bid + 1, math.floor(adjusted_fair - quote_edge))
        ask_quote = max(best_ask - 1, math.ceil(adjusted_fair + quote_edge))

        if bid_quote >= ask_quote:
            bid_quote = min(best_bid, ask_quote - 1)
            ask_quote = max(best_ask, bid_quote + 1)

        return bid_quote, ask_quote

    def _best_prices(self, depth: OrderDepth) -> Tuple[int, int]:
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        return best_bid, best_ask

    def _wall_mid(self, depth: OrderDepth) -> float:
        wall_bid = max(depth.buy_orders.items(), key=lambda level: level[1])[0]
        wall_ask = min(depth.sell_orders.items(), key=lambda level: abs(level[1]))[0]
        return (wall_bid + wall_ask) / 2.0

    def _microprice(self, depth: OrderDepth) -> float:
        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        bid_volume = depth.buy_orders[best_bid]
        ask_volume = abs(depth.sell_orders[best_ask])
        return (best_bid * ask_volume + best_ask * bid_volume) / (bid_volume + ask_volume)
