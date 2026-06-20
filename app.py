from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src.data_sources import fetch_price_history, load_keywords, load_watchlist, news_metrics
from src.indicators import add_indicators, latest_snapshot
from src.scoring import build_trade_plan, manual_structure_score, news_score, technical_score

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


def format_price(x: float) -> str:
    try:
        if pd.isna(x):
            return "-"
        return f"{x:,.0f}"
    except Exception:
        return "-"


def make_chart(df: pd.DataFrame, title: str):
    data = add_indicators(df)
    if data.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=data.index, open=data["open"], high=data["high"], low=data["low"], close=data["close"], name="가격"))
    fig.add_trace(go.Scatter(x=data.index, y=data["ma20"], mode="lines", name="20일선"))
    fig.add_trace(go.Scatter(x=data.index, y=data["ma60"], mode="lines", name="60일선"))
    fig.update_layout(title=title, height=430, margin=dict(l=10, r=10, t=45, b=10), xaxis_rangeslider_visible=False)
    return fig


st.title("개인 투자 대시보드")
st.caption("네 기준: 내러티브 → 정책/CAPEX → 병목/공급제한 → 지속 매수 주체 → 아직 덜 반영된 구간 → 차트 확인")

with st.expander("이 대시보드가 판단하는 행동 기준", expanded=False):
    st.markdown(
        """
- **1차 매수 가능**: 구조점수 높음 + 20일 고점 돌파 + 거래량 증가. 단, 종가 확인 후 분할 진입.
- **진입대기**: 추세는 괜찮지만 돌파/거래량/눌림 지지 중 하나가 아직 부족한 상태.
- **신규매수 금지·분할매도 검토**: RSI 과열 또는 20일 수익률 급등. 새로 따라 들어가는 구간이 아니라 보유분 관리 구간.
- **분할매도 우선**: 극단 과열, 거래량 폭증, 전고점 돌파 실패 가능성이 큰 상태.
- **손절/비중축소**: 20일선과 60일선을 동시에 이탈해 진입 근거가 약해진 상태.
- **전량매도/손절 우선**: 20일 최저가 이탈 등 핵심 지지선 붕괴. 알림이 늦을 수 있으므로 가격 조건을 미리 정해두고 대응.
        """
    )

with st.sidebar:
    st.header("설정")
    refresh_min = st.selectbox("자동 새로고침", ["끄기", "5분", "15분", "30분", "60분"], index=2)
    period = st.selectbox("가격 조회 기간", ["6mo", "9mo", "1y", "2y"], index=1)
    interval = st.selectbox("봉 기준", ["1d", "1wk"], index=0)
    use_news = st.toggle("뉴스/RSS 점수 사용", value=True)
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

sector_filter = st.sidebar.multiselect("섹터 필터", sorted(watchlist["sector"].unique().tolist()), default=[])
status_filter = st.sidebar.multiselect("상태 필터", ["진입가능", "진입대기", "관심", "관찰", "추격금지", "손절위험", "데이터없음"], default=[])
action_filter = st.sidebar.multiselect(
    "행동 신호 필터",
    ["1차 매수 가능", "진입대기", "관심·반등 확인", "신규매수 금지·분할매도 검토", "분할매도 우선", "손절/비중축소", "전량매도/손절 우선", "관찰", "데이터 확인"],
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
    plan = build_trade_plan(row, snap, structure, tech, ns)

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
            "RSI": round(snap.get("rsi14", 0), 1) if snap else None,
            "거래량배율": round(snap.get("volume_ratio", 0), 2) if snap else None,
            "20일수익률%": round(snap.get("ret_20d", 0), 1) if snap else None,
            "구조점수": round(structure, 1),
            "차트점수": round(tech, 1),
            "뉴스점수": round(ns, 1),
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
col1.metric("매수 후보", int((result["행동신호"] == "1차 매수 가능").sum()))
col2.metric("진입대기", int((result["행동신호"] == "진입대기").sum()))
col3.metric("분할매도/추격금지", int(result["행동신호"].isin(["신규매수 금지·분할매도 검토", "분할매도 우선"]).sum()))
col4.metric("손절/전량매도", int(result["행동신호"].isin(["손절/비중축소", "전량매도/손절 우선"]).sum()))

urgent = result[result["알림우선순위"].isin(["긴급", "높음"])].copy()
if not urgent.empty:
    st.subheader("우선 확인 신호")
    st.dataframe(
        urgent[["알림우선순위", "행동신호", "종목", "현재가", "손절가", "강제손절가", "알림이유", "경고"]]
        .style.format({"현재가": "{:,.0f}", "손절가": "{:,.0f}", "강제손절가": "{:,.0f}"}),
        use_container_width=True,
        hide_index=True,
    )

st.subheader("오늘의 후보")
view_cols = ["행동신호", "알림우선순위", "상태", "종합점수", "종목", "티커", "섹터", "테마", "현재가", "손절가", "강제손절가", "RSI", "거래량배율", "20일수익률%", "구조점수", "차트점수", "뉴스점수", "경고"]
st.dataframe(
    result[view_cols].style.format({"현재가": "{:,.0f}", "손절가": "{:,.0f}", "강제손절가": "{:,.0f}", "종합점수": "{:.1f}"}),
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

    a, b, c, d = st.columns(4)
    a.metric("행동 신호", selected["행동신호"])
    b.metric("종합점수", f"{selected['종합점수']:.1f}")
    c.metric("현재가", format_price(selected["현재가"]))
    d.metric("손절가", format_price(selected["손절가"]))

    st.markdown(f"""
### {selected['종목']} · {selected['섹터']} · {selected['테마']}

**미보유자 매수 조건**  
{selected['매수조건']}

**보유자 분할매도 조건**  
{selected['분할매도조건']}

**보유자 전량매도 조건**  
{selected['전량매도조건']}

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

st.divider()
st.caption(
    f"마지막 계산: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
    "이 대시보드는 투자 판단 보조용입니다. 데이터 지연/오류 가능성이 있으므로 실제 주문 전 원자료를 반드시 확인하세요."
)
