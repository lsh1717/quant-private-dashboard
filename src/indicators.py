from __future__ import annotations

import numpy as np
import pandas as pd


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean OHLCV dataframe with lowercase columns.

    yfinance can return either normal columns or MultiIndex columns.
    With auto_adjust=False it often returns both Close and Adj Close.
    If Adj Close is renamed to close while Close already exists, pandas creates
    duplicate close columns and rolling calculations fail. This function keeps
    one clean OHLCV set and ignores Adj Close when Close is available.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    data = df.copy()

    if isinstance(data.columns, pd.MultiIndex):
        # For single-ticker downloads, first level is usually OHLCV/Adj Close.
        # Example: ("Close", "000660.KS"). Keep only the OHLCV name.
        data.columns = [str(c[0]).strip().lower().replace(" ", "_") for c in data.columns]
    else:
        data.columns = [str(c).strip().lower().replace(" ", "_") for c in data.columns]

    # Prefer real Close. Use Adj Close only if Close is missing.
    if "close" not in data.columns and "adj_close" in data.columns:
        data = data.rename(columns={"adj_close": "close"})

    # Remove duplicate columns defensively. This fixes the common Close + Adj Close issue.
    data = data.loc[:, ~data.columns.duplicated()].copy()

    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in data.columns]
    if "close" not in keep:
        return pd.DataFrame()

    data = data[keep].copy()
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    if "volume" in data.columns:
        data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
    return data.dropna(subset=["close"])


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    data = normalize_ohlcv(df)
    if data.empty:
        return data

    data["ma20"] = data["close"].rolling(20).mean()
    data["ma60"] = data["close"].rolling(60).mean()
    data["ma120"] = data["close"].rolling(120).mean()
    data["rsi14"] = rsi(data["close"], 14)
    data["vol20"] = data["volume"].rolling(20).mean() if "volume" in data.columns else np.nan
    data["volume_ratio"] = data["volume"] / data["vol20"] if "volume" in data.columns else np.nan
    data["high20"] = data["high"].rolling(20).max() if "high" in data.columns else data["close"].rolling(20).max()
    data["low20"] = data["low"].rolling(20).min() if "low" in data.columns else data["close"].rolling(20).min()
    data["high60"] = data["high"].rolling(60).max() if "high" in data.columns else data["close"].rolling(60).max()
    data["low60"] = data["low"].rolling(60).min() if "low" in data.columns else data["close"].rolling(60).min()
    data["ret_5d"] = data["close"].pct_change(5) * 100
    data["ret_20d"] = data["close"].pct_change(20) * 100
    return data


def latest_snapshot(df: pd.DataFrame) -> dict:
    data = add_indicators(df)
    if data.empty:
        return {}
    last = data.iloc[-1]
    prev = data.iloc[-2] if len(data) >= 2 else last

    def val(name: str, default=np.nan):
        try:
            x = last.get(name, default)
            return float(x) if pd.notna(x) else np.nan
        except Exception:
            return np.nan

    close = val("close")
    ma20 = val("ma20")
    ma60 = val("ma60")
    high20 = val("high20")
    low20 = val("low20")
    high60 = val("high60")
    low60 = val("low60")
    volume_ratio = val("volume_ratio")
    rsi14 = val("rsi14", 50)

    prev_close = float(prev.get("close", close)) if pd.notna(prev.get("close", np.nan)) else close
    prev_high20 = float(prev.get("high20", high20)) if pd.notna(prev.get("high20", np.nan)) else high20

    return {
        "close": close,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": val("ma120"),
        "rsi14": rsi14,
        "volume_ratio": volume_ratio,
        "high20": high20,
        "low20": low20,
        "high60": high60,
        "low60": low60,
        "ret_5d": val("ret_5d"),
        "ret_20d": val("ret_20d"),
        "above_ma20": bool(close > ma20) if pd.notna(ma20) else False,
        "above_ma60": bool(close > ma60) if pd.notna(ma60) else False,
        "breakout_20d": bool(close > prev_high20 and prev_close <= prev_high20) if pd.notna(prev_high20) else False,
        "near_high20": bool(close >= high20 * 0.97) if pd.notna(high20) else False,
        "near_low20": bool(close <= low20 * 1.05) if pd.notna(low20) else False,
    }
