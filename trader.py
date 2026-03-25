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
                
                # ─── 1. TAKE: Hit orders crossing our FV ───
                for ask, vol in depth.sell_orders.items():
                    if ask <= fv - 1 and buy_cap > 0:
                        qty = min(-vol, buy_cap)
                        orders.append(Order(product, ask, qty))
                        buy_cap -= qty
                        position += qty # Update position for accurate skewing
                
                for bid, vol in depth.buy_orders.items():
                    if bid >= fv + 1 and sell_cap > 0:
                        qty = min(vol, sell_cap)
                        orders.append(Order(product, bid, -qty))
                        sell_cap -= qty
                        position -= qty
                        
                # ─── 2. MAKE: Smart Pennying with Inventory Skew ───
                skew = (position / 40.0) # Skews quotes up to 2 ticks based on inventory
                target_bid = math.floor(fv - 2.0 - skew)
                target_ask = math.ceil(fv + 2.0 - skew)

                bid_quote = target_bid
                if best_bid is not None and best_bid < target_bid:
                    bid_quote = min(target_bid, best_bid + 1)
                    
                ask_quote = target_ask
                if best_ask is not None and best_ask > target_ask:
                    ask_quote = max(target_ask, best_ask - 1)

                # Hard safety: Never quote at or worse than fair value
                bid_quote = min(bid_quote, int(fv - 1))
                ask_quote = max(ask_quote, int(fv + 1))

                if buy_cap > 0:
                    orders.append(Order(product, bid_quote, buy_cap))
                if sell_cap > 0:
                    orders.append(Order(product, ask_quote, -sell_cap))

                result[product] = orders

            elif product == "TOMATOES":
                if not best_bid or not best_ask:
                    result[product] = []
                    continue
                    
                mid = (best_bid + best_ask) / 2.0
                prev_ema = data.get("tomatoes_ema", mid)
                
                # Fast EMA to track the trending asset without lagging
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
                        
                # ─── 2. MAKE ───
                skew = (position / 40.0) * 1.5 # Skews up to 3 ticks
                target_bid = math.floor(fv - 2.0 - skew)
                target_ask = math.ceil(fv + 2.0 - skew)

                bid_quote = target_bid
                if best_bid is not None and best_bid < target_bid:
                    bid_quote = min(target_bid, best_bid + 1)
                    
                ask_quote = target_ask
                if best_ask is not None and best_ask > target_ask:
                    ask_quote = max(target_ask, best_ask - 1)

                bid_quote = min(bid_quote, math.floor(fv - 1))
                ask_quote = max(ask_quote, math.ceil(fv + 1))

                if buy_cap > 0:
                    orders.append(Order(product, bid_quote, buy_cap))
                if sell_cap > 0:
                    orders.append(Order(product, ask_quote, -sell_cap))

                result[product] = orders

        return result, 0, json.dumps(data)
