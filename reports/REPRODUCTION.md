# 재현 방법과 기록

## 방법

1. 저장소를 새 폴더에 내려받는다: `git clone <저장소> pharm_v1 && cd pharm_v1 && git checkout v1.0`
2. 새 가상환경: `make venv` (Python 3.11, `requirements.txt` 설치)
3. 데이터 배치: 원본은 저장소에 없다. [docs/DATA.md](../docs/DATA.md) "원본 파일 목록"의 출처에서 받아 `data/raw/`에 같은 이름으로 둔다. 표의 크기·SHA-256으로 대조한다: `python -I scripts/data_manifest.py --check`
4. 전체 실행: `make all` (적재 → 표본 회귀 → 변화 기록표 → 분석 → 리포트 → 처방 수요 예측과 진단 → 문서 → 전체 시험)
5. 시험만 다시: `make test`
6. 숫자 대조: `python -I scripts/reproduce_check.py <원래 폴더> <재현 폴더>` — 주요 숫자(처분 예고 비율, 검증 표본 정밀도, 예측 주 결과·추가 확인)가 같은지 본다

## 기록

<!-- 재현 기록 시작 (재현 실행 뒤 채움) -->
아직 실행하지 않았다.
<!-- 재현 기록 끝 -->
