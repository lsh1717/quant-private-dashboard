# v8.1 Free - 수급 자동 캐시 보강

이번 버전은 GitHub Actions가 KRX/pykrx에서 수급을 못 가져올 때 Naver Finance 투자자별 매매동향을 fallback으로 시도합니다.

우선순위:
1. pykrx/KRX 수급
2. Naver Finance 기관/외국인 수급 fallback
3. 그래도 실패하면 flow_auto.csv에 진단 행 기록

참고: Naver fallback은 기관/외국인 수급 중심이며 연기금/공매도 잔고는 비어 있을 수 있습니다.
