import pulp
import math
from typing import List, Dict, Any, Optional
from .pnl import calc_expiry_pnl

# -----------------------------
# CONSTANTS
# -----------------------------
STRATEGY_TYPES = { "stock": 0, "long_call": 2, "short_call": 2, "long_put": 2, "short_put": 2 } # maximum amount of strategies allowed per new options

def is_valid_protection_for_request(req: Dict[str, Any], prot: Dict[str, Any], current_date: Any) -> bool:
    return (
        prot["ticker"] == req["ticker"]
        and float(prot["strike"]) >= float(req["strike"])
        and str(prot["expiry"]) > str(req["expiry"])
        and str(prot["date"]) <= str(current_date)
        # and float(prot.get("available_amount", 0)) > 0
    )

def is_valid_option_for_request(req: Dict[str, Any], prot: Dict[str, Any], current_date: Any) -> bool:
    return (
        prot["ticker"] == req["ticker"]
        and float(prot["strike"]) >= float(req["strike"])
        and str(prot["expiry"]) > str(req["expiry"])
        and str(prot["date"]) == str(current_date)
    )

def is_valid_strategy_for_request(req: Dict[str, Any], strategy: Dict[str, Any], current_date: Any) -> bool:
    if strategy["ticker"] != req["ticker"]:
        return False
    if strategy["type"] == "stock":
        return str(strategy["date"]) == str(current_date)
    return (
        str(strategy["expiry"]) > str(req["expiry"])
        and str(strategy["date"]) == str(current_date)
    )

def solve_milp(
    requests: List[Dict[str, Any]],
    protections: List[Dict[str, Any]],
    options: List[Dict[str, Any]],
    strategies: List[Dict[str, Any]],
    prem,
    base_option_price,
    date):

    # -----------------------------
    # MODEL
    # -----------------------------

    model = pulp.LpProblem("Daily_Protection_MILP", pulp.LpMaximize)

    # -----------------------------
    # PRE-FILTER: compute valid combinations once
    # -----------------------------

    valid_inventory = [
        (r, g) for r in requests for g in protections
        if is_valid_protection_for_request(r, g, date)
    ]
    valid_inventory_pairs_by_gid = {}
    for r, g in valid_inventory:
        if float(g["available_amount"]) > 0:
            valid_inventory_pairs_by_gid.setdefault(g["id"], []).append((r, g))
    valid_inventory_ids = set(valid_inventory_pairs_by_gid.keys())
    protection_by_id = {}
    for g in protections:
        if g["id"] in valid_inventory_ids and g["id"] not in protection_by_id:
            protection_by_id[g["id"]] = g

    valid_option = [
        (r, o) for r in requests for o in options
        if is_valid_option_for_request(r, o, date)
    ]
    # create a list of all the valid options - requests pairs
    valid_option_pairs_by_oid = {}
    for r, o in valid_option:
        valid_option_pairs_by_oid.setdefault(o["id"], []).append((r, o))
    valid_option_ids = set(valid_option_pairs_by_oid.keys())
    option_by_id = {}
    for o in options:
        if o["id"] in valid_option_ids and o["id"] not in option_by_id:
            option_by_id[o["id"]] = o

    # Valid strategies per option (global, not per request) - only for valid options
    valid_strategy_per_option = [
        (o, s) for o in options for s in strategies
        if o["id"] in valid_option_ids
        and any(is_valid_strategy_for_request(r, s, date) for r in requests)
    ]

    # -----------------------------
    # DECISION VARIABLES (only for valid combinations)
    # -----------------------------

    # how much of the request to protect via available options (inventory)
    p_inventory = {
        (r["id"], g["id"]): pulp.LpVariable(
            f"p_inv_{r['id']}_{g['id']}",
            lowBound=0,
            upBound=min(r["amount"], g["available_amount"]),
            cat="Integer"
        )
        for r, g in valid_inventory
    }

    protection_used = {
        gid: pulp.LpVariable(
            f"protection_used_{gid}",
            lowBound=0,
            upBound=1,
            cat="Binary"
        )
        for gid in valid_inventory_ids
    }

    # calcs an upper bound per option
    max_contracts_for_option = {}
    total_coverable_demand_for_option = {}
    for oid, pairs in valid_option_pairs_by_oid.items():
        size = float(pairs[0][1]["size"])
        total_coverable_demand = sum(float(r["amount"]) for r, _ in pairs)
        total_coverable_demand_for_option[oid] = sum(float(r["amount"]) for r, _ in pairs)
        max_contracts_for_option[oid] = math.ceil(total_coverable_demand / size)

    # how many new option contracts to buy — GLOBAL per option, not per request
    new_option_contracts = {
        oid: pulp.LpVariable(
            f"new_contracts_{oid}",
            lowBound=0,
            upBound=max_contracts_for_option[oid],
            cat="Integer"
        )
        for oid in valid_option_ids
    }

    # how much of the request to protect via buying new options
    p_buy = {
        (r["id"], o["id"]): pulp.LpVariable(
            f"p_buy_{r['id']}_{o['id']}",
            lowBound=0,
            upBound=float(r["amount"]),
            cat="Integer"
        )
        for r, o in valid_option
    }

    # variable to make sure if an option is used it is fully used before buying new option
    option_used = {
        oid: pulp.LpVariable(
            f"option_used_{oid}",
            lowBound=0,
            upBound=1,
            cat="Binary"
        )
        for oid in valid_option_ids
    }

    # # how much of the request to protect via cash
    # p_cash = {
    #     r["id"]: pulp.LpVariable(
    #         f"p_cash_{r['id']}",
    #         lowBound=0,
    #         upBound=float(r["amount"]),
    #         cat="Integer"
    #     )
    #     for r in requests
    # }

    # strategies per option — GLOBAL (o_id, s_id), not per request
    new_option_strategies = {
        (o["id"], s["id"]): pulp.LpVariable(
            f"nos_{o['id']}_{s['id']}",
            lowBound=0,
            upBound=STRATEGY_TYPES.get(s["type"], 0) * max_contracts_for_option[o["id"]],
            cat="Integer",
        )
        for o, s in valid_strategy_per_option
    }

    # -----------------------------
    # CONSTRAINTS
    # -----------------------------

    # total allocation from option o cannot exceed total contracts bought AND stop unused options buy
    for oid in valid_option_ids:
        o = option_by_id[oid]
        size = float(o["size"])
        total_buy_for_option = pulp.lpSum(
            p_buy.get((r["id"], oid), 0) for r in requests
        )
        # total allocation from option oid cannot exceed total contracts bought
        model += (
            total_buy_for_option
            <= size * new_option_contracts[oid]
        ), f"buy_total_capacity_{oid}"
        # if option_used = 0 -> cannot buy contracts
        model += (
            new_option_contracts[oid]
            <= max_contracts_for_option[oid] * option_used[oid]
        ), f"contracts_only_if_used_{oid}"
        # if option_used = 0 -> cannot allocate coverage from this option
        model += (
            total_buy_for_option
            <= total_coverable_demand_for_option[oid] * option_used[oid]
        ), f"buy_only_if_used_{oid}"

    # inventory total usage cannot exceed available amount
    for g in protections:
        prot_id = g["id"]
        model += (
            pulp.lpSum(p_inventory.get((r["id"], prot_id), 0) for r in requests)
            <= float(g["available_amount"])
        ), f"inventory_capacity_{prot_id}"

    for gid in valid_inventory_ids:
        g = protection_by_id[gid]
        avail = float(g["available_amount"])
        total_inventory_use_for_protection = pulp.lpSum(
            p_inventory.get((r["id"], gid), 0) for r in requests
        )
        model += (
            total_inventory_use_for_protection
            <= avail * protection_used[gid]
        ), f"inventory_only_if_used_{gid}"
        model += (
            total_inventory_use_for_protection
            >= avail * protection_used[gid] - (avail - 1)
        ), f"inventory_min_use_if_used_{gid}"

    # every request must be fully covered
    for r in requests:
        req_id = r["id"]
        amt = float(r["amount"])
        model += (
            pulp.lpSum(p_inventory.get((req_id, g["id"]), 0) for g in protections)
            + pulp.lpSum(p_buy.get((req_id, o["id"]), 0) for o in options)
            # + p_cash[req_id]
            == amt
        ), f"protect_request_{req_id}"

    # strategy requires option to be bought (global)
    for o, s in valid_strategy_per_option:
        oid, sid = o["id"], s["id"]
        if oid not in new_option_contracts:
            continue
        max_per_contract = STRATEGY_TYPES.get(s["type"], 0)
        model += (
            new_option_strategies[(oid, sid)]
            <= max_per_contract * new_option_contracts[oid]
        ), f"strategy_requires_option_{oid}_{sid}"

    # only 1 per strategy type per option (GLOBAL)
    valid_option_ids = set(new_option_contracts.keys())
    for oid in valid_option_ids:
        for stype, max_per_contract in STRATEGY_TYPES.items():
            model += (
                pulp.lpSum(
                    new_option_strategies.get((oid, s["id"]), 0)
                    for s in strategies if s["type"] == stype
                ) <= max_per_contract * new_option_contracts[oid]
            ), f"max_strategy_type_per_option_{oid}_{stype}"

    # -----------------------------
    # OBJECTIVE FUNCTION
    # -----------------------------

    inventory_base_revenue = pulp.lpSum(
        float(g["price"]) * (1 + prem) * p_inventory.get((r["id"], g["id"]), 0)
        for r, g in valid_inventory
    )

    buy_base_revenue = pulp.lpSum(
        float(o["price"]) * (1 + prem) * p_buy.get((r["id"], o["id"]), 0)
        for r, o in valid_option
    )

    # cash_base_revenue = pulp.lpSum(
    #     base_option_price * (1 + prem) * p_cash[r["id"]]
    #     for r in requests
    # )

    total_revenue = (
        inventory_base_revenue
        + buy_base_revenue
        # + cash_base_revenue
    )

    new_option_cost = pulp.lpSum(
        float(o["price"]) * float(o["size"]) * new_option_contracts[o["id"]]
        for r, o in valid_option
    )

    strategy_cost = pulp.lpSum(
        (-1 if str(s.get("type", "")).lower().startswith("short") else 1)
        # (1 if str(s.get("type", "")).lower().startswith("stock") else 0) # for now calc for stocks only
        * float(s.get("buy_price", 0.0)) * float(s.get("amount", 0.0))
        * new_option_strategies.get((o["id"], s["id"]), 0)
        for o in options for s in strategies
        if (o["id"], s["id"]) in new_option_strategies
    )

    # strategy_pnl = pulp.lpSum(
    #     float(s.get("pnl") or 0.0) * new_option_strategies.get((o["id"], s["id"]), 0)
    #     for o in options for s in strategies
    #     if (o["id"], s["id"]) in new_option_strategies
    # )

    # cash_cost_total = pulp.lpSum(
    #     base_option_price * p_cash[r["id"]]
    #     for r in requests
    # )

    true_profit = (
        total_revenue
        - new_option_cost
        - strategy_cost
        # + strategy_pnl
        # - cash_cost_total
    )

    # model += (
    #     total_revenue
    #     - new_option_cost
    #     - strategy_cost
    #     + strategy_pnl
    #     # - cash_cost_total
    # ), "Linear_Daily_Objective"

    # -----------------------------
    # SOLVE
    # -----------------------------

    # SOLVE - STAGE 1
    # minimize number of opened options
    model.setObjective(
        pulp.lpSum(option_used[oid] for oid in valid_option_ids)
    )
    model.sense = pulp.LpMinimize

    print("valid_inventory", valid_inventory)
    print("valid_option", valid_option)

    solver = pulp.PULP_CBC_CMD(
        msg=True,
        timeLimit=120,
        presolve=True,
        cuts=2,
        strong=10,
    )
    model.solve(solver)
    status_stage1 = pulp.LpStatus[model.status]
    print("status stage 1 =", status_stage1)

    if status_stage1 == "Infeasible":
        return {
            "status": "infeasible",
            "objective": 0.0,
            "new_requests": [],
            "protections": protections,
            "new_options": [],
            "new_strategies": []
        }

    # fix minimum number of opened options
    min_open_options = int(round(pulp.value(
        pulp.lpSum(option_used[oid] for oid in valid_option_ids)
    ) or 0))

    model += (
        pulp.lpSum(option_used[oid] for oid in valid_option_ids)
        == min_open_options
    ), "fix_min_open_options"

    # SOLVE - STAGE 1
    # maximize true profit without penalty distortion
    model.setObjective(true_profit)
    model.sense = pulp.LpMaximize

    model.solve(solver)
    status = pulp.LpStatus[model.status]
    print("status stage 2 =", status)

    # -----------------------------
    # RETURN NEW DATA
    # -----------------------------

    # return new requests with updated premium
    for r in requests:
        rid = r["id"]
        inv_value = sum(
            float(g["price"]) * (1 + prem) * float(p_inventory.get((rid, g["id"])).varValue or 0.0)
            for g in protections if (rid, g["id"]) in p_inventory
        )
        buy_value = sum(
            float(o["price"]) * (1 + prem) * float(p_buy.get((rid, o["id"])).varValue or 0.0)
            for o in options if (rid, o["id"]) in p_buy
        )
        # cash_value = float(base_option_price) * (1 + prem) * float(p_cash[rid].varValue or 0.0)
        r["premium"] = inv_value + buy_value # + cash_value

    # updated protections
    updated_protections = [dict(g) for g in protections]

    # keep ids of only today's newly-bought options
    new_option_ids = []

    for oid in valid_option_ids:
        o = option_by_id[oid]
        bought_contracts = float(new_option_contracts[oid].varValue or 0.0)
        contracts_int = int(round(bought_contracts))

        for k in range(contracts_int):
            new_option_row = {
                "id": f"{oid}__{k+1}",
                "source_option_id": oid,
                "ticker": o["ticker"],
                "strike": o["strike"],
                "expiry": o["expiry"],
                "price": o["price"],
                "date": o["date"],
                "available_amount": float(o["size"]),
            }
            updated_protections.append(dict(new_option_row))
            new_option_ids.append(new_option_row["id"])

    # build index
    protection_index = {p["id"]: p for p in updated_protections}
    protections_by_source_option = {}
    for p in updated_protections:
        src = p.get("source_option_id")
        if src:
            protections_by_source_option.setdefault(src, []).append(p)

    # update available_amount for inventory usage
    for (rid, gid), var in p_inventory.items():
        used = float(var.varValue or 0.0)
        if used > 0:
            protection_index[gid]["available_amount"] -= used

    # update available_amount for bought protections usage
    for (rid, oid), var in p_buy.items():
        used = float(var.varValue or 0.0)
        if used <= 0:
            continue
        remaining = used
        for p in protections_by_source_option.get(oid, []):
            avail = float(p.get("available_amount", 0.0))
            if avail <= 0:
                continue
            take = min(avail, remaining)
            p["available_amount"] = avail - take
            remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            raise ValueError(f"Not enough bought protection capacity for option {oid}: remaining={remaining}")
        
    # build today's new options AFTER available_amount was updated
    new_options = [dict(protection_index[pid]) for pid in new_option_ids]

    # new strategies
    new_strategies = []
    strategy_index = {s["id"]: s for s in strategies}
    for (oid, sid), var in new_option_strategies.items():
        qty = int(round(float(var.varValue or 0.0)))
        if qty > 0:
            s = strategy_index.get(sid)
            for k in range(qty):
                new_strategies.append({
                    "id": f"{sid}__{oid}__{k+1}",
                    "source_strategy_id": sid,
                    "option_id": oid,
                    "ticker": s["ticker"],
                    "strike": s.get("strike") if s else None,
                    "expiry": s.get("expiry") if s else None,
                    "buy_price": s["buy_price"],
                    "type": s["type"],
                    "date": s["date"],
                    "amount": s["amount"]
                })

    return {
        "status": status,
        "objective": float(pulp.value(true_profit) or 0.0),
        "new_requests": requests,
        "protections": list(protection_index.values()),
        "new_options": new_options,
        "new_strategies": new_strategies
    }
