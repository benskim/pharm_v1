# 단계 실행. 각 단계는 사용자가 지시할 때 구현한다 (CLAUDE.md "한 번에 한 단계").
PY ?= .venv/bin/python

.PHONY: help venv sample s0 check-sample test \
        p1-s1 p1-s2 p1-s3 p1-s4 p1-s5 p1-s6 p1-s7 report \
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
	diff outputs/precheck_sample.md reference/precheck_v3_output.md && echo "check-sample: 차이 없음"

test:
	$(PY) -m pytest -q tests

define TODO
	@echo "$@: 아직 구현하지 않은 단계 (prompts/STAGES.md)"; exit 1
endef

p1-s1 p1-s2 p1-s3 p1-s4 p1-s5 p1-s6 p1-s7:
	$(TODO)

report:
	$(TODO)

p2-t1 p2-t2 p2-t3 p2-t4 p2-t5:
	$(TODO)

all: s0 check-sample test p1-s1 p1-s2 p1-s3 p1-s4 p1-s5 p1-s6 p2-t1 p2-t2 p2-t3 p2-t4
