import json
import math
from typing import Any, List, Dict, Tuple, Optional
from datamodel import Order, OrderDepth, TradingState, ProsperityEncoder, Symbol

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
        payload = [self._partial_state(state, self.truncate(state.traderData, max_item)), self.compress_orders(orders), conversions, self.truncate(trader_data, max_item), self.truncate(self.logs, max_item)]
        print(self.to_json(payload))
        self.logs = ""
    def _partial_state(self, state: TradingState, trader_data: str) -> list:
        return [state.timestamp, trader_data, [], {}, [], [], state.position, {}]
    def compress_orders(self, orders: Dict[Symbol, List[Order]]) -> List[List[Any]]:
        compressed = []
        for arr in orders.values():
            for o in arr: compressed.append([o.symbol, o.price, o.quantity])
        return compressed
    def to_json(self, v: Any) -> str:
        return json.dumps(v, cls=ProsperityEncoder, separators=(",", ":"))
    def truncate(self, value: str, max_length: int) -> str:
        if len(json.dumps(value)) <= max_length: return value
        return value[:max_length//2] + "..."

logger = Logger()

class Trader:
    def __init__(self):
        self.limits = {"EMERALDS": 80, "TOMATOES": 80}
        
    def run(self, state: TradingState) -> Tuple[Dict[Symbol, List[Order]], int, str]:
        data = json.loads(state.traderData) if state.traderData else {}
        result: Dict[Symbol, List[Order]] = {}

        for product in ["EMERALDS", "TOMATOES"]:
            if product not in state.order_depths: continue
            depth = state.order_depths[product]
            position = state.position.get(product, 0)
            limit = self.limits[product]
            buy_cap, sell_cap = limit - position, limit + position
            best_bid, best_ask = self._get_best_prices(depth)
            if best_bid is None or best_ask is None: continue

            if product == "EMERALDS":
                fv, risk_factor, edge = 10000.0, 0.02, 1.5 # Ultra-low risk for Emeralds
            else:
                mid = (best_bid + best_ask) / 2.0
                wall_mid = self._get_wall_mid(depth)
                micro = self._get_microprice(depth)
                vol_bid, vol_ask = depth.buy_orders[best_bid], abs(depth.sell_orders[best_ask])
                oib = (vol_bid - vol_ask) / (vol_bid + vol_ask)
                
                # Snappier signal (Alpha 0.8) and high OIB conviction
                raw_signal = mid + 0.4 * (wall_mid - mid) + 0.5 * (micro - mid) + 1.8 * oib
                prev_ema = data.get("tomatoes_ema", raw_signal)
                fv = 0.8 * raw_signal + 0.2 * prev_ema
                data["tomatoes_ema"] = fv
                risk_factor, edge = 0.03, 1.5 # Loosened risk to ride trends

            res_price = fv - (position * risk_factor)
            product_orders: List[Order] = []
            
            # 1. TAKE Phase (The Sniper)
            t_bid, t_ask = math.floor(res_price - edge), math.ceil(res_price + edge)
            for ask_price, vol in sorted(depth.sell_orders.items()):
                if ask_price <= t_bid and buy_cap > 0:
                    qty = min(-vol, buy_cap)
                    product_orders.append(Order(product, ask_price, qty))
                    buy_cap -= qty; position += qty
            for bid_price, vol in sorted(depth.buy_orders.items(), reverse=True):
                if bid_price >= t_ask and sell_cap > 0:
                    qty = min(vol, sell_cap)
                    product_orders.append(Order(product, bid_price, -qty))
                    sell_cap -= qty; position -= qty

            # 2. MAKE Phase (The Ladder)
            # Split remaining capacity into two levels: Competitive and Greedy
            if buy_cap > 0:
                comp_buy_qty = buy_cap // 2
                greedy_buy_qty = buy_cap - comp_buy_qty
                
                comp_bid = min(math.floor(res_price - edge), best_bid + 1)
                greedy_bid = comp_bid - 1 # One tick deeper for big sweeps
                
                product_orders.append(Order(product, int(comp_bid), int(comp_buy_qty)))
                if greedy_buy_qty > 0:
                    product_orders.append(Order(product, int(greedy_bid), int(greedy_buy_qty)))

            if sell_cap > 0:
                comp_sell_qty = sell_cap // 2
                greedy_sell_qty = sell_cap - comp_sell_qty
                
                comp_ask = max(math.ceil(res_price + edge), best_ask - 1)
                greedy_ask = comp_ask + 1
                
                product_orders.append(Order(product, int(comp_ask), int(-comp_sell_qty)))
                if greedy_sell_qty > 0:
                    product_orders.append(Order(product, int(greedy_ask), int(-greedy_sell_qty)))

            result[product] = product_orders

        # 3. Final Flatten (Safety Valve)
        if state.timestamp > 990000: # Last 1% of the day
            risk_factor = 0.5 # Aggressively dump inventory to close at zero

        trader_data = json.dumps(data)
        logger.flush(state, result, 0, trader_data)
        return result, 0, trader_data

    def _get_best_prices(self, depth: OrderDepth):
        return (max(depth.buy_orders.keys()), min(depth.sell_orders.keys())) if depth.buy_orders and depth.sell_orders else (None, None)
    def _get_wall_mid(self, depth: OrderDepth):
        w_bid = max(depth.buy_orders.items(), key=lambda x: x[1])[0]
        w_ask = min(depth.sell_orders.items(), key=lambda x: abs(x[1]))[0]
        return (w_bid + w_ask) / 2.0
    def _get_microprice(self, depth: OrderDepth):
        bb, ba = self._get_best_prices(depth)
        vb, va = depth.buy_orders[bb], abs(depth.sell_orders[ba])
        return (bb * va + ba * vb) / (vb + va)
