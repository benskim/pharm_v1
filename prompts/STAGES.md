# 단계별 지시문 (Claude Code에 그대로 붙여 넣는다)

단계마다 새 세션에서 시작해도 된다. `reports/<단계>_result.md`의 "확인 필요"에 답한 뒤 다음 단계로 간다.

순서: **S0** → P1 S1 → S2 → S3 → (P2 T1·T2는 S1 이후 병행 가능) → S4 → P2 T3 → T4 → P1 S6 → S5 → S7·T5

---

## S0 자료 점검 (P1·P2 공통)
```
CLAUDE.md, docs/DATA.md, docs/PLAN_P1.md, docs/PLAN_P2.md, docs/NOTE_RULES.md, docs/DECISIONS.md를 읽고 S0만 수행해.
구현은 하지 말고 점검만 한다.
1) docs/DATA.md §5의 6개 항목을 점검한다.
2) python reference/precheck_v3.py data/sample 출력을 reference/precheck_v3_output.md와 한 줄씩 비교한다.
3) 2022-01~2025-12 급여목록·대체조제 목록의 비고 문형 빈도표(숫자를 9로 치환, 연도별)를 만들고,
   NOTE_RULES.md의 R1~R7·P1~P3으로 설명되지 않는 문형을 예시 5개씩 나열한다. 규칙을 새로 만들지 말고 나열만 한다.
   미래 날짜 비고(기준일 이후 날짜)를 유형별로 센다.
4) 사용통계 원본을 가공본(data/sample/fact_cmpn_stat_*)과 같은 단위로 재집계해 2023~2025를 대조한다.
5) P2용: 성분별 관측 연속성 표, 급여목록 '분류'(약효분류번호) 빈도표 상위 50개.
6) Makefile 골격(s0, p1-s1…, p2-t1…, check-sample, all), config/schema_map.yaml 초안, requirements.txt 확인.
7) reports/S0_result.md를 쓰고 멈춘다. "확인 필요"에 내가 답할 질문을 번호로 적는다.
```

---

## P1

### S1 적재·비고 파서
```
reports/S0_result.md와 내가 단 답을 읽고 P1 S1만 수행해. 설계는 docs/PLAN_P1.md §4, 완료 기준은 §6의 S1.
- L1 테이블: snapshot_inventory, stg_benefit, stg_substitute, stg_usage(원본 단위, observed 포함), note_token(is_future 포함), attr_2026.
- 테스트를 먼저 쓴다. 기대값은 docs/NOTE_RULES.md의 문형 예시와 reference 출력에서 가져오고 구현 출력으로 만들지 않는다.
- is_future는 스냅샷의 실제 기준일과 비교한다 (2025-03-04 같은 달 포함 테스트). 기간 명시 정지(R3b)는 시작일·종료일을 각각 판정한다.
- S0에서 정한 변형 문형 테스트: U1→R4, U4·U5(괄호)→R2, U6→R5, U9(신설)→R1, U2·U3·U8→R3b, U7→R9.
- make check-sample로 표본 구간 숫자를 reference와 비교한다.
```

### S2 상태·속성·연결
```
P1 S2만 수행해. product_state_month, product_attr_scd2, bridge_product_item, bridge_code_recode, 오렌지북 연결과 product_role(reference / generic / NULL, role_source).
- 상태 판정은 docs/PLAN_P1.md §4의 state 표.
- 합성 스냅샷 테스트: 등재 예정(처음 관측 / 기존 제품), 월중 급여정지, 정지 중 삭제, 집행정지 중 가격 변화, 산정불가의 두 위치, 기간 명시 정지(종료일 뒤 비고가 남아도 복귀), 종료일 없는 정지의 복귀(1개월 공백 후 재등장 = 이벤트 없음 / 두 목록 모두 2개월 연속 공백 = 복귀, 첫 달 고쳐 쓰기 / 분석 끝 1개월 공백 = 미확정), 창 시작 전 집행정지(stay_flag만, STAY_START 없음).
- 완료 기준은 §6의 S2. 연결률을 연도별·삭제 여부별·경로별(code_map / ②품목기준코드)로 보고한다. is_original은 쓰지 않는다. product_role 역할별 제품 수(연도별·삭제 여부별)와 근거 충돌 건수·사례를 보고한다.
```

### S3 신호·이벤트 원장
```
P1 S3만 수행해. signal_ledger, signal_outcome, event_ledger, 스냅샷 차이 방식과의 대조표.
- 신호와 이벤트는 따로 기록한다. 신호만으로 이벤트를 만들지 않는다 (CLAUDE.md 핵심 원칙).
- 유효일 규칙은 §4. 실현되지 않은 신호의 날짜가 어떤 이벤트에도 쓰이지 않는지 테스트한다.
- 합성 테스트: 예고 후 실현 / 예고 후 금액 다름 / 예고 후 비고 사라지고 가격 유지 / 예고 후 삭제 / 분석 끝 censored.
- 새 신호 유형 합성 테스트: SUSPEND_SCHEDULED 확인·미확인(exposure_frac 고쳐 쓰기와 exposure_revised), RESUME_SCHEDULED 실현·새 정지로 미실현, DELISTING_SCHEDULED 삭제·코드 교체(F7)로 미실현, U6 신호 달에는 이벤트 없음 → 다음 달 금액 확인 뒤 STAY_END+PRICE_CHANGE.
- 두 목록 중복 신호(P4)가 하나로 합쳐지고 실현율 분모가 한 번만 세지는지 테스트한다.
- 진입·퇴출 재분류는 §4의 7단계 순서. 증분 시험: 한 달씩 추가한 결과 = 한 번에 만든 결과 (exposure_frac·복귀 첫 달 state 고쳐 쓰기 포함).
- reports/P1_S3_result.md에 연도별 대조표와 신호 유형별 outcome 건수를 넣는다.
```

### S4 분석 A
```
P1 S4만 수행해. docs/PLAN_P1.md §5 A1~A6.
- A4는 대상 선정 기준과 제외 건수(비공개로 빠진 성분 포함)를 결과보다 먼저 보고한다.
- 실현율은 censored를 뺀 분모, Wilson 95% 구간.
- outputs/validation/sample_60.csv를 시드 고정으로 만든다. 라벨 컬럼은 비운다. 라벨은 내가 채운다.
- 라벨이 채워진 뒤 정밀도를 계산하는 스크립트를 따로 둔다.
```

### S5 분석 B·C
```
P1 S5만 수행해. §5 B1, B2, C. 한계 절을 결과보다 먼저 쓴다. 표본 탈락 단계표를 넣는다.
```

### S6 월간 리포트
```
P1 S6만 수행해. make report MONTH=YYYY-MM. 완료 기준은 §6의 S6.
- 신호는 "예고"로 쓰고, 같은 유형의 과거 실현율을 옆에 적는다. "예정 = 변화"로 읽히는 문장을 쓰지 않는다.
- 같은 입력 → 같은 파일 해시 테스트. 대시보드용 CSV를 outputs/dashboard/에 낸다.
```

### S7 문서
```
P1 S7만 수행해. README의 P1 절, 2쪽 요약(공고 문구별 근거 링크), 한계 문서, 새 환경 make all 재현 로그.
통과하지 못한 검정과 실현되지 않은 신호도 요약에 넣는다.
```

---

## P2

### T1 예측 마트
```
docs/PLAN_P2.md를 읽고 P2 T1만 수행해. fc_panel(성분×시도×월, 원외 청구수량·청구건수), 대상 성분 선정, 커버리지 표.
- 비공개 칸은 0이 아니라 미관측이다. 첫 관측월 제외 규칙을 테스트한다.
- 원본 재집계 합 = 마트 합 테스트. 대상 선정 탈락 단계표를 보고한다.
```

### T2 기준선
```
P2 T2만 수행해. M0, M1과 롤링 origin 평가 틀. 누수 테스트를 먼저 쓴다(미래 행을 바꿔도 과거 origin의 예측이 같아야 한다).
usage_publication_lag_months를 0과 내가 정한 값 두 가지로 돌린다. 값이 아직 없으면 "확인 필요"로 묻는다.
```

### T3 전역 모델·신호
```
P2 T3만 수행해. P1 S3의 signal_ledger가 있어야 한다. M2, M3.
- 신호 특징은 origin 시점에 기준일이 지난 스냅샷만으로 만든다. origin 이후 스냅샷을 지워도 특징이 같은지 테스트한다.
- 나중에 고쳐 쓴 exposure_frac(exposure_revised)을 특징에 쓰지 않는다. origin 시점의 signal_ledger 행만 쓰는지 누수 테스트에 넣는다.
- M3 대 M2는 origin 2023-01 이후에서만 비교한다. 검정 결과가 유의하지 않으면 "차이 없음"으로 쓴다.
```

### T4 분위·발주
```
P2 T4만 수행해. 분위 예측(P50·P90), 포함률, 계층 정합(선택), 발주 시뮬레이션.
한계 절(시도 단위 가상 재고, 가정한 리드타임·비용 비율)을 결과보다 먼저 쓴다.
```

### T5 문서
```
P2 T5만 수행해. README의 P2 절, 2쪽 요약. 개선이 없던 결과도 넣는다.
```

---

## 수정 요청용
```
reports/<단계>_result.md의 [항목]이 완료 기준을 못 맞췄다. 원인을 먼저 설명하고, 고칠 범위를 제안한 뒤 내 승인을 기다려. 테스트 기대값은 바꾸지 마.
```
