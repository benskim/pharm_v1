# 단계 실행. 각 단계는 사용자가 지시할 때 구현한다 (CLAUDE.md "한 번에 한 단계").
PY ?= .venv/bin/python
export PYTHONPATH := src

.PHONY: help venv sample s0 check-sample test p1-all \
        p1-s1 p1-s2 p1-s3 p1-v4-legacy p1-v5 p1-s4 p1-s4-precision p1-s5 p1-s6 p1-s7 report \
        p2-t1 p2-t2 p2-t3 p2-diag p2-extra p2-min p2-t4 p2-t5 p1-build all p1-holdout-v53 \
        p2v2-select p2v2-power p2v2-ewc-check p2v2-power-ewc p2v2-dryrun p2v2-rho p2v2-prereg-check p2v2-backtest p2v3-group p2v3-regroup p2v3-hybrid p2v3-pooling p2v3-consistency p2v3-exp-smoke p2v3-exp p2v4-smoke p2v4 p2v4-resp

help:
	@echo "make venv | sample | s0 | check-sample | test | p1-s1..p1-s7 | p1-v5 | report MONTH=YYYY-MM | p2-t1..p2-t5 | all"

venv:
	python3.11 -m venv .venv && $(PY) -m pip install --upgrade pip && $(PY) -m pip install -r requirements.txt

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

# P1 S3 (v4 원장, 기본 경로 아님, RULES §11-4): v4 보관본 outputs/v4_legacy/ 만들기와 v4 회귀 시험
p1-v4-legacy:
	$(PY) -I scripts/v4_legacy_export.py $(if $(FORCE),--force,)
	$(PY) -m pytest -q tests/test_s3_synthetic.py tests/test_s3_integration.py tests/test_s2_integration.py

p1-s3: p1-v4-legacy

# P1 원장 v5 (docs/RULES_2026-11-17.md): v5 표 → 독립 점검(rule_check, data/raw만) → 라벨 정답 칸·정밀도 → 개정 전후 비교. 표 outputs/v5/
p1-v5:
	$(PY) -I scripts/v4_legacy_export.py
	$(PY) -m pbd.ledger.v5_build
	$(PY) -I scripts/rule_check.py > /dev/null
	$(PY) -I scripts/v5_labels.py
	$(PY) -I scripts/v5_compare.py > /dev/null
	$(PY) -I scripts/v5_1_checks.py > /dev/null
	$(PY) -m pytest -q tests/test_v5_rules.py tests/test_v5_integration.py

# P1 S4: 분석 A (A1~A6) → outputs/s4/. 표본은 sample_60.new.csv(최종 라벨이 있을 때) 또는 sample_60.generated.csv. 정밀도: `make p1-s4-precision` (sample_60.user_reviewed.csv)
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

# P2 최소판 (주력 묶음 성분 × 전국, M0·M2·M3, 예측 대상 M+1). T1 → T2 → T3 순서
p2-t1:
	$(PY) -m pbd.forecast.t1_panel
	$(PY) -m pytest -q tests/test_p2_t1.py

p2-t2:
	$(PY) -m pytest -q tests/test_p2_t2.py
	$(PY) -m pbd.forecast.t2_baseline

p2-t3:
	$(PY) -m pytest -q tests/test_p2_t3.py tests/test_p2_posthoc.py
	$(PY) -m pbd.forecast.t3_models

# P2 진단 실험 (검토 기록용, 주 모델을 바꾸지 않는다): E0~E6·E1′, 여섯 달 부록, 오라클·시점 이동, 금액 오라클, 오라클 특징 점검
p2-diag:
	$(PY) -m pbd.forecast.t3_models claim_amt
	$(PY) -m pbd.forecast.t3_experiments
	$(PY) -m pbd.forecast.t3_experiments e1_constant
	$(PY) -m pbd.forecast.t3_horizons
	$(PY) -I scripts/p2_diag_oracle_offset.py
	$(PY) -I scripts/p2_diag_oracle_offset.py claim_amt
	$(PY) -I scripts/p2_check_oracle_features.py

# P2 추가 확인 A~D (2026-11-19, 주 결과·결론 문장과 따로): 사후 수정 간접 시험, 1~12개월 앞, 상위 200, 층화 200, 청구금액 가중 WAPE
p2-extra:
	$(PY) -m pbd.forecast.extra_revision
	$(PY) -m pbd.forecast.extra_weight
	$(PY) -m pbd.forecast.extra_horizon
	$(PY) -m pbd.forecast.extra_top200 top200
	$(PY) -m pbd.forecast.extra_top200 strat200
	$(PY) -m pbd.forecast.extra_horizon top200
	$(PY) -m pbd.forecast.extra_signif
	$(PY) -m pbd.forecast.extra_window
	$(PY) -m pbd.forecast.extra_recent
	$(PY) -m pbd.forecast.extra_2023h1

# v5.3 새 검증 표본 라벨 화면 (한 번만. 정답이 채워지면 덮어쓰지 않는다). 채점은 라벨을 마친 뒤 scripts/holdout_v53_score.py --run
p1-holdout-v53:
	$(PY) -I scripts/holdout_v53.py

# P2 T5 문서 (결과 보고서 → 2쪽 요약·README·PLAN §8 → P1 요약의 P2 행)
p2-t5:
	$(PY) -I scripts/p1_disposition.py
	$(PY) -I scripts/p2_min_report.py
	$(PY) -I scripts/p2_docs.py
	$(PY) -I scripts/p1_docs.py
	$(PY) -I scripts/overview.py

p2-min: p2-t1 p2-t2 p2-t3 p2-diag p2-extra p2-t5

p2-t4:
	$(TODO)

# P1 전체. P2 단계는 구현한 뒤 all에 넣는다 (사용자 결정 2026-11-16)
p1-all: s0 check-sample p1-s1 p1-s2 p1-v5 p1-s4 p1-s5 p1-s6 test

# 전체 (2026-11-19): P1 단계 → P2 최소판(진단·추가 확인·문서 포함) → 전체 시험
p1-build: s0 check-sample p1-s1 p1-s2 p1-v5 p1-s4 p1-s5 p1-s6

all: p1-build p2-min test

# P2 v2 사전등록 전 단계 (docs/PLAN_P2_v2.md §7-2). 사용통계는 2022-12 이하만 읽고 예측 오차(평가 기간)는 계산하지 않는다
p2v2-select:
	$(PY) -m pbd.forecast_v2.select
	$(PY) -m pytest -q tests/test_p2v2_select.py

p2v2-power: p2v2-select
	$(PY) -m pbd.forecast_v2.power
	$(PY) -m pytest -q tests/test_p2v2_dm.py

# EWC 주 검정 후보 검증 V1 ~ V6 (DECISIONS 2026-11-20). 3단계 산출(power_pre_series.csv)만 읽는다
p2v2-ewc-check:
	$(PY) -m pytest -q tests/test_p2v2_ewc.py
	$(PY) -I scripts/p2v2_ewc_check.py

# 주 검정(EWC) 기준 사전 검출력 → outputs/p2v2/power_ewc.csv (DM 기준 power.csv는 보존, 2026-11-20)
p2v2-power-ewc:
	$(PY) -m pbd.forecast_v2.power --ewc

# 합성 자료 전 과정 시험 (dry run, PLAN §7-2). 실제 자료를 읽지 않는다 → outputs/p2v2/dryrun/
p2v2-dryrun:
	$(PY) -m pbd.forecast_v2.dryrun
	$(PY) -I scripts/p2v2_verdict.py outputs/p2v2/dryrun

# ρ_M 캐시 (origin M마다 build_asof(M) 원장의 약가 예고 정시 실현 비율). 사용통계는 읽지 않는다 → outputs/p2v2/rho_asof.csv
p2v2-rho:
	$(PY) -m pbd.forecast_v2.rho

# 사전등록 잠금 상태·해시 보고 (사용통계를 읽지 않는다) → outputs/p2v2/prereg_check.json
p2v2-prereg-check:
	$(PY) -m pbd.forecast_v2.real --check

# 실제 자료 백테스트 (태그 p2v2-prereg-r1과 잠금 파일이 같을 때만. 태그가 없으면 거부)
p2v2-backtest:
	$(PY) -m pbd.forecast_v2.real

# P2 v3 (P2 첨부 별도 분석, 사후 탐색): 약효분류 2자리·ATC 2단계 분류군별 오차와 사용 패턴 → outputs/p2v3/ (docs/PLAN_P2_v3.md)
p2v3-group:
	$(PY) -m pytest -q tests/test_p2v3_group.py
	$(PY) -m pbd.forecast_v3.run
	$(PY) -m pbd.forecast_v3.report

# P2 v3 다시 묶기 (사후 탐색): ATC 1단계·패턴 군집으로 묶으면 군간 차이가 커지는가 → outputs/p2v3/regroup/ (PLAN_P2_v3 §9)
p2v3-regroup: p2v3-group
	$(PY) -m pbd.forecast_v3.regroup

# P2 v3 혼합 묶음 (사후 탐색): ATC 2단계 군을 ATC 1단계 안에서만 골라 합치는 방식 비교 → outputs/p2v3/hybrid/ (PLAN_P2_v3 §10)
p2v3-hybrid: p2v3-regroup
	$(PY) -W ignore -m pbd.forecast_v3.hybrid

# P2 v3 부분 풀링 (사후 탐색): 딱 잘라 나눈 묶음과 '한 성분 빼고 맞히기'로 비교 → outputs/p2v3/pooling/ (PLAN_P2_v3 §11)
p2v3-pooling: p2v3-hybrid
	$(PY) -W ignore -m pbd.forecast_v3.pooling

# P2 v3 모델 우위 시점 일관성 (사후 탐색): 따로 모델을 둘 만한 군이 있는가 → outputs/p2v3/consistency/ (PLAN_P2_v3 §12)
p2v3-consistency: p2v3-group
	$(PY) -W ignore -m pbd.forecast_v3.consistency

# P2 v3 실험 (PLAN_P2_v3 §13, 사전등록 r2). smoke = 잠금 전 동작 확인(2022, ATC 1단계 P·R, B3 캐시 확인, 오차 숫자 출력 안 함)
p2v3-exp-smoke:
	$(PY) -m pytest -q tests/test_p2v3_exp.py
	$(PY) -W ignore -m pbd.forecast_v3.exp --smoke

# 본 실행: 태그 p2v3-prereg-r2와 잠금 파일·B3 캐시 해시가 같을 때만 → outputs/p2v3/exp/, reports/P2v3_exp_result.md
p2v3-exp:
	$(PY) -m pytest -q tests/test_p2v3_exp.py
	$(PY) -W ignore -m pbd.forecast_v3.exp

# P2 v4 (docs/PLAN_P2_v4.md). smoke = 잠금 전 동작 확인(2022, ATC 1단계 P·R, 오차 숫자 출력 안 함), p2v4 = 태그 p2v4-prereg-r1 뒤에만
p2v4-smoke:
	$(PY) -m pytest -q tests/test_p2v4.py
	$(PY) -W ignore -m pbd.forecast_v4.run --smoke

p2v4:
	$(PY) -m pytest -q tests/test_p2v4.py
	$(PY) -W ignore -m pbd.forecast_v4.run

# P2 v4 호흡기 성분 성능 저하 분석 (사후 탐색, P2 v4 결과·캐시를 읽기만 함) → outputs/p2v4_resp/
p2v4-resp:
	$(PY) -m pytest -q tests/test_p2v4_resp.py
	$(PY) -W ignore scripts/p2v4_resp_analysis.py

# 사건(약가인하·경쟁제품 추가·대체조제 목록 추가) 전후 성분 처방수량 변화 → outputs/event_qty/
event-qty:
	$(PY) -m pytest -q tests/test_event_qty.py
	$(PY) -W ignore scripts/event_qty_analysis.py
