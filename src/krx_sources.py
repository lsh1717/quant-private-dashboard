from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd


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


def _fallback_weekday_yyyymmdd(d: datetime | None = None) -> str:
    d = d or datetime.now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _past_yyyymmdd(days: int = 90) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def _nearest_business_day(stock_module: Any, date_yyyymmdd: str) -> str:
    """Use pykrx business-day helper when it exists, otherwise fallback to weekday.

    KRX data often fails on weekends/holidays or when a non-trading day is used.
    pykrx has changed helper behavior across versions, so this wrapper degrades
    safely instead of breaking the dashboard.
    """
    try:
        fn = getattr(stock_module, "get_nearest_business_day_in_a_week", None)
        if callable(fn):
            result = fn(date_yyyymmdd)
            if result:
                return str(result).replace("-", "")
    except Exception:
        pass
    try:
        return _fallback_weekday_yyyymmdd(datetime.strptime(date_yyyymmdd, "%Y%m%d"))
    except Exception:
        return _fallback_weekday_yyyymmdd()


def _date_range(stock_module: Any, lookback_calendar_days: int = 90) -> tuple[str, str]:
    today = datetime.now().strftime("%Y%m%d")
    end = _nearest_business_day(stock_module, today)
    try:
        end_dt = datetime.strptime(end, "%Y%m%d")
    except Exception:
        end_dt = datetime.now()
    start_raw = (end_dt - timedelta(days=lookback_calendar_days)).strftime("%Y%m%d")
    start = _nearest_business_day(stock_module, start_raw)
    # If the start helper returns a date after end for any odd reason, use raw.
    if start > end:
        start = start_raw
    return start, end


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


def _call_trading_value_by_date(stock_module: Any, start: str, end: str, ticker: str) -> pd.DataFrame:
    """Try pykrx investor-flow calls with several signatures.

    pykrx/KRX occasionally changes accepted parameters or returns empty data for
    detail=True. Try detail=True first for pension data, then fallback to the
    simpler investor groups.
    """
    attempts: list[tuple[str, dict[str, Any]]] = [
        ("detail=True", {"detail": True}),
        ("detail=False", {}),
    ]
    last_error: Exception | None = None
    for _, kwargs in attempts:
        try:
            df = stock_module.get_market_trading_value_by_date(start, end, ticker, **kwargs)
            if df is not None and not df.empty:
                return df
        except TypeError as exc:
            last_error = exc
            # Old pykrx may not support detail. Retry without kwargs.
            try:
                df = stock_module.get_market_trading_value_by_date(start, end, ticker)
                if df is not None and not df.empty:
                    return df
            except Exception as exc2:  # noqa: BLE001
                last_error = exc2
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if last_error:
        raise last_error
    return pd.DataFrame()


def fetch_investor_flow(ticker: str, lookback_calendar_days: int = 90) -> dict[str, Any]:
    """Fetch KRX investor net trading value using pykrx.

    Values are usually KRW net trading value. The function degrades gracefully
    because KRX/pykrx may be delayed, blocked, or unavailable on Streamlit Cloud.
    """
    krx_ticker = normalize_kr_ticker(ticker)
    if not krx_ticker:
        return {"available": False, "reason": "KRX 종목 아님"}

    try:
        from pykrx import stock  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"pykrx 미설치: {exc}"}

    start, end = _date_range(stock, lookback_calendar_days)
    try:
        df = _call_trading_value_by_date(stock, start, end, krx_ticker)
        if df is None or df.empty:
            # One more fallback with a wider range. This helps after long market holidays.
            start2, end2 = _date_range(stock, 180)
            df = _call_trading_value_by_date(stock, start2, end2, krx_ticker)
            start, end = start2, end2
        if df is None or df.empty:
            return {"available": False, "reason": f"수급 데이터 없음({start}~{end})"}

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
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"KRX 수급 조회 실패({start}~{end}): {exc}"}


def investor_flow_score(flow: dict[str, Any]) -> float:
    if not flow or not flow.get("available"):
        return 50.0
    score = 50.0
    inst_5d = flow.get("inst_5d", np.nan)
    inst_20d = flow.get("inst_20d", np.nan)
    foreign_5d = flow.get("foreign_5d", np.nan)
    foreign_20d = flow.get("foreign_20d", np.nan)
    pension_20d = flow.get("pension_20d", np.nan)
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


def _try_short_frame(stock_module: Any, start: str, end: str, ticker: str) -> tuple[pd.DataFrame, str]:
    """Try several pykrx short-selling functions.

    Some pykrx versions throw KeyError('거래량') in get_shorting_volume_by_date
    because the KRX response columns changed. In that case use value/status
    based functions and read any ratio-like column.
    """
    candidates = [
        "get_shorting_volume_by_date",
        "get_shorting_value_by_date",
        "get_shorting_status_by_date",
    ]
    errors: list[str] = []
    for name in candidates:
        fn = getattr(stock_module, name, None)
        if not callable(fn):
            continue
        try:
            df = fn(start, end, ticker)
            if df is not None and not df.empty:
                return df, name
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors) if errors else "공매도 함수 없음")


def fetch_short_metrics(ticker: str, lookback_calendar_days: int = 90) -> dict[str, Any]:
    """Fetch short-selling metrics using pykrx when available."""
    krx_ticker = normalize_kr_ticker(ticker)
    if not krx_ticker:
        return {"available": False, "reason": "KRX 종목 아님"}
    try:
        from pykrx import stock  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"pykrx 미설치: {exc}"}

    start, end = _date_range(stock, lookback_calendar_days)
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
        vol_df, source_name = _try_short_frame(stock, start, end, krx_ticker)
        if vol_df is not None and not vol_df.empty:
            ratio_col = _find_col(vol_df, ["비중", "공매도비중", "공매도 비중", "공매도거래비중"])
            if ratio_col:
                out["short_ratio_latest"] = _safe_last(vol_df[ratio_col])
                out["available"] = True
                out["short_trade_source"] = source_name
            else:
                errors.append(f"공매도 거래 비중 컬럼 없음: {list(map(str, vol_df.columns))}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"공매도 거래 조회 실패: {exc}")

    try:
        fn = getattr(stock, "get_shorting_balance_by_date", None)
        if callable(fn):
            bal_df = fn(start, end, krx_ticker)
            if bal_df is not None and not bal_df.empty:
                ratio_col = _find_col(bal_df, ["비중", "잔고비중", "공매도잔고비중", "상장주식수대비"])
                if ratio_col:
                    s = pd.to_numeric(bal_df[ratio_col].dropna(), errors="coerce")
                    if not s.empty:
                        out["short_balance_ratio_latest"] = float(s.iloc[-1])
                        if len(s) >= 6:
                            out["short_balance_ratio_change_5d"] = float(s.iloc[-1] - s.iloc[-6])
                        out["available"] = True
                else:
                    errors.append(f"공매도 잔고 비중 컬럼 없음: {list(map(str, bal_df.columns))}")
        else:
            errors.append("get_shorting_balance_by_date 없음")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"공매도 잔고 조회 실패: {exc}")

    if out["available"]:
        out["from"] = start
        out["to"] = end
        out["short_score"] = short_score(out)
        out["short_signal"] = short_signal(out)
    else:
        out["reason"] = " / ".join(errors) if errors else f"공매도 데이터 없음({start}~{end})"
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
