from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.krx_sources import build_flow_pack, normalize_kr_ticker  # noqa: E402

WATCHLIST_PATH = ROOT / "data" / "watchlist.csv"
OUT_PATH = ROOT / "data" / "flow_auto.csv"


def clean_number(value: Any):
    """Return a CSV-friendly number or blank."""
    try:
        if value is None:
            return ""
        if pd.isna(value):
            return ""
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return ""
        return round(v, 4)
    except Exception:
        return ""


def main() -> int:
    if not WATCHLIST_PATH.exists():
        print(f"watchlist not found: {WATCHLIST_PATH}")
        return 1

    watchlist = pd.read_csv(WATCHLIST_PATH)
    if "ticker" not in watchlist.columns:
        print("watchlist.csv needs ticker column")
        return 1

    rows: list[dict[str, Any]] = []
    updated_at = datetime.now().isoformat(timespec="seconds")

    for _, item in watchlist.iterrows():
        ticker = str(item.get("ticker", "")).strip().upper()
        name = str(item.get("name", "")).strip()
        if not normalize_kr_ticker(ticker):
            print(f"skip non-KRX ticker: {ticker}")
            continue

        print(f"collect: {ticker} {name}")
        pack = build_flow_pack(ticker, enable=True)
        flow = pack.get("flow", {}) or {}
        short = pack.get("short", {}) or {}

        rows.append({
            "ticker": ticker,
            "종목": name,
            "updated_at": updated_at,
            "기관5일": clean_number(flow.get("inst_5d")),
            "기관20일": clean_number(flow.get("inst_20d")),
            "외국인5일": clean_number(flow.get("foreign_5d")),
            "외국인20일": clean_number(flow.get("foreign_20d")),
            "연기금5일": clean_number(flow.get("pension_5d")),
            "연기금20일": clean_number(flow.get("pension_20d")),
            "공매도비중%": clean_number(short.get("short_ratio_latest")),
            "공매도잔고비중%": clean_number(short.get("short_balance_ratio_latest")),
            "잔고증감5일%p": clean_number(short.get("short_balance_ratio_change_5d")),
            "수급데이터상태": flow.get("reason") or flow.get("source") or "조회성공",
            "공매도데이터상태": short.get("reason") or short.get("short_trade_source") or short.get("source") or "조회성공",
        })

    if not rows:
        print("No KRX rows collected. Not writing flow_auto.csv.")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(rows)

    # Avoid deleting an existing good cache when KRX returns blanks for all rows.
    important_cols = ["기관5일", "외국인5일", "연기금20일", "공매도비중%", "공매도잔고비중%"]
    has_any_value = False
    for col in important_cols:
        if col in new_df.columns and new_df[col].astype(str).str.strip().replace("", pd.NA).notna().any():
            has_any_value = True
            break

    if not has_any_value and OUT_PATH.exists():
        print("Collected data is blank; keeping existing flow_auto.csv.")
        return 0

    new_df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"written: {OUT_PATH} ({len(new_df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
