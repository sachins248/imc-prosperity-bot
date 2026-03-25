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
                        position += qty
                
                for bid, vol in depth.buy_orders.items():
                    if bid >= fv + 1 and sell_cap > 0:
                        qty = min(vol, sell_cap)
                        orders.append(Order(product, bid, -qty))
                        sell_cap -= qty
                        position -= qty
                        
                # ─── 2. MAKE: Smart Pennying with Inventory Skew ───
                skew = (position / 40.0)
                target_bid = math.floor(fv - 2.0 - skew)
                target_ask = math.ceil(fv + 2.0 - skew)

                bid_quote = target_bid
                if best_bid is not None and best_bid < target_bid:
                    bid_quote = min(target_bid, best_bid + 1)
                    
                ask_quote = target_ask
                if best_ask is not None and best_ask > target_ask:
                    ask_quote = max(target_ask, best_ask - 1)

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
                    
                # ─── ADVANCED OIB FAIR VALUE ───
                mid = (best_bid + best_ask) / 2.0
                
                wall_bid = max(depth.buy_orders.items(), key=lambda level: level[1])[0]
                wall_ask = min(depth.sell_orders.items(), key=lambda level: abs(level[1]))[0]
                wall_mid = (wall_bid + wall_ask) / 2.0
                
                bid_vol = depth.buy_orders[best_bid]
                ask_vol = abs(depth.sell_orders[best_ask])
                microprice = (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)
                oib = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                
                raw_signal = mid + 0.7 * (wall_mid - mid) + 0.8 * (microprice - mid) + 0.75 * oib
                
                prev_ema = data.get("tomatoes_ema", raw_signal)
                fv = 0.45 * raw_signal + 0.55 * prev_ema
                data["tomatoes_ema"] = fv
                
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
                skew = (position / 40.0) * 1.5 
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

                # Safe sizing to avoid over-committing on heavy skew
                safe_buy = min(buy_cap, max(0, limit - position))
                safe_sell = min(sell_cap, max(0, limit + position))

                if safe_buy > 0:
                    orders.append(Order(product, bid_quote, safe_buy))
                if safe_sell > 0:
                    orders.append(Order(product, ask_quote, -safe_sell))

                result[product] = orders

        return result, 0, json.dumps(data)
