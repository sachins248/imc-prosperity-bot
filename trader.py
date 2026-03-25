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

            # Safely get best prices
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            
            if best_bid is None or best_ask is None:
                result[product] = []
                continue

            # ─── 1. FAIR VALUE CALCULATION ───
            if product == "EMERALDS":
                fv = 10000.0
                risk_factor = 0.05  # How much to shift price per unit of inventory
                edge = 1.5          # Minimum profit required per side
                
            elif product == "TOMATOES":
                mid = (best_bid + best_ask) / 2.0
                prev_ema = data.get("tomatoes_ema", mid)
                # Fast, smooth EMA to track drift but ignore 1-tick noise
                fv = 0.45 * mid + 0.55 * prev_ema
                data["tomatoes_ema"] = fv
                
                risk_factor = 0.08  # Higher risk factor for drifting asset
                edge = 1.5

            # ─── 2. THE RESERVATION PRICE ───
            # Shift our internal valuation away from our inventory risk
            reservation_price = fv - (position * risk_factor)

            # Calculate our optimal symmetrical quotes around the reservation price
            target_bid = math.floor(reservation_price - edge)
            target_ask = math.ceil(reservation_price + edge)

            # ─── 3. EXECUTION: TAKE ───
            # ONLY cross the spread if a quote is objectively mispriced against our Reservation Price
            for ask_price, vol in list(depth.sell_orders.items()):
                if ask_price <= target_bid and buy_cap > 0:
                    qty = min(-vol, buy_cap)
                    orders.append(Order(product, ask_price, qty))
                    buy_cap -= qty
                    position += qty
            
            for bid_price, vol in list(depth.buy_orders.items()):
                if bid_price >= target_ask and sell_cap > 0:
                    qty = min(vol, sell_cap)
                    orders.append(Order(product, bid_price, -qty))
                    sell_cap -= qty
                    position -= qty

            # ─── 4. EXECUTION: MAKE ───
            # Recalculate Reservation Price with updated position after taking
            reservation_price = fv - (position * risk_factor)
            make_bid = math.floor(reservation_price - edge)
            make_ask = math.ceil(reservation_price + edge)

            # Queue Priority Optimization (Smart Pennying bounded by our true target edge)
            if best_bid is not None and best_bid < make_bid:
                make_bid = min(make_bid, best_bid + 1)
            if best_ask is not None and best_ask > make_ask:
                make_ask = max(make_ask, best_ask - 1)

            # Hard Safety: Ensure we do not accidentally cross the remaining order book
            if best_ask is not None:
                make_bid = min(make_bid, best_ask - 1)
            if best_bid is not None:
                make_ask = max(make_ask, best_bid + 1)

            # Send passive quotes with our remaining capacity
            if buy_cap > 0:
                orders.append(Order(product, make_bid, buy_cap))
            if sell_cap > 0:
                orders.append(Order(product, make_ask, -sell_cap))

            result[product] = orders

        return result, 0, json.dumps(data)
