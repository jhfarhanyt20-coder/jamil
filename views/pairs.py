import pandas as pd
import streamlit as st

from pairs import all_pairs


def render(worker):
    st.title("Monitored Pairs")
    st.caption("Registry of all currency pairs watched by the signal engine.")

    pairs = all_pairs()
    query = st.text_input("Search pairs", placeholder="🔍 Search by symbol or name…", label_visibility="collapsed")

    if query:
        q = query.strip().lower()
        pairs = [p for p in pairs if q in p["symbol"].lower() or q in p["displayName"].lower()]

    st.caption(f"Showing {len(pairs)} of {len(all_pairs())} monitored assets.")

    if not pairs:
        st.info(f'No pairs found matching "{query}".')
        return

    df = pd.DataFrame([
        {"Symbol": p["symbol"], "Display Name": p["displayName"], "Market Type": p["market"].upper()}
        for p in pairs
    ])
    st.dataframe(df, width="stretch", hide_index=True)
