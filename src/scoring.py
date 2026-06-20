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


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    return float(max(low, min(high, value)))


def _fmt_price(x: Any) -> str:
    try:
        if x is None or pd.isna(x):
            return "-"
        return f"{float(x):,.0f}"
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
        score += 22
    if pd.notna(ma60) and close > ma60:
        score += 20
    if s.get("breakout_20d"):
        score += 22
    elif s.get("near_high20"):
        score += 12
    if pd.notna(volume_ratio):
        if volume_ratio >= 2.0:
            score += 18
        elif volume_ratio >= 1.3:
            score += 12
        elif volume_ratio >= 1.0:
            score += 6
    if 45 <= rsi <= 65:
        score += 12
    elif 35 <= rsi < 45:
        score += 8
    elif 65 < rsi <= 75:
        score += 6
    elif rsi > 80:
        score -= 12
    if pd.notna(ret20) and ret20 > 35:
        score -= 15
    return clamp(score)


def news_score(article_count: int, keyword_hits: int) -> float:
    base = min(article_count * 7, 42) + min(keyword_hits * 3, 38)
    return clamp(base)


def manual_structure_score(row: pd.Series) -> float:
    weights = {
        "manual_narrative": 0.25,
        "manual_policy": 0.18,
        "manual_bottleneck": 0.24,
        "manual_smart_money": 0.18,
        "manual_reflection": 0.15,
    }
    total = 0.0
    for key, weight in weights.items():
        total += clamp(float(row.get(key, 0))) * weight
    return clamp(total)


def total_score(structure: float, tech: float, news: float) -> float:
    # 네 기준상 구조/내러티브를 최우선, 타이밍은 보조, 뉴스는 트리거로 사용.
    return clamp(structure * 0.50 + tech * 0.35 + news * 0.15)


def _level_candidates(snapshot: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    close = snapshot.get("close", np.nan)
    ma20 = snapshot.get("ma20", np.nan)
    ma60 = snapshot.get("ma60", np.nan)
    low20 = snapshot.get("low20", np.nan)
    levels = [x for x in [ma20, ma60, low20] if pd.notna(x) and x > 0]
    if not levels or pd.isna(close):
        return None, None, None
    below = [x for x in levels if x < close]
    stop_price = max(below) if below else min(levels)
    hard_stop_price = min(levels)
    return float(stop_price), float(hard_stop_price), float(close)


def build_trade_plan(row: pd.Series, snapshot: dict[str, Any], structure: float, tech: float, news: float) -> TradePlan:
    score = total_score(structure, tech, news)
    close = snapshot.get("close", np.nan)
    ma20 = snapshot.get("ma20", np.nan)
    ma60 = snapshot.get("ma60", np.nan)
    low20 = snapshot.get("low20", np.nan)
    high20 = snapshot.get("high20", np.nan)
    high60 = snapshot.get("high60", np.nan)
    rsi = snapshot.get("rsi14", np.nan)
    vr = snapshot.get("volume_ratio", np.nan)
    ret5 = snapshot.get("ret_5d", np.nan)
    ret20 = snapshot.get("ret_20d", np.nan)

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
        )

    overheat = (pd.notna(rsi) and rsi >= 78) or (pd.notna(ret20) and ret20 >= 35)
    extreme_overheat = (pd.notna(rsi) and rsi >= 83) or (pd.notna(ret20) and ret20 >= 50)
    breakdown = (pd.notna(ma20) and close < ma20) and (pd.notna(ma60) and close < ma60)
    hard_breakdown = pd.notna(low20) and close < low20
    breakout = snapshot.get("breakout_20d", False)
    near_high = snapshot.get("near_high20", False)
    near_low = snapshot.get("near_low20", False)
    volume_ok = pd.notna(vr) and vr >= 1.3
    volume_strong = pd.notna(vr) and vr >= 2.0
    above_ma20 = pd.notna(ma20) and close > ma20
    above_ma60 = pd.notna(ma60) and close > ma60
    trend_ok = above_ma20 and above_ma60

    if hard_breakdown:
        status = "손절위험"
    elif overheat:
        status = "추격금지"
    elif breakdown:
        status = "손절위험"
    elif structure >= 75 and breakout and volume_ok:
        status = "진입가능"
    elif structure >= 72 and tech >= 55 and (near_high or volume_ok):
        status = "진입대기"
    elif structure >= 65:
        status = "관심"
    else:
        status = "관찰"

    stop_price, hard_stop_price, _ = _level_candidates(snapshot)

    # 미보유자 기준 매수 조건
    if breakout and volume_ok:
        entry = "20일 신고가 돌파 + 거래량 증가 확인. 종가가 돌파선 위에서 유지될 때만 1차 진입."
        buy_rules = (
            f"1차 매수: 20일 고점 {_fmt_price(high20)} 돌파 후 종가 유지 + 거래량 20일 평균의 1.3배 이상. "
            "2차 매수: 돌파 후 눌림에서 돌파선/20일선 지지 확인. 추격 매수는 금지."
        )
    elif above_ma20:
        entry = "20일선 위에서 눌림 대기. 20일선 근처 지지 + 거래량 재증가 시 분할 진입."
        buy_rules = (
            f"1차 매수: 20일선 {_fmt_price(ma20)} 부근 눌림 후 양봉 전환. "
            "2차 매수: 전고점 재돌파 또는 거래량 1.3배 이상 동반 시."
        )
    elif above_ma60:
        entry = "60일선 회복 초기 구간. 20일선 재회복 또는 박스권 상단 돌파 확인 필요."
        buy_rules = (
            f"관심 매수만 가능: 60일선 {_fmt_price(ma60)} 위를 유지하고 20일선 재회복 확인. "
            "확정 진입은 박스권 상단 돌파 후."
        )
    else:
        entry = "아직 진입보다 관찰. 20일선/60일선 회복, 거래량 증가, 뉴스 트리거 확인 필요."
        buy_rules = (
            f"매수 금지: 20일선 {_fmt_price(ma20)} 또는 60일선 {_fmt_price(ma60)} 회복 전까지 관찰. "
            "뉴스만 보고 선진입하지 않기."
        )

    # 손절 조건
    if stop_price and hard_stop_price:
        stop_text = (
            f"1차 손절 {stop_price:,.0f} 부근. 강제 손절 {hard_stop_price:,.0f} 부근. "
            "진입 근거였던 지지선/돌파선 이탈 시 매도."
        )
        stop_rule_detail = (
            f"손절: 종가 기준 {stop_price:,.0f} 이탈 시 보유분 50% 이상 축소. "
            f"{hard_stop_price:,.0f} 이탈 또는 장대음봉+거래량 급증이면 전량 정리 후보."
        )
    else:
        stop_text = "직전 저점 이탈 또는 -5~-8% 손실 구간에서 강제 점검."
        stop_rule_detail = "손절: 진입가 대비 -5~-8% 또는 직전 저점 이탈 시 매매 아이디어 재검토."

    # 보유자 기준 분할매도 / 전량매도 조건
    partial_sell_rules = []
    full_sell_rules = []

    if overheat:
        partial_sell_rules.append("RSI 과열 또는 20일 수익률 급등: 신규 매수 금지, 보유자는 20~30% 분할매도 검토")
    if near_high and not volume_ok:
        partial_sell_rules.append("전고점 부근인데 거래량 부족: 돌파 실패 가능성, 일부 이익실현 검토")
    if pd.notna(vr) and vr >= 2.5 and pd.notna(rsi) and rsi >= 75:
        partial_sell_rules.append("거래량 폭증 + 과매수: 단기 피크 가능성, 분할매도 우선")
    if above_ma20 and not above_ma60:
        partial_sell_rules.append("20일선은 지키지만 60일선 아래: 반등 실패 시 비중 축소")

    if breakdown:
        full_sell_rules.append("20일선과 60일선을 동시에 이탈: 보유 명분 약화, 전량매도 후보")
    if hard_breakdown:
        full_sell_rules.append("20일 최저가 이탈: 손절/전량매도 우선")
    if pd.notna(ret5) and ret5 <= -12 and pd.notna(vr) and vr >= 1.5:
        full_sell_rules.append("5일 급락 + 거래량 증가: 악성 매물 가능성, 방어 우선")
    if news >= 70 and tech < 45:
        full_sell_rules.append("뉴스는 강한데 차트가 약함: 재료 소멸/기대 선반영 가능성 점검")

    partial_sell_text = " / ".join(partial_sell_rules) if partial_sell_rules else "분할매도 조건 아님: 20일선 유지, 거래량 정상, 과열 신호 낮으면 보유 가능"
    full_sell_text = " / ".join(full_sell_rules) if full_sell_rules else "전량매도 조건 아님: 핵심 지지선 이탈 전까지는 보유 관찰"

    sell = (
        "분할매도: 과열, 전고점 돌파 실패, 거래량 폭증 후 윗꼬리, 좋은 뉴스에도 상승 실패 시. "
        "전량매도: 20일선+60일선 동시 이탈, 20일 최저가 이탈, 진입 근거였던 뉴스/수급 훼손 시."
    )

    # 지금 당장 화면에 띄울 행동 신호
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
    elif structure >= 75 and breakout and volume_ok:
        action = "1차 매수 가능"
        alert_priority = "높음"
        alert_reason = "구조점수 높고 돌파+거래량 확인"
    elif structure >= 72 and trend_ok and (near_high or volume_ok):
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
    if overheat:
        warning_parts.append("단기 과열 가능성")
    if pd.notna(rsi) and rsi < 35:
        warning_parts.append("과매도지만 추세 확인 필요")
    if pd.notna(vr) and vr < 0.8:
        warning_parts.append("거래량 부족")
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
    )
