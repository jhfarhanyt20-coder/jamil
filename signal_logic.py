"""
signal_logic.py
================
1-Minute Binary Trading Signal Logic (Multi-Indicator Confirmation - Enhanced)

IMPORTANT HONESTY NOTE:
No combination of indicators reliably achieves 70-90% accuracy on
1-minute binary options. This module gives you a CONFIDENCE SCORE
(0-100%) based on how many independent signals agree with each other.
That confidence score is NOT a guaranteed win-rate. Test on demo first.

ENHANCEMENTS:
- Added CCI (Commodity Channel Index) with extreme levels
- Full candlestick pattern detection (engulfing, doji, hammer, shooting star, etc.)
- Compound scoring: More confirmations = higher confidence
- Reversal strategy integration (counter-trend signals)
- Pivot-level entry trigger (classic floor pivot from recent range)
- Real RSI divergence detection (price vs RSI swing comparison)
"""

import pandas as pd
import numpy as np
from datetime import datetime


def get_next_candle_window(period_seconds: int, now: float = None):
    """
    Given the candle period (e.g. 60 for 1M, 300 for 5M), returns the
    ENTRY time (the start of the next candle that hasn't opened yet) and
    the EXIT/expiry time (one candle later) — i.e. the window a signal
    generated right now is actually valid for.

    Returns: (entry_dt, exit_dt, seconds_until_entry)
        entry_dt / exit_dt -> datetime objects (local time)
        seconds_until_entry -> float, how long until the entry candle opens
    """
    import time as _time
    if now is None:
        now = _time.time()
    next_boundary = (int(now // period_seconds) + 1) * period_seconds
    entry_dt = datetime.fromtimestamp(next_boundary)
    exit_dt = datetime.fromtimestamp(next_boundary + period_seconds)
    seconds_until_entry = round(next_boundary - now, 1)
    return entry_dt, exit_dt, seconds_until_entry


# ============================================================
# Safe value helper
# ============================================================

def get_value_safe(series, index=-1, default=0.0):
    """
    Safely grab a value from a pandas Series/column, avoiding
    IndexError / KeyError / NaN crashes.
    """
    try:
        val = series.iloc[index]
        if pd.isna(val):
            return default
        return float(val)
    except Exception:
        return default


# ============================================================
# Indicator calculations
# ============================================================

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expects df with columns: time/open/close/high/low/volume (any case).
    Normalizes column names, computes all indicators, returns df with
    a 'Close' column (capital C, used by the rest of the app) plus
    indicator columns.
    """
    df = df.copy()

    # Normalize column names (Quotex sometimes returns lowercase)
    rename_map = {}
    for col in df.columns:
        lc = str(col).lower()
        if lc in ("open", "o"):
            rename_map[col] = "Open"
        elif lc in ("close", "c", "price"):
            rename_map[col] = "Close"
        elif lc in ("high", "h", "max"):
            rename_map[col] = "High"
        elif lc in ("low", "l", "min"):
            rename_map[col] = "Low"
        elif lc in ("volume", "v"):
            rename_map[col] = "Volume"
    df = df.rename(columns=rename_map)

    # Fallbacks if High/Low missing (some feeds only give open/close)
    if "High" not in df.columns:
        df["High"] = df[["Open", "Close"]].max(axis=1)
    if "Low" not in df.columns:
        df["Low"] = df[["Open", "Close"]].min(axis=1)
    if "Volume" not in df.columns:
        df["Volume"] = 0

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # ---------------- EMA (trend, fast-reacting) ----------------
    df["EMA5"] = close.ewm(span=5, adjust=False).mean()
    df["EMA13"] = close.ewm(span=13, adjust=False).mean()
    df["EMA50"] = close.ewm(span=50, adjust=False).mean()

    # ---------------- RSI (7-period, faster than default 14) ----
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 7, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 7, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(50)

    # ---------------- MACD (12, 26, 9) ---------------------------
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

    # ---------------- Bollinger Bands (20, 2) ---------------------
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    df["BB_Mid"] = sma20
    df["BB_Upper"] = sma20 + 2 * std20
    df["BB_Lower"] = sma20 - 2 * std20

    # ---------------- Stochastic (5, 3, 3) -------------------------
    low5 = low.rolling(5).min()
    high5 = high.rolling(5).max()
    k = 100 * (close - low5) / (high5 - low5).replace(0, np.nan)
    df["Stoch_K"] = k.fillna(50)
    df["Stoch_D"] = df["Stoch_K"].rolling(3).mean().fillna(50)

    # ---------------- CCI (Commodity Channel Index, 14) ------------
    tp = (high + low + close) / 3  # Typical price
    sma_tp = tp.rolling(14).mean()
    mad = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["CCI"] = (tp - sma_tp) / (0.015 * mad)
    df["CCI"] = df["CCI"].fillna(0)

    # ---------------- ATR (volatility filter, 14) -------------------
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    # ---------------- ADX (trend strength, 14) -----------------------
    # Wilder's smoothing. ADX < ~20 means the market is choppy/ranging,
    # where breakout-style indicator signals (EMA cross, MACD, etc.)
    # produce far more false positives -- used below as a confidence gate.
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr14_wilder = tr.ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14_wilder
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14_wilder
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["ADX"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0)
    df["Plus_DI"] = plus_di.fillna(0)
    df["Minus_DI"] = minus_di.fillna(0)

    # ---------------- Swing high/low (support/resistance, 20) --------
    df["Swing_High"] = high.rolling(20).max()
    df["Swing_Low"] = low.rolling(20).min()

    # ---------------- Classic Pivot Points (floor pivot) --------------
    # Approximated from a rolling window (since we don't always have the
    # previous session's official OHLC available from the feed). P/R1/S1
    # act as the "pivot level" used by the entry-trigger logic below.
    piv_window = 20
    win_high = high.rolling(piv_window).max()
    win_low = low.rolling(piv_window).min()
    win_close = close.shift(1)  # last completed candle close of the window
    df["Pivot_P"] = (win_high + win_low + win_close) / 3
    df["Pivot_R1"] = (2 * df["Pivot_P"]) - win_low
    df["Pivot_S1"] = (2 * df["Pivot_P"]) - win_high

    return df


# ============================================================
# Higher-timeframe trend (multi-timeframe confirmation)
# ============================================================

def calculate_htf_trend(df: pd.DataFrame) -> str:
    """
    Given a candle dataframe from a HIGHER timeframe (e.g. 5-minute candles
    while the signal itself is computed on 1-minute candles), returns the
    prevailing trend direction: 'bull' / 'bear' / 'neutral'.

    Trading in the direction of the higher-timeframe trend filters out a lot
    of 1-minute noise-driven false signals.
    """
    if df is None or len(df) < 20:
        return "neutral"
    df = calculate_indicators(df)
    ema13 = get_value_safe(df["EMA13"])
    ema50 = get_value_safe(df["EMA50"])
    close = get_value_safe(df["Close"])
    if close > ema13 > ema50:
        return "bull"
    if close < ema13 < ema50:
        return "bear"
    return "neutral"


# ============================================================
# Pivot-level entry trigger
# ============================================================

def check_pivot_entry_trigger(df: pd.DataFrame, confirm_candles: int = 3):
    """
    Mirrors the "Enter PUT if price remains below the pivot level" style
    trigger: checks whether price has stayed on one side of the pivot
    point for the last `confirm_candles` closes, i.e. it's not just a
    single-candle poke through the level but sustained positioning.

    Returns: (direction, description, weight)
        direction: 'CALL' / 'PUT' / None
        description: string for the reasons list
        weight: 0 (no trigger) or a fixed strength for a confirmed trigger
    """
    if df is None or len(df) < confirm_candles + 1:
        return None, "", 0
    if "Pivot_P" not in df.columns:
        return None, "", 0

    pivot = get_value_safe(df["Pivot_P"])
    if pivot == 0:
        return None, "", 0

    recent_closes = df["Close"].tail(confirm_candles)
    if (recent_closes < pivot).all():
        return "PUT", (
            f"Price held below pivot ({pivot:.5f}) for last {confirm_candles} candles"
        ), 15
    if (recent_closes > pivot).all():
        return "CALL", (
            f"Price held above pivot ({pivot:.5f}) for last {confirm_candles} candles"
        ), 15
    return None, f"Price chopping around pivot ({pivot:.5f}) — no sustained trigger", 0


# ============================================================
# RSI Divergence detection
# ============================================================

def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 14):
    """
    Compares the two most recent swing highs/lows in price against RSI to
    find regular divergence:
      - Bearish divergence: price makes a HIGHER high, RSI makes a LOWER
        high  -> momentum fading on the way up -> PUT bias.
      - Bullish divergence: price makes a LOWER low, RSI makes a HIGHER
        low -> momentum fading on the way down -> CALL bias.

    This is a lightweight swing-point comparison (not a full pivot/fractal
    algorithm), intended as one extra confirmation, not a standalone signal.

    Returns: (direction, description, weight)
    """
    if df is None or len(df) < lookback + 5 or "RSI" not in df.columns:
        return None, "", 0

    window = df.tail(lookback).reset_index(drop=True)
    highs = window["High"]
    lows = window["Low"]
    rsi = window["RSI"]

    # Find index of the two highest highs (excluding immediate neighbors
    # of each other so we're comparing two distinct swings)
    high_idx_sorted = highs.sort_values(ascending=False).index.tolist()
    swing_high_idxs = []
    for idx in high_idx_sorted:
        if all(abs(idx - existing) >= 3 for existing in swing_high_idxs):
            swing_high_idxs.append(idx)
        if len(swing_high_idxs) == 2:
            break

    low_idx_sorted = lows.sort_values(ascending=True).index.tolist()
    swing_low_idxs = []
    for idx in low_idx_sorted:
        if all(abs(idx - existing) >= 3 for existing in swing_low_idxs):
            swing_low_idxs.append(idx)
        if len(swing_low_idxs) == 2:
            break

    # Bearish divergence: check the two swing highs in chronological order
    if len(swing_high_idxs) == 2:
        i1, i2 = sorted(swing_high_idxs)
        price_higher_high = highs[i2] > highs[i1]
        rsi_lower_high = rsi[i2] < rsi[i1]
        if price_higher_high and rsi_lower_high:
            return "PUT", "Bearish RSI divergence (price HH, RSI LH)", 15

    # Bullish divergence: check the two swing lows in chronological order
    if len(swing_low_idxs) == 2:
        i1, i2 = sorted(swing_low_idxs)
        price_lower_low = lows[i2] < lows[i1]
        rsi_higher_low = rsi[i2] > rsi[i1]
        if price_lower_low and rsi_higher_low:
            return "CALL", "Bullish RSI divergence (price LL, RSI HL)", 15

    return None, "", 0


# ============================================================
# Advanced Candlestick pattern detection
# ============================================================

def _detect_candle_patterns(df):
    """
    Detects multiple candlestick patterns from the last 2 candles.
    Returns: (direction, description, weight)
        direction: 'CALL' / 'PUT' / None
        description: string description
        weight: 10 (strong) or 5 (weak)
    """
    if len(df) < 2:
        return None, "", 0

    o1, c1, h1, l1 = (df["Open"].iloc[-2], df["Close"].iloc[-2],
                       df["High"].iloc[-2], df["Low"].iloc[-2])
    o2, c2, h2, l2 = (df["Open"].iloc[-1], df["Close"].iloc[-1],
                       df["High"].iloc[-1], df["Low"].iloc[-1])

    body1 = abs(c1 - o1)
    body2 = abs(c2 - o2)
    range2 = h2 - l2 if (h2 - l2) != 0 else 1e-9
    range1 = h1 - l1 if (h1 - l1) != 0 else 1e-9

    # --- 1. Bullish Engulfing (STRONG) ---
    if c1 < o1 and c2 > o2 and c2 >= o1 and o2 <= c1:
        return "CALL", "Bullish engulfing (strong reversal)", 10

    # --- 2. Bearish Engulfing (STRONG) ---
    if c1 > o1 and c2 < o2 and c2 <= o1 and o2 >= c1:
        return "PUT", "Bearish engulfing (strong reversal)", 10

    # --- 3. Bullish Pin Bar / Hammer (STRONG) ---
    lower_wick2 = min(o2, c2) - l2
    upper_wick2 = h2 - max(o2, c2)
    if lower_wick2 > body2 * 2 and lower_wick2 / range2 > 0.6 and c2 > o2:
        return "CALL", "Bullish hammer/pin bar (strong reversal)", 10

    # --- 4. Bearish Pin Bar / Shooting Star (STRONG) ---
    if upper_wick2 > body2 * 2 and upper_wick2 / range2 > 0.6 and c2 < o2:
        return "PUT", "Bearish shooting star/pin bar (strong reversal)", 10

    # --- 5. Doji (NEUTRAL - need context) ---
    is_doji = body2 <= (range2 * 0.1) if range2 > 0 else False
    if is_doji:
        # Check previous candle for context
        if c1 > o1 and body1 > range1 * 0.3:  # Previous bullish
            return "PUT", "Doji after bullish candle (potential reversal down)", 5
        elif c1 < o1 and body1 > range1 * 0.3:  # Previous bearish
            return "CALL", "Doji after bearish candle (potential reversal up)", 5
        else:
            return None, "Doji (indecision, wait for confirmation)", 0

    # --- 6. Bullish Harami ---
    if c1 < o1 and c2 > o2 and o2 > o1 and c2 < c1 and body2 < body1 * 0.5:
        return "CALL", "Bullish harami (reversal potential)", 8

    # --- 7. Bearish Harami ---
    if c1 > o1 and c2 < o2 and o2 < o1 and c2 > c1 and body2 < body1 * 0.5:
        return "PUT", "Bearish harami (reversal potential)", 8

    # --- 8. Bullish Marubozu (strong momentum) ---
    if c2 > o2 and upper_wick2 < body2 * 0.1 and lower_wick2 < body2 * 0.1:
        return "CALL", "Bullish marubozu (strong upward momentum)", 8

    # --- 9. Bearish Marubozu (strong momentum) ---
    if c2 < o2 and upper_wick2 < body2 * 0.1 and lower_wick2 < body2 * 0.1:
        return "PUT", "Bearish marubozu (strong downward momentum)", 8

    # --- 10. Bullish Morning Star (3-candle pattern) ---
    if len(df) >= 3:
        o3, c3, h3, l3 = (df["Open"].iloc[-3], df["Close"].iloc[-3],
                           df["High"].iloc[-3], df["Low"].iloc[-3])
        body3 = abs(c3 - o3)
        # First candle bearish, second doji, third bullish closing above first candle's midpoint
        if c3 < o3 and body2 <= range2 * 0.15 and c2 > o2 and c2 > ((o3 + c3) / 2):
            return "CALL", "Morning star (strong reversal)", 10

    # --- 11. Bearish Evening Star (3-candle pattern) ---
    if len(df) >= 3:
        o3, c3, h3, l3 = (df["Open"].iloc[-3], df["Close"].iloc[-3],
                           df["High"].iloc[-3], df["Low"].iloc[-3])
        body3 = abs(c3 - o3)
        # First candle bullish, second doji, third bearish closing below first candle's midpoint
        if c3 > o3 and body2 <= range2 * 0.15 and c2 < o2 and c2 < ((o3 + c3) / 2):
            return "PUT", "Evening star (strong reversal)", 10

    return None, "", 0


# ============================================================
# Main signal generator - COMPOUND SCORING
# ============================================================

def get_signal_simple(df: pd.DataFrame, htf_trend: str = "neutral", min_confidence: float = 70.0):
    """
    ENHANCED: Combines all indicators with compound scoring.

    COMPOUND SCORING: More confirmations = exponentially higher confidence.
    Each indicator confirmation adds to the score. With 4+ confirmations,
    confidence can reach 70%+. With 6+ confirmations, 85%+.

    REVERSAL STRATEGY: Identifies potential reversal points when extreme
    conditions align (overbought/oversold + candlestick reversals).

    PIVOT ENTRY TRIGGER + RSI DIVERGENCE: Two extra confirmations layered
    on top of the existing indicator stack (not standalone signals) --
    they only add to whichever side (CALL/PUT) the rest of the confluence
    already favors, same as the ADX and higher-timeframe factors below.

    Returns: (signal, confidence, reasons)
      signal     -> 'CALL' | 'PUT' | None (None = no clear setup)
      confidence -> 0-100 (compound weighted score)
      reasons    -> list of strings with ✅ / ❌ markers for the UI
    """
    reasons = []
    call_score = 0.0
    put_score = 0.0
    max_score = 0.0
    confirmations = []  # Track all confirmed signals for logging

    if df is None or len(df) < 30:
        return None, 0, ["❌ Not enough candle data (need at least 30)"]

    # Get indicator values
    ema5 = get_value_safe(df["EMA5"])
    ema13 = get_value_safe(df["EMA13"])
    ema50 = get_value_safe(df["EMA50"])
    rsi = get_value_safe(df["RSI"], default=50)
    rsi_prev = get_value_safe(df["RSI"], index=-2, default=50)
    macd_hist = get_value_safe(df["MACD_Hist"])
    macd_hist_prev = get_value_safe(df["MACD_Hist"], index=-2)
    close = get_value_safe(df["Close"])
    bb_upper = get_value_safe(df["BB_Upper"])
    bb_lower = get_value_safe(df["BB_Lower"])
    bb_mid = get_value_safe(df["BB_Mid"])
    stoch_k = get_value_safe(df["Stoch_K"], default=50)
    stoch_d = get_value_safe(df["Stoch_D"], default=50)
    stoch_k_prev = get_value_safe(df["Stoch_K"], index=-2, default=50)
    stoch_d_prev = get_value_safe(df["Stoch_D"], index=-2, default=50)
    cci = get_value_safe(df["CCI"], default=0)
    cci_prev = get_value_safe(df["CCI"], index=-2, default=0)
    atr = get_value_safe(df["ATR"])
    atr_avg = df["ATR"].tail(30).mean() if "ATR" in df.columns else 0
    adx = get_value_safe(df["ADX"], default=0)
    swing_high = get_value_safe(df["Swing_High"], default=close)
    swing_low = get_value_safe(df["Swing_Low"], default=close)

    # ---- REVERSAL STRATEGY DETECTION ----
    reversal_call = False
    reversal_put = False
    reversal_weight = 0

    # Overbought/Oversold conditions with RSI + CCI + Stochastic
    if rsi >= 70 and cci >= 100 and stoch_k >= 80:
        reversal_put = True
        reversal_weight = 15
        reasons.append("🔴 EXTREME OVERBOUGHT: RSI≥70, CCI≥100, Stoch≥80 (reversal down likely)")
    elif rsi <= 30 and cci <= -100 and stoch_k <= 20:
        reversal_call = True
        reversal_weight = 15
        reasons.append("🟢 EXTREME OVERSOLD: RSI≤30, CCI≤-100, Stoch≤20 (reversal up likely)")

    # ---- COMPOUND SCORING (each indicator adds to score) ----

    # 1) TREND: EMA alignment (weight 20)
    w = 20
    max_score += w
    if ema5 > ema13 > ema50:
        call_score += w
        reasons.append("✅ Trend: EMA5 > EMA13 > EMA50 (bullish)")
        confirmations.append("trend_bull")
    elif ema5 < ema13 < ema50:
        put_score += w
        reasons.append("✅ Trend: EMA5 < EMA13 < EMA50 (bearish)")
        confirmations.append("trend_bear")
    else:
        reasons.append("❌ Trend: EMAs not aligned")

    # 2) RSI momentum (weight 15)
    w = 15
    max_score += w
    if 50 < rsi < 70 and rsi > rsi_prev:
        call_score += w
        reasons.append(f"✅ RSI {rsi:.1f}: rising bullish momentum")
        confirmations.append("rsi_bull")
    elif 30 < rsi < 50 and rsi < rsi_prev:
        put_score += w
        reasons.append(f"✅ RSI {rsi:.1f}: falling bearish momentum")
        confirmations.append("rsi_bear")
    elif rsi >= 70 and reversal_put:
        put_score += w * 0.7
        reasons.append(f"⚠️ RSI {rsi:.1f}: overbought (reversal signal)")
        confirmations.append("rsi_reversal_put")
    elif rsi <= 30 and reversal_call:
        call_score += w * 0.7
        reasons.append(f"⚠️ RSI {rsi:.1f}: oversold (reversal signal)")
        confirmations.append("rsi_reversal_call")
    else:
        reasons.append(f"❌ RSI {rsi:.1f}: neutral")

    # 3) MACD histogram (weight 18)
    w = 18
    max_score += w
    if macd_hist > 0 and macd_hist > macd_hist_prev and macd_hist_prev <= 0:
        call_score += w
        reasons.append("✅ MACD: bullish crossover above zero")
        confirmations.append("macd_bull")
    elif macd_hist < 0 and macd_hist < macd_hist_prev and macd_hist_prev >= 0:
        put_score += w
        reasons.append("✅ MACD: bearish crossover below zero")
        confirmations.append("macd_bear")
    elif macd_hist > 0 and macd_hist > macd_hist_prev:
        call_score += w * 0.5
        reasons.append("✅ MACD: rising bullish histogram")
        confirmations.append("macd_bull_weak")
    elif macd_hist < 0 and macd_hist < macd_hist_prev:
        put_score += w * 0.5
        reasons.append("✅ MACD: falling bearish histogram")
        confirmations.append("macd_bear_weak")
    else:
        reasons.append("❌ MACD: no clear momentum")

    # 4) Bollinger Bands (weight 15)
    w = 15
    max_score += w
    if close <= bb_lower:
        call_score += w
        reasons.append("✅ Price at lower Bollinger Band (oversold bounce)")
        confirmations.append("bb_bull")
    elif close >= bb_upper:
        put_score += w
        reasons.append("✅ Price at upper Bollinger Band (overbought pullback)")
        confirmations.append("bb_bear")
    else:
        reasons.append("❌ Price inside Bollinger Bands")

    # 5) Stochastic crossover (weight 15)
    w = 15
    max_score += w
    if stoch_k > stoch_d and stoch_k_prev <= stoch_d_prev:
        if stoch_k < 80:
            call_score += w
            reasons.append("✅ Stochastic: bullish crossover")
            confirmations.append("stoch_bull")
        elif reversal_put:
            put_score += w * 0.5
            reasons.append("⚠️ Stochastic: bullish crossover in overbought (caution)")
    elif stoch_k < stoch_d and stoch_k_prev >= stoch_d_prev:
        if stoch_k > 20:
            put_score += w
            reasons.append("✅ Stochastic: bearish crossover")
            confirmations.append("stoch_bear")
        elif reversal_call:
            call_score += w * 0.5
            reasons.append("⚠️ Stochastic: bearish crossover in oversold (caution)")
    else:
        reasons.append("❌ Stochastic: no crossover")

    # 6) CCI (Commodity Channel Index) - NEW (weight 12)
    w = 12
    max_score += w
    if cci > 100 and cci > cci_prev:
        if reversal_put:
            put_score += w * 0.6
            reasons.append(f"⚠️ CCI {cci:.1f}: extremely overbought (reversal down)")
            confirmations.append("cci_reversal_put")
        else:
            put_score += w * 0.3
            reasons.append(f"⚠️ CCI {cci:.1f}: overbought, trend may continue")
    elif cci < -100 and cci < cci_prev:
        if reversal_call:
            call_score += w * 0.6
            reasons.append(f"⚠️ CCI {cci:.1f}: extremely oversold (reversal up)")
            confirmations.append("cci_reversal_call")
        else:
            call_score += w * 0.3
            reasons.append(f"⚠️ CCI {cci:.1f}: oversold, trend may continue")
    elif cci > 0 and cci > cci_prev:
        call_score += w * 0.5
        reasons.append(f"✅ CCI {cci:.1f}: rising positive momentum")
        confirmations.append("cci_bull")
    elif cci < 0 and cci < cci_prev:
        put_score += w * 0.5
        reasons.append(f"✅ CCI {cci:.1f}: falling negative momentum")
        confirmations.append("cci_bear")
    else:
        reasons.append(f"❌ CCI {cci:.1f}: neutral")

    # 7) Candlestick Patterns (weight 15 - higher priority for reversal)
    w = 15
    max_score += w
    pattern_dir, pattern_desc, pattern_weight = _detect_candle_patterns(df)
    if pattern_dir == "CALL":
        actual_weight = w if pattern_weight >= 8 else w * 0.6
        call_score += actual_weight
        reasons.append(f"✅ Candle: {pattern_desc}")
        confirmations.append("candle_bull")
    elif pattern_dir == "PUT":
        actual_weight = w if pattern_weight >= 8 else w * 0.6
        put_score += actual_weight
        reasons.append(f"✅ Candle: {pattern_desc}")
        confirmations.append("candle_bear")
    else:
        reasons.append("❌ Candle: no strong pattern")

    # 8) Reversal Bonus (extra weight for extreme conditions)
    if reversal_call:
        w = 15
        max_score += w
        call_score += w
        reasons.append("🔥 REVERSAL: Extreme oversold across multiple indicators")
        confirmations.append("reversal_call")
    elif reversal_put:
        w = 15
        max_score += w
        put_score += w
        reasons.append("🔥 REVERSAL: Extreme overbought across multiple indicators")
        confirmations.append("reversal_put")

    # ---- VOLATILITY FILTER ----
    low_volatility = atr_avg > 0 and atr < (atr_avg * 0.4)
    if low_volatility:
        reasons.append("⚠️ Volatility very low — signals less reliable")

    # 9) ADX Trend Strength (weight 12) -- confirms the market is actually
    #    trending rather than choppy/ranging, where EMA/MACD crossovers are
    #    unreliable. Does not pick a direction on its own; it agrees with
    #    whichever side the DI lines favor.
    w = 12
    max_score += w
    strong_trend = adx >= 25
    if strong_trend and call_score >= put_score:
        call_score += w
        reasons.append(f"✅ ADX {adx:.1f}: strong trend supports CALL side")
        confirmations.append("adx_trend")
    elif strong_trend and put_score > call_score:
        put_score += w
        reasons.append(f"✅ ADX {adx:.1f}: strong trend supports PUT side")
        confirmations.append("adx_trend")
    else:
        reasons.append(f"❌ ADX {adx:.1f}: weak/no trend (choppy market)")

    # 10) Higher-timeframe trend confirmation (weight 15) -- only rewards
    #     agreement, never penalizes when unavailable (htf_trend defaults
    #     to "neutral" when the caller has no higher-timeframe data).
    w = 15
    if htf_trend in ("bull", "bear"):
        max_score += w
        if htf_trend == "bull" and call_score >= put_score:
            call_score += w
            reasons.append("✅ Higher timeframe trend: bullish (aligned)")
            confirmations.append("htf_bull")
        elif htf_trend == "bear" and put_score > call_score:
            put_score += w
            reasons.append("✅ Higher timeframe trend: bearish (aligned)")
            confirmations.append("htf_bear")
        else:
            reasons.append(f"❌ Higher timeframe trend ({htf_trend}) disagrees with setup")

    # 11) Pivot-level entry trigger (weight 15) -- only rewards whichever
    #     side already leads, same pattern as ADX/HTF above, so it can't
    #     single-handedly flip the signal on its own.
    w = 15
    max_score += w
    pivot_dir, pivot_desc, pivot_weight = check_pivot_entry_trigger(df)
    if pivot_dir == "PUT" and put_score >= call_score:
        put_score += w
        reasons.append(f"✅ Pivot trigger: {pivot_desc}")
        confirmations.append("pivot_put")
    elif pivot_dir == "CALL" and call_score >= put_score:
        call_score += w
        reasons.append(f"✅ Pivot trigger: {pivot_desc}")
        confirmations.append("pivot_call")
    elif pivot_dir:
        reasons.append(f"⚠️ Pivot trigger points {pivot_dir} but disagrees with the rest of the setup")
    else:
        reasons.append(f"❌ Pivot: {pivot_desc}" if pivot_desc else "❌ Pivot: no sustained trigger")

    # 12) RSI Divergence (weight 15) -- same "only adds to leading side"
    #     pattern; divergence against the leading side is logged but not
    #     applied, since a lone divergence signal is not reliable enough
    #     to flip an otherwise-aligned setup on a 1-minute chart.
    w = 15
    max_score += w
    div_dir, div_desc, div_weight = detect_rsi_divergence(df)
    if div_dir == "PUT" and put_score >= call_score:
        put_score += w
        reasons.append(f"✅ {div_desc}")
        confirmations.append("divergence_put")
    elif div_dir == "CALL" and call_score >= put_score:
        call_score += w
        reasons.append(f"✅ {div_desc}")
        confirmations.append("divergence_call")
    elif div_dir:
        reasons.append(f"⚠️ {div_desc} but disagrees with the rest of the setup")
    else:
        reasons.append("❌ RSI Divergence: none detected")

    # ---- SUPPORT/RESISTANCE FILTER ----
    # Fading into a fresh CALL right under a recent swing high (resistance),
    # or a fresh PUT right above a recent swing low (support), is a common
    # source of losing 1-minute signals -- price often stalls/bounces there.
    sr_buffer = atr * 0.5 if atr > 0 else 0
    near_resistance = sr_buffer > 0 and (swing_high - close) <= sr_buffer
    near_support = sr_buffer > 0 and (close - swing_low) <= sr_buffer
    if near_resistance and call_score > put_score:
        call_score *= 0.6
        reasons.append("⚠️ Price near recent resistance -- CALL confidence reduced")
    if near_support and put_score > call_score:
        put_score *= 0.6
        reasons.append("⚠️ Price near recent support -- PUT confidence reduced")

    # ---- COMPOUND CONFIDENCE CALCULATION ----
    # Base confidence from weighted score
    call_confidence = round((call_score / max_score) * 100, 1) if max_score else 0
    put_confidence = round((put_score / max_score) * 100, 1) if max_score else 0

    # CONFIDENCE BOOST: More confirmations = higher confidence (compound effect)
    confirmation_count = len(confirmations)

    # Boost confidence based on number of confirmations (non-linear)
    # 2 confs: +5%, 3: +10%, 4: +18%, 5: +25%, 6+: +35%
    if confirmation_count >= 6:
        boost = 35
        reasons.append(f"🔥 STRONG SIGNAL: {confirmation_count} confirmations! (+35% boost)")
    elif confirmation_count >= 5:
        boost = 25
        reasons.append(f"🔥 GOOD SIGNAL: {confirmation_count} confirmations! (+25% boost)")
    elif confirmation_count >= 4:
        boost = 18
        reasons.append(f"✅ DECENT SIGNAL: {confirmation_count} confirmations (+18% boost)")
    elif confirmation_count >= 3:
        boost = 10
        reasons.append(f"✅ MODERATE: {confirmation_count} confirmations (+10% boost)")
    elif confirmation_count >= 2:
        boost = 5
        reasons.append(f"ℹ️ WEAK: {confirmation_count} confirmations (+5% boost)")
    else:
        boost = 0
        reasons.append(f"⚠️ VERY WEAK: {confirmation_count} confirmations (no boost)")

    # Apply boost only to the dominant direction
    if call_confidence > put_confidence:
        call_confidence = min(98, call_confidence + boost)
    elif put_confidence > call_confidence:
        put_confidence = min(98, put_confidence + boost)

    # ---- FINAL DECISION ----
    # Hard floor: never return a signal below `min_confidence`, no matter
    # what the dynamic adjustments below would otherwise allow. This is a
    # user-set gate (default 70%) — "give nothing below this, anything
    # above it is fine."
    MIN_CONFIDENCE = min_confidence

    # Volatility / choppy-market adjustments still apply ON TOP of the
    # floor (they can only raise the bar further, never lower it below
    # min_confidence).
    if low_volatility:
        MIN_CONFIDENCE = max(MIN_CONFIDENCE, min_confidence + 5)

    if not strong_trend:
        MIN_CONFIDENCE += 5
        reasons.append(f"⚠️ Weak trend (ADX {adx:.1f}) -- confidence threshold raised to {MIN_CONFIDENCE}%")

    # REVERSAL STRATEGY: previously allowed to fire a bit below the normal
    # bar on extreme conditions -- now also respects the same hard floor,
    # since "never below min_confidence" was an explicit requirement.
    reversal_floor = max(min_confidence, 55)
    if reversal_call and call_confidence >= reversal_floor:
        return "CALL", call_confidence, reasons
    elif reversal_put and put_confidence >= reversal_floor:
        return "PUT", put_confidence, reasons

    # Normal signal
    if call_confidence >= MIN_CONFIDENCE and call_confidence > put_confidence:
        return "CALL", call_confidence, reasons
    elif put_confidence >= MIN_CONFIDENCE and put_confidence > call_confidence:
        return "PUT", put_confidence, reasons
    else:
        reasons.append(
            f"ℹ️ Scores: CALL {call_confidence}% / PUT {put_confidence}% "
            f"(need ≥{MIN_CONFIDENCE}% with {confirmation_count} confirmations)"
        )
        return None, max(call_confidence, put_confidence), reasons
