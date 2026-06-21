# 개인 투자 대시보드 v8 Free

무료 자동수급 구조를 추가한 버전입니다.

## 핵심 구조

이 버전은 Railway/Supabase 없이 무료로 시작합니다.

```text
GitHub Actions
→ 평일 장마감 후 collector/update_flow_cache.py 실행
→ data/flow_auto.csv 자동 생성/갱신
→ Streamlit 대시보드가 flow_auto.csv 자동 인식
```

수급 데이터 우선순위는 다음과 같습니다.

```text
1순위: data/flow_auto.csv 자동 수급 캐시
2순위: 사이드바에서 직접 업로드한 수급 CSV
3순위: 앱 내부 KRX 직접 조회
4순위: 데이터없음
```

## 새로 추가된 파일

```text
collector/update_flow_cache.py
.github/workflows/update-flow-cache.yml
data/flow_auto.csv
```

## 적용 방법

1. ZIP 압축을 풉니다.
2. GitHub 저장소에 압축 푼 안쪽 파일 전체를 업로드합니다.
3. Commit changes를 누릅니다.
4. Streamlit 앱에서 Reboot app을 누릅니다.
5. GitHub 저장소의 Actions 탭으로 이동합니다.
6. `Update KRX Flow Cache` 워크플로를 선택합니다.
7. `Run workflow`를 눌러 수동 실행 테스트를 합니다.
8. 실행 성공 후 `data/flow_auto.csv`가 채워졌는지 확인합니다.

## 자동 실행 시간

기본값은 평일 한국시간 오후 7시 30분입니다.

```yaml
- cron: "30 10 * * 1-5"
```

GitHub Actions의 cron은 UTC 기준입니다. 한국시간 19:30은 UTC 10:30입니다.

## 주의

KRX/pykrx 응답 자체가 빈 값이면 GitHub Actions에서도 수집이 실패할 수 있습니다. 다만 이 방식은 Streamlit 앱 안에서 직접 조회하는 것보다 안정적이고, 실패해도 GitHub Actions 로그에서 원인을 확인할 수 있습니다.

`data/flow_auto.csv`가 빈 상태이면 대시보드에서는 자동 수급 캐시 없음 또는 데이터없음으로 보입니다. 이때는 Actions 로그를 확인하거나 기존처럼 수급 CSV를 직접 업로드하면 됩니다.
