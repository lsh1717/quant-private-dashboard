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
        if isinstance(x, str):
            x = x.replace(",", "").strip()
            if x in ["", "-", "None", "nan"]:
                return default
        return float(x)
    except Exception:
        return default


def _max_drawdown(equity: pd.Series) -> float:
    if equity is None or equity.empty:
        return 0.0
    equity = pd.to_numeric(equity, errors="coerce").dropna()
    if equity.empty:
        return 0.0
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    return float(dd.min() * 100.0)


def _pick_stop(signal_row: pd.Series, entry_price: float, stop_loss_pct: float) -> float:
    """Pick a support stop capped by a fixed percentage stop.

    This uses only information available at the signal candle: ma20, ma60,
    previous 20-day low, and fib618. The fixed percentage stop prevents the
    technical stop from being too wide.
    """
    supports = []
    for col in ["ma20", "ma60", "low20_prev", "fib618"]:
        val = _safe_float(signal_row.get(col))
        if pd.notna(val) and val > 0 and val < entry_price:
            supports.append(val)
    support_stop = max(supports) if supports else entry_price * (1 - stop_loss_pct / 100.0)
    fixed_stop = entry_price * (1 - stop_loss_pct / 100.0)
    return float(max(support_stop, fixed_stop))


def prepare_backtest_data(df: pd.DataFrame) -> pd.DataFrame:
    data = add_indicators(df).copy()
    if data.empty:
        return data

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


def _buy_hold_metrics(data: pd.DataFrame, initial_capital: float, commission: float, start_i: int = 60) -> dict[str, float]:
    if data.empty or len(data) <= start_i + 2:
        return {"매수보유수익률%": 0.0, "매수보유MDD%": 0.0}

    start_price = _safe_float(data.iloc[start_i].get("open"), _safe_float(data.iloc[start_i].get("close")))
    if pd.isna(start_price) or start_price <= 0:
        start_price = _safe_float(data.iloc[start_i].get("close"))
    end_price = _safe_float(data.iloc[-1].get("close"))
    if pd.isna(start_price) or pd.isna(end_price) or start_price <= 0 or end_price <= 0:
        return {"매수보유수익률%": 0.0, "매수보유MDD%": 0.0}

    shares = initial_capital * (1.0 - commission) / start_price
    close = pd.to_numeric(data["close"].iloc[start_i:], errors="coerce").dropna()
    bh_equity = close * shares
    # assume sell at final close for fair cost comparison
    bh_return = ((end_price * shares * (1.0 - commission)) / initial_capital - 1.0) * 100.0
    return {"매수보유수익률%": float(bh_return), "매수보유MDD%": _max_drawdown(bh_equity)}


def _entry_signal(
    row: pd.Series,
    strategy: str,
    structure_score: float,
    supply_score: float,
    structure_min: float,
    supply_min: float,
    volume_min: float,
    breakout_rsi_min: float,
    breakout_rsi_max: float,
    pullback_rsi_min: float,
    pullback_rsi_max: float,
) -> str | None:
    if structure_score < structure_min or supply_score < supply_min:
        return None

    use_breakout = strategy in ["돌파", "돌파+눌림"]
    use_pullback = strategy in ["눌림", "돌파+눌림"]

    close = _safe_float(row.get("close"))
    ma20 = _safe_float(row.get("ma20"))
    ma60 = _safe_float(row.get("ma60"))
    high20_prev = _safe_float(row.get("high20_prev"))
    rsi = _safe_float(row.get("rsi14"), 50.0)
    vr = _safe_float(row.get("volume_ratio"))
    fib_zone = bool(row.get("fib_zone_bt", False))

    if pd.isna(close):
        return None

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

    if breakout_signal:
        return "돌파"
    if pullback_signal:
        return "눌림"
    return None


def run_backtest(
    df: pd.DataFrame,
    strategy: str = "돌파+눌림",
    exit_mode: str = "추세보유형",
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
    partial_sell_pct: float = 30.0,
) -> BacktestResult:
    """Daily close based backtest.

    v6.4 adds four exit styles:
      - 기본형: earlier fast exits; useful for short swing sanity checks.
      - 추세보유형: avoid selling only because RSI is high; hold until 60D trend/20D low breaks.
      - 분할매도+추세보유: take partial profits in overheat, keep the core until trend breaks.
      - 코어보유형: take only partial profits in overheat and keep a core position until 120D/large trend breaks.

    Entry signal uses only information available at the signal candle and fills at
    next day's open. This is not broker-grade execution; it is for strategy comparison.
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
    exit_mode = str(exit_mode)
    commission = commission_bps / 10000.0
    partial_ratio = max(0.0, min(0.9, partial_sell_pct / 100.0))

    # Portfolio state
    cash = float(initial_capital)
    shares = 0.0
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_points: list[dict[str, Any]] = []

    def current_equity(row: pd.Series) -> float:
        close_val = _safe_float(row.get("close"))
        if pd.isna(close_val) or close_val <= 0:
            return float(cash)
        return float(cash + shares * close_val)

    def record_event(
        action: str,
        date: Any,
        price: float,
        qty_ratio: float,
        reason: str,
        entry_type: str,
        entry_date: Any,
        entry_price: float,
        stop_price: float,
        equity_after: float,
    ) -> None:
        ret_pct = (price / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0
        trades.append(
            {
                "구분": action,
                "진입일": entry_date,
                "청산일": date,
                "신호": entry_type,
                "진입가": entry_price,
                "청산가": float(price),
                "손절가": float(stop_price),
                "매도비중%": float(qty_ratio * 100.0),
                "수익률%": float(ret_pct - commission * 100.0),
                "보유일": max(1, (pd.Timestamp(date) - pd.Timestamp(entry_date)).days),
                "청산사유": reason,
                "자본": float(equity_after),
            }
        )

    start_i = 60
    # Iterate until len-1 because entries are filled next open.
    for i in range(start_i, len(data) - 1):
        row = data.iloc[i]
        nxt = data.iloc[i + 1]
        date = data.index[i]
        next_date = data.index[i + 1]
        close = _safe_float(row.get("close"))
        open_next = _safe_float(nxt.get("open"), _safe_float(nxt.get("close")))
        if pd.isna(close) or pd.isna(open_next) or open_next <= 0:
            continue

        equity_points.append({"date": date, "equity": current_equity(row)})

        if position is None or shares <= 0:
            sig = _entry_signal(
                row,
                strategy,
                structure_score,
                supply_score,
                structure_min,
                supply_min,
                volume_min,
                breakout_rsi_min,
                breakout_rsi_max,
                pullback_rsi_min,
                pullback_rsi_max,
            )
            if sig:
                entry_price = float(open_next)
                buy_cash = cash
                shares = (buy_cash * (1.0 - commission)) / entry_price
                cash = 0.0
                stop_price = _pick_stop(row, entry_price, stop_loss_pct)
                position = {
                    "entry_date": next_date,
                    "entry_price": entry_price,
                    "entry_type": sig,
                    "signal_date": date,
                    "stop_price": stop_price,
                    "highest_close": close,
                    "initial_shares": shares,
                    "core_min_shares": shares * 0.50,
                    "partial1_done": False,
                    "partial2_done": False,
                }
            continue

        # Manage open position.
        assert position is not None
        entry_price = float(position["entry_price"])
        entry_date = position["entry_date"]
        entry_type = position["entry_type"]
        stop_price = float(position["stop_price"])

        low = _safe_float(row.get("low"), close)
        high = _safe_float(row.get("high"), close)
        ma20 = _safe_float(row.get("ma20"))
        ma60 = _safe_float(row.get("ma60"))
        ma120 = _safe_float(row.get("ma120"))
        low20_prev = _safe_float(row.get("low20_prev"))
        rsi = _safe_float(row.get("rsi14"), 50.0)
        ret20 = _safe_float(row.get("ret_20d"), 0.0)
        highest_close = _safe_float(position.get("highest_close"), close)
        profit_pct = (close / entry_price - 1.0) * 100.0 if entry_price > 0 and pd.notna(close) else 0.0

        if pd.notna(close):
            position["highest_close"] = max(float(position.get("highest_close", close)), float(close))

        # Stop handling by exit style.
        # Basic/trend modes trail around 60D/20D supports. Core mode gives strong leaders
        # more room so that a temporary 20D/60D shakeout does not cut the whole position.
        if exit_mode == "코어보유형":
            core_stop_candidates = [stop_price]
            if profit_pct >= 20.0:
                # Once a position is a meaningful winner, do not let the core turn into a large loss.
                core_stop_candidates.append(entry_price * 0.95)
                if pd.notna(ma120) and ma120 > 0 and ma120 < close:
                    core_stop_candidates.append(float(ma120) * 0.97)
                if pd.notna(highest_close) and highest_close > 0:
                    core_stop_candidates.append(float(highest_close) * 0.70)  # 30% peak drawdown guard
            position["stop_price"] = max(core_stop_candidates)
        else:
            # Trend-mode trailing stop: use broader trend support, not just fast 20D noise.
            trail_candidates = [stop_price]
            if pd.notna(ma60) and ma60 > 0 and ma60 < close:
                trail_candidates.append(float(ma60) * 0.985)
            if pd.notna(low20_prev) and low20_prev > 0 and low20_prev < close:
                trail_candidates.append(float(low20_prev) * 0.98)
            trend_stop = max(trail_candidates)
            position["stop_price"] = max(stop_price, trend_stop)
        stop_price = float(position["stop_price"])

        # Partial profit taking first, if enabled. It does not close the core.
        if exit_mode in ["분할매도+추세보유", "코어보유형"] and shares > 0:
            overheat1 = rsi >= sell_rsi or (pd.notna(ret20) and ret20 >= overheat_ret20)
            overheat2 = rsi >= 83.0 or (pd.notna(ret20) and ret20 >= 50.0)
            core_min_shares = float(position.get("core_min_shares", 0.0)) if exit_mode == "코어보유형" else 0.0
            sellable_shares = max(0.0, shares - core_min_shares)
            if overheat1 and not position.get("partial1_done", False) and partial_ratio > 0 and sellable_shares > 0:
                sell_shares = min(shares * partial_ratio, sellable_shares)
                actual_ratio = sell_shares / max(shares, 1e-12)
                cash += sell_shares * close * (1.0 - commission)
                shares -= sell_shares
                position["partial1_done"] = True
                record_event("분할매도1", date, close, actual_ratio, "과열 1차 분할매도", entry_type, entry_date, entry_price, stop_price, current_equity(row))
            elif overheat2 and not position.get("partial2_done", False) and partial_ratio > 0 and sellable_shares > 0:
                sell_shares = min(shares * partial_ratio, sellable_shares)
                actual_ratio = sell_shares / max(shares, 1e-12)
                cash += sell_shares * close * (1.0 - commission)
                shares -= sell_shares
                position["partial2_done"] = True
                record_event("분할매도2", date, close, actual_ratio, "극단과열 2차 분할매도", entry_type, entry_date, entry_price, stop_price, current_equity(row))

        exit_price = np.nan
        exit_reason = ""

        if exit_mode == "기본형":
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
        elif exit_mode == "코어보유형":
            # Core holding: full sell only on broad trend break or severe peak drawdown.
            peak_drawdown = close / highest_close - 1.0 if pd.notna(highest_close) and highest_close > 0 else 0.0
            if pd.notna(low) and low <= stop_price and profit_pct < 20.0:
                exit_price = stop_price
                exit_reason = "초기손절"
            elif take_profit_pct and pd.notna(high) and high >= entry_price * (1 + take_profit_pct / 100.0):
                # If a fixed take-profit is intentionally set, close the remainder.
                exit_price = entry_price * (1 + take_profit_pct / 100.0)
                exit_reason = f"고정익절 {take_profit_pct:.1f}%"
            elif pd.notna(ma120) and close < ma120 and rsi < 50:
                exit_price = close
                exit_reason = "120일선 이탈"
            elif peak_drawdown <= -0.30 and rsi < 55:
                exit_price = close
                exit_reason = "고점대비 30% 하락"
            elif pd.notna(ma60) and close < ma60 and rsi < 40:
                exit_price = close
                exit_reason = "60일선+RSI 급약세"
        else:
            # Trend holding: overheat alone is not a full sell. Let winners run.
            if pd.notna(low) and low <= stop_price:
                exit_price = stop_price
                exit_reason = "추세손절/트레일링스탑"
            elif take_profit_pct and pd.notna(high) and high >= entry_price * (1 + take_profit_pct / 100.0):
                # In trend mode, fixed take-profit is optional full take-profit.
                exit_price = entry_price * (1 + take_profit_pct / 100.0)
                exit_reason = f"고정익절 {take_profit_pct:.1f}%"
            elif pd.notna(ma60) and close < ma60 and rsi < 50:
                exit_price = close
                exit_reason = "60일선 이탈"
            elif pd.notna(low20_prev) and close < low20_prev and rsi < 45:
                exit_price = close
                exit_reason = "20일저점+RSI 약세"

        if pd.notna(exit_price) and exit_price > 0 and shares > 0:
            qty_ratio = 1.0
            cash += shares * float(exit_price) * (1.0 - commission)
            shares = 0.0
            record_event("전량매도", date, float(exit_price), qty_ratio, exit_reason, entry_type, entry_date, entry_price, stop_price, cash)
            equity_points.append({"date": date, "equity": float(cash)})
            position = None

    # Close open position at final close for reporting.
    if position is not None and shares > 0:
        last = data.iloc[-1]
        last_date = data.index[-1]
        last_close = _safe_float(last.get("close"))
        if pd.notna(last_close) and last_close > 0:
            cash += shares * last_close * (1.0 - commission)
            entry_price = float(position["entry_price"])
            record_event(
                "기간종료매도",
                last_date,
                float(last_close),
                1.0,
                "기간종료",
                position["entry_type"],
                position["entry_date"],
                entry_price,
                float(position["stop_price"]),
                cash,
            )
            shares = 0.0
            position = None
            equity_points.append({"date": last_date, "equity": float(cash)})

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_points)
    if not equity_df.empty:
        equity_df["date"] = pd.to_datetime(equity_df["date"])
        equity_df = equity_df.sort_values("date").drop_duplicates("date", keep="last")

    final_capital = float(cash)
    bh = _buy_hold_metrics(data, initial_capital, commission, start_i=start_i)

    # For metrics, evaluate only full exits and period-end exits as completed trades.
    full_exits = trades_df[trades_df.get("구분", pd.Series(dtype=str)).isin(["전량매도", "기간종료매도"])] if not trades_df.empty else pd.DataFrame()
    if full_exits.empty:
        metrics = {
            "총 거래": 0,
            "승률%": 0.0,
            "평균수익률%": 0.0,
            "누적수익률%": float((final_capital / initial_capital - 1.0) * 100.0),
            "MDD%": _max_drawdown(equity_df["equity"]) if not equity_df.empty else 0.0,
            "평균보유일": 0.0,
            "손익비": 0.0,
        }
    else:
        rets = pd.to_numeric(full_exits["수익률%"], errors="coerce").dropna()
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        profit_factor = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else float("inf")
        metrics = {
            "총 거래": int(len(full_exits)),
            "총 이벤트": int(len(trades_df)),
            "승률%": float((rets > 0).mean() * 100.0) if len(rets) else 0.0,
            "평균수익률%": float(rets.mean()) if len(rets) else 0.0,
            "중앙수익률%": float(rets.median()) if len(rets) else 0.0,
            "최고수익률%": float(rets.max()) if len(rets) else 0.0,
            "최저수익률%": float(rets.min()) if len(rets) else 0.0,
            "누적수익률%": float((final_capital / initial_capital - 1.0) * 100.0),
            "MDD%": _max_drawdown(equity_df["equity"]) if not equity_df.empty else 0.0,
            "평균보유일": float(full_exits["보유일"].mean()) if "보유일" in full_exits else 0.0,
            "손익비": profit_factor,
        }

    metrics.update(bh)
    metrics["초과수익률%"] = float(metrics.get("누적수익률%", 0.0) - metrics.get("매수보유수익률%", 0.0))
    metrics["MDD개선%p"] = float(metrics.get("매수보유MDD%", 0.0) - metrics.get("MDD%", 0.0))
    metrics["청산방식"] = exit_mode

    return BacktestResult(metrics=metrics, trades=trades_df, equity_curve=equity_df, data=data)
