# 단계 실행. 각 단계는 사용자가 지시할 때 구현한다 (CLAUDE.md "한 번에 한 단계").
PY ?= .venv/bin/python
export PYTHONPATH := src

.PHONY: help venv sample s0 check-sample test \
        p1-s1 p1-s2 p1-s3 p1-s4 p1-s4-precision p1-s5 p1-s6 p1-s7 report \
        p2-t1 p2-t2 p2-t3 p2-t4 p2-t5 all

help:
	@echo "make venv | sample | s0 | check-sample | test | p1-s1..p1-s7 | report MONTH=YYYY-MM | p2-t1..p2-t5 | all"

venv:
	python3.11 -m venv .venv && $(PY) -m pip install -r requirements.txt

# 회귀 시험용 표본 (data/raw → data/sample)
sample:
	$(PY) scripts/build_sample.py

# S0 자료 점검: 결과 표는 outputs/s0/
s0:
	$(PY) -I scripts/s0_inspect.py

# S0 추가 확인 3건 (약효분류 후보, 종료일 없는 정지, 생동대조약)
s0-followup:
	$(PY) -I scripts/s0_followup.py

# 표본으로 돌린 precheck 출력 = reference 출력
check-sample:
	$(PY) -I reference/precheck_v3.py data/sample > outputs/precheck_sample.md
	diff outputs/precheck_sample.md reference/precheck_v3_output.md && echo "check-sample: precheck 차이 없음"
	@if [ -f data/interim/pbd.duckdb ]; then $(PY) -m pytest -q tests/test_s1_integration.py -k "sample or f2"; fi

test:
	$(PY) -m pytest -q tests

define TODO
	@echo "$@: 아직 구현하지 않은 단계 (prompts/STAGES.md)"; exit 1
endef

# P1 S1: L1 정규화 테이블 → data/interim/pbd.duckdb
p1-s1:
	$(PY) -m pbd.ledger.s1_load
	$(PY) -m pytest -q tests/test_notes.py tests/test_s1_load.py tests/test_s1_integration.py

# P1 S2: 상태·속성·연결 → product_state_month 등 (data/interim/pbd.duckdb), 보고용 표 outputs/s2/
p1-s2:
	$(PY) -m pbd.ledger.s2_state
	$(PY) -m pytest -q tests/test_state_rules.py tests/test_s2_synthetic.py tests/test_s2_integration.py

# P1 S3: 신호·이벤트 원장 → signal_ledger, signal_outcome, event_ledger (data/interim/pbd.duckdb), 표 outputs/s3/
p1-s3:
	$(PY) -m pbd.ledger.s3_ledger
	$(PY) -m pytest -q tests/test_s3_synthetic.py tests/test_s3_integration.py

# P1 S4: 분석 A (A1~A6) → outputs/s4/, outputs/validation/sample_60.csv. 라벨을 채운 뒤 `make p1-s4-precision`
p1-s4:
	$(PY) -m pbd.ledger.s4_analysis
	$(PY) -m pytest -q tests/test_s4.py

p1-s4-precision:
	$(PY) -I scripts/s4_precision.py

# P1 S5: 분석 B·C (B1 전가율, B2 진입 군집, C 대체조제 그룹) → outputs/s5/
p1-s5:
	$(PY) -m pbd.ledger.s5_market
	$(PY) -m pytest -q tests/test_s5.py

# P1 S6: 월간 리포트 예시 2개 (2024-09, 2025-11) → outputs/reports/, outputs/dashboard/, 성분 묶음 검토표 outputs/s6/
p1-s6:
	$(PY) -m pbd.ledger.s6_report 2024-09
	$(PY) -m pbd.ledger.s6_report 2025-11
	$(PY) -m pbd.ledger.s6_report --groups 2025-12
	$(PY) -m pytest -q tests/test_s6.py

p1-s7:
	$(TODO)

# 한 달 리포트: make report MONTH=YYYY-MM (MONTH까지의 스냅샷만 쓴다)
report:
	@test -n "$(MONTH)" || (echo "사용: make report MONTH=YYYY-MM"; exit 1)
	$(PY) -m pbd.ledger.s6_report $(MONTH)

p2-t1 p2-t2 p2-t3 p2-t4 p2-t5:
	$(TODO)

all: s0 check-sample test p1-s1 p1-s2 p1-s3 p1-s4 p1-s5 p1-s6 p2-t1 p2-t2 p2-t3 p2-t4
