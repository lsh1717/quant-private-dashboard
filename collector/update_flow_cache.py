from __future__ import annotations

import math
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

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


def _to_number(value: Any) -> float:
    try:
        if value is None or pd.isna(value):
            return float("nan")
        text = str(value).strip()
        if not text or text == "-":
            return float("nan")
        # Naver often uses comma separated signed integers.
        text = text.replace(",", "").replace("+", "")
        return float(text)
    except Exception:
        return float("nan")


def _flat_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [" ".join(str(x) for x in col if str(x) != "nan").strip() for col in out.columns]
    else:
        out.columns = [str(c).strip() for c in out.columns]
    return out


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for cand in candidates:
        for col in map(str, df.columns):
            if cand == col or cand in col:
                return col
    return None


def fetch_naver_investor_flow(ticker: str) -> dict[str, Any] | None:
    """Free fallback: parse Naver Finance investor table.

    This usually provides daily institutional/foreign net trading for Korean
    stocks. It does not provide pension or short-balance metrics, so those stay
    blank. This fallback is useful when pykrx/KRX returns empty data on GitHub
    Actions.
    """
    code = normalize_kr_ticker(ticker)
    if not code:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
        "Referer": f"https://finance.naver.com/item/main.naver?code={code}",
    }
    url = f"https://finance.naver.com/item/frgn.naver?code={code}&page=1"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        html = resp.content.decode("euc-kr", errors="ignore")
        tables = pd.read_html(StringIO(html))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"Naver 수급 조회 실패: {exc}"}

    target = None
    for t in tables:
        df = _flat_columns(t).dropna(how="all")
        date_col = _find_col(df, ["날짜", "일자"])
        inst_col = _find_col(df, ["기관", "기관계"])
        foreign_col = _find_col(df, ["외국인", "외국계"])
        if date_col and (inst_col or foreign_col) and len(df) >= 3:
            target = df
            break

    if target is None:
        return {"available": False, "reason": "Naver 수급 테이블 없음"}

    inst_col = _find_col(target, ["기관", "기관계"])
    foreign_col = _find_col(target, ["외국인", "외국계"])

    inst = target[inst_col].map(_to_number) if inst_col else pd.Series(dtype="float64")
    foreign = target[foreign_col].map(_to_number) if foreign_col else pd.Series(dtype="float64")

    def tail_sum(s: pd.Series, n: int) -> float:
        s2 = pd.to_numeric(s.dropna(), errors="coerce").tail(n)
        return float(s2.sum()) if not s2.empty else float("nan")

    def pos_days(s: pd.Series, n: int) -> int:
        s2 = pd.to_numeric(s.dropna(), errors="coerce").tail(n)
        return int((s2 > 0).sum()) if not s2.empty else 0

    out = {
        "available": True,
        "source": "Naver Finance frgn fallback",
        "reason": "Naver Finance 수급 사용(pykrx 빈값 fallback)",
        "inst_5d": tail_sum(inst, 5),
        "inst_20d": tail_sum(inst, 20),
        "foreign_5d": tail_sum(foreign, 5),
        "foreign_20d": tail_sum(foreign, 20),
        "pension_5d": float("nan"),
        "pension_20d": float("nan"),
        "inst_pos_days_5d": pos_days(inst, 5),
        "foreign_pos_days_5d": pos_days(foreign, 5),
        "pension_pos_days_20d": 0,
        "raw_columns": ",".join(map(str, target.columns)),
    }

    important = [out["inst_5d"], out["inst_20d"], out["foreign_5d"], out["foreign_20d"]]
    if all(pd.isna(x) for x in important):
        return {"available": False, "reason": f"Naver 수급 숫자 없음: {out['raw_columns']}"}
    return out


def has_important_flow(flow: dict[str, Any]) -> bool:
    for k in ["inst_5d", "inst_20d", "foreign_5d", "foreign_20d", "pension_20d"]:
        try:
            if pd.notna(flow.get(k)):
                return True
        except Exception:
            pass
    return False


def existing_cache_has_rows() -> bool:
    try:
        if not OUT_PATH.exists():
            return False
        df = pd.read_csv(OUT_PATH)
        return len(df) > 0
    except Exception:
        return False


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

        # If pykrx/KRX gave no usable investor values, try Naver Finance fallback.
        if not has_important_flow(flow):
            naver_flow = fetch_naver_investor_flow(ticker)
            if naver_flow and naver_flow.get("available"):
                print(f"  using Naver fallback for {ticker}")
                flow = naver_flow
            else:
                reason = naver_flow.get("reason") if isinstance(naver_flow, dict) else "Naver fallback unavailable"
                print(f"  investor flow blank for {ticker}; {reason}")

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

    important_cols = ["기관5일", "기관20일", "외국인5일", "외국인20일", "연기금20일", "공매도비중%", "공매도잔고비중%"]
    has_any_value = False
    for col in important_cols:
        if col in new_df.columns and new_df[col].astype(str).str.strip().replace("", pd.NA).notna().any():
            has_any_value = True
            break

    # Keep an existing non-empty good cache if today's collection is totally blank.
    # If the file only has a header/no rows, write diagnostic rows anyway.
    if not has_any_value and existing_cache_has_rows():
        print("Collected data is blank; keeping existing non-empty flow_auto.csv.")
        return 0

    new_df.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"written: {OUT_PATH} ({len(new_df)} rows, has_any_value={has_any_value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
