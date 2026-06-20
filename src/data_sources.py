from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser
import pandas as pd
import requests
import yaml
import yfinance as yf


def load_watchlist(path: str = "data/watchlist.csv") -> pd.DataFrame:
    df = pd.read_csv(path)
    numeric_cols = [
        "manual_narrative",
        "manual_policy",
        "manual_bottleneck",
        "manual_smart_money",
        "manual_reflection",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(50)
    return df


def load_keywords(path: str = "config/keywords.yaml") -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_price_history(ticker: str, period: str = "9mo", interval: str = "1d") -> pd.DataFrame:
    # auto_adjust=False keeps OHLC values easier to understand for stop calculations.
    return yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)


def google_news_rss_url(query: str) -> str:
    # Google News RSS. This is a lightweight MVP source and may be delayed/incomplete.
    from urllib.parse import quote_plus

    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"


def fetch_news_for_query(query: str, max_items: int = 20) -> list[dict[str, Any]]:
    feed = feedparser.parse(google_news_rss_url(query))
    items = []
    for entry in feed.entries[:max_items]:
        published = entry.get("published", "")
        items.append(
            {
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": published,
                "source": getattr(entry, "source", {}).get("title", "Google News") if hasattr(entry, "source") else "Google News",
            }
        )
    return items


def news_metrics(name: str, sector: str, theme: str, keywords_by_sector: dict[str, list[str]]) -> tuple[int, int, list[dict[str, Any]]]:
    keywords = keywords_by_sector.get(sector, [])
    query = " OR ".join([name, theme] + keywords[:4])
    articles = fetch_news_for_query(query, max_items=12)
    text = " ".join([a["title"] for a in articles])
    hits = 0
    for kw in keywords:
        hits += text.lower().count(str(kw).lower())
    return len(articles), hits, articles


def send_telegram_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
    resp.raise_for_status()
    return True
