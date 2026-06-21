# NEXT STEPS - v8 Free

## 바로 할 일

1. GitHub에 v8 파일 전체 업로드
2. Streamlit 앱 Reboot
3. GitHub Actions 탭에서 `Update KRX Flow Cache` 수동 실행
4. 실행 후 `data/flow_auto.csv` 내용 확인
5. 대시보드 사이드바에서 `자동 수급 캐시 N개 티커 인식` 문구 확인

## 실패했을 때 확인

- Actions 로그에서 pykrx 설치 실패인지 확인
- KRX 조회 결과가 빈 값인지 확인
- `data/flow_auto.csv`가 실제로 commit 됐는지 확인
- 그래도 안 되면 기존 수급 CSV 직접 업로드 방식 사용

## 다음 발전 방향

- Actions 실행 결과를 텔레그램으로 알림
- 수급 캐시 성공/실패 상태를 대시보드 상단에 표시
- 종목별 수급 변화 추세 차트 추가
- 백테스트 결과를 종목별 전략타입 추천에 자동 연결
