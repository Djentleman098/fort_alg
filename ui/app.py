import streamlit as st
import json
import os
import re
import subprocess
import sys
from datetime import date
from collections import Counter

import plotly.graph_objects as go

# ── paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE_FILE = os.path.join(PROJECT_DIR, "state.json")
ORCHA_PATH = os.path.join(PROJECT_DIR, "orchastrator.py")
MANAGE_PATH = os.path.join(PROJECT_DIR, "manage_data.py")

ICON = "C:\\Users\\johna\\Documents\\Programming\\fort milp\\icon\\images\\bull2.png"

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MILP Options Simulator",
    page_icon=ICON if os.path.exists(ICON) else None,
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("MILP Options Trading Simulator")

# ── helpers for reading / writing constants ───────────────────────────────────


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def set_constant(path, var_name, value, multiline=False):
    content = read_file(path)
    if isinstance(value, str):
        new_val = f'"{value}"'
    else:
        new_val = str(value)
    pattern = re.compile(rf"^({var_name}\s*=\s*).+$", re.MULTILINE)
    content = pattern.sub(rf"\g<1>{new_val}", content)
    write_file(path, content)


def set_multiline_constant(path, var_name, value):
    """Replace a multiline assignment (e.g. dict literal) for var_name."""
    content = read_file(path)
    # match: VAR_NAME = { ...everything until closing } at start of line...}
    pattern = re.compile(rf"^({var_name}\s*=\s*){{[\s\S]*?}}", re.MULTILINE)
    content = pattern.sub(rf"{var_name} = {value}", content)
    write_file(path, content)


# ── load state ───────────────────────────────────────────────────────────────


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ── render the full dashboard page ──────────────────────────────────────────


def render_page(state, is_live=False):
    if is_live:
        st.divider()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Date", state.get("date", "-"))
    col2.metric(
        "Cumulative PnL",
        f"${state.get('current_pnl', 0):,.2f}"
        if state.get("current_pnl") is not None
        else "-",
    )
    col3.metric("Total Requests", f"{sum(int(r.get('amount', 0)) for r in state.get('requests_history', [])):,}")
    col4.metric("Active Options", len(state.get("active_options", [])))
    col5.metric("Active Strategies", len(state.get("active_strategies", [])))

    st.divider()

    # ── charts ───────────────────────────────────────────────────────────

    # 1) PnL history
    profit_history = state.get("profit_history", [])
    if profit_history:
        dates = [p["date"] for p in profit_history]
        daily = [p["daily_profit"] for p in profit_history]
        cumulative = [p["cumulative_profit"] for p in profit_history]
        objectives = [p["objective"] for p in profit_history]

        st.subheader("Profit & Loss")

        fig_pnl = go.Figure()
        fig_pnl.add_trace(
            go.Scatter(
                x=dates,
                y=cumulative,
                name="Cumulative PnL",
                mode="lines+markers",
                line=dict(color="#1f77b4", width=2),
                marker=dict(size=8),
            )
        )
        fig_pnl.add_trace(
            go.Bar(
                x=dates,
                y=daily,
                name="Daily Profit",
                opacity=0.4,
                marker_color="#ff7f0e",
            )
        )
        fig_pnl.update_layout(barmode="overlay", hovermode="x unified", height=350)
        st.plotly_chart(fig_pnl, use_container_width=True)

        fig_obj = go.Figure()
        fig_obj.add_trace(
            go.Bar(x=dates, y=objectives, name="Objective Value", marker_color="#2ca02c")
        )
        fig_obj.add_trace(
            go.Bar(x=dates, y=daily, name="Daily Profit", marker_color="#ff7f0e")
        )
        fig_obj.update_layout(barmode="group", hovermode="x unified", height=300)
        st.plotly_chart(fig_obj, use_container_width=True)

    # 2) Options history
    options_history = state.get("options_history", [])
    if options_history:
        st.subheader("Options")
        st.dataframe(options_history, use_container_width=True, hide_index=True)

        strike_counts = Counter(str(o.get("strike")) for o in options_history)
        fig_strike = go.Figure(
            data=[
                go.Bar(
                    x=list(strike_counts.keys()),
                    y=list(strike_counts.values()),
                    marker_color="#d62728",
                )
            ]
        )
        fig_strike.update_layout(
            xaxis_title="Strike", yaxis_title="Count", height=300
        )
        st.plotly_chart(fig_strike, use_container_width=True)

        expiry_counts = Counter(
            o.get("expiry", "unknown") for o in options_history
        )
        fig_expiry = go.Figure(
            data=[
                go.Bar(
                    x=list(expiry_counts.keys()),
                    y=list(expiry_counts.values()),
                    marker_color="#9467bd",
                )
            ]
        )
        fig_expiry.update_layout(
            xaxis_title="Expiry", yaxis_title="Count", height=300
        )
        st.plotly_chart(fig_expiry, use_container_width=True)

    # 3) Strategies history
    strategies_history = state.get("strategies_history", [])
    if strategies_history:
        st.subheader("Strategies")
        st.dataframe(strategies_history, use_container_width=True, hide_index=True)

        type_counts = Counter(s.get("type", "unknown") for s in strategies_history)
        fig_type = go.Figure(
            data=[
                go.Pie(
                    labels=list(type_counts.keys()),
                    values=list(type_counts.values()),
                    hole=0.4,
                )
            ]
        )
        fig_type.update_layout(height=350)
        st.plotly_chart(fig_type, use_container_width=True)

        fig_price = go.Figure()
        for idx, s in enumerate(strategies_history):
            fig_price.add_trace(
                go.Bar(
                    x=[f"{s.get('type')}@{s.get('strike')}"],
                    y=[s.get("buy_price", 0)],
                    name=s.get("type"),
                )
            )
        fig_price.update_layout(
            barmode="overlay",
            xaxis_title="Strategy",
            yaxis_title="Buy Price",
            height=300,
        )
        st.plotly_chart(fig_price, use_container_width=True)

    # 4) Requests history
    requests_history = state.get("requests_history", [])
    if requests_history:
        st.subheader("Requests")
        req_dates = [r.get("date", "-") for r in requests_history]
        req_amounts = [float(r.get("amount", 0)) for r in requests_history]
        req_strikes = [float(r.get("strike", 0)) for r in requests_history]

        fig_req = go.Figure()
        fig_req.add_trace(
            go.Bar(
                x=req_dates,
                y=req_amounts,
                name="Request Amount",
                marker_color="#8c564b",
            )
        )
        fig_req.update_layout(xaxis_title="Date", yaxis_title="Amount", height=300)
        st.plotly_chart(fig_req, use_container_width=True)

        fig_strikes = go.Figure()
        fig_strikes.add_trace(
            go.Scatter(
                x=req_dates,
                y=req_strikes,
                mode="lines+markers",
                name="Request Strike",
                line=dict(color="#1ac9b6"),
            )
        )
        fig_strikes.update_layout(
            xaxis_title="Date", yaxis_title="Strike Price", height=300
        )
        st.plotly_chart(fig_strikes, use_container_width=True)


# ── load current constants ───────────────────────────────────────────────────

orch_content = read_file(ORCHA_PATH)
manage_content = read_file(MANAGE_PATH)


def extract_val(content, var_name, cast=str):
    m = re.search(rf"^({var_name}\s*=\s*)(.+)$", content, re.MULTILINE)
    if m:
        raw = m.group(2).strip()
        # extract trailing inline comment as description
        desc = None
        if not raw.startswith("{"):
            if "#" in raw:
                idx = raw.index("#")
                desc = raw[idx+1:].strip()
                raw = raw[:idx].strip()
        raw = raw.strip('"').strip("'")
        return cast(raw), desc
    return None, None


cur_days_forward, desc_days_forward = extract_val(orch_content, "DAYS_FORWARD", int)
cur_max_expiry, desc_max_expiry = extract_val(orch_content, "MAX_EXPIRY", int)
cur_min_strike, desc_min_strike = extract_val(orch_content, "MIN_STRIKE_PERC", float)
cur_max_strike, desc_max_strike = extract_val(orch_content, "MAX_STRIKE_PERC", float)
cur_start_date, desc_start_date = extract_val(orch_content, "START_DATE", str)
cur_steps, desc_steps = extract_val(orch_content, "STEPS", int)
cur_ticker, desc_ticker = extract_val(orch_content, "TICKER", str)
cur_prem, desc_prem = extract_val(orch_content, "PREM", float)
milp_content = read_file(os.path.join(PROJECT_DIR, "alg", "milp.py"))

cur_req_expiry_weeks, desc_req_expiry_weeks = extract_val(manage_content, "REQ_EXPIRY_WEEKS", int)

cur_min_requests, desc_min_requests = extract_val(manage_content, "MIN_REQUESTS", int)
cur_max_requests, desc_max_requests = extract_val(manage_content, "MAX_REQUESTS", int)

# extract multiline STRATEGY_TYPES dict — value only, no comments
_m = re.search(rf"^STRATEGY_TYPES\s*=\s*({{[\s\S]*?}})", milp_content, re.MULTILINE)
cur_strategy_types = None
if _m:
    cur_strategy_types = _m.group(1).strip()

state = load_state()

# ── sidebar: controls ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Parameters")

    with st.form("constants_form"):
        st.subheader("Orchestrator")
        days_forward = st.number_input(
            f"DAYS_FORWARD {desc_days_forward or ''}", value=cur_days_forward, min_value=1
        )
        max_expiry = st.number_input(
            f"MAX_EXPIRY {desc_max_expiry or ''}", value=cur_max_expiry, min_value=1
        )
        min_strike = st.number_input(
            f"MIN_STRIKE_PERC {desc_min_strike or ''}", value=cur_min_strike, min_value=0.0, max_value=1.0, step=0.01
        )
        max_strike = st.number_input(
            f"MAX_STRIKE_PERC {desc_max_strike or ''}", value=cur_max_strike, min_value=1.0, max_value=2.0, step=0.01
        )
        try:
            _sd = date.fromisoformat(cur_start_date) if cur_start_date else date(2024, 1, 8)
        except (ValueError, TypeError):
            _sd = date(2024, 1, 8)
        start_date = st.date_input(
            f"START_DATE {desc_start_date or ''}",
            value=_sd,
        )
        steps = st.number_input(f"STEPS {desc_steps or ''}", value=cur_steps, min_value=1)
        ticker = st.text_input(f"TICKER {desc_ticker or ''}", value=cur_ticker or "AAPL")
        prem = st.number_input(
            f"PREM {desc_prem or ''}", value=cur_prem, min_value=0.0, step=0.01
        )

        st.subheader("Request Generation")
        req_expiry_weeks = st.number_input(
            f"REQ_EXPIRY_WEEKS {desc_req_expiry_weeks or ''}", value=cur_req_expiry_weeks, min_value=1
        )
        min_requests = st.number_input(
            f"MIN_REQUESTS {desc_min_requests or ''}", value=cur_min_requests, min_value=1
        )
        max_requests = st.number_input(
            f"MAX_REQUESTS {desc_max_requests or ''}", value=cur_max_requests, min_value=1
        )

        strategy_types_json = st.text_area("STRATEGY_TYPES", value=cur_strategy_types or '{ "stock": 1, "long_call": 1, "short_call": 1, "long_put": 1, "short_put": 1 }', height=120, key="stypes")

        submitted = st.form_submit_button("Save Parameters")
        if submitted:
            print(f"=== FORM SUBMITTED === strategy_types_json = {repr(strategy_types_json[:100])}")
            # save all params from inside the form (we have direct access here)
            set_constant(ORCHA_PATH, "DAYS_FORWARD", days_forward)
            set_constant(ORCHA_PATH, "MAX_EXPIRY", max_expiry)
            set_constant(ORCHA_PATH, "MIN_STRIKE_PERC", min_strike)
            set_constant(ORCHA_PATH, "MAX_STRIKE_PERC", max_strike)
            set_constant(ORCHA_PATH, "START_DATE", str(start_date))
            set_constant(ORCHA_PATH, "STEPS", steps)
            set_constant(ORCHA_PATH, "TICKER", ticker)
            set_constant(ORCHA_PATH, "PREM", prem)
            set_constant(MANAGE_PATH, "REQ_EXPIRY_WEEKS", req_expiry_weeks)
            set_constant(MANAGE_PATH, "MIN_REQUESTS", min_requests)
            set_constant(MANAGE_PATH, "MAX_REQUESTS", max_requests)
            strategy_list = strategy_types_json.strip()
            json.loads(strategy_list)
            _milp_path = os.path.join(PROJECT_DIR, "alg", "milp.py")
            set_multiline_constant(_milp_path, "STRATEGY_TYPES", strategy_list)
            st.success("Parameters saved!")

    # ── action buttons ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Actions")

    if st.button("Start Simulation", type="primary", use_container_width=True):
        error_log = os.path.join(PROJECT_DIR, "ui", ".sim_error.log")
        empty_state = {
            "date": None,
            "current_pnl": None,
            "active_requests": [],
            "active_options": [],
            "active_strategies": [],
            "requests_history": [],
            "options_history": [],
            "strategies_history": [],
            "profit_history": [],
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(empty_state, f, indent=4, ensure_ascii=False)

        with open(error_log, "w", encoding="utf-8") as f:
            pass  # truncate

        proc = subprocess.Popen(
            [sys.executable, ORCHA_PATH],
            cwd=PROJECT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=open(error_log, "w", encoding="utf-8"),
            text=True,
        )
        st.session_state["sim_pid"] = proc.pid
        st.session_state["sim_process"] = proc
        st.rerun()

    if st.button("Reset State", use_container_width=True):
        empty_state = {
            "date": None,
            "current_pnl": None,
            "active_requests": [],
            "active_options": [],
            "active_strategies": [],
            "requests_history": [],
            "options_history": [],
            "strategies_history": [],
            "profit_history": [],
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(empty_state, f, indent=4, ensure_ascii=False)
        st.success("State reset!")
        st.rerun()

# ── live simulation polling (polls during simulation, renders full page) ─────

sim_running = "sim_process" in st.session_state

if sim_running:
    # ── stop button (shown during sim) ─────────────────────────────────
    if st.button("Stop Simulation", use_container_width=True):
        _proc = st.session_state.get("sim_process")
        if _proc:
            _proc.kill()
        st.session_state.pop("sim_process", None)
        st.session_state.pop("sim_pid", None)
        st.warning("Simulation stopped by user.")
        st.rerun()

    proc = st.session_state["sim_process"]
    poll_result = proc.poll()

    if poll_result is None:
        import time
        state = load_state()  # live re-read
        date_now = state.get("date")
        pnl_now = state.get("current_pnl")
        label = f"**Date:** {date_now}  |  **Cumulative PnL:** ${pnl_now:,.2f}" if (date_now and pnl_now is not None) else "Initializing…"
        st.info(f"Simulation running...  {label}")
        st.warning("Dashboard below shows data accumulated so far — page will auto-refresh.")
        render_page(state, is_live=True)
        time.sleep(1.5)
        st.rerun()
    else:
        del st.session_state["sim_process"]
        del st.session_state["sim_pid"]
        if poll_result == 0:
            state = load_state()
            render_page(state)
            st.success("Simulation completed!")
        else:
            err_log = os.path.join(PROJECT_DIR, "ui", ".sim_error.log")
            if os.path.exists(err_log):
                with open(err_log, "r", encoding="utf-8") as f:
                    err = f.read(4000)
            else:
                err = "No error log found"
            st.error(f"Simulation failed:\n{err}")
else:
    # no simulation running
    if not state.get("date"):
        st.info(
            "No simulation data yet. Set parameters in the sidebar and click **Start Simulation**."
        )
    else:
        render_page(state)
