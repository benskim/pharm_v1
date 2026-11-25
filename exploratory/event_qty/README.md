# 탐색 분석 (보류): 사건 전후 성분 처방수량

- **상태**: 2026-11-25 사용자 결정으로 보류. 핵심 분석 경로(`make`, `tests/`, `reports/`) 밖에 둔다. 원래 판은 커밋 `eb3f554`에 있다.
- **이유**: 알고 싶은 것은 성분 안의 이동인데 자료는 성분 총량뿐이다.
- **읽는 법**: 총량 추정 결과를 "영향 없음"이나 "이동 없음"의 근거로 쓰지 않는다.
- **재개 조건**: 제품별 월 청구량이 있을 때 제품 점유율 기준으로 새로 설계한다.
- **코드 확인(기록용)**: `python -m pytest exploratory/event_qty/check_event_qty.py`, `python exploratory/event_qty/analysis.py`
