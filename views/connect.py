import streamlit as st

import storage
import theme
from env_parser import parse_env_text

PLACEHOLDER = "QX_EMAIL=trader@example.com\nQX_PASSWORD=your_password\nQX_COOKIES=...\nQX_TOKEN=..."


def render(worker):
    st.title("Broker Connection")
    st.caption("Configure your credentials to connect the signal engine.")

    conn = storage.get_connection()
    status = conn.get("status") or "disconnected"

    with st.container(border=True):
        head_col, badge_col = st.columns([4, 1])
        with head_col:
            st.subheader("🖥 Engine Status")
            st.caption("Current connection state to Quotex servers.")
        with badge_col:
            st.markdown(theme.status_badge(status))

        if status == "error" and conn.get("last_error"):
            st.error(conn["last_error"], icon="⚠️")

        c1, c2 = st.columns(2)
        with c1:
            st.caption("ACTIVE ACCOUNT")
            st.markdown(f"`{conn.get('email_masked') or 'None'}`")
        with c2:
            st.caption("LAST CONNECTED")
            last = conn.get("last_connected_at")
            st.markdown(f"`{last[:19].replace('T', ' ') if last else 'Never'}`")

        if status in ("connected", "connecting"):
            if st.button("🗑 Disconnect Engine", type="secondary"):
                worker.stop()
                storage.clear_connection()
                st.session_state.pop("last_generate_result", None)
                st.toast("Disconnected. Signal engine stopped.", icon="🔌")
                st.rerun()

    st.divider()

    with st.container(border=True):
        st.subheader("🔑 Configure Credentials")
        st.caption(
            "Paste your .env file contents below. We extract QX_EMAIL, QX_PASSWORD, QX_COOKIES, and QX_TOKEN. "
            "Once saved, this app reconnects automatically on launch using the saved cookies/token."
        )
        content = st.text_area("Credentials", placeholder=PLACEHOLDER, height=200, label_visibility="collapsed")

        if st.button("⬆ Upload & Connect", type="primary", width="stretch"):
            if not content.strip():
                st.toast("Please paste your .env contents first.", icon="⚠️")
            else:
                parsed = parse_env_text(content)
                email = parsed.get("QX_EMAIL")
                password = parsed.get("QX_PASSWORD")
                cookies = parsed.get("QX_COOKIES")
                token = parsed.get("QX_TOKEN")
                if not cookies or not token:
                    st.toast("QX_COOKIES and QX_TOKEN are required.", icon="⚠️")
                else:
                    storage.save_credentials(email, password, cookies, token)
                    worker.start(email, password, cookies, token, force=True)
                    st.toast("Credentials uploaded — connecting to broker…", icon="🚀")
                    st.rerun()
