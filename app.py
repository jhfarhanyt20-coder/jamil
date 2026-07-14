"""
Signal Desk -- Streamlit Edition
==================================
A Streamlit port of the Signal Desk web dashboard. Same underlying engine
(quotexapi + signal_logic.py indicators), same monitored pairs, running
entirely on your own machine with `streamlit run app.py`.
"""

import streamlit as st

import engine_worker
import storage
import theme
from views import connect, dashboard, history, pairs

st.set_page_config(page_title="Signal Desk", page_icon="⚡", layout="wide")

storage.init_db()

# ── Cloud bootstrap ───────────────────────────────────────────────────────────
# On Streamlit Cloud the filesystem resets on every restart, so the SQLite DB
# starts empty even though the user already put their credentials into
# App Settings → Secrets.  Read st.secrets here and seed the DB row so that
# boot_reconnect_once() below can pick them up and auto-connect -- just like
# a normal localhost session where the DB already has saved credentials.
#
# This block is safe to run on localhost too: if no secrets are configured it
# catches the KeyError/AttributeError quietly and falls through.
try:
    _secrets_cookies = st.secrets.get("QX_COOKIES") or st.secrets.get("qx_cookies")
    _secrets_token   = st.secrets.get("QX_TOKEN")   or st.secrets.get("qx_token")
    if _secrets_cookies and _secrets_token:
        _existing = storage.get_connection()
        if not _existing.get("cookies"):
            # DB is empty (cloud restart) -- seed it from secrets so the
            # auto-reconnect boot sequence works correctly.
            storage.save_credentials(
                st.secrets.get("QX_EMAIL") or st.secrets.get("qx_email") or "",
                st.secrets.get("QX_PASSWORD") or st.secrets.get("qx_password") or "",
                _secrets_cookies,
                _secrets_token,
            )
except Exception:
    # st.secrets unavailable (old Streamlit build) or no secrets configured --
    # fall through and let the user paste credentials manually as usual.
    pass
# ─────────────────────────────────────────────────────────────────────────────

worker = engine_worker.get_worker()

conn = storage.get_connection()
engine_worker.boot_reconnect_once(conn)
engine_worker.drain_events_into_storage(storage)
conn = storage.get_connection()  # re-read: drain may have just updated status/history

# -- optional near-live auto refresh (falls back to manual refresh) --------
# Skipped while a "Generate Signal" click is being processed: the timer
# fires every 4s, but a generate call can take longer than that, and a
# refresh landing mid-click is what makes the button look like it needs
# two presses to register. See views/dashboard.py for the matching flag.
try:
    from streamlit_autorefresh import st_autorefresh

    AUTOREFRESH_AVAILABLE = True
    if not st.session_state.get("_generating"):
        st_autorefresh(interval=4000, key="engine_poll")
except ImportError:
    AUTOREFRESH_AVAILABLE = False

with st.sidebar:
    st.markdown("### ⚡ Signal Desk")
    page = st.radio(
        "Navigate",
        ["Dashboard", "History", "Pairs", "Connection"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("API HEALTH")
    st.markdown(":green[● Ok]")
    st.caption("ENGINE STATUS")
    status = conn.get("status") or "disconnected"
    st.markdown(theme.status_badge(status))
    if conn.get("email_masked"):
        st.caption(conn["email_masked"])
    if not AUTOREFRESH_AVAILABLE:
        st.divider()
        if st.button("🔄 Refresh now", width="stretch"):
            st.rerun()
        st.caption("Tip: `pip install streamlit-autorefresh` for automatic live updates.")

if page == "Dashboard":
    dashboard.render(worker)
elif page == "History":
    history.render(worker)
elif page == "Pairs":
    pairs.render(worker)
elif page == "Connection":
    connect.render(worker)
