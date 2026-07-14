"""Small helpers for rendering the Signal Desk brand colors using
Streamlit's native `:color[text]` markdown directive -- no custom CSS.
The `.streamlit/config.toml` theme (dark base + amber primary) supplies
the actual color values; this module just picks which keyword to use."""

DIRECTION_LABEL = {"call": "CALL", "put": "PUT", "neutral": "WAIT"}
DIRECTION_ICON = {"call": "▲", "put": "▼", "neutral": "−"}
DIRECTION_MD_COLOR = {"call": "green", "put": "red", "neutral": "gray"}

STATUS_MD_COLOR = {
    "connected": "green",
    "connecting": "primary",
    "error": "red",
    "disconnected": "gray",
}


def direction_badge(direction: str) -> str:
    color = DIRECTION_MD_COLOR.get(direction, "gray")
    icon = DIRECTION_ICON.get(direction, "•")
    label = DIRECTION_LABEL.get(direction, direction.upper())
    return f":{color}[{icon} {label}]"


def status_badge(status: str) -> str:
    color = STATUS_MD_COLOR.get(status, "gray")
    return f":{color}[● {status.upper()}]"
