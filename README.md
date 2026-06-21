# 개인 투자 대시보드 MVP v6.5

네 투자 기준을 기반으로 종목을 점수화하고, 가격·거래량·RSI·이동평균·피보나치·뉴스·수급 CSV를 반영해 후보를 정리하는 개인용 Streamlit 대시보드입니다.

## v6.5 핵심 변경

- 종목별 `전략타입` 추가
  - 코어보유형
  - 코어+스윙형
  - 추세스윙형
  - 트레이딩형
  - 관찰형
- 코어비중/트레이딩비중 추가
- 같은 신호라도 전략타입별로 다르게 해석
  - 코어보유형의 `분할매도 우선`은 전량매도가 아니라 일부익절/코어유지
  - 트레이딩형의 `손절위험`은 빠른 정리 우선
- 종목별 매매 계획에 `전략타입별 실전 해석` 추가
- `watchlist.csv`에 Sandisk Corporation, Kioxia Holdings Corporation 예시 추가
- v6.4의 코어보유형 백테스트 기능 유지

## 배포

Streamlit Cloud에서 다음 설정으로 배포합니다.

```text
Repository: lsh1717/quant-private-dashboard
Branch: main
Main file path: app.py
Python: 3.12
```

Secrets 예시:

```toml
DASHBOARD_PASSWORD = "원하는비밀번호"
```

## watchlist.csv 컬럼

```csv
ticker,name,market,sector,theme,manual_narrative,manual_policy,manual_bottleneck,manual_smart_money,manual_reflection,strategy_type,core_ratio,trading_ratio,notes
```

`strategy_type`은 비워도 앱이 자동 추정하지만, 직접 넣는 것이 더 정확합니다.

## 주의

이 대시보드는 투자 판단 보조용입니다. 데이터 지연, 오류, 무료 데이터 제한이 있을 수 있으므로 실제 주문 전 증권사/거래소 원자료를 반드시 확인하세요.
