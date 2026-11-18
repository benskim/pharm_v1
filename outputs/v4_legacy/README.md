# v4 원장 보관본 (읽기 전용)

RULES_2026-11-17(v5) 개정 전 원장이다 (사용자 지시 2026-11-18, RULES §11-4). 파이프라인 기본 경로(`make p1-all`)는 이 파일을 만들거나 읽지 않는다.
읽는 곳: 개정 전후 비교(`scripts/v5_compare.py`), 라벨의 개정 전 주장(`scripts/v5_labels.py`), v4 회귀 시험(`tests/test_s2_integration.py`,
`tests/test_s3_integration.py`, `tests/test_s5.py`의 reference F8 분포).

- `v4_legacy.duckdb`: product_state_month, s2_state_override, s2_suspend_signal_check, s3_cal, s3_entry_exit, s3_entry_exit_sample, signal_ledger, signal_outcome, event_ledger, s3_compare_by_year
- `s3/`, `s4/`, `s5/`: v4 원장으로 만든 산출 CSV (2026-11-18 이전 실행)
- 다시 만들기: `make p1-v4-legacy` (`--force`). 파일 권한은 읽기 전용(444)이다.
