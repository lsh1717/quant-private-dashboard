from __future__ import annotations

import os
import inspect
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src.data_sources import fetch_price_history, load_keywords, load_watchlist, news_metrics
from src.indicators import add_indicators, latest_snapshot
from src.scoring import build_trade_plan, manual_structure_score, news_score, technical_score
from src.krx_sources import build_flow_pack, investor_flow_score, investor_flow_signal, short_score, short_signal
from src.backtester import run_backtest

load_dotenv()
BASE_DIR = Path(__file__).parent

st.set_page_config(page_title="개인 투자 대시보드", page_icon="📈", layout="wide")


def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except Exception:
        return os.getenv(name, default)


def password_gate() -> None:
    expected = get_secret("DASHBOARD_PASSWORD", "")
    if not expected:
        return
    if st.session_state.get("authenticated"):
        return
    st.title("개인 투자 대시보드")
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 맞지 않습니다.")
    st.stop()


password_gate()

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None


@st.cache_data(ttl=60 * 10, show_spinner=False)
def cached_price(ticker: str, period: str, interval: str) -> pd.DataFrame:
    return fetch_price_history(ticker, period=period, interval=interval)


@st.cache_data(ttl=60 * 20, show_spinner=False)
def cached_news(name: str, sector: str, theme: str, keywords: dict) -> tuple[int, int, list[dict]]:
    try:
        return news_metrics(name, sector, theme, keywords)
    except Exception:
        return 0, 0, []


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def cached_flow_pack(ticker: str, enabled: bool) -> dict:
    try:
        return build_flow_pack(ticker, enable=enabled)
    except Exception as exc:
        return {
            "flow": {"available": False, "reason": str(exc)},
            "short": {"available": False, "reason": str(exc)},
            "auto_flow_score": 50.0,
            "short_score": 50.0,
            "composite_supply_score": 50.0,
        }




def _ticker_keys(ticker: str) -> list[str]:
    """Return matching keys for yfinance/KRX style tickers."""
    t = str(ticker).strip().upper()
    keys = [t]
    for suffix in [".KS", ".KQ"]:
        if t.endswith(suffix):
            keys.append(t[: -len(suffix)])
    if len(t) == 6 and t.isdigit():
        keys.extend([f"{t}.KS", f"{t}.KQ"])
    return list(dict.fromkeys(keys))


def _to_number(value):
    """Parse numbers from KRX/export CSV cells.

    Supports plain numbers, comma separated values, and simple Korean units
    like 12.3억 / 1.2조. Blank cells return NaN.
    """
    try:
        if value is None or pd.isna(value):
            return pd.NA
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if text in ["", "-", "None", "nan", "NaN"]:
            return pd.NA
        sign = -1 if text.startswith("-") else 1
        text_clean = text.replace("+", "").replace("-", "").replace(",", "").replace("원", "").strip()
        mult = 1.0
        if text_clean.endswith("조"):
            mult = 1_0000_0000_0000
            text_clean = text_clean[:-1]
        elif text_clean.endswith("억"):
            mult = 1_0000_0000
            text_clean = text_clean[:-1]
        elif text_clean.endswith("만"):
            mult = 1_0000
            text_clean = text_clean[:-1]
        return sign * float(text_clean) * mult
    except Exception:
        return pd.NA


def _col(df: pd.DataFrame, names: list[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for name in names:
        key = name.strip().lower()
        if key in lowered:
            return lowered[key]
    for c in df.columns:
        cstr = str(c).strip().lower()
        for name in names:
            if name.strip().lower() in cstr:
                return c
    return None


def load_manual_supply_csv(uploaded_file) -> dict[str, dict]:
    """Load optional user-supplied investor/short CSV.

    Required ticker column: ticker / 티커 / 종목코드.
    Optional numeric columns:
    기관5일, 기관20일, 외국인5일, 외국인20일, 연기금5일, 연기금20일,
    공매도비중%, 공매도잔고비중%, 잔고증감5일%p.
    """
    if uploaded_file is None:
        return {}
    try:
        df = pd.read_csv(uploaded_file)
    except UnicodeDecodeError:
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file, encoding="cp949")
    except Exception as exc:
        st.sidebar.error(f"수급 CSV 읽기 실패: {exc}")
        return {}
    ticker_col = _col(df, ["ticker", "티커", "종목코드", "code", "코드"])
    if ticker_col is None:
        st.sidebar.error("수급 CSV에 ticker/티커/종목코드 컬럼이 필요합니다.")
        return {}

    colmap = {
        "inst_5d": _col(df, ["기관5일", "기관_5일", "inst_5d", "institution_5d"]),
        "inst_20d": _col(df, ["기관20일", "기관_20일", "inst_20d", "institution_20d"]),
        "foreign_5d": _col(df, ["외국인5일", "외국인_5일", "foreign_5d"]),
        "foreign_20d": _col(df, ["외국인20일", "외국인_20일", "foreign_20d"]),
        "pension_5d": _col(df, ["연기금5일", "연기금_5일", "pension_5d"]),
        "pension_20d": _col(df, ["연기금20일", "연기금_20일", "pension_20d"]),
        "short_ratio_latest": _col(df, ["공매도비중%", "공매도비중", "short_ratio_latest"]),
        "short_balance_ratio_latest": _col(df, ["공매도잔고비중%", "공매도잔고비중", "short_balance_ratio_latest"]),
        "short_balance_ratio_change_5d": _col(df, ["잔고증감5일%p", "잔고증감5일", "short_balance_ratio_change_5d"]),
    }
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        raw = str(r.get(ticker_col, "")).strip().upper()
        if not raw:
            continue
        vals = {}
        for key, c in colmap.items():
            vals[key] = _to_number(r.get(c)) if c is not None else pd.NA
        for key in _ticker_keys(raw):
            out[key] = vals
    return out


def manual_supply_pack(ticker: str, manual_map: dict[str, dict]) -> dict | None:
    vals = None
    for key in _ticker_keys(ticker):
        if key in manual_map:
            vals = manual_map[key]
            break
    if vals is None:
        return None

    flow = {
        "available": False,
        "reason": "수동 CSV에 수급값 없음",
        "source": "수동 CSV",
        "inst_5d": vals.get("inst_5d", pd.NA),
        "inst_20d": vals.get("inst_20d", pd.NA),
        "foreign_5d": vals.get("foreign_5d", pd.NA),
        "foreign_20d": vals.get("foreign_20d", pd.NA),
        "pension_5d": vals.get("pension_5d", pd.NA),
        "pension_20d": vals.get("pension_20d", pd.NA),
        "inst_pos_days_5d": 0,
        "foreign_pos_days_5d": 0,
        "pension_pos_days_20d": 0,
    }
    important = [flow["inst_5d"], flow["inst_20d"], flow["foreign_5d"], flow["foreign_20d"], flow["pension_20d"]]
    if any(pd.notna(x) for x in important):
        flow["available"] = True
        flow["reason"] = "수동 CSV 사용"
        flow["flow_score"] = investor_flow_score(flow)
        flow["flow_signal"] = investor_flow_signal(flow)

    short = {
        "available": False,
        "reason": "수동 CSV에 공매도값 없음",
        "source": "수동 CSV",
        "short_ratio_latest": vals.get("short_ratio_latest", pd.NA),
        "short_balance_ratio_latest": vals.get("short_balance_ratio_latest", pd.NA),
        "short_balance_ratio_change_5d": vals.get("short_balance_ratio_change_5d", pd.NA),
    }
    if any(pd.notna(short.get(k)) for k in ["short_ratio_latest", "short_balance_ratio_latest", "short_balance_ratio_change_5d"]):
        short["available"] = True
        short["reason"] = "수동 CSV 사용"
        short["short_score"] = short_score(short)
        short["short_signal"] = short_signal(short)

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

def format_price(x: float) -> str:
    try:
        if pd.isna(x):
            return "-"
        return f"{x:,.0f}"
    except Exception:
        return "-"


def format_money(x: float) -> str:
    try:
        if pd.isna(x):
            return "-"
        v = float(x)
        sign = "+" if v > 0 else "" if v == 0 else "-"
        av = abs(v)
        if av >= 1_0000_0000_0000:
            return f"{sign}{av / 1_0000_0000_0000:.1f}조"
        if av >= 1_0000_0000:
            return f"{sign}{av / 1_0000_0000:.1f}억"
        if av >= 1_0000:
            return f"{sign}{av / 1_0000:.1f}만"
        return f"{v:,.0f}"
    except Exception:
        return "-"




def format_float(x: float, digits: int = 1) -> str:
    try:
        if pd.isna(x):
            return "-"
        return f"{float(x):,.{digits}f}"
    except Exception:
        return "-"

def format_percent(x: float, digits: int = 2) -> str:
    try:
        if pd.isna(x):
            return "-"
        return f"{float(x):,.{digits}f}"
    except Exception:
        return "-"



def safe_float_value(x, default=None):
    """Robustly convert dashboard cells to float.

Streamlit/Pandas may occasionally pass values as formatted strings
(e.g. "2,764,000", "-", "None") when a row is selected.
This function accepts both raw numeric values and display-like strings so
condition checklists do not fall back to 확인불가 unnecessarily.
"""
    try:
        if x is None:
            return default
        if isinstance(x, str):
            text = x.strip()
            if text in ["", "-", "None", "none", "nan", "NaN", "데이터없음"]:
                return default
            parsed = _to_number(text.replace("%", ""))
            if pd.isna(parsed):
                return default
            return float(parsed)
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def enrich_selected_with_snapshot(selected: pd.Series, hist: pd.DataFrame) -> pd.Series:
    """Fill selected-row checklist fields from the already loaded price data.

The main table can show price/RSI values correctly while the checklist may
receive blanks from a styled/display row in some Streamlit Cloud sessions.
This makes the checklist use the same latest_snapshot source as the main
summary table, preventing false '확인불가' rows.
"""
    s = selected.copy()
    try:
        snap = latest_snapshot(hist) if hist is not None and not hist.empty else {}
    except Exception:
        snap = {}
    if not snap:
        return s

    mapping = {
        "현재가": "close",
        "20일선": "ma20",
        "60일선": "ma60",
        "20일고점": "high20",
        "20일저점": "low20",
        "60일고점": "high60",
        "60일저점": "low60",
        "피보38.2": "fib382",
        "피보50": "fib50",
        "피보61.8": "fib618",
    }
    for col, key in mapping.items():
        val = snap.get(key)
        if val is not None and not pd.isna(val):
            s[col] = val

    if snap.get("rsi14") is not None and not pd.isna(snap.get("rsi14")):
        s["RSI"] = round(float(snap.get("rsi14")), 1)
    if snap.get("volume_ratio") is not None and not pd.isna(snap.get("volume_ratio")):
        s["거래량배율"] = round(float(snap.get("volume_ratio")), 2)
    if snap.get("ret_20d") is not None and not pd.isna(snap.get("ret_20d")):
        s["20일수익률%"] = round(float(snap.get("ret_20d")), 1)
    if snap.get("fib_zone") is not None:
        s["피보구간"] = "예" if snap.get("fib_zone") else "아니오"

    return s


def pass_label(ok: bool | None) -> str:
    if ok is None:
        return "⚪ 확인불가"
    return "🟢 충족" if ok else "🔴 미충족"


def condition_row(name: str, ok: bool | None, current: str, criteria: str, use: str, note: str) -> dict:
    return {
        "조건": name,
        "판정": pass_label(ok),
        "현재값": current,
        "기준": criteria,
        "용도": use,
        "해석": note,
    }


def build_condition_checklist(selected: pd.Series) -> tuple[pd.DataFrame, dict[str, str]]:
    """Build a plain-language checklist for the selected stock.

    The goal is to show exactly where each buy/sell condition is checked,
    so the user does not have to infer it from a long sentence.
    """
    close = safe_float_value(selected.get("현재가"))
    ma20 = safe_float_value(selected.get("20일선"))
    ma60 = safe_float_value(selected.get("60일선"))
    high20 = safe_float_value(selected.get("20일고점"))
    low20 = safe_float_value(selected.get("20일저점"))
    rsi = safe_float_value(selected.get("RSI"))
    volume_ratio = safe_float_value(selected.get("거래량배율"))
    structure = safe_float_value(selected.get("구조점수"))
    supply = safe_float_value(selected.get("종합수급점수"))
    short_score = safe_float_value(selected.get("공매도점수"))
    fib382 = safe_float_value(selected.get("피보38.2"))
    fib50 = safe_float_value(selected.get("피보50"))
    fib618 = safe_float_value(selected.get("피보61.8"))
    stop_price = safe_float_value(selected.get("손절가"))
    hard_stop = safe_float_value(selected.get("강제손절가"))
    fib_zone_text = str(selected.get("피보구간", "아니오"))
    supply_signal = str(selected.get("수급판정", "데이터없음"))
    short_signal = str(selected.get("공매도판정", "데이터없음"))

    rows = []
    rows.append(condition_row(
        "구조점수",
        None if structure is None else structure >= 75,
        "-" if structure is None else f"{structure:.1f}",
        "75점 이상",
        "공통 매수 필터",
        "네 기준의 내러티브·정책·병목·수급기대·미반영 점수",
    ))
    rows.append(condition_row(
        "20일선 위",
        None if close is None or ma20 is None else close > ma20,
        f"현재가 {format_price(close)} / 20일선 {format_price(ma20)}",
        "현재가 > 20일선",
        "추세 유지",
        "20일선 위면 단기 추세가 완전히 깨진 상태는 아님",
    ))
    rows.append(condition_row(
        "60일선 위",
        None if close is None or ma60 is None else close > ma60,
        f"현재가 {format_price(close)} / 60일선 {format_price(ma60)}",
        "현재가 > 60일선",
        "중기 추세",
        "눌림 매수는 최소한 60일선 위를 더 좋게 봄",
    ))
    rows.append(condition_row(
        "20일 고점 돌파",
        None if close is None or high20 is None else close >= high20,
        f"현재가 {format_price(close)} / 20일고점 {format_price(high20)}",
        "현재가 또는 종가 ≥ 20일고점",
        "돌파 매수",
        "장중 돌파보다 종가 유지가 더 안전함",
    ))
    rows.append(condition_row(
        "거래량 증가",
        None if volume_ratio is None else volume_ratio >= 1.3,
        "-" if volume_ratio is None else f"{volume_ratio:.2f}배",
        "20일 평균 거래량의 1.3배 이상",
        "돌파 신뢰도",
        "거래량이 붙어야 가짜 돌파 가능성이 줄어듦",
    ))
    rows.append(condition_row(
        "RSI 돌파 적정",
        None if rsi is None else 45 <= rsi <= 70,
        "-" if rsi is None else f"{rsi:.1f}",
        "45~70",
        "돌파 매수",
        "너무 낮으면 힘 부족, 70 초과는 추격 위험",
    ))
    rows.append(condition_row(
        "피보나치 눌림 구간",
        None if fib382 is None or fib618 is None else fib_zone_text == "예",
        f"현재가 {format_price(close)} / 38.2% {format_price(fib382)} / 50% {format_price(fib50)} / 61.8% {format_price(fib618)}",
        "38.2~61.8% 부근 지지",
        "눌림 매수",
        "이 구간에서 버티고 다시 올라가야 눌림 매수 후보",
    ))
    rows.append(condition_row(
        "RSI 눌림 적정",
        None if rsi is None else 38 <= rsi <= 58,
        "-" if rsi is None else f"{rsi:.1f}",
        "38~58",
        "눌림 매수",
        "과열이 식었지만 완전히 무너진 과매도는 아닌 구간",
    ))
    rows.append(condition_row(
        "수급 개선",
        None if supply is None else supply >= 68,
        f"종합수급점수 {'-' if supply is None else f'{supply:.1f}'} / {supply_signal}",
        "68점 이상 또는 기관·외국인 우호",
        "매수 신뢰도",
        "기관/외국인/연기금/수동수급기대/공매도 점수를 합친 값",
    ))
    rows.append(condition_row(
        "공매도 부담 낮음",
        None if short_score is None else short_score >= 50 and "부담 큼" not in short_signal,
        f"공매도점수 {'-' if short_score is None else f'{short_score:.1f}'} / {short_signal}",
        "50점 이상, 부담 큼 아님",
        "리스크 필터",
        "공매도 잔고·비중이 부담이면 신규매수 신뢰도 하락",
    ))
    rows.append(condition_row(
        "1차 손절선 위",
        None if close is None or stop_price is None else close > stop_price,
        f"현재가 {format_price(close)} / 손절가 {format_price(stop_price)}",
        "현재가 > 손절가",
        "리스크 관리",
        "손절선 아래면 신규매수보다 방어 우선",
    ))
    rows.append(condition_row(
        "20일 저점 이탈 아님",
        None if close is None or low20 is None else close > low20,
        f"현재가 {format_price(close)} / 20일저점 {format_price(low20)}",
        "현재가 > 20일저점",
        "전량매도 방어",
        "20일 저점 이탈은 진입 근거 훼손 신호",
    ))
    rows.append(condition_row(
        "강제손절선 위",
        None if close is None or hard_stop is None else close > hard_stop,
        f"현재가 {format_price(close)} / 강제손절가 {format_price(hard_stop)}",
        "현재가 > 강제손절가",
        "최종 방어선",
        "강제손절선 아래면 반등 기대보다 원금 방어 우선",
    ))

    df = pd.DataFrame(rows)

    breakout_checks = [
        structure is not None and structure >= 75,
        close is not None and ma20 is not None and close > ma20,
        close is not None and ma60 is not None and close > ma60,
        close is not None and high20 is not None and close >= high20,
        volume_ratio is not None and volume_ratio >= 1.3,
        rsi is not None and 45 <= rsi <= 70,
        supply is not None and supply >= 68,
        short_score is not None and short_score >= 50 and "부담 큼" not in short_signal,
    ]
    pullback_checks = [
        structure is not None and structure >= 75,
        close is not None and ma60 is not None and close > ma60,
        fib_zone_text == "예",
        rsi is not None and 38 <= rsi <= 58,
        supply is not None and supply >= 64,
        short_score is not None and short_score >= 50 and "부담 큼" not in short_signal,
    ]
    risk_checks = [
        close is not None and ma20 is not None and ma60 is not None and not (close < ma20 and close < ma60),
        close is not None and low20 is not None and close > low20,
        close is not None and hard_stop is not None and close > hard_stop,
    ]

    summary = {
        "돌파매수": f"{sum(bool(x) for x in breakout_checks)} / {len(breakout_checks)}개 충족",
        "눌림매수": f"{sum(bool(x) for x in pullback_checks)} / {len(pullback_checks)}개 충족",
        "방어조건": f"{sum(bool(x) for x in risk_checks)} / {len(risk_checks)}개 안전",
    }
    return df, summary


def make_chart(df: pd.DataFrame, title: str):
    data = add_indicators(df)
    if data.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=data.index, open=data["open"], high=data["high"], low=data["low"], close=data["close"], name="가격"))
    fig.add_trace(go.Scatter(x=data.index, y=data["ma20"], mode="lines", name="20일선"))
    fig.add_trace(go.Scatter(x=data.index, y=data["ma60"], mode="lines", name="60일선"))
    if "fib382" in data.columns:
        fig.add_trace(go.Scatter(x=data.index, y=data["fib382"], mode="lines", name="피보38.2"))
    if "fib50" in data.columns:
        fig.add_trace(go.Scatter(x=data.index, y=data["fib50"], mode="lines", name="피보50"))
    if "fib618" in data.columns:
        fig.add_trace(go.Scatter(x=data.index, y=data["fib618"], mode="lines", name="피보61.8"))
    fig.update_layout(title=title, height=430, margin=dict(l=10, r=10, t=45, b=10), xaxis_rangeslider_visible=False)
    return fig


def _safe_bt_number(value, default: float = 0.0) -> float:
    """Safely convert table values like '데이터없음', '-', None, 2,764,000 to float."""
    try:
        if value is None or pd.isna(value):
            return float(default)
        if isinstance(value, str):
            text = value.replace(',', '').strip()
            if text in ['', '-', 'None', 'nan', 'NaN', '데이터없음', '확인불가']:
                return float(default)
            return float(text)
        return float(value)
    except Exception:
        return float(default)


def _call_run_backtest_compat(df: pd.DataFrame, **kwargs):
    """Call run_backtest with only parameters supported by the deployed backtester.

    This prevents Streamlit Cloud from crashing if app.py updates before
    src/backtester.py is fully overwritten or cached.
    """
    try:
        params = inspect.signature(run_backtest).parameters
        filtered = {k: v for k, v in kwargs.items() if k in params}
        return run_backtest(df, **filtered)
    except TypeError:
        # Last-resort fallback for older backtester versions.
        fallback_keys = {
            'strategy', 'structure_score', 'supply_score', 'structure_min', 'supply_min',
            'volume_min', 'breakout_rsi_min', 'breakout_rsi_max', 'pullback_rsi_min',
            'pullback_rsi_max', 'stop_loss_pct', 'take_profit_pct', 'sell_rsi',
            'overheat_ret20', 'commission_bps', 'initial_capital'
        }
        filtered = {k: v for k, v in kwargs.items() if k in fallback_keys}
        return run_backtest(df, **filtered)

def make_backtest_chart(bt_data: pd.DataFrame, trades: pd.DataFrame, title: str):
    if bt_data is None or bt_data.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=bt_data.index, y=bt_data["close"], mode="lines", name="종가"))
    if "ma20" in bt_data.columns:
        fig.add_trace(go.Scatter(x=bt_data.index, y=bt_data["ma20"], mode="lines", name="20일선"))
    if "ma60" in bt_data.columns:
        fig.add_trace(go.Scatter(x=bt_data.index, y=bt_data["ma60"], mode="lines", name="60일선"))
    if trades is not None and not trades.empty:
        entries = trades.dropna(subset=["진입일", "진입가"]).copy()
        if "진입일" in entries.columns:
            entries = entries.drop_duplicates(subset=["진입일", "진입가"], keep="first")
        exits = trades.dropna(subset=["청산일", "청산가"]).copy()
        fig.add_trace(go.Scatter(
            x=entries["진입일"], y=entries["진입가"], mode="markers",
            name="매수", marker=dict(symbol="triangle-up", size=11)
        ))
        fig.add_trace(go.Scatter(
            x=exits["청산일"], y=exits["청산가"], mode="markers",
            name="매도/분할매도", marker=dict(symbol="triangle-down", size=11)
        ))
    fig.update_layout(title=title, height=430, margin=dict(l=10, r=10, t=45, b=10), xaxis_rangeslider_visible=False)
    return fig


def _metric_fmt(value, digits: int = 1, suffix: str = "") -> str:
    try:
        if value == float("inf"):
            return "무한"
        if pd.isna(value):
            return "-"
        return f"{float(value):,.{digits}f}{suffix}"
    except Exception:
        return "-"


st.title("개인 투자 대시보드")
st.caption("네 기준: 내러티브 → 정책/CAPEX → 병목/공급제한 → 지속 매수 주체 → 아직 덜 반영된 구간 → 차트 확인")

with st.expander("이 대시보드가 판단하는 행동 기준", expanded=False):
    st.markdown(
        """
- **강한 매수 후보**: 구조점수 높음 + 실제/종합 수급 우호 + 공매도 부담 낮음 + 20일 고점 돌파 + 거래량 증가 + RSI 적정.
- **1차 매수 가능**: 돌파 매수 조건 충족. 단, 종가 확인 후 분할 진입.
- **눌림 매수 후보**: 상승 추세 유지 + 피보나치 38.2~61.8% 구간 지지 + RSI 안정 + 수급 악화 없음.
- **진입대기**: 추세는 괜찮지만 돌파/거래량/RSI/피보나치 지지 중 하나가 아직 부족한 상태.
- **신규매수 금지·분할매도 검토**: RSI 과열 또는 20일 수익률 급등. 새로 따라 들어가는 구간이 아니라 보유분 관리 구간.
- **분할매도 우선**: 극단 과열, 거래량 폭증, 전고점 돌파 실패 가능성이 큰 상태.
- **손절/비중축소**: 20일선과 60일선을 동시에 이탈해 진입 근거가 약해진 상태.
- **전량매도/손절 우선**: 20일 최저가 또는 피보나치 61.8%/핵심 지지선 붕괴. 알림이 늦을 수 있으므로 가격 조건을 미리 정해두고 대응.
- **조건 체크리스트**: 종목별 매매 계획에서 20일선, 20일 고점, 거래량, RSI, 피보나치, 수급, 공매도, 손절선을 각각 충족/미충족으로 확인.
        """
    )

with st.sidebar:
    st.header("설정")
    refresh_min = st.selectbox("자동 새로고침", ["끄기", "5분", "15분", "30분", "60분"], index=2)
    period = st.selectbox("가격 조회 기간", ["6mo", "9mo", "1y", "2y"], index=1)
    interval = st.selectbox("봉 기준", ["1d", "1wk"], index=0)
    use_news = st.toggle("뉴스/RSS 점수 사용", value=True)
    use_krx_flow = st.toggle("KRX 실제 수급/공매도 사용", value=True, help="국내 종목(.KS/.KQ)만 적용됩니다. KRX/pykrx 조회가 실패하면 수동 수급기대점수로 대체합니다.")
    st.divider()
    st.caption("온라인 배포 시 .env 또는 Streamlit secrets에 DASHBOARD_PASSWORD를 넣으면 비밀번호가 걸립니다.")

if refresh_min != "끄기" and st_autorefresh:
    minutes = int(refresh_min.replace("분", ""))
    st_autorefresh(interval=minutes * 60 * 1000, key="dashboard_refresh")
elif refresh_min != "끄기" and not st_autorefresh:
    st.info("streamlit-autorefresh 패키지를 설치하면 자동 새로고침을 사용할 수 있습니다.")

watchlist_path = BASE_DIR / "data" / "watchlist.csv"
keywords_path = BASE_DIR / "config" / "keywords.yaml"
watchlist = load_watchlist(str(watchlist_path))
keywords = load_keywords(str(keywords_path))

uploaded = st.sidebar.file_uploader("관심종목 CSV 교체", type=["csv"])
if uploaded is not None:
    watchlist = pd.read_csv(uploaded)

manual_supply_upload = st.sidebar.file_uploader(
    "수급/공매도 CSV 직접 업로드",
    type=["csv"],
    help="자동 KRX 조회가 안 될 때 사용합니다. ticker, 기관5일, 외국인5일, 연기금20일, 공매도비중%, 공매도잔고비중% 컬럼을 넣으면 됩니다.",
)
manual_supply_map = load_manual_supply_csv(manual_supply_upload)
if manual_supply_map:
    st.sidebar.success(f"수동 수급 CSV {len(manual_supply_map)}개 티커 인식")

sector_filter = st.sidebar.multiselect("섹터 필터", sorted(watchlist["sector"].unique().tolist()), default=[])
status_filter = st.sidebar.multiselect("상태 필터", ["진입가능", "진입대기", "관심", "관찰", "추격금지", "손절위험", "데이터없음"], default=[])
action_filter = st.sidebar.multiselect(
    "행동 신호 필터",
    ["강한 매수 후보", "1차 매수 가능", "눌림 매수 후보", "진입대기", "관심·반등 확인", "신규매수 금지·분할매도 검토", "분할매도 우선", "손절/비중축소", "전량매도/손절 우선", "관찰", "데이터 확인"],
    default=[],
)

rows = []
price_data = {}
news_data = {}

progress = st.progress(0, text="데이터 계산 중")
for i, row in watchlist.iterrows():
    ticker = str(row["ticker"])
    try:
        hist = cached_price(ticker, period, interval)
    except Exception:
        hist = pd.DataFrame()
    price_data[ticker] = hist
    snap = latest_snapshot(hist)
    structure = manual_structure_score(row)
    tech = technical_score(snap)
    if use_news:
        article_count, keyword_hits, articles = cached_news(str(row["name"]), str(row["sector"]), str(row["theme"]), keywords)
    else:
        article_count, keyword_hits, articles = 0, 0, []
    news_data[ticker] = articles
    ns = news_score(article_count, keyword_hits)
    flow_pack = cached_flow_pack(ticker, use_krx_flow)
    manual_pack = manual_supply_pack(ticker, manual_supply_map)
    if manual_pack is not None:
        # 수동 CSV가 있으면 자동 KRX 조회 결과보다 우선합니다.
        flow_pack = manual_pack
    plan = build_trade_plan(row, snap, structure, tech, ns, flow_pack=flow_pack)
    flow = flow_pack.get("flow", {}) or {}
    short = flow_pack.get("short", {}) or {}

    flow_reason = str(flow.get("reason") or flow.get("source") or "조회성공") if flow.get("available") else str(flow.get("reason", "데이터없음"))
    short_reason = str(short.get("reason") or short.get("source") or "조회성공") if short.get("available") else str(short.get("reason", "데이터없음"))

    rows.append(
        {
            "상태": plan.status,
            "종합점수": round(plan.score, 1),
            "종목": row["name"],
            "티커": ticker,
            "섹터": row["sector"],
            "테마": row["theme"],
            "현재가": snap.get("close"),
            "20일선": snap.get("ma20"),
            "60일선": snap.get("ma60"),
            "20일고점": snap.get("high20"),
            "20일저점": snap.get("low20"),
            "60일고점": snap.get("high60"),
            "60일저점": snap.get("low60"),
            "RSI": round(snap.get("rsi14", 0), 1) if snap else None,
            "거래량배율": round(snap.get("volume_ratio", 0), 2) if snap else None,
            "20일수익률%": round(snap.get("ret_20d", 0), 1) if snap else None,
            "구조점수": round(structure, 1),
            "차트점수": round(tech, 1),
            "뉴스점수": round(ns, 1),
            "수동수급기대": round(float(row.get("manual_smart_money", 0)), 1),
            "실제수급점수": round(plan.real_flow_score, 1),
            "공매도점수": round(plan.short_score, 1),
            "종합수급점수": round(plan.supply_score, 1),
            "수급판정": plan.supply_signal,
            "수급데이터상태": flow_reason,
            "공매도판정": plan.short_signal,
            "공매도데이터상태": short_reason,
            "기관5일": flow.get("inst_5d"),
            "외국인5일": flow.get("foreign_5d"),
            "연기금20일": flow.get("pension_20d"),
            "기관5일+일수": flow.get("inst_pos_days_5d"),
            "외국인5일+일수": flow.get("foreign_pos_days_5d"),
            "공매도비중%": short.get("short_ratio_latest"),
            "공매도잔고비중%": short.get("short_balance_ratio_latest"),
            "잔고증감5일%p": short.get("short_balance_ratio_change_5d"),
            "피보38.2": snap.get("fib382"),
            "피보50": snap.get("fib50"),
            "피보61.8": snap.get("fib618"),
            "피보구간": "예" if snap.get("fib_zone") else "아니오",
            "뉴스수": article_count,
            "행동신호": plan.action,
            "알림우선순위": plan.alert_priority,
            "알림이유": plan.alert_reason,
            "손절가": plan.stop_price,
            "강제손절가": plan.hard_stop_price,
            "매수조건": plan.buy_rules,
            "분할매도조건": plan.partial_sell_rules,
            "전량매도조건": plan.full_sell_rules,
            "진입조건": plan.entry_trigger,
            "손절기준": plan.stop_loss,
            "매도기준": plan.sell_rules,
            "경고": plan.warning,
            "메모": row.get("notes", ""),
        }
    )
    progress.progress((i + 1) / len(watchlist), text=f"계산 중: {row['name']}")
progress.empty()

result = pd.DataFrame(rows).sort_values(["종합점수"], ascending=False)
if sector_filter:
    result = result[result["섹터"].isin(sector_filter)]
if status_filter:
    result = result[result["상태"].isin(status_filter)]
if action_filter:
    result = result[result["행동신호"].isin(action_filter)]

status_order = ["진입가능", "진입대기", "관심", "관찰", "추격금지", "손절위험", "데이터없음"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("돌파 매수", int(result["행동신호"].isin(["강한 매수 후보", "1차 매수 가능"]).sum()))
col2.metric("눌림 매수", int((result["행동신호"] == "눌림 매수 후보").sum()))
col3.metric("수급 우호", int(result["수급판정"].isin(["기관+외국인+연기금 우호", "기관+외국인 동반매수"]).sum()))
col4.metric("손절/전량매도", int(result["행동신호"].isin(["손절/비중축소", "전량매도/손절 우선"]).sum()))

urgent = result[result["알림우선순위"].isin(["긴급", "높음"])].copy()
if not urgent.empty:
    st.subheader("우선 확인 신호")
    st.dataframe(
        urgent[["알림우선순위", "행동신호", "종목", "현재가", "종합수급점수", "수급판정", "공매도판정", "손절가", "강제손절가", "알림이유", "경고"]]
        .style.format({"현재가": format_price, "손절가": format_price, "강제손절가": format_price, "종합수급점수": lambda x: format_float(x, 1)}),
        use_container_width=True,
        hide_index=True,
    )

st.subheader("오늘의 후보")
view_cols = ["행동신호", "알림우선순위", "상태", "종합점수", "종목", "티커", "섹터", "테마", "현재가", "손절가", "강제손절가", "RSI", "거래량배율", "20일수익률%", "구조점수", "종합수급점수", "실제수급점수", "수동수급기대", "수급판정", "수급데이터상태", "기관5일", "외국인5일", "연기금20일", "공매도판정", "공매도데이터상태", "공매도비중%", "공매도잔고비중%", "차트점수", "뉴스점수", "피보구간", "경고"]
st.dataframe(
    result[view_cols].style.format({
        "현재가": format_price,
        "손절가": format_price,
        "강제손절가": format_price,
        "종합점수": lambda x: format_float(x, 1),
        "종합수급점수": lambda x: format_float(x, 1),
        "실제수급점수": lambda x: format_float(x, 1),
        "수동수급기대": lambda x: format_float(x, 1),
        "RSI": lambda x: format_float(x, 1),
        "거래량배율": lambda x: format_float(x, 2),
        "20일수익률%": lambda x: format_float(x, 1),
        "구조점수": lambda x: format_float(x, 1),
        "차트점수": lambda x: format_float(x, 1),
        "뉴스점수": lambda x: format_float(x, 1),
        "기관5일": format_money,
        "외국인5일": format_money,
        "연기금20일": format_money,
        "공매도비중%": lambda x: format_percent(x, 2),
        "공매도잔고비중%": lambda x: format_percent(x, 2),
    }),
    use_container_width=True,
    hide_index=True,
)

with st.expander("KRX 수급/공매도 데이터 진단", expanded=False):
    st.caption("수급이 데이터없음으로 보일 때는 여기서 원인을 확인하세요. 해외주식은 KRX 대상이 아니며, Streamlit Cloud에서 KRX 접속이 실패할 수도 있습니다. 자동 조회가 계속 비면 사이드바의 수급/공매도 CSV 직접 업로드를 사용하세요.")
    diag_cols = ["종목", "티커", "수급판정", "수급데이터상태", "기관5일", "외국인5일", "연기금20일", "공매도판정", "공매도데이터상태", "공매도비중%", "공매도잔고비중%"]
    st.dataframe(
        result[diag_cols].style.format({
            "기관5일": format_money,
            "외국인5일": format_money,
            "연기금20일": format_money,
            "공매도비중%": lambda x: format_percent(x, 2),
            "공매도잔고비중%": lambda x: format_percent(x, 2),
        }),
        use_container_width=True,
        hide_index=True,
    )

csv = result.to_csv(index=False).encode("utf-8-sig")
st.download_button("결과 CSV 다운로드", data=csv, file_name="investment_dashboard_signals.csv", mime="text/csv")

st.subheader("종목별 매매 계획")
if result.empty:
    st.warning("필터 조건에 맞는 종목이 없습니다.")
else:
    selected_name = st.selectbox("종목 선택", result["종목"].tolist())
    selected = result[result["종목"] == selected_name].iloc[0]
    ticker = selected["티커"]
    # 조건 체크리스트는 위쪽 표와 같은 최신 가격 스냅샷을 사용하도록 보정합니다.
    # 일부 Streamlit Cloud 세션에서 선택 행의 가격/이평/RSI 값이 표시 문자열로 변해
    # 확인불가로 뜨는 문제를 방지합니다.
    selected = enrich_selected_with_snapshot(selected, price_data.get(ticker, pd.DataFrame()))

    a, b, c, d = st.columns(4)
    a.metric("행동 신호", selected["행동신호"])
    b.metric("종합점수", f"{selected['종합점수']:.1f}")
    c.metric("현재가", format_price(selected["현재가"]))
    d.metric("손절가", format_price(selected["손절가"]))

    checklist, checklist_summary = build_condition_checklist(selected)
    s1, s2, s3 = st.columns(3)
    s1.metric("돌파매수 체크", checklist_summary["돌파매수"])
    s2.metric("눌림매수 체크", checklist_summary["눌림매수"])
    s3.metric("방어조건 체크", checklist_summary["방어조건"])

    st.markdown("#### 조건 체크리스트")
    st.dataframe(checklist, use_container_width=True, hide_index=True)
    st.caption("초록색이 많아질수록 조건이 가까워진 것이고, 빨간색이 핵심 조건이면 아직 매수 신호가 아닙니다. 실제 주문 전에는 증권사 원자료 가격·거래량·수급을 다시 확인하세요.")

    st.markdown(f"""
### {selected['종목']} · {selected['섹터']} · {selected['테마']}

**미보유자 매수 조건**  
{selected['매수조건']}

**보유자 분할매도 조건**  
{selected['분할매도조건']}

**보유자 전량매도 조건**  
{selected['전량매도조건']}

**수급/공매도 확인**  
종합수급점수 {selected['종합수급점수']:.1f} · 실제수급점수 {selected['실제수급점수']:.1f} · 수급판정 {selected['수급판정']} · 공매도판정 {selected['공매도판정']}  
수급데이터상태: {selected.get('수급데이터상태', '-')} · 공매도데이터상태: {selected.get('공매도데이터상태', '-')}  
기관5일 {format_money(selected['기관5일'])} · 외국인5일 {format_money(selected['외국인5일'])} · 연기금20일 {format_money(selected['연기금20일'])} · 공매도비중 {selected['공매도비중%'] if pd.notna(selected['공매도비중%']) else '-'}% · 잔고비중 {selected['공매도잔고비중%'] if pd.notna(selected['공매도잔고비중%']) else '-'}%

**피보나치 구간**  
38.2% {format_price(selected['피보38.2'])} · 50% {format_price(selected['피보50'])} · 61.8% {format_price(selected['피보61.8'])} · 현재 피보구간: {selected['피보구간']}

**손절 기준**  
{selected['손절기준']}

**알림 이유**  
{selected['알림우선순위']} · {selected['알림이유']}

**경고**  
{selected['경고']}

**메모**  
{selected['메모']}
""")

    fig = make_chart(price_data.get(ticker, pd.DataFrame()), f"{selected['종목']} 가격/이동평균")
    if fig:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("차트 데이터를 불러오지 못했습니다.")

    if use_news:
        st.markdown("#### 관련 뉴스")
        articles = news_data.get(ticker, [])[:8]
        if not articles:
            st.caption("뉴스 데이터를 불러오지 못했거나 결과가 없습니다.")
        for article in articles:
            title = article.get("title", "제목 없음")
            link = article.get("link", "")
            published = article.get("published", "")
            if link:
                st.markdown(f"- [{title}]({link})  ")
            else:
                st.markdown(f"- {title}")
            if published:
                st.caption(published)


st.subheader("백테스트")
st.caption("현재 대시보드의 차트 조건을 과거 일봉에 적용해보는 검증용입니다. v6.4는 Buy & Hold 비교, 추세보유형, 분할매도+추세보유, 코어보유형 청산을 지원합니다. 수급/뉴스/컨센서스 과거 데이터는 기본 백테스트에 포함되지 않습니다.")

if result.empty:
    st.info("백테스트할 종목이 없습니다.")
else:
    with st.expander("백테스트 설정", expanded=False):
        bt_col1, bt_col2, bt_col3 = st.columns(3)
        bt_name = bt_col1.selectbox("백테스트 종목", result["종목"].tolist(), key="bt_name")
        bt_period = bt_col2.selectbox("백테스트 기간", ["6mo", "1y", "2y", "5y", "max"], index=3)
        bt_strategy = bt_col3.selectbox("진입 전략", ["돌파+눌림", "돌파", "눌림"], index=0)

        bt_col4, bt_col5, bt_col6 = st.columns(3)
        bt_exit_mode = bt_col4.selectbox(
            "청산 방식",
            ["코어보유형", "추세보유형", "분할매도+추세보유", "기본형"],
            index=0,
            help="코어보유형은 과열 때 일부만 팔고 120일선/큰 추세 이탈까지 핵심 물량을 유지합니다. 추세보유형은 60일선 중심, 기본형은 짧은 스윙입니다.",
        )
        bt_stop = bt_col5.slider("고정 손절률 상한", 3.0, 20.0, 10.0, 0.5, help="기술적 손절선이 너무 멀면 이 손절률로 제한합니다.")
        bt_take_profit = bt_col6.slider("고정 익절률", 0.0, 100.0, 0.0, 1.0, help="0이면 고정 익절을 사용하지 않습니다. 추세주는 보통 0으로 두고 추세 이탈까지 보유하는 쪽을 먼저 비교하세요.")

        bt_col7, bt_col8, bt_col9 = st.columns(3)
        bt_fee = bt_col7.number_input("왕복 전 편도 수수료(bps)", min_value=0.0, max_value=100.0, value=1.5, step=0.5)
        bt_vol = bt_col8.slider("돌파 거래량 배율", 1.0, 3.0, 1.3, 0.1)
        bt_supply_min = bt_col9.slider("수급점수 최소값", 0, 100, 50, 5, help="과거 수급 데이터가 없으므로 현재 종합수급점수를 고정값으로 사용합니다.")

        bt_col10, bt_col11 = st.columns(2)
        bt_partial = bt_col10.slider("분할매도 비중", 10.0, 50.0, 30.0, 5.0, help="청산 방식이 분할매도+추세보유일 때 과열 구간마다 매도할 비중입니다.")
        bt_capital = bt_col11.number_input("초기자본", min_value=100000.0, value=10000000.0, step=1000000.0)

        run_bt = st.button("백테스트 실행", type="primary")

    if run_bt:
        bt_selected = result[result["종목"] == bt_name].iloc[0]
        bt_ticker = str(bt_selected["티커"])
        try:
            bt_hist = cached_price(bt_ticker, bt_period, "1d")
        except Exception as exc:
            st.error(f"백테스트 가격 데이터 조회 실패: {exc}")
            bt_hist = pd.DataFrame()

        structure_val = _safe_bt_number(bt_selected.get("구조점수", 80), 80)
        supply_val = _safe_bt_number(
            bt_selected.get("종합수급점수", bt_selected.get("수동수급기대", 50)),
            _safe_bt_number(bt_selected.get("수동수급기대", 50), 50),
        )

        bt = _call_run_backtest_compat(
            bt_hist,
            strategy=bt_strategy,
            exit_mode=bt_exit_mode,
            structure_score=structure_val,
            supply_score=supply_val,
            supply_min=float(bt_supply_min),
            volume_min=float(bt_vol),
            stop_loss_pct=float(bt_stop),
            take_profit_pct=float(bt_take_profit),
            commission_bps=float(bt_fee),
            initial_capital=float(bt_capital),
            partial_sell_pct=float(bt_partial),
        )

        if bt.metrics.get("오류"):
            st.warning(bt.metrics["오류"])
        else:
            m = bt.metrics
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("총 거래", f"{int(m.get('총 거래', 0))}회")
            m2.metric("승률", _metric_fmt(m.get("승률%"), 1, "%"))
            m3.metric("전략 누적", _metric_fmt(m.get("누적수익률%"), 1, "%"))
            m4.metric("Buy & Hold", _metric_fmt(m.get("매수보유수익률%"), 1, "%"))
            m5.metric("초과수익", _metric_fmt(m.get("초과수익률%"), 1, "%"))

            m6, m7, m8, m9, m10 = st.columns(5)
            m6.metric("전략 MDD", _metric_fmt(m.get("MDD%"), 1, "%"))
            m7.metric("보유 MDD", _metric_fmt(m.get("매수보유MDD%"), 1, "%"))
            m8.metric("평균 보유일", _metric_fmt(m.get("평균보유일"), 1, "일"))
            m9.metric("손익비", _metric_fmt(m.get("손익비"), 2))
            m10.metric("최저/최고 거래", f"{_metric_fmt(m.get('최저수익률%'), 1, '%')} / {_metric_fmt(m.get('최고수익률%'), 1, '%')}")

            if float(m.get("초과수익률%", 0) or 0) < 0:
                st.warning("이 설정은 단순 보유보다 수익률이 낮았습니다. 초강한 추세주는 코어보유형/분할매도+추세보유형을 비교해보세요.")
            elif float(m.get("MDD%", 0) or 0) < -30:
                st.warning("전략 MDD가 큽니다. 손절률, 거래량 조건, 청산 방식을 조절해서 낙폭을 줄일 수 있는지 확인하세요.")
            else:
                st.success("단순 보유 대비 성과와 MDD를 함께 확인하세요. 거래 횟수가 너무 적으면 신뢰도는 낮습니다.")

            chart = make_backtest_chart(bt.data, bt.trades, f"{bt_name} 백테스트 매수/매도 표시")
            if chart:
                st.plotly_chart(chart, use_container_width=True)

            if not bt.equity_curve.empty:
                eq_fig = go.Figure()
                eq_fig.add_trace(go.Scatter(x=bt.equity_curve["date"], y=bt.equity_curve["equity"], mode="lines+markers", name="전략 자본"))
                try:
                    bh_close = bt.data.loc[bt.equity_curve["date"], "close"].astype(float)
                    if len(bh_close) > 1 and bh_close.iloc[0] > 0:
                        bh_equity = float(bt_capital) * (bh_close / bh_close.iloc[0])
                        eq_fig.add_trace(go.Scatter(x=bt.equity_curve["date"], y=bh_equity, mode="lines", name="Buy & Hold 비교"))
                except Exception:
                    pass
                eq_fig.update_layout(title="전략 자본 vs Buy & Hold", height=320, margin=dict(l=10, r=10, t=45, b=10))
                st.plotly_chart(eq_fig, use_container_width=True)

            st.markdown("#### 거래 기록")
            if bt.trades.empty:
                st.info("해당 조건으로 발생한 거래가 없습니다. 기간을 늘리거나 조건을 완화해보세요.")
            else:
                show_trades = bt.trades.copy()
                st.dataframe(
                    show_trades.style.format({
                        "진입가": format_price,
                        "청산가": format_price,
                        "손절가": format_price,
                        "수익률%": lambda x: format_float(x, 2),
                        "자본": format_price,
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
                st.download_button(
                    "백테스트 거래기록 CSV 다운로드",
                    data=show_trades.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"backtest_{bt_ticker}.csv",
                    mime="text/csv",
                )

            st.caption("주의: 이 백테스트는 일봉 기반 단순 검증입니다. 신호일 종가 확인 후 다음 거래일 시가 진입, 일중 손절은 저가 기준으로 근사합니다. v6.2의 추세보유형은 과열만으로 전량매도하지 않고 60일선/추세 이탈까지 핵심 물량을 유지합니다. 슬리피지, 세금, 호가 공백, 뉴스·수급 과거 변화는 완전히 반영하지 않습니다.")

st.divider()
st.caption(
    f"마지막 계산: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
    "이 대시보드는 투자 판단 보조용입니다. 데이터 지연/오류 가능성이 있으므로 실제 주문 전 원자료를 반드시 확인하세요."
)
