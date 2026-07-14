"""
engine_worker.py
================
Runs the exact same read-only Quotex signal engine logic as the web app's
python/engine.py (connect, keep-alive, periodic scan of every monitored
pair), as a single background thread with its own asyncio event loop.

Streamlit reruns the whole script on every interaction, so the engine
can't live in a normal local variable -- it lives in a module-level
singleton instead. Since Python only imports this module once per server
process, `get_worker()` / `get_queue()` return the *same* instance across
every rerun and every page view, which is exactly what we want: one
background Quotex session shared by the whole app.

This process never places trades or calls any buy/sell endpoint.
"""

import asyncio
import os
import queue
import sys
import tempfile
import threading
import time
import uuid

# quotexapi computes its session-file base directory from os.getcwd() at
# *import time* -- chdir into a scratch dir first so session.json (which
# holds live broker cookies/token) never lands inside the app folder.
_RUNTIME_DIR = os.path.join(tempfile.gettempdir(), "signal-desk-streamlit")
os.makedirs(_RUNTIME_DIR, exist_ok=True)
_ORIGINAL_CWD = os.getcwd()
os.chdir(_RUNTIME_DIR)

sys.path.insert(0, _ORIGINAL_CWD)

from quotexapi.stable_api import Quotex  # noqa: E402
from signal_logic import calculate_indicators, get_signal_simple, calculate_htf_trend  # noqa: E402
from pairs import all_pairs  # noqa: E402

POLL_INTERVAL_SECONDS = 20
KEEPALIVE_INTERVAL_SECONDS = 30
CANDLE_PERIOD_SECONDS = 60
CANDLE_OFFSET_SECONDS = 3600 * 3
MAX_CONSECUTIVE_KEEPALIVE_FAILURES = 5
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

PAIRS_BY_SYMBOL = {pair["symbol"]: pair for pair in all_pairs()}


async def fetch_candles_df(client: Quotex, symbol: str, period_seconds: int = CANDLE_PERIOD_SECONDS):
    import pandas as pd

    candles = await client.get_candles(
        symbol,
        end_from_time=time.time(),
        offset=CANDLE_OFFSET_SECONDS,
        period=period_seconds,
    )
    if not candles:
        return None
    return pd.DataFrame(candles)


def indicator_snapshot(df) -> dict:
    last = df.iloc[-1]
    keys = [
        "EMA5", "EMA13", "EMA50", "RSI", "MACD", "MACD_Signal",
        "BB_Upper", "BB_Lower", "Stoch_K", "Stoch_D", "CCI", "ATR", "ADX",
    ]
    snapshot = {}
    for key in keys:
        if key in df.columns:
            value = last[key]
            try:
                snapshot[key] = None if value != value else round(float(value), 5)
            except (TypeError, ValueError):
                snapshot[key] = None
    return snapshot


async def compute_signal(client: Quotex, symbol: str, display_name: str, market: str,
                          period_seconds: int = CANDLE_PERIOD_SECONDS) -> dict:
    htf_period = max(period_seconds * 5, 300)
    # The main candle fetch and the higher-timeframe fetch are independent
    # network round-trips -- run them concurrently instead of one after the
    # other so "Generate Signal" comes back in roughly half the time.
    df, htf_df = await asyncio.gather(
        fetch_candles_df(client, symbol, period_seconds),
        fetch_candles_df(client, symbol, htf_period),
        return_exceptions=True,
    )
    if isinstance(df, Exception) or df is None or len(df) < 30:
        return {
            "symbol": symbol, "displayName": display_name, "market": market,
            "direction": "neutral", "confidence": 0, "price": None, "indicators": {},
        }

    try:
        htf_trend = "neutral" if isinstance(htf_df, Exception) else calculate_htf_trend(htf_df)
    except Exception:  # noqa: BLE001
        htf_trend = "neutral"

    df = calculate_indicators(df)
    signal, confidence, reasons = get_signal_simple(df, htf_trend=htf_trend)
    direction = {"CALL": "call", "PUT": "put"}.get(signal, "neutral")
    last_close = df["Close"].iloc[-1]
    price = None if last_close != last_close else float(last_close)

    snapshot = indicator_snapshot(df)
    snapshot["reasons"] = reasons[-6:]

    return {
        "symbol": symbol, "displayName": display_name, "market": market,
        "direction": direction, "confidence": confidence, "price": price,
        "indicators": snapshot,
    }


class EngineWorker:
    """Owns one background thread + asyncio loop that maintains a single
    Quotex session (connect + keep-alive + periodic scan) and can also
    service one-off "generate signal" requests from the UI."""

    def __init__(self, event_queue: "queue.Queue"):
        self.event_queue = event_queue
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Quotex | None = None
        self._tasks = []
        self._running = False

    # -- lifecycle -----------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def is_busy(self) -> bool:
        """True while a connect attempt / session is alive on the
        background thread (used to avoid double-starting on reruns)."""
        return bool(self._thread and self._thread.is_alive())

    def start(self, email: str, password: str, cookies: str, token: str, force: bool = False):
        if self.is_busy():
            if not force:
                return
            self.stop()
            time.sleep(0.3)
        self._thread = threading.Thread(
            target=self._thread_main, args=(email, password, cookies, token), daemon=True
        )
        self._thread.start()

    def stop(self):
        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(self._cancel_all(), self._loop)
        self._running = False

    async def _cancel_all(self):
        for t in self._tasks:
            t.cancel()

    @staticmethod
    def _loop_exception_handler(loop, context):
        """Swallow benign Windows Proactor connection-reset noise.

        On Windows, asyncio's ProactorEventLoop logs a scary but harmless
        traceback (WinError 10054 / ConnectionResetError) whenever a
        websocket the exchange server closed gets torn down during normal
        reconnects. It doesn't affect the engine -- just filter it out and
        let anything else through to the default handler.
        """
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError):
            return
        loop.default_exception_handler(context)

    def _thread_main(self, email, password, cookies, token):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.set_exception_handler(self._loop_exception_handler)
        try:
            self._loop.run_until_complete(self._main(email, password, cookies, token))
        except Exception as exc:  # noqa: BLE001
            self.event_queue.put({"type": "status", "status": "error", "error": str(exc)})
        finally:
            self._running = False

    # -- main engine loop ------------------------------------------------

    async def _main(self, email, password, cookies, token):
        if not cookies or not token:
            self.event_queue.put({
                "type": "status", "status": "error",
                "error": "Missing cookies or token -- cannot start a cookie/token session.",
            })
            return

        self.event_queue.put({"type": "status", "status": "connecting"})

        client = Quotex(email=email or "session-user@example.com",
                         password=password or "session-only",
                         lang="en", user_agent=USER_AGENT)
        client.set_session(user_agent=USER_AGENT, cookies=cookies, ssid=token)
        check, reason = await client.connect()

        if not check:
            self.event_queue.put({"type": "status", "status": "error", "error": str(reason) or "Failed to connect"})
            return

        self._client = client
        self._running = True
        self.event_queue.put({"type": "status", "status": "connected"})

        self._tasks = [
            asyncio.create_task(self._scan_loop(client)),
            asyncio.create_task(self._keep_alive_loop(client)),
        ]
        done, pending = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc:
                self.event_queue.put({"type": "status", "status": "error", "error": str(exc)})
        self._running = False
        self._client = None

    async def _scan_pair(self, client: Quotex, pair: dict):
        try:
            result = await compute_signal(client, pair["symbol"], pair["displayName"], pair["market"])
            self.event_queue.put({"type": "signal", **result})
        except Exception as exc:  # noqa: BLE001
            self.event_queue.put({"type": "log", "level": "warn", "message": f"Failed to scan {pair['symbol']}: {exc}"})

    async def _scan_loop(self, client: Quotex):
        pairs = all_pairs()
        while True:
            for pair in pairs:
                await self._scan_pair(client, pair)
                await asyncio.sleep(1.5)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _keep_alive_loop(self, client: Quotex):
        consecutive_failures = 0
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
            try:
                connected = await Quotex.check_connect()
                if not connected:
                    self.event_queue.put({"type": "log", "level": "warn", "message": "Keep-alive: reconnecting"})
                    await client.connect()
                self.event_queue.put({"type": "keepalive", "at": time.time()})
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                self.event_queue.put({"type": "log", "level": "error", "message": f"Keep-alive failed: {exc}"})
                if consecutive_failures >= MAX_CONSECUTIVE_KEEPALIVE_FAILURES:
                    self.event_queue.put({
                        "type": "status", "status": "error",
                        "error": f"Lost connection to Quotex after {consecutive_failures} failed keep-alive attempts: {exc}",
                    })
                    return

    # -- on-demand "Generate Signal" -------------------------------------

    def generate_signal_sync(self, symbol: str, duration_seconds: int, timeout: float = 25.0) -> dict:
        """Blocking wrapper used by Streamlit's request/response model --
        schedules the coroutine on the engine's own loop and waits for the
        result (or a timeout) before returning."""
        if not self._loop or not self._client:
            return {"error": "Not connected to Quotex."}

        pair = PAIRS_BY_SYMBOL.get(symbol)
        if not pair:
            return {"error": f"Unknown pair: {symbol}"}

        async def _run():
            return await compute_signal(self._client, pair["symbol"], pair["displayName"], pair["market"], duration_seconds)

        try:
            future = asyncio.run_coroutine_threadsafe(_run(), self._loop)
            return future.result(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


# -- module-level singleton --------------------------------------------
# Persists across Streamlit reruns because this module is only imported
# once per server process.

_worker: EngineWorker | None = None
_event_queue: "queue.Queue | None" = None
_boot_attempted = False


def get_queue() -> "queue.Queue":
    global _event_queue
    if _event_queue is None:
        _event_queue = queue.Queue()
    return _event_queue


def get_worker() -> EngineWorker:
    global _worker
    if _worker is None:
        _worker = EngineWorker(get_queue())
    return _worker


def boot_reconnect_once(connection: dict):
    """Mirrors the web app's reconcileConnectionOnBoot: if we already have
    saved cookies/token from a previous session, reconnect automatically
    -- but only try this once per server process, not on every rerun."""
    global _boot_attempted
    if _boot_attempted:
        return
    _boot_attempted = True
    worker = get_worker()
    if not worker.is_busy() and connection.get("cookies") and connection.get("token"):
        worker.start(connection.get("email"), connection.get("password"), connection.get("cookies"), connection.get("token"))


def drain_events_into_storage(storage_module):
    """Pulls every pending engine event off the queue and persists it via
    the given storage module. Call this near the top of every Streamlit
    rerun so displayed data reflects whatever the background thread has
    produced since the last rerun."""
    q = get_queue()
    drained_any = False
    try:
        while True:
            event = q.get_nowait()
            drained_any = True
            etype = event.get("type")
            if etype == "status":
                status = event.get("status")
                if status == "connected":
                    storage_module.set_status("connected", connected=True)
                elif status == "connecting":
                    storage_module.set_status("connecting")
                elif status == "error":
                    storage_module.set_status("error", last_error=event.get("error"))
            elif etype == "signal":
                storage_module.insert_signal(event)
            # "keepalive" / "log" events are informational only.
    except queue.Empty:
        pass
    return drained_any
