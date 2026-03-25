import json
import math
from typing import Dict, List
from datamodel import Order, OrderDepth, TradingState

class Trader:
    def run(self, state: TradingState):
        data = json.loads(state.traderData) if state.traderData else {}
        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            if product not in ["EMERALDS", "TOMATOES"]:
                continue

            orders: List[Order] = []
            position = state.position.get(product, 0)
            limit = 80
            
            buy_cap = limit - position
            sell_cap = limit + position

            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None

            if product == "EMERALDS":
                fv = 10000.0
                
                # ─── 1. TAKE: Sweep mispriced orders ───
                for ask, vol in depth.sell_orders.items():
                    if ask <= fv - 1 and buy_cap > 0:
                        qty = min(-vol, buy_cap)
                        orders.append(Order(product, ask, qty))
                        buy_cap -= qty
                        position += qty
                
                for bid, vol in depth.buy_orders.items():
                    if bid >= fv + 1 and sell_cap > 0:
                        qty = min(vol, sell_cap)
                        orders.append(Order(product, bid, -qty))
                        sell_cap -= qty
                        position -= qty
                        
                # ─── 2. MAKE: Strict Target Quoting (No Pennying) ───
                skew = (position / 40.0) * 1.5 
                bid_quote = math.floor(fv - 2.0 - skew)
                ask_quote = math.ceil(fv + 2.0 - skew)

                # Hard safety: Never cross the current spread
                if best_ask is not None:
                    bid_quote = min(bid_quote, best_ask - 1)
                if best_bid is not None:
                    ask_quote = max(ask_quote, best_bid + 1)

                if buy_cap > 0:
                    orders.append(Order(product, bid_quote, buy_cap))
                if sell_cap > 0:
                    orders.append(Order(product, ask_quote, -sell_cap))

                result[product] = orders

            elif product == "TOMATOES":
                if not best_bid or not best_ask:
                    result[product] = []
                    continue
                    
                # ─── The 2512 Smooth Brain ───
                mid = (best_bid + best_ask) / 2.0
                prev_ema = data.get("tomatoes_ema", mid)
                
                ema = 0.45 * mid + 0.55 * prev_ema
                data["tomatoes_ema"] = ema
                fv = ema
                
                # ─── 1. TAKE ───
                for ask, vol in depth.sell_orders.items():
                    if ask <= math.floor(fv - 1) and buy_cap > 0:
                        qty = min(-vol, buy_cap)
                        orders.append(Order(product, ask, qty))
                        buy_cap -= qty
                        position += qty
                
                for bid, vol in depth.buy_orders.items():
                    if bid >= math.ceil(fv + 1) and sell_cap > 0:
                        qty = min(vol, sell_cap)
                        orders.append(Order(product, bid, -qty))
                        sell_cap -= qty
                        position -= qty
                        
                # ─── 2. MAKE: Strict Quoting + Aggressive Eject Skew ───
                # Increased skew slightly to act as a stronger eject button since we are taking more volume
                skew = (position / 40.0) * 2.0 
                bid_quote = math.floor(fv - 2.0 - skew)
                ask_quote = math.ceil(fv + 2.0 - skew)

                # Hard safety: Never cross the current spread
                if best_ask is not None:
                    bid_quote = min(bid_quote, best_ask - 1)
                if best_bid is not None:
                    ask_quote = max(ask_quote, best_bid + 1)

                # Cap max passive order size slightly so we don't get 80-lot dumped on in a single tick
                safe_buy = min(buy_cap, 40)
                safe_sell = min(sell_cap, 40)

                if safe_buy > 0:
                    orders.append(Order(product, bid_quote, safe_buy))
                if safe_sell > 0:
                    orders.append(Order(product, ask_quote, -safe_sell))

                result[product] = orders

        return result, 0, json.dumps(data)
