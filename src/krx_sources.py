from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd


def _today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def _past_yyyymmdd(days: int = 60) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def normalize_kr_ticker(ticker: str) -> str | None:
    """Return six-digit KRX ticker from yfinance-style ticker.

    Examples:
    - 000660.KS -> 000660
    - 277810.KQ -> 277810
    - 005930 -> 005930
    - NVDA -> None
    """
    if ticker is None:
        return None
    t = str(ticker).strip().upper()
    for suffix in [".KS", ".KQ"]:
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    if len(t) == 6 and t.isdigit():
        return t
    return None


def _safe_sum(series: pd.Series, n: int) -> float:
    try:
        s = pd.to_numeric(series.dropna().tail(n), errors="coerce")
        return float(s.sum()) if not s.empty else np.nan
    except Exception:
        return np.nan


def _safe_last(series: pd.Series) -> float:
    try:
        s = pd.to_numeric(series.dropna(), errors="coerce")
        return float(s.iloc[-1]) if not s.empty else np.nan
    except Exception:
        return np.nan


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = [str(c) for c in df.columns]
    for cand in candidates:
        for col in cols:
            if cand == col or cand in col:
                return col
    return None


def _signed_days(series: pd.Series, n: int, positive: bool = True) -> int:
    try:
        s = pd.to_numeric(series.dropna().tail(n), errors="coerce")
        if positive:
            return int((s > 0).sum())
        return int((s < 0).sum())
    except Exception:
        return 0


def fetch_investor_flow(ticker: str, lookback_calendar_days: int = 45) -> dict[str, Any]:
    """Fetch KRX investor net trading value using pykrx.

    The values are usually in KRW trading value and can be positive/negative.
    This function intentionally degrades gracefully. If pykrx/KRX blocks the
    request or the ticker is not Korean, it returns available=False.
    """
    krx_ticker = normalize_kr_ticker(ticker)
    if not krx_ticker:
        return {"available": False, "reason": "KRX 종목 아님"}

    try:
        from pykrx import stock  # type: ignore
    except Exception as exc:
        return {"available": False, "reason": f"pykrx 미설치: {exc}"}

    start = _past_yyyymmdd(lookback_calendar_days)
    end = _today_yyyymmdd()
    try:
        # detail=True gives 금융투자/투신/연기금 등 세부 컬럼 when supported.
        try:
            df = stock.get_market_trading_value_by_date(start, end, krx_ticker, detail=True)
        except TypeError:
            df = stock.get_market_trading_value_by_date(start, end, krx_ticker)
        if df is None or df.empty:
            return {"available": False, "reason": "수급 데이터 없음"}

        inst_col = _find_col(df, ["기관합계", "기관"])
        foreign_col = _find_col(df, ["외국인합계", "외국인"])
        pension_col = _find_col(df, ["연기금 등", "연기금"])

        out: dict[str, Any] = {
            "available": True,
            "from": start,
            "to": end,
            "trading_days": int(len(df)),
            "inst_5d": np.nan,
            "inst_20d": np.nan,
            "foreign_5d": np.nan,
            "foreign_20d": np.nan,
            "pension_5d": np.nan,
            "pension_20d": np.nan,
            "inst_pos_days_5d": 0,
            "foreign_pos_days_5d": 0,
            "pension_pos_days_20d": 0,
            "raw_columns": ",".join(map(str, df.columns)),
        }
        if inst_col:
            out["inst_5d"] = _safe_sum(df[inst_col], 5)
            out["inst_20d"] = _safe_sum(df[inst_col], 20)
            out["inst_pos_days_5d"] = _signed_days(df[inst_col], 5, positive=True)
        if foreign_col:
            out["foreign_5d"] = _safe_sum(df[foreign_col], 5)
            out["foreign_20d"] = _safe_sum(df[foreign_col], 20)
            out["foreign_pos_days_5d"] = _signed_days(df[foreign_col], 5, positive=True)
        if pension_col:
            out["pension_5d"] = _safe_sum(df[pension_col], 5)
            out["pension_20d"] = _safe_sum(df[pension_col], 20)
            out["pension_pos_days_20d"] = _signed_days(df[pension_col], 20, positive=True)
        out["flow_score"] = investor_flow_score(out)
        out["flow_signal"] = investor_flow_signal(out)
        return out
    except Exception as exc:
        return {"available": False, "reason": f"KRX 수급 조회 실패: {exc}"}


def investor_flow_score(flow: dict[str, Any]) -> float:
    if not flow or not flow.get("available"):
        return 50.0
    score = 50.0

    inst_5d = flow.get("inst_5d", np.nan)
    inst_20d = flow.get("inst_20d", np.nan)
    foreign_5d = flow.get("foreign_5d", np.nan)
    foreign_20d = flow.get("foreign_20d", np.nan)
    pension_20d = flow.get("pension_20d", np.nan)

    # Direction is more robust than absolute value because values differ by market cap.
    if pd.notna(inst_5d):
        score += 10 if inst_5d > 0 else -10
    if pd.notna(inst_20d):
        score += 8 if inst_20d > 0 else -8
    if pd.notna(foreign_5d):
        score += 10 if foreign_5d > 0 else -10
    if pd.notna(foreign_20d):
        score += 8 if foreign_20d > 0 else -8
    if pd.notna(pension_20d):
        score += 8 if pension_20d > 0 else -6

    score += min(max(int(flow.get("inst_pos_days_5d", 0)) - 2, -2), 3) * 2
    score += min(max(int(flow.get("foreign_pos_days_5d", 0)) - 2, -2), 3) * 2
    return float(max(0, min(100, score)))


def investor_flow_signal(flow: dict[str, Any]) -> str:
    if not flow or not flow.get("available"):
        return "데이터없음"
    inst_5d = flow.get("inst_5d", np.nan)
    foreign_5d = flow.get("foreign_5d", np.nan)
    pension_20d = flow.get("pension_20d", np.nan)
    pos_inst = pd.notna(inst_5d) and inst_5d > 0
    pos_foreign = pd.notna(foreign_5d) and foreign_5d > 0
    pos_pension = pd.notna(pension_20d) and pension_20d > 0
    neg_inst = pd.notna(inst_5d) and inst_5d < 0
    neg_foreign = pd.notna(foreign_5d) and foreign_5d < 0
    if pos_inst and pos_foreign and pos_pension:
        return "기관+외국인+연기금 우호"
    if pos_inst and pos_foreign:
        return "기관+외국인 동반매수"
    if pos_inst or pos_foreign:
        return "한쪽 수급 우호"
    if neg_inst and neg_foreign:
        return "기관+외국인 동반매도"
    return "중립"


def fetch_short_metrics(ticker: str, lookback_calendar_days: int = 45) -> dict[str, Any]:
    """Fetch short-selling metrics using pykrx when available."""
    krx_ticker = normalize_kr_ticker(ticker)
    if not krx_ticker:
        return {"available": False, "reason": "KRX 종목 아님"}
    try:
        from pykrx import stock  # type: ignore
    except Exception as exc:
        return {"available": False, "reason": f"pykrx 미설치: {exc}"}

    start = _past_yyyymmdd(lookback_calendar_days)
    end = _today_yyyymmdd()
    out: dict[str, Any] = {
        "available": False,
        "short_ratio_latest": np.nan,
        "short_balance_ratio_latest": np.nan,
        "short_balance_ratio_change_5d": np.nan,
        "short_score": 50.0,
        "short_signal": "데이터없음",
    }
    errors: list[str] = []

    try:
        vol_df = stock.get_shorting_volume_by_date(start, end, krx_ticker)
        if vol_df is not None and not vol_df.empty:
            ratio_col = _find_col(vol_df, ["비중", "공매도비중"])
            if ratio_col:
                out["short_ratio_latest"] = _safe_last(vol_df[ratio_col])
                out["available"] = True
    except Exception as exc:
        errors.append(f"공매도 거래 조회 실패: {exc}")

    try:
        bal_df = stock.get_shorting_balance_by_date(start, end, krx_ticker)
        if bal_df is not None and not bal_df.empty:
            ratio_col = _find_col(bal_df, ["비중", "잔고비중"])
            if ratio_col:
                s = pd.to_numeric(bal_df[ratio_col].dropna(), errors="coerce")
                if not s.empty:
                    out["short_balance_ratio_latest"] = float(s.iloc[-1])
                    if len(s) >= 6:
                        out["short_balance_ratio_change_5d"] = float(s.iloc[-1] - s.iloc[-6])
                    out["available"] = True
    except Exception as exc:
        errors.append(f"공매도 잔고 조회 실패: {exc}")

    if out["available"]:
        out["short_score"] = short_score(out)
        out["short_signal"] = short_signal(out)
    else:
        out["reason"] = " / ".join(errors) if errors else "공매도 데이터 없음"
    return out


def short_score(short: dict[str, Any]) -> float:
    if not short or not short.get("available"):
        return 50.0
    score = 50.0
    ratio = short.get("short_ratio_latest", np.nan)
    bal = short.get("short_balance_ratio_latest", np.nan)
    chg = short.get("short_balance_ratio_change_5d", np.nan)

    if pd.notna(ratio):
        if ratio >= 20:
            score -= 18
        elif ratio >= 10:
            score -= 10
        elif ratio <= 3:
            score += 8
    if pd.notna(bal):
        if bal >= 5:
            score -= 18
        elif bal >= 2:
            score -= 8
        elif bal <= 0.5:
            score += 8
    if pd.notna(chg):
        if chg > 0.3:
            score -= 10
        elif chg < -0.3:
            score += 10
    return float(max(0, min(100, score)))


def short_signal(short: dict[str, Any]) -> str:
    if not short or not short.get("available"):
        return "데이터없음"
    ratio = short.get("short_ratio_latest", np.nan)
    bal = short.get("short_balance_ratio_latest", np.nan)
    chg = short.get("short_balance_ratio_change_5d", np.nan)
    if (pd.notna(ratio) and ratio >= 20) or (pd.notna(bal) and bal >= 5):
        return "공매도 부담 큼"
    if pd.notna(chg) and chg > 0.3:
        return "잔고 증가 주의"
    if pd.notna(chg) and chg < -0.3:
        return "잔고 감소 우호"
    return "보통"


def build_flow_pack(ticker: str, enable: bool = True) -> dict[str, Any]:
    if not enable:
        return {
            "flow": {"available": False, "reason": "비활성화"},
            "short": {"available": False, "reason": "비활성화"},
            "auto_flow_score": 50.0,
            "short_score": 50.0,
            "composite_supply_score": 50.0,
        }
    flow = fetch_investor_flow(ticker)
    short = fetch_short_metrics(ticker)
    auto_flow_score = float(flow.get("flow_score", 50.0)) if flow.get("available") else 50.0
    short_s = float(short.get("short_score", 50.0)) if short.get("available") else 50.0
    composite = max(0, min(100, auto_flow_score * 0.70 + short_s * 0.30))
    return {
        "flow": flow,
        "short": short,
        "auto_flow_score": auto_flow_score,
        "short_score": short_s,
        "composite_supply_score": composite,
    }
