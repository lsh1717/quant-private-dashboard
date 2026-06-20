from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TradePlan:
    status: str
    score: float
    entry_trigger: str
    stop_loss: str
    sell_rules: str
    warning: str
    action: str = "관찰"
    buy_rules: str = ""
    partial_sell_rules: str = ""
    full_sell_rules: str = ""
    stop_price: float | None = None
    hard_stop_price: float | None = None
    alert_priority: str = "낮음"
    alert_reason: str = ""
    supply_score: float = 50.0
    real_flow_score: float = 50.0
    short_score: float = 50.0
    supply_signal: str = "데이터없음"
    short_signal: str = "데이터없음"


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    return float(max(low, min(high, value)))


def _num(value: Any, default: float = np.nan) -> float:
    try:
        return float(value) if pd.notna(value) else default
    except Exception:
        return default


def _fmt_price(x: Any) -> str:
    try:
        if x is None or pd.isna(x):
            return "-"
        return f"{float(x):,.0f}"
    except Exception:
        return "-"


def _fmt_money(x: Any) -> str:
    """Format KRW trading value for table text."""
    try:
        if x is None or pd.isna(x):
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


def technical_score(s: dict[str, Any]) -> float:
    if not s:
        return 0.0
    score = 0.0
    close = s.get("close", np.nan)
    ma20 = s.get("ma20", np.nan)
    ma60 = s.get("ma60", np.nan)
    rsi = s.get("rsi14", 50)
    volume_ratio = s.get("volume_ratio", np.nan)
    ret20 = s.get("ret_20d", 0)

    if pd.notna(ma20) and close > ma20:
        score += 18
    if pd.notna(ma60) and close > ma60:
        score += 18
    if s.get("breakout_20d"):
        score += 22
    elif s.get("near_high20"):
        score += 10
    if s.get("fib_zone") or s.get("near_fib_support"):
        score += 10
    if pd.notna(volume_ratio):
        if volume_ratio >= 2.0:
            score += 16
        elif volume_ratio >= 1.3:
            score += 12
        elif volume_ratio >= 1.0:
            score += 6
    if 45 <= rsi <= 68:
        score += 14
    elif 38 <= rsi < 45:
        score += 10
    elif 68 < rsi <= 75:
        score += 2
    elif rsi > 78:
        score -= 16
    if pd.notna(ret20) and ret20 > 35:
        score -= 15
    return clamp(score)


def news_score(article_count: int, keyword_hits: int) -> float:
    base = min(article_count * 7, 42) + min(keyword_hits * 3, 38)
    return clamp(base)


def manual_structure_score(row: pd.Series) -> float:
    # 기업 구조 점수. manual_smart_money는 v5에서 실제 수급과 분리되어 구조적 수급 기대치로만 사용.
    weights = {
        "manual_narrative": 0.27,
        "manual_policy": 0.18,
        "manual_bottleneck": 0.25,
        "manual_smart_money": 0.13,
        "manual_reflection": 0.17,
    }
    total = 0.0
    for key, weight in weights.items():
        total += clamp(float(row.get(key, 0))) * weight
    return clamp(total)


def total_score(structure: float, tech: float, news: float, supply: float = 50.0) -> float:
    return clamp(structure * 0.42 + tech * 0.28 + supply * 0.20 + news * 0.10)


def _level_candidates(snapshot: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    close = snapshot.get("close", np.nan)
    ma20 = snapshot.get("ma20", np.nan)
    ma60 = snapshot.get("ma60", np.nan)
    low20 = snapshot.get("low20", np.nan)
    fib618 = snapshot.get("fib618", np.nan)
    levels = [x for x in [ma20, ma60, low20, fib618] if pd.notna(x) and x > 0]
    if not levels or pd.isna(close):
        return None, None, None
    below = [x for x in levels if x < close]
    stop_price = max(below) if below else min(levels)
    hard_stop_price = min(levels)
    return float(stop_price), float(hard_stop_price), float(close)


def _fib_text(snapshot: dict[str, Any]) -> str:
    fib382 = snapshot.get("fib382", np.nan)
    fib50 = snapshot.get("fib50", np.nan)
    fib618 = snapshot.get("fib618", np.nan)
    if any(pd.isna(x) for x in [fib382, fib50, fib618]):
        return "피보나치 계산 불가"
    return f"피보나치 눌림 구간 38.2% {_fmt_price(fib382)} / 50% {_fmt_price(fib50)} / 61.8% {_fmt_price(fib618)}"


def _flow_pack_values(flow_pack: dict[str, Any] | None, manual_smart: float) -> tuple[float, float, float, str, str, bool, bool, bool]:
    if not flow_pack:
        return manual_smart, 50.0, 50.0, "데이터없음", "데이터없음", False, False, False
    real_flow_score = float(flow_pack.get("auto_flow_score", 50.0))
    short_s = float(flow_pack.get("short_score", 50.0))
    composite = float(flow_pack.get("composite_supply_score", 50.0))
    flow = flow_pack.get("flow", {}) or {}
    short = flow_pack.get("short", {}) or {}
    supply_signal = str(flow.get("flow_signal", "데이터없음")) if flow.get("available") else "데이터없음"
    short_signal = str(short.get("short_signal", "데이터없음")) if short.get("available") else "데이터없음"
    # 실제 수급이 있으면 실제 데이터를 더 크게 반영. 없으면 수동 기대점수 중심.
    if flow.get("available") or short.get("available"):
        supply_score = clamp(manual_smart * 0.30 + composite * 0.70)
    else:
        supply_score = manual_smart

    inst_5d = flow.get("inst_5d", np.nan)
    foreign_5d = flow.get("foreign_5d", np.nan)
    pension_20d = flow.get("pension_20d", np.nan)
    inst_pos = pd.notna(inst_5d) and inst_5d > 0
    foreign_pos = pd.notna(foreign_5d) and foreign_5d > 0
    pension_pos = pd.notna(pension_20d) and pension_20d > 0
    return supply_score, real_flow_score, short_s, supply_signal, short_signal, inst_pos, foreign_pos, pension_pos


def build_trade_plan(
    row: pd.Series,
    snapshot: dict[str, Any],
    structure: float,
    tech: float,
    news: float,
    flow_pack: dict[str, Any] | None = None,
) -> TradePlan:
    close = snapshot.get("close", np.nan)
    ma20 = snapshot.get("ma20", np.nan)
    ma60 = snapshot.get("ma60", np.nan)
    low20 = snapshot.get("low20", np.nan)
    high20 = snapshot.get("high20", np.nan)
    rsi = snapshot.get("rsi14", np.nan)
    vr = snapshot.get("volume_ratio", np.nan)
    ret5 = snapshot.get("ret_5d", np.nan)
    ret20 = snapshot.get("ret_20d", np.nan)
    fib382 = snapshot.get("fib382", np.nan)
    fib618 = snapshot.get("fib618", np.nan)
    manual_smart = clamp(_num(row.get("manual_smart_money", 0)))

    supply_score, real_flow_score, short_s, supply_signal, short_signal, inst_pos, foreign_pos, pension_pos = _flow_pack_values(flow_pack, manual_smart)
    score = total_score(structure, tech, news, supply_score)

    if not snapshot or pd.isna(close):
        return TradePlan(
            "데이터없음",
            score,
            "가격 데이터를 확인해야 함",
            "계산 불가",
            "계산 불가",
            "데이터 소스 연결 필요",
            action="데이터 확인",
            buy_rules="가격 데이터가 들어와야 매수 조건 계산 가능",
            partial_sell_rules="계산 불가",
            full_sell_rules="계산 불가",
            alert_priority="중간",
            alert_reason="데이터 오류",
            supply_score=supply_score,
            real_flow_score=real_flow_score,
            short_score=short_s,
            supply_signal=supply_signal,
            short_signal=short_signal,
        )

    overheat = (pd.notna(rsi) and rsi >= 78) or (pd.notna(ret20) and ret20 >= 35)
    extreme_overheat = (pd.notna(rsi) and rsi >= 83) or (pd.notna(ret20) and ret20 >= 50)
    breakdown = (pd.notna(ma20) and close < ma20) and (pd.notna(ma60) and close < ma60)
    hard_breakdown = pd.notna(low20) and close < low20
    breakout = snapshot.get("breakout_20d", False)
    near_high = snapshot.get("near_high20", False)
    near_low = snapshot.get("near_low20", False)
    fib_zone = snapshot.get("fib_zone", False)
    near_fib_support = snapshot.get("near_fib_support", False)
    volume_ok = pd.notna(vr) and vr >= 1.3
    above_ma20 = pd.notna(ma20) and close > ma20
    above_ma60 = pd.notna(ma60) and close > ma60
    trend_ok = above_ma20 and above_ma60
    rsi_breakout_ok = pd.notna(rsi) and 45 <= rsi <= 70
    rsi_pullback_ok = pd.notna(rsi) and 38 <= rsi <= 58
    not_overheated = not overheat

    real_flow_available = bool(flow_pack and ((flow_pack.get("flow", {}) or {}).get("available") or (flow_pack.get("short", {}) or {}).get("available")))
    supply_ok = supply_score >= 68
    supply_strong = supply_score >= 78
    actual_double_buy = inst_pos and foreign_pos
    short_bad = short_signal in ["공매도 부담 큼", "잔고 증가 주의"] and short_s < 45
    short_good = short_signal == "잔고 감소 우호" or short_s >= 60

    # v5 핵심 신호: 수동 기대점수만 쓰던 v4와 달리 실제 KRX 수급/공매도 데이터를 반영.
    breakout_buy = structure >= 75 and supply_ok and trend_ok and breakout and volume_ok and rsi_breakout_ok and not_overheated and not short_bad
    strong_breakout_buy = breakout_buy and supply_strong and (actual_double_buy or not real_flow_available or short_good)
    pullback_buy = structure >= 75 and supply_score >= 64 and above_ma60 and (fib_zone or near_fib_support) and rsi_pullback_ok and not hard_breakdown and not extreme_overheat and not short_bad
    wait_signal = structure >= 72 and above_ma60 and not_overheated and (near_high or volume_ok or fib_zone or near_fib_support or supply_score >= 70)

    if hard_breakdown:
        status = "손절위험"
    elif overheat:
        status = "추격금지"
    elif breakdown:
        status = "손절위험"
    elif breakout_buy or pullback_buy:
        status = "진입가능"
    elif wait_signal:
        status = "진입대기"
    elif structure >= 65:
        status = "관심"
    else:
        status = "관찰"

    stop_price, hard_stop_price, _ = _level_candidates(snapshot)
    fib_txt = _fib_text(snapshot)

    flow_note = f"수급판정: {supply_signal}, 공매도: {short_signal}, 종합수급점수 {supply_score:.1f}."

    if strong_breakout_buy:
        entry = "강한 돌파 매수 조건 충족: 구조+실제 수급/공매도+추세+RSI+거래량+20일 고점 돌파 확인."
        buy_rules = (
            f"강한 매수 후보: 구조점수 {structure:.1f}, 종합수급점수 {supply_score:.1f}, RSI {rsi:.1f}. "
            f"20일 고점 {_fmt_price(high20)} 돌파 후 종가 유지 + 거래량 1.3배 이상 + {flow_note} "
            "1차 진입 후 돌파선/20일선 지지 확인 시 2차 진입."
        )
    elif breakout_buy:
        entry = "돌파 매수 조건 충족: 구조+수급+추세+RSI+거래량+20일 고점 돌파 확인."
        buy_rules = (
            f"돌파 매수: 구조점수 {structure:.1f}, 종합수급점수 {supply_score:.1f}, RSI {rsi:.1f}. "
            f"20일 고점 {_fmt_price(high20)} 돌파 후 종가 유지 + 거래량 1.3배 이상이면 1차 진입. "
            f"{flow_note} RSI 70 초과 시 신규 추격 금지."
        )
    elif pullback_buy:
        entry = "눌림 매수 조건 충족: 상승 추세 유지 + 피보나치 되돌림 구간 지지 + RSI 안정 + 수급 악화 없음."
        buy_rules = (
            f"눌림 매수: {fib_txt}. 현재가가 이 구간에서 지지되고 RSI {rsi:.1f}가 38~58 사이면 1차 진입 후보. "
            f"{flow_note} 20일선 {_fmt_price(ma20)} 회복 또는 거래량 재증가 시 2차 진입. 61.8% {_fmt_price(fib618)} 이탈 시 실패."
        )
    elif wait_signal:
        entry = "진입대기: 구조/추세/수급 중 일부는 괜찮지만 돌파·거래량·RSI·피보나치 지지 확정이 부족."
        buy_rules = (
            "아직 매수 아님. 조건 중 하나를 기다림: "
            f"① 20일 고점 {_fmt_price(high20)} 거래량 동반 돌파, "
            f"② {fib_txt}에서 지지 후 양봉 전환, "
            "③ RSI 45~70에서 거래량 1.3배 이상, "
            "④ 기관/외국인 동반매수 또는 공매도 잔고 감소 확인."
        )
    elif above_ma20:
        entry = "20일선 위에서 관찰. 구조/수급 또는 거래량 확인 부족."
        buy_rules = (
            f"조건부 관심: 20일선 {_fmt_price(ma20)} 위는 유지 중. "
            f"매수는 20일 고점 {_fmt_price(high20)} 돌파+거래량 증가 또는 피보나치 지지+수급개선 확인 후."
        )
    elif above_ma60:
        entry = "60일선 위의 초기 회복 구간. 아직 확정 매수 아님."
        buy_rules = f"관심 매수만 가능: 60일선 {_fmt_price(ma60)} 위를 유지하고 20일선 재회복, 피보나치 지지, 수급개선 확인 필요."
    else:
        entry = "아직 진입보다 관찰. 20일선/60일선 회복, 거래량 증가, 뉴스 트리거 확인 필요."
        buy_rules = f"매수 금지: 20일선 {_fmt_price(ma20)} 또는 60일선 {_fmt_price(ma60)} 회복 전까지 관찰. 뉴스만 보고 선진입하지 않기."

    if stop_price and hard_stop_price:
        stop_text = (
            f"1차 손절 {stop_price:,.0f} 부근. 강제 손절 {hard_stop_price:,.0f} 부근. "
            "진입 근거였던 지지선/돌파선/피보나치 61.8% 이탈 시 매도. 수급 동반 악화면 손절을 늦추지 않기."
        )
    else:
        stop_text = "직전 저점 이탈 또는 -5~-8% 손실 구간에서 강제 점검."

    partial_sell_rules = []
    full_sell_rules = []

    if overheat:
        partial_sell_rules.append("RSI 과열 또는 20일 수익률 급등: 신규 매수 금지, 보유자는 20~30% 분할매도 검토")
    if pd.notna(rsi) and rsi >= 70 and not volume_ok:
        partial_sell_rules.append("RSI 70 이상인데 거래량 부족: 상승 탄력 약화 가능성, 일부 이익실현 검토")
    if near_high and not volume_ok:
        partial_sell_rules.append("전고점 부근인데 거래량 부족: 돌파 실패 가능성, 일부 이익실현 검토")
    if pd.notna(vr) and vr >= 2.5 and pd.notna(rsi) and rsi >= 75:
        partial_sell_rules.append("거래량 폭증 + 과매수: 단기 피크 가능성, 분할매도 우선")
    if pd.notna(fib382) and close < fib382 and pd.notna(rsi) and rsi >= 68:
        partial_sell_rules.append("피보나치 38.2% 위 안착 실패 + RSI 부담: 눌림 가능성")
    if real_flow_available and supply_score < 45 and not breakdown:
        partial_sell_rules.append("실제 수급점수 약화: 기관/외국인 매수 둔화 가능성, 비중 일부 축소 검토")
    if short_bad:
        partial_sell_rules.append("공매도 부담 또는 잔고 증가: 신규매수 금지, 보유자는 일부 방어")

    if breakdown:
        full_sell_rules.append("20일선과 60일선을 동시에 이탈: 보유 명분 약화, 전량매도 후보")
    if hard_breakdown:
        full_sell_rules.append("20일 최저가 이탈: 손절/전량매도 우선")
    if pd.notna(fib618) and close < fib618 and above_ma60 is False:
        full_sell_rules.append("피보나치 61.8%와 60일선 아래로 밀림: 눌림 실패, 방어 우선")
    if pd.notna(ret5) and ret5 <= -12 and pd.notna(vr) and vr >= 1.5:
        full_sell_rules.append("5일 급락 + 거래량 증가: 악성 매물 가능성, 방어 우선")
    if real_flow_available and supply_signal == "기관+외국인 동반매도" and breakdown:
        full_sell_rules.append("기관+외국인 동반매도 + 추세 이탈: 전량매도 우선")
    if short_signal == "공매도 부담 큼" and breakdown:
        full_sell_rules.append("공매도 부담 큼 + 추세 이탈: 손절 우선")
    if news >= 70 and tech < 45:
        full_sell_rules.append("뉴스는 강한데 차트가 약함: 재료 소멸/기대 선반영 가능성 점검")

    partial_sell_text = " / ".join(partial_sell_rules) if partial_sell_rules else "분할매도 조건 아님: 20일선 유지, 거래량 정상, 과열·수급악화 신호 낮으면 보유 가능"
    full_sell_text = " / ".join(full_sell_rules) if full_sell_rules else "전량매도 조건 아님: 핵심 지지선 이탈 전까지는 보유 관찰"

    sell = (
        "분할매도: RSI 70 이상에서 거래량 둔화, 전고점 돌파 실패, 거래량 폭증 후 윗꼬리, 기관/외국인 수급 둔화, 공매도 잔고 증가 시. "
        "전량매도: 20일선+60일선 동시 이탈, 20일 최저가 이탈, 피보나치 61.8% 이탈 후 회복 실패, 기관/외국인 동반매도, 진입 근거 훼손 시."
    )

    if hard_breakdown:
        action = "전량매도/손절 우선"
        alert_priority = "긴급"
        alert_reason = "20일 최저가 이탈"
    elif breakdown:
        action = "손절/비중축소"
        alert_priority = "높음"
        alert_reason = "20일선+60일선 동시 이탈"
    elif extreme_overheat:
        action = "분할매도 우선"
        alert_priority = "높음"
        alert_reason = "극단 과열"
    elif overheat:
        action = "신규매수 금지·분할매도 검토"
        alert_priority = "중간"
        alert_reason = "단기 과열"
    elif strong_breakout_buy:
        action = "강한 매수 후보"
        alert_priority = "높음"
        alert_reason = "실제/종합 수급 우호 + 돌파+거래량+RSI 적정"
    elif breakout_buy:
        action = "1차 매수 가능"
        alert_priority = "높음"
        alert_reason = "돌파+거래량+RSI 적정+수급 악화 없음"
    elif pullback_buy:
        action = "눌림 매수 후보"
        alert_priority = "높음"
        alert_reason = "피보나치 지지+RSI 안정+수급 악화 없음"
    elif wait_signal:
        action = "진입대기"
        alert_priority = "중간"
        alert_reason = "추세 양호, 확인 신호 대기"
    elif structure >= 65 and near_low:
        action = "관심·반등 확인"
        alert_priority = "중간"
        alert_reason = "구조는 있으나 가격은 눌림 구간"
    else:
        action = "관찰"
        alert_priority = "낮음"
        alert_reason = "확정 신호 부족"

    warning_parts = []
    if manual_smart < 60:
        warning_parts.append("수동 수급기대 낮음")
    if real_flow_available and supply_score < 45:
        warning_parts.append("실제 수급 약함")
    if supply_signal == "기관+외국인 동반매도":
        warning_parts.append("기관+외국인 동반매도")
    if short_bad:
        warning_parts.append("공매도 부담")
    if pd.notna(rsi) and rsi > 70:
        warning_parts.append("RSI 70 초과, 신규매수 주의")
    if pd.notna(rsi) and rsi < 35:
        warning_parts.append("과매도지만 추세 확인 필요")
    if pd.notna(vr) and vr < 0.8:
        warning_parts.append("거래량 부족")
    if overheat:
        warning_parts.append("단기 과열 가능성")
    if news >= 70 and tech < 45:
        warning_parts.append("뉴스는 강하지만 차트 확인 부족")
    if breakdown:
        warning_parts.append("핵심 이동평균선 이탈")
    warning = ", ".join(warning_parts) if warning_parts else "특이 경고 없음"

    return TradePlan(
        status=status,
        score=score,
        entry_trigger=entry,
        stop_loss=stop_text,
        sell_rules=sell,
        warning=warning,
        action=action,
        buy_rules=buy_rules,
        partial_sell_rules=partial_sell_text,
        full_sell_rules=full_sell_text,
        stop_price=stop_price,
        hard_stop_price=hard_stop_price,
        alert_priority=alert_priority,
        alert_reason=alert_reason,
        supply_score=supply_score,
        real_flow_score=real_flow_score,
        short_score=short_s,
        supply_signal=supply_signal,
        short_signal=short_signal,
    )
