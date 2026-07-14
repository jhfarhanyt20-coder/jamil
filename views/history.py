import pandas as pd
import streamlit as st

import storage


def render(worker):
    st.title("Signal History")
    st.caption("Chronological log of all generated signals across pairs.")

    signals = storage.list_signal_history(100)
    if not signals:
        st.info("No signal history available yet.")
        return

    rows = []
    for sig in signals:
        direction = sig.get("direction", "neutral")
        label = {"call": "▲ CALL", "put": "▼ PUT", "neutral": "− WAIT"}.get(direction, direction)
        rows.append({
            "Time": (sig.get("created_at") or "")[:19].replace("T", " "),
            "Asset": sig.get("display_name", ""),
            "Signal": label,
            "Confidence": sig.get("confidence", 0),
            "Price": sig.get("price"),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%d%%"),
            "Price": st.column_config.NumberColumn("Price", format="%.5f"),
        },
    )
