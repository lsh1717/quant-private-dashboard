from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from src.data_sources import fetch_price_history, load_keywords, load_watchlist, news_metrics, send_telegram_message
from src.indicators import latest_snapshot
from src.scoring import build_trade_plan, manual_structure_score, news_score, technical_score

load_dotenv()
BASE_DIR = Path(__file__).parent


def main() -> None:
    watchlist = load_watchlist(str(BASE_DIR / "data" / "watchlist.csv"))
    keywords = load_keywords(str(BASE_DIR / "config" / "keywords.yaml"))
    alerts = []

    for _, row in watchlist.iterrows():
        ticker = str(row["ticker"])
        try:
            hist = fetch_price_history(ticker, period="9mo", interval="1d")
            snap = latest_snapshot(hist)
        except Exception:
            snap = {}

        structure = manual_structure_score(row)
        tech = technical_score(snap)
        try:
            article_count, keyword_hits, _ = news_metrics(str(row["name"]), str(row["sector"]), str(row["theme"]), keywords)
        except Exception:
            article_count, keyword_hits = 0, 0
        ns = news_score(article_count, keyword_hits)
        plan = build_trade_plan(row, snap, structure, tech, ns)

        if plan.status in ["진입가능", "손절위험"]:
            close = snap.get("close")
            close_text = f"{close:,.0f}" if close else "가격 확인 불가"
            alerts.append(
                f"[{plan.status}] {row['name']}({ticker})\n"
                f"점수: {plan.score:.1f} / 현재가: {close_text}\n"
                f"진입: {plan.entry_trigger}\n"
                f"손절: {plan.stop_loss}\n"
                f"경고: {plan.warning}"
            )

    if not alerts:
        print("알림 조건 없음")
        return

    text = "\n\n---\n\n".join(alerts[:8])
    sent = send_telegram_message(text)
    print("텔레그램 전송 완료" if sent else text)


if __name__ == "__main__":
    main()
