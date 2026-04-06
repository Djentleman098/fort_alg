import json
import os
import csv
from datetime import datetime, timedelta
from typing import List, Dict
from alg.milp import solve_milp
from alg.pnl import calc_expiry_pnl
from manage_data import load_options_from_rows, load_strategies_from_rows
from manage_data import generate_requests, update_state, update_state_pnl
from manage_data import get_ticker_price_from_strategies, get_base_option_price_from_options

STATE_FILE = "state.json"

DAYS_FORWARD = 60
MAX_EXPIRY = 40

MIN_STRIKE_PERC = 0.9
MAX_STRIKE_PERC = 1.1

START_DATE = "2024-01-08"
STEPS = 100
TICKER = "AAPL"
PREM = 0.2

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {
            "date": None,
            "current_pnl": 0.0,
            # active state for next run
            "active_requests": [],
            "active_options": [],
            "active_strategies": [],
            # history
            "requests_history": [],
            "options_history": [],
            "strategies_history": [],
            "profit_history": []
        }
    with open(STATE_FILE, "r", encoding="utf-8") as file:
        state = json.load(file)
    state.setdefault("date", None)
    state.setdefault("current_pnl", 0.0)
    state.setdefault("active_requests", [])
    state.setdefault("active_options", [])
    state.setdefault("active_strategies", [])
    state.setdefault("requests_history", [])
    state.setdefault("options_history", [])
    state.setdefault("strategies_history", [])
    state.setdefault("profit_history", [])
    return state

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=4, ensure_ascii=False)

# --------------------------------------------------------------------------
# CSV pre-parsing
# --------------------------------------------------------------------------

_DATETIME_CACHE = {}

def _parse_date_cached(s: str):
    if s not in _DATETIME_CACHE:
        _DATETIME_CACHE[s] = datetime.strptime(s, "%Y-%m-%d").date()
    return _DATETIME_CACHE[s]

def preparse_csv(path: str) -> List[Dict]:
    """Read a CSV once, return rows with pre-parsed date objects."""
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        result = []
        for row in reader:
            r = dict(row)
            r["_date"] = _parse_date_cached(r["date"]) if r.get("date") else None
            r["_expiry"] = _parse_date_cached(r["expiry"]) if r.get("expiry") else None
            result.append(r)
    return result

def filter_by_date_fast(
    rows: List[Dict],
    current_date,
    ticker_price: float,
    days_forward: int = DAYS_FORWARD,
) -> List[Dict]:
    max_date = current_date + timedelta(days=days_forward)
    lo, hi = ticker_price * MIN_STRIKE_PERC, ticker_price * MAX_STRIKE_PERC
    result = []
    for r in rows:
        d = datetime.strptime(r.get("date"), "%Y-%m-%d").date()
        if d is None or d < current_date or d > max_date:
            continue
        strike = r.get("strike")
        if strike and float(strike) != 0 and not (lo <= float(strike) <= hi):
            continue
        expiry = r.get("expiry")
        max_expiry = current_date + timedelta(days=MAX_EXPIRY)
        if expiry and not (current_date < datetime.strptime(expiry, "%Y-%m-%d").date() <= max_expiry):
            continue
        result.append(r)
    return result

# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main():
    # load and pre-parse CSVs once at startup
    all_options_raw = preparse_csv("data/options.csv")
    all_strategies_raw = preparse_csv("data/strategies.csv")

    # loop params
    start_date = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    steps = STEPS
    ticker = TICKER
    prem = PREM

    # initialize state
    state = load_state()
    if not state["date"]:
        state["date"] = start_date.isoformat()

    print(state["date"], "Starting loop.")

    for _ in range(steps):
        current_date_str = state["date"]
        current_date = datetime.strptime(current_date_str, "%Y-%m-%d").date()

        # fetch stock price and base option price
        ticker_price = get_ticker_price_from_strategies(
            strategies=load_strategies_from_rows(all_strategies_raw),
            ticker=ticker,
            current_date=current_date_str,
        )
        base_option_price = get_base_option_price_from_options(
            options=load_options_from_rows(all_options_raw),
            ticker=ticker,
            current_date=current_date_str,
            ticker_price=ticker_price,
        )
        print(current_date_str, " Fetched base prices - ", "$", ticker_price, "$", base_option_price)

        # filter options and strategies (fast -- pre-parsed dates)
        filtered_options_raw = filter_by_date_fast(all_options_raw, current_date, ticker_price)
        filtered_strategies_raw = filter_by_date_fast(all_strategies_raw, current_date, ticker_price)
        options = load_options_from_rows(filtered_options_raw)
        strategies = load_strategies_from_rows(filtered_strategies_raw)
        print(current_date_str, "Filtered Options", len(options))
        print(current_date_str, "Filtered Strategies", len(strategies))

        # generate daily inputs
        daily_requests = generate_requests(ticker, ticker_price, current_date_str)
        print(current_date_str, "Generated requests.")

        # load current options
        current_protections = state["options_history"]

        result = solve_milp(daily_requests, current_protections, options, strategies, prem, base_option_price, current_date_str)

        if result["status"] == "infeasible":
            print(current_date_str, "SKIPPED — no feasible coverage for today")
            next_date = current_date + timedelta(days=1)
            state["date"] = next_date.isoformat()
            continue

        print(current_date_str, "Finished MILP", "$", result["objective"])

        # update the state with requests, options, strategy
        state = update_state(result, state, current_date_str)

        # load current state data after updating
        current_requests = state["requests_history"]
        current_strategies = state["strategies_history"]
        current_protections = state["options_history"]

        # calculate PNL @ expires today
        pnl = calc_expiry_pnl(ticker_price, current_date_str, current_requests, current_protections, current_strategies)
        print(current_date_str, "PNL", "$", pnl)
        daily_profit = float(pnl or 0.0) + float(result.get("objective") or 0.0)
        
        # update the state with pnl
        state = update_state_pnl(result, daily_profit, state, current_date_str)

        save_state(state)
        print(current_date_str, "Saved State.")

        # continue to next day
        next_date = datetime.strptime(state["date"], "%Y-%m-%d").date() + timedelta(days=1)
        state["date"] = next_date.isoformat()

    print("Done.")
    print(json.dumps(state, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    main()
