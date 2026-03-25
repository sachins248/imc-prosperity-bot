import json
import math
import statistics
from typing import Any, List, Dict, Tuple, Optional
from datamodel import Order, OrderDepth, TradingState, ProsperityEncoder, Symbol

# ─── INFRASTRUCTURE: LOGGER ──────────────────────────────────────────────────
class Logger:
    def __init__(self) -> None:
        self.max_log_length = 2000
        self.logs: str = ""

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: Dict[Symbol, List[Order]], conversions: int, trader_data: str) -> None:
        base_payload = [self._partial_state(state, ""), [], conversions, "", ""]
        base_len = len(self.to_json(base_payload))
        max_item = max((self.max_log_length - base_len) // 3, 0)

        payload = [
            self._partial_state(state, self.truncate(state.traderData, max_item)),
            self.compress_orders(orders),
            conversions,
            self.truncate(trader_data, max_item),
            self.truncate(self.logs, max_item),
        ]
        print(self.to_json(payload))
        self.logs = ""

    def _partial_state(self, state: TradingState, trader_data: str) -> list:
        return [state.timestamp, trader_data, [], {}, [], [], state.position, {}]

    def compress_orders(self, orders: Dict[Symbol, List[Order]]) -> List[List[Any]]:
        compressed = []
        for arr in orders.values():
            for o in arr:
                compressed.append([o.symbol, o.price, o.quantity])
        return compressed

    def to_json(self, v: Any) -> str:
        return json.dumps(v, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        if len(json.dumps(value)) <= max_length: return value
        return value[:max_length//2] + "..."

logger = Logger()

# ─── THE GOD BOT TRADER ───────────────────────────────────────────────────────
class Trader:
    def __init__(self):
        self.limits = {"EMERALDS": 80, "TOMATOES": 80}
        
    def run(self, state: TradingState) -> Tuple[Dict[Symbol, List[Order]], int, str]:
        data = json.loads(state.traderData) if state.traderData else {}
        result: Dict[Symbol, List[Order]] = {}

        for product in ["EMERALDS", "TOMATOES"]:
            if product not in state.order_depths:
                continue

            depth = state.order_depths[product]
            position = state.position.get(product, 0)
            limit = self.limits[product]
            
            buy_cap = limit - position
            sell_cap = limit + position

            best_bid, best_ask = self._get_best_prices(depth)
            if best_bid is None or best_ask is None:
                continue

            # ─── 1. FAIR VALUE (SIGNAL FUSION) ───
            if product == "EMERALDS":
                fv = 10000.0
                risk_factor = 0.03  # Calculated holding cost
                edge = 2.0          # Target profit spread
            else:
                # Signal Fusion: Mid + Wall + Microprice + Order Imbalance
                mid = (best_bid + best_ask) / 2.0
                wall_mid = self._get_wall_mid(depth)
                micro = self._get_microprice(depth)
                
                vol_bid = depth.buy_orders[best_bid]
                vol_ask = abs(depth.sell_orders[best_ask])
                oib = (vol_bid - vol_ask) / (vol_bid + vol_ask)

                # The God-Tier Alpha Fair Value
                raw_signal = mid + 0.6 * (wall_mid - mid) + 0.7 * (micro - mid) + 0.75 * oib
                
                prev_ema = data.get("tomatoes_ema", raw_signal)
                fv = 0.5 * raw_signal + 0.5 * prev_ema
                data["tomatoes_ema"] = fv
                
                risk_factor = 0.05
                edge = 2.5

            # ─── 2. RESERVATION PRICE (INVENTORY SKEW) ───
            # Shift internal valuation based on risk
            res_price = fv - (position * risk_factor)

            # ─── 3. EXECUTION: TAKE ───
            product_orders: List[Order] = []
            take_bid_target = math.floor(res_price - edge)
            take_ask_target = math.ceil(res_price + edge)

            # Sweep mispriced asks
            for ask_price, vol in sorted(depth.sell_orders.items()):
                if ask_price <= take_bid_target and buy_cap > 0:
                    qty = min(-vol, buy_cap)
                    product_orders.append(Order(product, ask_price, qty))
                    buy_cap -= qty
                    position += qty
            
            # Hit rich bids
            for bid_price, vol in sorted(depth.buy_orders.items(), reverse=True):
                if bid_price >= take_ask_target and sell_cap > 0:
                    qty = min(vol, sell_cap)
                    product_orders.append(Order(product, bid_price, -qty))
                    sell_cap -= qty
                    position -= qty

            # ─── 4. EXECUTION: MAKE (SMART PENNYING) ───
            # Update reservation price after TAKE fills
            res_price = fv - (position * risk_factor)
            make_bid = math.floor(res_price - edge)
            make_ask = math.ceil(res_price + edge)

            # Queue Priority Optimization
            if best_bid < make_bid:
                make_bid = min(make_bid, best_bid + 1)
            if best_ask > make_ask:
                make_ask = max(make_ask, best_ask - 1)

            # Hard Spread Safety
            make_bid = min(make_bid, best_ask - 1)
            make_ask = max(make_ask, best_bid + 1)

            if buy_cap > 0:
                product_orders.append(Order(product, make_bid, buy_cap))
            if sell_cap > 0:
                product_orders.append(Order(product, make_ask, -sell_cap))

            result[product] = product_orders
            logger.print(f"{product} | Pos: {position} | FV: {fv:.2f} | Res: {res_price:.2f}")

        trader_data = json.dumps(data)
        logger.flush(state, result, 0, trader_data)
        return result, 0, trader_data

    # ─── HELPERS ───
    def _get_best_prices(self, depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
        best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
        return best_bid, best_ask

    def _get_wall_mid(self, depth: OrderDepth) -> float:
        wall_bid = max(depth.buy_orders.items(), key=lambda x: x[1])[0]
        wall_ask = min(depth.sell_orders.items(), key=lambda x: abs(x[1]))[0]
        return (wall_bid + wall_ask) / 2.0

    def _get_microprice(self, depth: OrderDepth) -> float:
        best_bid, best_ask = self._get_best_prices(depth)
        v_bid = depth.buy_orders[best_bid]
        v_ask = abs(depth.sell_orders[best_ask])
        return (best_bid * v_ask + best_ask * v_bid) / (v_bid + v_ask)
