# 개인 투자 대시보드 v9 Flow

네 투자 기준을 기반으로 종목을 점수화하고, 가격·거래량·RSI·이동평균·피보나치·뉴스·기관/외국인 수급을 반영해 **오늘 무엇을 해야 하는지** 정리하는 개인용 Streamlit 대시보드입니다.

## v9 핵심 변경

- 기관/외국인 수급을 단순 점수뿐 아니라 **변화 방향**으로 해석
  - 쌍끌이 매수
  - 쌍끌이 매도
  - 수급개선
  - 수급악화
  - 기관 재진입
  - 외국인 재진입
  - 기관/외국인 단기이탈
- 후보를 더 단순하게 자동 분류
  - A급 매수 후보
  - B급 매수 후보
  - 수급선행 후보
  - 수급 없는 반등
  - 보유관리
  - 추격금지
  - 위험관리
- 오늘 할 일 화면 강화
  - A급 후보 탭
  - 신규매수/추가 후보 탭
  - 수급선행/수급주의 탭
  - 보유관리 탭
  - 위험·대기 탭
- 종목별 매매 계획에 바로 쓸 수 있는 가격 계획 추가
  - 매수구간
  - 손절계획
  - 분할매도구간
- GitHub Actions 무료 자동수급 구조 유지
  - `collector/update_flow_cache.py`
  - `.github/workflows/update-flow-cache.yml`
  - `data/flow_auto.csv`

## 무료 수급 자동화 구조

```text
GitHub Actions
→ 평일 한국시간 장마감 후 자동 실행
→ pykrx/KRX 수급 조회 시도
→ 실패하면 Naver Finance 기관/외국인 수급 fallback
→ data/flow_auto.csv 자동 저장
→ Streamlit 대시보드가 flow_auto.csv 읽기
```

현재 무료 구조에서는 기관/외국인 수급은 자동화 가능성이 높고, 연기금/공매도는 환경에 따라 비어 있을 수 있습니다.

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

## GitHub Actions 수급 실행

파일 업로드 후 GitHub에서:

```text
Actions → Update KRX Flow Cache → Run workflow
```

실행 후 `data/flow_auto.csv`에 종목 행이 생기면 성공입니다. 이후 Streamlit에서 `Manage app → Reboot app`을 하면 대시보드에 반영됩니다.

## watchlist.csv 컬럼

```csv
ticker,name,market,sector,theme,manual_narrative,manual_policy,manual_bottleneck,manual_smart_money,manual_reflection,strategy_type,core_ratio,trading_ratio,notes
```

`strategy_type`은 비워도 앱이 자동 추정하지만, 직접 넣는 것이 더 정확합니다.

## 주의

이 대시보드는 투자 판단 보조용입니다. 데이터 지연, 오류, 무료 데이터 제한이 있을 수 있으므로 실제 주문 전 증권사/거래소 원자료를 반드시 확인하세요.
