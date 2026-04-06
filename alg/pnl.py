import pandas as pd
from typing import List, Dict, Any, Optional

# helper to compare dates
def same_expiry(x: Any, expiry: str) -> bool:
    return isinstance(x, str) and x == expiry

# calc P&L for strategies
def calc_strategy_pnl(expiry_stock_price: float, type: str, amount: float, strike: Optional[float] = None, buy_price: float = 0.0) -> float:
    
    ############ add this because its calculated in milp
    buy_price = 0.0
    
    if type == "stock":
        return (expiry_stock_price - buy_price) * amount

    if strike is None:
        raise ValueError(f"strike is required for option type={type}")
    
    if type == "long_call":
        per_share = max(expiry_stock_price - strike, 0.0) - buy_price
    elif type == "short_call":
        per_share = buy_price - max(expiry_stock_price - strike, 0.0)
    elif type == "long_put":
        per_share = max(strike - expiry_stock_price, 0.0) - buy_price
    elif type == "short_put":
        per_share = buy_price - max(strike - expiry_stock_price, 0.0)
    else:
        raise ValueError(f"Unknown type: {type}")

    return per_share * amount

# calc P&L at expiry
def calc_expiry_pnl (expiry_stock_price: float,
                     expiry: str,
                     protection_requests: List[Dict[str, Any]], 
                     protections: List[Dict[str, Any]], 
                     strategies: List[Dict[str, Any]]) -> float:
    
    premiums = 0.0
    compensation = 0.0
    for r in protection_requests:
        if same_expiry(r.get("expiry"), expiry):
            # get premiums from protection requests
            premiums += float(r.get("premium", 0.0)) * float(r.get("amount", 0.0))
            # calculate compensation for clients
            strike = float(r.get("strike", 0.0))
            amount = float(r.get("amount", 0.0))
            compensation += max(strike - expiry_stock_price, 0.0) * amount

    protection_cost = 0.0
    protection_intrinsic = 0.0
    for p in protections:
        if same_expiry(p.get("expiry"), expiry):
            # price buying the protections
            protection_cost += float(p.get("price", 0.0)) * 100
            strike = float(p.get("strike", 0.0))
            # compensation for my holdings
            protection_intrinsic += max(strike - expiry_stock_price, 0.0) * 100

    strategies_pnl = 0.0
    for s in strategies:
        t = s.get("type")
        if t == "stock" or same_expiry(s.get("expiry"), expiry):
            strike_val = s.get("strike")
            strike_val = float(strike_val) if strike_val is not None else None
            strategies_pnl += calc_strategy_pnl(expiry_stock_price=expiry_stock_price,
                                                type=str(t),
                                                amount=float(s.get("amount", 0.0)),
                                                strike=strike_val,
                                                buy_price=float(s.get("buy_price", 0.0)))

    ############ add this because its calculated in milp
    premiums = 0.0
    protection_cost = 0.0
    # strategies_pnl = 0.0
    final_pnl = premiums - compensation - protection_cost + protection_intrinsic + strategies_pnl
    return final_pnl