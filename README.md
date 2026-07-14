# Signal Desk — Streamlit Edition

A Streamlit port of the Signal Desk Quotex signal dashboard — same
indicator engine (`signal_logic.py` + vendored `quotexapi`), running as a
local web app in your browser (no Replit, no server, no database
required).

> ⚠️ Read-only analysis tool. It never places trades or calls any buy/sell
> endpoint. Confidence scores are not a guaranteed win rate — test on demo
> first.

## Setup

```bash
cd signal-desk-streamlit
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

This opens the app at `http://localhost:8501` in your browser.

## Connecting your Quotex account

Open **Connection** in the sidebar and paste your `.env` contents:

```
QX_EMAIL=trader@example.com
QX_PASSWORD=your_password
QX_COOKIES=<your Quotex session cookies>
QX_TOKEN=<your Quotex session token / SSID>
```

Click **Upload & Connect**. Credentials are saved locally in
`data/signal_desk.db` (SQLite, created next to `app.py`), so the app
reconnects automatically every time you relaunch it — you only need to
paste your session once. The engine keeps the session alive with
periodic pings for as long as the app stays running and your Quotex
session itself remains valid (typically ~24h, same as the web version).

To get `QX_COOKIES` / `QX_TOKEN`, log into Quotex in your browser, open
dev tools → Application/Storage → Cookies, and copy the session cookie
string and the `ssid`/token value.

## Live updates

The app installs `streamlit-autorefresh` so the Dashboard/Connection
pages refresh automatically every ~4 seconds while open, mirroring the
web app's polling behavior. If that package isn't available, use the
"🔄 Refresh now" button in the sidebar instead — the engine keeps
scanning in the background either way.

## What's inside

| File / folder | Purpose |
|---|---|
| `app.py` | Entry point — page config, sidebar nav, event draining |
| `theme.py` | Maps signal/status states to Streamlit's native `:color[]` markdown |
| `storage.py` | Local SQLite persistence (connection + signal history) |
| `engine_worker.py` | Background thread running the Quotex session, keep-alive, periodic scan, and on-demand signal generation |
| `signal_logic.py` | Indicator + confidence scoring engine (identical to the web app's copy) |
| `pairs.py` | Static registry of monitored pairs |
| `quotexapi/` | Vendored Quotex API client (cookie/token session, candles, no trading calls) |
| `env_parser.py` | Parses pasted `.env` text into QX_EMAIL/QX_PASSWORD/QX_COOKIES/QX_TOKEN |
| `views/` | Page renderers: Dashboard, Connection, History, Pairs |

## Notes

- All data is local — nothing leaves your machine except the direct
  connection to Quotex's own servers.
- Delete `data/signal_desk.db` to fully reset saved credentials and
  signal history.
- There's also a Tkinter desktop-app edition of Signal Desk if you'd
  rather have a native window instead of a browser tab.
