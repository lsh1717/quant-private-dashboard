from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .indicators import add_indicators


@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    data: pd.DataFrame


def _safe_float(x: Any, default: float = np.nan) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def _max_drawdown(equity: pd.Series) -> float:
    if equity is None or equity.empty:
        return 0.0
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    return float(dd.min() * 100.0)


def _pick_stop(signal_row: pd.Series, entry_price: float, stop_loss_pct: float) -> float:
    """Pick a realistic support-based stop, capped by a fixed percentage stop.

    The support stop uses only information available at the signal candle:
    ma20, ma60, previous 20-day low, and fib618. The fixed percentage stop
    prevents a very wide technical stop from making risk too large.
    """
    supports = []
    for col in ["ma20", "ma60", "low20_prev", "fib618"]:
        val = _safe_float(signal_row.get(col))
        if pd.notna(val) and val > 0 and val < entry_price:
            supports.append(val)
    support_stop = max(supports) if supports else entry_price * (1 - stop_loss_pct / 100.0)
    fixed_stop = entry_price * (1 - stop_loss_pct / 100.0)
    # Use the tighter stop to keep risk controlled.
    return float(max(support_stop, fixed_stop))


def prepare_backtest_data(df: pd.DataFrame) -> pd.DataFrame:
    data = add_indicators(df).copy()
    if data.empty:
        return data
    # For breakout decisions, use yesterday's 20-day high/low to avoid using
    # today's high in today's signal.
    if "high" in data.columns:
        data["high20_prev"] = data["high"].rolling(20).max().shift(1)
    else:
        data["high20_prev"] = data["close"].rolling(20).max().shift(1)
    if "low" in data.columns:
        data["low20_prev"] = data["low"].rolling(20).min().shift(1)
    else:
        data["low20_prev"] = data["close"].rolling(20).min().shift(1)
    data["fib_zone_bt"] = (
        data["close"].notna()
        & data["fib382"].notna()
        & data["fib618"].notna()
        & (data["close"] >= data["fib618"] * 0.98)
        & (data["close"] <= data["fib382"] * 1.02)
    )
    return data


def run_backtest(
    df: pd.DataFrame,
    strategy: str = "돌파+눌림",
    structure_score: float = 80.0,
    supply_score: float = 70.0,
    structure_min: float = 75.0,
    supply_min: float = 50.0,
    volume_min: float = 1.3,
    breakout_rsi_min: float = 45.0,
    breakout_rsi_max: float = 70.0,
    pullback_rsi_min: float = 38.0,
    pullback_rsi_max: float = 58.0,
    stop_loss_pct: float = 7.0,
    take_profit_pct: float = 0.0,
    sell_rsi: float = 78.0,
    overheat_ret20: float = 35.0,
    commission_bps: float = 1.5,
    initial_capital: float = 10_000_000.0,
) -> BacktestResult:
    """Run a simple daily close based backtest.

    Entry is generated at the signal candle close and filled at the next day's
    open. Intraday stop is approximated using the day's low. Other exits are
    close-based. This is a strategy sanity check, not broker-grade execution.
    """
    data = prepare_backtest_data(df)
    if data.empty or len(data) < 80:
        return BacktestResult(
            metrics={"오류": "가격 데이터가 부족합니다."},
            trades=pd.DataFrame(),
            equity_curve=pd.DataFrame(),
            data=data,
        )

    strategy = str(strategy)
    use_breakout = strategy in ["돌파", "돌파+눌림"]
    use_pullback = strategy in ["눌림", "돌파+눌림"]
    commission = commission_bps / 10000.0

    trades: list[dict[str, Any]] = []
    equity_points: list[dict[str, Any]] = [{"date": data.index[min(60, len(data) - 1)], "equity": float(initial_capital)}]
    capital = float(initial_capital)
    position: dict[str, Any] | None = None

    # Iterate until len-1 because entries are filled next open.
    for i in range(60, len(data) - 1):
        row = data.iloc[i]
        nxt = data.iloc[i + 1]
        date = data.index[i]
        next_date = data.index[i + 1]

        close = _safe_float(row.get("close"))
        open_next = _safe_float(nxt.get("open"), _safe_float(nxt.get("close")))
        if pd.isna(close) or pd.isna(open_next) or open_next <= 0:
            continue

        if position is None:
            if structure_score < structure_min or supply_score < supply_min:
                continue

            ma20 = _safe_float(row.get("ma20"))
            ma60 = _safe_float(row.get("ma60"))
            high20_prev = _safe_float(row.get("high20_prev"))
            rsi = _safe_float(row.get("rsi14"), 50.0)
            vr = _safe_float(row.get("volume_ratio"))
            fib_zone = bool(row.get("fib_zone_bt", False))

            trend_ok = pd.notna(ma20) and pd.notna(ma60) and close > ma20 and close > ma60
            breakout_signal = (
                use_breakout
                and trend_ok
                and pd.notna(high20_prev)
                and close >= high20_prev
                and pd.notna(vr)
                and vr >= volume_min
                and breakout_rsi_min <= rsi <= breakout_rsi_max
            )
            pullback_signal = (
                use_pullback
                and pd.notna(ma60)
                and close > ma60
                and fib_zone
                and pullback_rsi_min <= rsi <= pullback_rsi_max
            )

            if breakout_signal or pullback_signal:
                entry_type = "돌파" if breakout_signal else "눌림"
                entry_price = float(open_next)
                stop_price = _pick_stop(row, entry_price, stop_loss_pct)
                position = {
                    "entry_date": next_date,
                    "entry_price": entry_price,
                    "entry_type": entry_type,
                    "signal_date": date,
                    "stop_price": stop_price,
                    "signal_close": close,
                }
            continue

        # Manage open position from current candle onward.
        entry_price = float(position["entry_price"])
        stop_price = float(position["stop_price"])
        low = _safe_float(row.get("low"), close)
        high = _safe_float(row.get("high"), close)
        ma20 = _safe_float(row.get("ma20"))
        low20_prev = _safe_float(row.get("low20_prev"))
        rsi = _safe_float(row.get("rsi14"), 50.0)
        ret20 = _safe_float(row.get("ret_20d"), 0.0)

        exit_price = np.nan
        exit_reason = ""
        if pd.notna(low) and low <= stop_price:
            exit_price = stop_price
            exit_reason = "손절"
        elif take_profit_pct and pd.notna(high) and high >= entry_price * (1 + take_profit_pct / 100.0):
            exit_price = entry_price * (1 + take_profit_pct / 100.0)
            exit_reason = f"고정익절 {take_profit_pct:.1f}%"
        elif pd.notna(low20_prev) and close < low20_prev:
            exit_price = close
            exit_reason = "20일저점 이탈"
        elif pd.notna(ma20) and close < ma20 and rsi < 50:
            exit_price = close
            exit_reason = "20일선 이탈"
        elif rsi >= sell_rsi or (pd.notna(ret20) and ret20 >= overheat_ret20):
            exit_price = close
            exit_reason = "과열매도"

        if pd.notna(exit_price) and exit_price > 0:
            gross = exit_price / entry_price - 1.0
            net = gross - commission * 2
            capital *= (1.0 + net)
            trades.append(
                {
                    "진입일": position["entry_date"],
                    "청산일": date,
                    "신호": position["entry_type"],
                    "진입가": entry_price,
                    "청산가": float(exit_price),
                    "손절가": stop_price,
                    "수익률%": net * 100.0,
                    "보유일": max(1, (pd.Timestamp(date) - pd.Timestamp(position["entry_date"])).days),
                    "청산사유": exit_reason,
                    "자본": capital,
                }
            )
            equity_points.append({"date": date, "equity": capital})
            position = None

    # Close open position at last close for reporting.
    if position is not None:
        last = data.iloc[-1]
        last_date = data.index[-1]
        last_close = _safe_float(last.get("close"))
        if pd.notna(last_close) and last_close > 0:
            entry_price = float(position["entry_price"])
            gross = last_close / entry_price - 1.0
            net = gross - commission * 2
            capital *= (1.0 + net)
            trades.append(
                {
                    "진입일": position["entry_date"],
                    "청산일": last_date,
                    "신호": position["entry_type"],
                    "진입가": entry_price,
                    "청산가": float(last_close),
                    "손절가": float(position["stop_price"]),
                    "수익률%": net * 100.0,
                    "보유일": max(1, (pd.Timestamp(last_date) - pd.Timestamp(position["entry_date"])).days),
                    "청산사유": "기간종료",
                    "자본": capital,
                }
            )
            equity_points.append({"date": last_date, "equity": capital})

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_points)
    if not equity_df.empty:
        equity_df["date"] = pd.to_datetime(equity_df["date"])
        equity_df = equity_df.sort_values("date")

    if trades_df.empty:
        metrics = {
            "총 거래": 0,
            "승률%": 0.0,
            "평균수익률%": 0.0,
            "누적수익률%": 0.0,
            "MDD%": 0.0,
            "평균보유일": 0.0,
            "손익비": 0.0,
        }
    else:
        rets = trades_df["수익률%"].astype(float)
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        profit_factor = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else float("inf")
        metrics = {
            "총 거래": int(len(trades_df)),
            "승률%": float((rets > 0).mean() * 100.0),
            "평균수익률%": float(rets.mean()),
            "중앙수익률%": float(rets.median()),
            "최고수익률%": float(rets.max()),
            "최저수익률%": float(rets.min()),
            "누적수익률%": float((capital / initial_capital - 1.0) * 100.0),
            "MDD%": _max_drawdown(equity_df["equity"]) if not equity_df.empty else 0.0,
            "평균보유일": float(trades_df["보유일"].mean()),
            "손익비": profit_factor,
        }

    return BacktestResult(metrics=metrics, trades=trades_df, equity_curve=equity_df, data=data)
