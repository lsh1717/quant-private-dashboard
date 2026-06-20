# 개인 투자 대시보드 MVP

네가 말한 투자 기준을 바탕으로 만든 개인용 대시보드 초안입니다.

핵심 목적은 `무조건 매수 추천`이 아니라, 관심 종목을 자동으로 정리해서 다음 상태로 나누는 것입니다.

- 진입가능
- 진입대기
- 관심
- 관찰
- 추격금지
- 손절위험
- 데이터없음

> 주의: 투자 판단 보조 도구입니다. 실제 주문 전 가격, 공시, 뉴스, 수급, 재무 데이터는 반드시 원자료로 재확인해야 합니다.

---

## 1. 들어있는 기능

### 종목 점수화

`data/watchlist.csv`에 있는 종목을 기준으로 아래 점수를 계산합니다.

1. 구조점수
   - 내러티브
   - 정책/CAPEX 연결
   - 병목/공급제한
   - 지속 매수 주체
   - 시장 반영 정도

2. 차트점수
   - 20일선/60일선 회복 여부
   - 20일 신고가 돌파 여부
   - 거래량 증가 여부
   - RSI
   - 단기 과열 여부

3. 뉴스점수
   - Google News RSS 기반 관련 기사 수
   - 섹터별 키워드 출현 빈도

### 자동 매매 계획 문장 생성

종목마다 아래 항목을 자동으로 표시합니다.

- 진입 조건
- 손절 기준
- 매도 기준
- 경고 문구

### 개인 페이지 보호

`.env` 또는 Streamlit secrets에 `DASHBOARD_PASSWORD`를 넣으면 비밀번호 입력 후 접속하게 됩니다.

### 텔레그램 알림

`alert_worker.py`를 실행하면 `진입가능` 또는 `손절위험` 상태인 종목을 텔레그램으로 보낼 수 있습니다.

---

## 2. 설치 방법

터미널 또는 PowerShell에서 프로젝트 폴더로 이동한 뒤 실행합니다.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

패키지 설치:

```bash
pip install -r requirements.txt
```

대시보드 실행:

```bash
streamlit run app.py
```

브라우저에서 보통 아래 주소가 열립니다.

```text
http://localhost:8501
```

---

## 3. 관심종목 수정 방법

`data/watchlist.csv` 파일을 열어서 종목을 추가/수정하면 됩니다.

필수 컬럼:

```text
ticker,name,market,sector,theme,manual_narrative,manual_policy,manual_bottleneck,manual_smart_money,manual_reflection,notes
```

예시:

```text
000660.KS,SK하이닉스,KR,반도체,HBM/AI 메모리,92,72,88,78,64,HBM 수주·DRAM 가격·컨센서스 추적
```

국내 종목은 yfinance 기준으로 `.KS` 또는 `.KQ`를 붙여야 합니다.

예시:

- 삼성전자: `005930.KS`
- SK하이닉스: `000660.KS`
- 에코프로비엠: `247540.KQ`

---

## 4. 섹터 키워드 수정 방법

`config/keywords.yaml`에서 섹터별 뉴스 키워드를 수정합니다.

예시:

```yaml
반도체:
  - HBM
  - DRAM
  - AI 반도체
  - 메모리
  - 데이터센터
```

---

## 5. 비밀번호 설정

`.env.example` 파일을 복사해서 `.env`로 바꾼 뒤 아래처럼 입력합니다.

```text
DASHBOARD_PASSWORD=원하는비밀번호
```

이후 다시 실행하면 비밀번호 입력 화면이 뜹니다.

---

## 6. 텔레그램 알림 설정

`.env`에 아래 값을 넣습니다.

```text
TELEGRAM_BOT_TOKEN=텔레그램봇토큰
TELEGRAM_CHAT_ID=내채팅ID
```

알림 실행:

```bash
python alert_worker.py
```

조건에 맞는 종목이 있으면 텔레그램으로 전송됩니다.

Windows 작업 스케줄러에 `python alert_worker.py`를 15분~1시간 간격으로 등록하면 자동 알림처럼 쓸 수 있습니다.

---

## 7. 점수 기준

현재 MVP의 종합점수 계산은 아래 비중입니다.

```text
종합점수 = 구조점수 50% + 차트점수 35% + 뉴스점수 15%
```

이유:

- 네 투자 기준에서는 내러티브/구조가 1순위
- 차트는 진입 타이밍 확인용
- 뉴스는 트리거지만, 뉴스만으로 추격 매수하면 위험

---

## 8. 다음에 붙이면 좋은 기능

1. 기관/외국인 수급 데이터
2. 공매도 잔고/대차잔고
3. 실적 컨센서스 변화
4. 공시 자동 요약
5. 섹터별 주도주 순환 감지
6. 매매일지 자동 저장
7. 보유 종목 손절선/익절선 관리
8. 배포용 로그인 강화

---

## 9. 파일 구조

```text
quant_private_dashboard/
├─ app.py
├─ alert_worker.py
├─ requirements.txt
├─ README.md
├─ .env.example
├─ .streamlit/
│  └─ secrets.toml.example
├─ data/
│  └─ watchlist.csv
├─ config/
│  └─ keywords.yaml
└─ src/
   ├─ data_sources.py
   ├─ indicators.py
   └─ scoring.py
```

## 행동 신호 기준

무료 클라우드 환경에서는 앱이 잠자거나 데이터가 늦게 들어올 수 있습니다. 그래서 알림에만 의존하지 않고, 화면에 미리 조건을 표시합니다.

- **1차 매수 가능**: 구조점수 높음 + 20일 고점 돌파 + 거래량 증가
- **진입대기**: 추세는 양호하지만 돌파/거래량/눌림 지지 중 확인이 더 필요한 상태
- **신규매수 금지·분할매도 검토**: 과열 구간, 신규 추격 금지
- **분할매도 우선**: 극단 과열 또는 전고점 돌파 실패 가능성
- **손절/비중축소**: 20일선과 60일선 동시 이탈
- **전량매도/손절 우선**: 20일 최저가 이탈 등 핵심 지지선 붕괴

종목별 상세 화면에서 다음 항목을 확인할 수 있습니다.

- 미보유자 매수 조건
- 보유자 분할매도 조건
- 보유자 전량매도 조건
- 손절가 / 강제손절가
- 알림 우선순위와 알림 이유
