import csv
import io
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple

MIN_REQUESTS = 40
MAX_REQUESTS = 50

def load_options_from_rows(rows: List[Dict]) -> List[Dict]:
    return [
        {
            "id": r["id"],
            "ticker": r["ticker"],
            "strike": float(r["strike"]),
            "expiry": r["expiry"],
            "price": float(r["price"]),
            "size": float(r["size"]),
            "date": r["date"] if r["date"] else None,
        }
        for r in rows
    ]

def load_strategies_from_rows(rows: List[Dict]) -> List[Dict]:
    return [
        {
            "id": r["id"],
            "ticker": r["ticker"],
            "strike": r.get("strike"),
            "expiry": r.get("expiry"),
            "buy_price": float(r.get("buy_price", 0)),
            "type": r["type"],
            "amount": float(r.get("amount", 0)),
            "date": r.get("date"),
            "pnl": r.get("pnl"),
        }
        for r in rows
    ]

# generate requests
def generate_requests(ticker: str, ticker_price: float, date: str) -> List[Dict[str, Any]]:
    # generates requests in this format
    # {id, ticker, amount, strike, expiry} - requests
    # where id is unique - date + something, total amount of all generated is between 2 VARS, strike is ticker price and expiry is 4 weeks from date
    req_date = datetime.strptime(date, "%Y-%m-%d").date()
    
    # expiry is friday in 3 weeks
    target_date = req_date + timedelta(weeks=3)
    days_until_friday = (4 - target_date.weekday()) % 7
    expiry = (target_date + timedelta(days=days_until_friday)).isoformat()

    total_amount_target = random.randint(MIN_REQUESTS, MAX_REQUESTS)

    requests = [{
            "id": int(date.replace("-", "")) * 1000,
            "date": date,
            "ticker": ticker,
            "amount": total_amount_target,
            "strike": float(round(ticker_price, 2)),
            "expiry": expiry,
        }]
    # remaining = total_amount_target
    # running_id_prefix = int(date.replace("-", "")) * 1000
    # counter = 1

    # while remaining > 0:
    #     amt = min(random.randint(50, 150), remaining)
    #     requests.append({
    #         "id": running_id_prefix + counter,
    #         "ticker": ticker,
    #         "amount": amt,
    #         "strike": float(round(ticker_price, 2)),
    #         "expiry": expiry,
    #     })

    #     remaining -= amt
    #     counter += 1

    return requests

# first update - state based on todays results
def update_state(result: Dict[str, Any], state: Dict[str, Any], current_date: str) -> Dict[str, Any]:
    updated_state = dict(state)

    # date
    updated_state["date"] = current_date

    # -------------------------
    # active state for next run
    # -------------------------
    updated_state["active_requests"] = result.get("new_requests") or []
    updated_state["active_options"] = result.get("protections") or []
    updated_state["active_strategies"] = (updated_state.get("active_strategies") or []) + (result.get("new_strategies") or [])

    # -------------------------
    # history
    # -------------------------
    updated_state["requests_history"] = (updated_state.get("requests_history") or []) + (result.get("new_requests") or [])

    # save only newly-created options to history
    # updated_state["options_history"] = (
    #     (updated_state.get("protections") or [])
    #     + (result.get("new_options") or [])
    # )
    updated_state["options_history"] = result.get("protections") or []

    updated_state["strategies_history"] = (updated_state.get("strategies_history") or []) + (result.get("new_strategies") or [])

    return updated_state

# second update - state based on todays results - pnl
def update_state_pnl(result: Dict[str, Any], daily_profit: float, state: Dict[str, Any], current_date: str) -> Dict[str, Any]:
    updated_state = dict(state)

    # cumulative pnl / profit
    updated_state["current_pnl"] = float(updated_state.get("current_pnl") or 0.0) + float(daily_profit or 0.0)

    # -------------------------
    # profit history
    # -------------------------
    prev_profit_history = updated_state.get("profit_history") or []
    prev_profit_history.append({
        "date": current_date,
        "daily_profit": float(daily_profit or 0.0),
        "cumulative_profit": float(updated_state["current_pnl"] or 0.0),
        "objective": float(result.get("objective", 0.0) or 0.0),
        "status": result.get("status"),
    })
    updated_state["profit_history"] = prev_profit_history

    return updated_state

def get_ticker_price_from_strategies(
    strategies: List[Dict[str, Any]],
    ticker: str,
    current_date: str,
) -> float:
    target_date = datetime.strptime(current_date, "%Y-%m-%d").date()

    candidates = [
        s for s in strategies
        if s.get("ticker") == ticker
        and s.get("type") == "stock"
        and s.get("date")
    ]

    if not candidates:
        raise ValueError(f"No stock strategy rows found for ticker={ticker}")

    best_row = min(
        candidates,
        key=lambda s: abs(
            (datetime.strptime(s["date"], "%Y-%m-%d").date() - target_date).days
        )
    )

    return float(best_row["buy_price"])


def get_base_option_price_from_options(
    options: List[Dict[str, Any]],
    ticker: str,
    current_date: str,
    ticker_price: float,
) -> float:
    target_date = datetime.strptime(current_date, "%Y-%m-%d").date() + timedelta(weeks=4)

    candidates = [
        o for o in options
        if o.get("ticker") == ticker
    ]

    if not candidates:
        raise ValueError(f"No option rows found for ticker={ticker}")

    def sort_key(o: Dict[str, Any]):
        expiry_date = datetime.strptime(o["expiry"], "%Y-%m-%d").date()
        expiry_distance = abs((expiry_date - target_date).days)
        strike_distance = abs(float(o["strike"]) - float(ticker_price))
        return (expiry_distance, strike_distance)

    best_row = min(candidates, key=sort_key)

    return float(best_row["price"])