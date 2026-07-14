import time

import streamlit as st

import storage
import theme
from pairs import all_pairs

DURATION_OPTIONS = {"1M": 60, "5M": 300, "15M": 900}


def _age_label(created_at_iso: str) -> str:
    try:
        from datetime import datetime, timezone

        created = datetime.fromisoformat(created_at_iso)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        seconds = (datetime.now(timezone.utc) - created).total_seconds()
    except Exception:  # noqa: BLE001
        return ""
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{int(seconds // 3600)}h ago"


def _render_generate_panel(worker):
    st.subheader("✨ Generate Signal")
    st.caption("Pick a pair and an entry time, then pull an on-demand read straight from the live engine.")

    pairs = all_pairs()
    labels = [f"{p['displayName']} ({p['symbol']})" for p in pairs]

    # A form batches the pair/duration pick with the submit click into one
    # atomic rerun. Outside a form, changing the selectbox fires its own
    # immediate rerun (separate from the button click), which is what let
    # the autorefresh timer or a fast double-interaction race the button
    # and "eat" the first press. Inside a form, nothing reruns until
    # "Generate Signal" itself is pressed, so one click always uses
    # whatever pair/duration is currently selected.
    with st.form("generate_signal_form", border=False):
        col1, col2, col3 = st.columns([3, 2, 1.4])
        with col1:
            chosen_label = st.selectbox("Pair", labels, label_visibility="collapsed")
            symbol = pairs[labels.index(chosen_label)]["symbol"]
        with col2:
            duration_label = st.radio(
                "Entry time", list(DURATION_OPTIONS.keys()), horizontal=True, label_visibility="collapsed",
            )
        with col3:
            generate_clicked = st.form_submit_button("Generate Signal", type="primary", width="stretch")

    connected = storage.get_connection().get("status") == "connected"
    if not connected:
        st.caption(":gray[⚠ Connect to Quotex first (see the Connection page) before generating a signal.]")

    if generate_clicked:
        if not connected:
            st.toast("Not connected to Quotex yet.", icon="⚠️")
        else:
            # Set before the blocking call so the *next* rerun (including
            # one queued by the autorefresh timer while we're waiting)
            # skips re-arming autorefresh -- see app.py. Cleared right
            # after, so a single click is all that's ever needed.
            st.session_state["_generating"] = True
            duration_seconds = DURATION_OPTIONS[duration_label]
            try:
                with st.spinner(f"Reading {symbol}…"):
                    result = worker.generate_signal_sync(symbol, duration_seconds)
            finally:
                st.session_state["_generating"] = False
            st.session_state["last_generate_result"] = result
            st.session_state["last_generate_duration"] = duration_seconds

    result = st.session_state.get("last_generate_result")
    if result:
        with st.container(border=True):
            if result.get("error"):
                st.error(result["error"])
            else:
                duration_seconds = st.session_state.get("last_generate_duration", 60)
                now_str = time.strftime("%H:%M:%S")
                expiry_str = time.strftime("%H:%M:%S", time.localtime(time.time() + duration_seconds))
                st.markdown(f"**{result.get('displayName', '')}**  &nbsp; {theme.direction_badge(result.get('direction', 'neutral'))}")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Entry time", now_str)
                c2.metric("Expiration", expiry_str)
                c3.metric("Confidence", f"{result.get('confidence', 0)}%")
                rsi = (result.get("indicators") or {}).get("RSI")
                c4.metric("RSI (14)", f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "—")


def render(worker):
    conn = storage.get_connection()
    header_col, live_col = st.columns([4, 1])
    with header_col:
        st.title("Market Overview")
        st.caption("Real-time technical intelligence across monitored pairs.")
    with live_col:
        if conn.get("status") == "connected":
            st.markdown(":primary[● LIVE ENGINE ACTIVE]")
        else:
            st.markdown(":gray[● ENGINE IDLE]")

    summary = storage.dashboard_summary()
    signals = storage.list_recent_signals(200)
    active = [s for s in signals if s.get("direction") != "neutral"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Monitored Assets", summary["activePairs"])
    c2.metric("Active Signals", len(active), help=f"{len(signals)} total monitored")
    c3.metric("Last 24h Signals", summary["signalsLast24h"], help=f"{summary['callCount']} Calls / {summary['putCount']} Puts")
    c4.metric("Avg Confidence", f"{round(summary['averageConfidence'])}%")

    st.divider()

    with st.container(border=True):
        _render_generate_panel(worker)

    st.divider()
    st.subheader("Signal Grid")

    if not signals:
        st.info("⚡ No active pairs yet. Ensure your broker connection is active and configured correctly on the Connection page.")
        return

    cols_per_row = 4
    rows = [signals[i:i + cols_per_row] for i in range(0, min(len(signals), 24), cols_per_row)]
    for row in rows:
        cols = st.columns(cols_per_row)
        for col, sig in zip(cols, row):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{sig.get('symbol', '')}**")
                    st.caption(sig.get("display_name", ""))
                    st.markdown(theme.direction_badge(sig.get("direction", "neutral")))
                    confidence = sig.get("confidence", 0)
                    st.progress(min(max(confidence, 0), 100) / 100, text=f"{confidence}% confidence")
                    price = sig.get("price")
                    price_text = f"{price:.5f}" if isinstance(price, (int, float)) else "—"
                    st.caption(f"Price: {price_text}  ·  Updated {_age_label(sig.get('created_at', ''))}")
