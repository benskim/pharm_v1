# 상태전이 전수조사: 급여목록·대체조제 목록 (2026-11-17)

- 목적: 제품의 상태전이를 빠짐없이, 겹치지 않게(MECE) 나열한다. 사용자가 이 표로 현재 판정 로직(S2 `state_rules.py`·`s2_state.py`, S3 `s3_ledger.py`)이 타당한지 점검한다.
- 발단: 급여정지 중인 제품의 비고에 가격 예고가 붙는다. 사용자가 정한 원칙은 "급여정지 대상은 집행정지되지 않은 이상 가격인하 대상이 아니다"이다.
- 범위: 2022-01 ~ 2025-12, 두 목록 각각 48개월(누락 월 0). 제품×월 1,114,812행 (급여목록 1,114,807, 대체조제 목록에만 있는 행 5), 월간 전이 1,097,049개(연속한 두 달 중 한 달 이상 목록에 있는 제품×달 쌍).
- 재현: `python -I scripts/state_transition_census.py` (읽기 전용, `data/interim/pbd.duckdb`). 결과 CSV는 `outputs/state_census/`에 있다. 이 문서의 숫자는 모두 이 스크립트를 이번에 돌려 나온 값이다.
- **파이프라인·규칙은 고치지 않았다.** §7은 규칙 변경 후보이고, 정하는 것은 사용자다.

## 0. 요약

1. 목록 상태 공간은 축 4개로 MECE가 된다: 목록 존재, 급여 상태, 집행정지 여부, 비고 토큰 조합. 이론상 가능한 전이 35칸 가운데 관측된 칸은 13개다(`state_asof` 기준). 관측되지 않은 22칸(예: 산정불가→급여, 정지→등재 예정)은 0건이다.
2. **허점이 데이터로 확인된다.** 월말 상태가 `suspended`인 제품의 상한금액이 바뀐 전이가 **115건**이다(정지→정지 114, 정지→급여 1). 현재 로직은 115건 모두 `PRICE_CHANGE` 이벤트로 만든다(unsignaled 103 / stay_released 6 / signaled 5. 관측월 행이 정지 상태인 이벤트 기준. 정지→급여 1건은 관측월 행이 급여 상태라 이 이벤트 기준 집계에 들어가지 않는다). 이 중 5건은 정지 제품의 가격 예고가 `realized`로 실현율 분자에 들어간다.
3. 정지 중 가격 예고(R2)는 6건이다(실현 5, 삭제 1). 정지 예고와 가격 예고가 함께 온 경우는 53건이다(동아에스티 52, 한국피엠지제약 1). 현재 로직은 이 둘을 서로 독립된 신호로 판정한다.
4. 정지와 집행정지가 한 칸에 함께 적힌 제품 10개는 **모두 집행정지일이 정지일보다 앞선다.** 날짜만 봐도 그 정지를 멈춘 집행정지가 아니다. 현재 로직은 이 제품들을 `suspended`로 두는데, 사용자 원칙과 맞는다.
5. 하류 영향: S5 시장 분석(B1·C, 업체 수)은 `state_asof = 'reimbursed'`로 걸러서 영향이 없다. 영향을 받는 곳은 event_ledger, A1 실현율(가격 예고 0.9821 → 정지 제외 시 0.9823), A5 대조표의 unsignaled 건수(9,412 중 103), S6 영향 규모 계산이다. S6는 분자에 상태 필터가 없고 분모는 `reimbursed`만 센다.

## 1. 상태 공간 정의 (MECE 축)

| 축 | 값 (서로 배타) | 출처 |
|---|---|---|
| A 목록 존재 | `B+S` 두 목록 / `B` 급여목록만 / `S_only` 대체조제 목록만 / `absent` 없음 | stg_benefit, stg_substitute |
| B 급여 상태 (B에 있을 때) | `scheduled_listing` > `suspended` > `unpriced` > `reimbursed` (우선순위 순. 한 행에 하나) | `state_asof`(기준일 시점) / `state_eom`(월말, 사후 확인 반영) |
| C 집행정지 | `stay_flag` 참/거짓. 상태가 아니라 겹쳐 붙는 표시 | 그 달 R4 있고 1일 이전 R5 없음 |
| D 비고 토큰 (두 목록 합침) | 근거(과거 날짜): R1 등재, R3 정지, R3b 기간정지, R4 집행정지, R5 해제·인하, R6 정지 해제, R7 산정불가 / 예고(미래 날짜): R1f, R2f 가격, R3f, R3bf, R4f, R9f 삭제 | note_token |

- `delisted`는 행이 아니라 `→ absent` 전이로 나타난다.
- 알 수 없는 문형(`unresolved`) 토큰은 0개다.

## 2. 행 단위 전수표: 급여 상태 × 비고 토큰 조합 (27종)

`state_asof` 기준. 비고 조합은 그 달 두 목록 토큰의 합집합이다. `f`가 붙은 토큰은 미래 날짜(예고)다.

| state_asof | 비고 조합 | 행 | 제품 | 기간 | 현재 로직의 처리 |
|---|---|---|---|---|---|
| reimbursed | (없음) | 1,086,849 | 28,529 | 22-01~25-12 | 정상 급여 |
| reimbursed | R4 | 13,614 | 571 | 22-01~25-12 | 급여 + stay_flag |
| reimbursed | R2f | 4,537 | 4,521 | 22-06~25-07 | PRICE_PENDING 신호 |
| reimbursed | R3f | 115 | 115 | 22-01~25-06 | SUSPEND_SCHEDULED 신호, 확인 전 급여 |
| reimbursed | R2f+R4 | 68 | 68 | 24-02~24-06 | 가격 신호 + stay (§3 표 C의 stay유지 signaled 68) |
| reimbursed | R1 (과거 날짜) | 65 | 65 | 22-01~25-11 | 등재일 근거 |
| reimbursed | R4f | 59 | 59 | 22-01~23-09 | stay_flag 참 (R4 날짜를 보지 않음, 151/151행) |
| reimbursed | **R2f+R3bf+R4f** | 52 | 52 | 22-05 | 동아에스티: 가격·정지 예고 둘 다 미실현 (§5 G6) |
| reimbursed | R3bf | 24 | 13 | 22-02~22-05 | 정지 예고 |
| reimbursed | R2f+R4f | 22 | 22 | 22-05 | 가격 신호 + stay |
| reimbursed | R3b (종료 뒤 잔존) | 18 | 9 | 22-05~23-02 | 날짜로 판정해 급여 (규칙 R3b) |
| reimbursed | R3bf+R4f | 18 | 18 | 22-05 | 정지 예고 + stay |
| reimbursed | R1f (기존 제품) | 10 | 10 | 25-11 | LISTING_NOTE_EXISTING |
| reimbursed | R5 | 7 | 7 | 25-12 | 해제·인하 근거 |
| reimbursed | R3f+R4 | 4 | 4 | 22-09 | 정지 예고 + stay |
| reimbursed | R4+R5 | 1 | 1 | 22-01 | U6 |
| reimbursed | R6 | 1 | 1 | 25-12 | 복귀 근거 |
| reimbursed | R9f | 1 | 1 | 23-06 | DELISTING_SCHEDULED |
| reimbursed | **R2f+R3f** | 1 | 1 | 24-02 | 정지 예고 + 가격 예고 독립 판정 (§5 G5) |
| scheduled_listing | R1f | 342 | 342 | 22-03~25-11 | 시행 전, 활성 제외 |
| suspended | R3 | 6,181 | 430 | 22-01~25-12 | 정지 |
| suspended | R3+R4 | 77 | 10 | 22-01~23-11 | 정지 + stay_flag (§5 G7) |
| suspended | (없음) | 68 | 65 | 22-02~25-07 | 2개월 공백 첫 달 65 (state_eom에서 고쳐 씀), 1개월 공백 뒤 재등장 3 |
| suspended | R3b | 36 | 11 | 22-04~22-12 | 기간 정지 |
| suspended | **R2f+R3** | 6 | 6 | 23-01~24-06 | **정지 중 가격 예고 (§5 G3·G4)** |
| unpriced | R7 | 2,631 | 65 | 22-01~25-12 | 산정불가 |
| (S_only) | (없음) | 5 | 5 | 23-04 | 급여목록에 없는 대체조제 행 |

참고: 정지 비고가 대체조제 목록에만 있는 정지 행은 32행이다(규칙 P4. 먼저 적힌 목록의 날짜를 쓴다).

## 3. 전이 전수표

### 3.1 상태쌍 (t → t+1), 이론상 가능한 칸 전체

`state_asof` 기준 (— = 정의상 제외, 0 = 관측 0건)

| from \ to | absent | S_only | scheduled_listing | reimbursed | suspended | unpriced |
|---|---|---|---|---|---|---|
| **absent** | — | 0 | 342 | 3,650 | 0 | 2 |
| **S_only** | 5 | 0 | 0 | 0 | 0 | 0 |
| **scheduled_listing** | 0 | 0 | 0 | 342 | 0 | 0 |
| **reimbursed** | 6,924 | 5 | 0 | 1,076,702 | 144 | 0 |
| **suspended** | 352 | 0 | 0 | 77 | 5,922 | 0 |
| **unpriced** | 16 | 0 | 0 | 0 | 0 | 2,566 |

`state_eom` 기준: `scheduled_listing` 행·열이 모두 0이다(342건 모두 월말에는 등재 확인). 나머지 차이: absent→reimbursed 3,992, reimbursed→reimbursed 1,076,978, reimbursed→suspended 143, suspended→suspended 5,989. 값은 `outputs/state_census/transition_eom_x_price.csv`에 있다.

관측 0건이라 규칙이 시험되지 않은 칸: 등재 예정 → 정지·삭제, 정지 → 산정불가, 산정불가 → 급여, 처음 관측부터 정지(창 시작 2022-01 좌측 절단 302행 제외), 대체조제 목록에만 있다가 급여목록으로 들어옴.

### 3.2 같은 상태 안의 상한금액 변화 (state_eom)

| 전이 | 그대로 | 인하 | 인상 | 금액 없음 |
|---|---|---|---|---|
| reimbursed → reimbursed | 1,062,937 | 13,708 | 333 | 0 |
| **suspended → suspended** | 5,875 | **114** | 0 | 0 |
| **suspended → reimbursed** | 76 | **1** | 0 | 0 |
| reimbursed → suspended | 143 | 0 | 0 | 0 |
| unpriced → unpriced | 0 | 0 | 0 | 2,566 |

급여→급여 가격 변화를 이벤트 종류와 집행정지 전이로 나누면 다음과 같다:

| 방향 | 집행정지 | 이벤트 kind | 건수 |
|---|---|---|---|
| 인하 | 없음 | unsignaled | 8,814 |
| 인하 | 없음 | signaled | 4,526 |
| 인하 | 유지 | unsignaled | 161 |
| 인하 | 유지 | signaled | 68 |
| 인하 | 해제 | stay_released | 138 |
| 인하 | 시작 | unsignaled | 1 |
| 인상 | 없음 | unsignaled | 319 |
| 인상 | 시작 | unsignaled | 13 |
| 인상 | 유지 | unsignaled | 1 |

### 3.3 상태 경계 전이의 경로

| 경계 | 경로 (kind / date_source) | 건수 |
|---|---|---|
| 진입 | new_item 3,145 · same_item_recode 338 · new_pack_existing_item 190 · transfer_by_name 164 · unlinked 85 · same_firm_swap 28 | 3,950 |
| 퇴출 | exit 6,648 · recode_transfer 530 · item_remains 119 | 7,297 |
| 급여 → 정지 (SUSPEND) | 비고 날짜(note_benefit) 105 · 예고 확인(signal_confirmed) 38 · 창 첫 달 1 | 144 |
| 정지 → 급여 (RESUME) | 2개월 연속 공백 65 · R3b 종료일 11 · R6 해제 1 | 77 |
| 정지 → 삭제 | 정지 상태로 삭제 (시장 이탈 = 정지일) | 352 |
| 급여 → 삭제 후 재등장 | `exit_reversed` | 44 |

## 4. 신호 × 상태 × 결과 (현재 판정)

발행 달·확인 달의 `state_eom`이다. `+stay`는 stay_flag가 참이라는 뜻이다.

| 신호 | 발행 때 | 확인 때 | outcome | reason | 건수 |
|---|---|---|---|---|---|
| PRICE_PENDING | reimbursed | reimbursed | realized | | 4,522 |
| PRICE_PENDING | reimbursed | reimbursed | realized_partial | | 4 |
| PRICE_PENDING | reimbursed | absent | not_realized | delisted | 6 |
| PRICE_PENDING | reimbursed | reimbursed+stay | not_realized | price_kept_stay_note | 3 |
| PRICE_PENDING | reimbursed+stay | reimbursed+stay | not_realized | price_kept_stay_note | 74 |
| PRICE_PENDING | reimbursed+stay | reimbursed+stay | realized | | 68 |
| **PRICE_PENDING** | **suspended** | **suspended** | **realized** | | **5** |
| **PRICE_PENDING** | **suspended** | absent | not_realized | delisted | **1** |
| SUSPEND_SCHEDULED | reimbursed | suspended | realized | | 11 |
| SUSPEND_SCHEDULED | suspended (월중 시작) | suspended | realized / realized_partial | | 28 / 35 |
| SUSPEND_SCHEDULED | suspended+stay | suspended+stay | realized_partial | | 4 |
| SUSPEND_SCHEDULED | reimbursed(+stay) | reimbursed+stay | not_realized | stay | 3 + 70 |
| SUSPEND_SCHEDULED | reimbursed | absent / reimbursed | not_realized | delisted / note_gone | 29 / 23 |
| RESUME_SCHEDULED | reimbursed | reimbursed | realized | | 11 |
| RESUME_SCHEDULED | (전제 정지 미실현) | | moot | | 72 |
| LISTING_SCHEDULED / NOTE_EXISTING | reimbursed | reimbursed | realized | | 342 / 10 |
| STAY_RELEASE_PRICE | reimbursed+stay | reimbursed | realized | | 1 |
| DELISTING_SCHEDULED | reimbursed | absent | realized | | 1 |

## 5. 허점 점검: 급여정지와 가격 (전수 사례 분류)

정지와 가격이 만나는 경우를 겹치지 않게 나누면 7가지다. G1~G4는 정지 기간 안의 상한금액 변화이고, G5~G7은 예고와 집행정지가 섞인 경우다.

| # | 경우 | 건수 | 사례 | 현재 로직 | 사용자 원칙과 비교 |
|---|---|---|---|---|---|
| G1 | 정지 중 **비고 없는** 상한금액 인하 | 103 (2023-09 102, 2022-03 1) | 2023-09는 전체 인하 7,468건 중 정지 제품 102개. 예: 052700200 259→189원, `급여정지(19.9.26.)` | PRICE_CHANGE `unsignaled`. A5 unsignaled에 들어감 | 목록 금액은 실제로 바뀌었지만, 급여되지 않는 제품의 시장 가격 변화로 셀 근거가 없다 |
| G2 | 정지 중 **집행정지 해제**로 인하 | 7 (정지 유지 6, 같은 달 복귀 1) | 비보존제약 658200180 `집행정지(18.7.5.), 급여정지(21.3.12.)` → 2022-06 212→170원 | PRICE_CHANGE `stay_released`. STAY_END와 짝 | 집행정지 대상은 정지 이전의 인하 처분이다. 정지 중에 그 인하가 목록에 반영됐다 |
| G3 | 정지 중 **가격 예고 → 금액 반영** | 5 (2024-07) | 삼진제약 647801211 `급여정지(23.5.4.) / 24.7.1., 2271원` | PRICE_PENDING `realized`, PRICE_CHANGE `signaled`. **A1 실현율 분자에 들어감** | 사용자가 지적한 허점이다. "인하 예고가 실현됐다"로 셀 대상이 아니다 |
| G4 | 정지 중 가격 예고 → 삭제 | 1 | 지엘파마 649702730 `급여정지(22.12.28.), '2023.1.9., 240원` → 2023-02 없음 | PRICE_PENDING `not_realized`(delisted). 분모에 들어감 | 정지가 먼저라 가격 예고를 판정할 대상이 아니다 |
| G5 | 정지 예고 + 가격 예고, **가격일 > 정지일** | 1 | 한국피엠지제약 697100360 `2024.3.1., 94원, 급여정지(24.2.6.)` → 2024-03 없음 | 두 신호 모두 `not_realized`(delisted). 서로 독립 | 정지가 실현됐다면 가격 예고는 판정 대상이 아니다. 현재는 이 종속 관계를 반영하지 않는다 |
| G6 | 정지 예고 + 가격 예고 + 집행정지 | 52 (2022-05) | 동아에스티 642500160: 급여목록 `집행정지(22.5.3.) / 급여정지(22.8.1.~22.8.31)`, 대체조제 목록 `22.5.4., 105원` | 정지 예고 `not_realized`(stay), 가격 예고 `not_realized`(price_kept_stay_note), RESUME 예고 `moot` | 정지가 집행정지돼 급여가 유지됐다. 원칙대로라면 가격 예고는 판정 대상이다. 현재 로직도 판정 대상으로 다룬다 |
| G7 | 정지 + 집행정지가 한 칸에 | 10개 제품 77행 | 파마킹 652300580 `급여정지(19.9.26.), 집행정지(18.4.1.)` | `suspended` + stay_flag | 10개 모두 집행정지일 < 정지일이다. 그 정지에 대한 집행정지가 아니므로 정지가 맞다. 원칙과 맞는다 |

**정지 중 가격이 바뀐 115개 제품의 이후 경로**: 정지 상태로 삭제 102, 분석 끝까지 정지 9, 복귀 4. 복귀한 4개는 정지 중 바뀐 금액으로 복귀했다:
- 646003620: 2023-09에 197→167원, 2023-10 복귀 167원
- 640902540: 2023-09에 103→88원, 2025-02 복귀 88원
- 656700050: 2024-07에 523→520원, 2025-11 복귀 520원
- 658200140: 2022-06에 인하(109→93원)와 복귀가 같은 달

정지 중 목록 금액 변화가 아무 뜻도 없는 것은 아니다. **복귀했을 때 적용되는 가격**을 정한다. 따라서 "이벤트로 만들지 않는다"와 "기록하지 않는다"는 다른 선택이다.

**데이터와 사용자 원칙이 다른 점**: 원칙은 "정지 중에는 가격인하 대상이 아니다"이다. 그런데 목록의 상한금액은 정지 중에도 115번 바뀌었다. 특히 2023-09 일괄 인하는 정지 제품도 함께 내렸다. 그래서 이 문서는 원칙을 "목록 금액은 바뀔 수 있지만, 급여되지 않는 동안에는 시장 가격 변화가 아니다"로 읽었다. 이렇게 읽는 것이 맞는지는 확인 필요다 (§7 Q1).

## 6. 하류 영향 (현재 로직 그대로일 때)

| 산출물 | 정지 제품 가격을 쓰는가 | 영향 |
|---|---|---|
| event_ledger PRICE_CHANGE | 쓴다 | 정지 상태 행의 이벤트: unsignaled 103 / 9,412, signaled 5 / 4,599, stay_released 6 / 145 |
| A1 실현율 (PRICE_PENDING) | 쓴다 | 전체 4,599 / 4,683 = 0.9821, 발행 달 정지 제품 6건 제외 시 4,594 / 4,677 = 0.9823 |
| A4 사용통계 검증 | 분자에 realized 5건이 들어갈 수 있음. "절반 이상" 분모는 `reimbursed`만 | 2024-07-01 예고 성분에 한함 (규모 미계산) |
| A5 대조표 | 쓴다 | unsignaled 건수에 G1 103건 |
| S5 B1·C·업체 수 | 쓰지 않는다 (`state_asof = 'reimbursed'`) | 없음 |
| S6 `new_signals` | 상태 필터 없음 | 2024-06 발행월 리포트에 정지 제품 가격 예고 5건이 "이번 달 신호"로 실린다 |
| S6 `impact_table` | **분자는 필터 없음, 분모 N은 reimbursed만** | 분모에 없는 제품이 분자에 들어간다. 해당 발행월: 2023-01, 2024-06 |

## 7. 확인 필요 (사용자 결정 사항)

**Q1. 정지 중 상한금액 변화 (G1·G2·G3, 115건)를 어떻게 기록할까?**
- (a) 지금처럼 PRICE_CHANGE로 두고 `during_suspension` 표시를 붙인다. 실현율·A5·S6에서는 뺀다.
- (b) PRICE_CHANGE를 만들지 않는다. `price_listed` 갱신(속성 변화)으로만 남기고, 복귀 때 RESUME 이벤트에 "정지 전 금액 → 복귀 금액"을 붙인다.
- (c) 지금처럼 둔다. 한계 문서에만 적는다.
- 추천은 (b)다. "정지 중 가격인하 대상 아님"을 이벤트 정의에 직접 반영하면서 복귀 가격 정보(4건)를 잃지 않는다.

**Q2. 정지 중 발행된 가격 예고 (G3·G4, 6건)는 어떻게 판정할까?**
- (a) 신호는 기록하고 outcome을 새 값 `not_applicable`(정지 중)로 둔다. `moot`처럼 실현율 분모에서 빼고 건수만 보고한다.
- (b) 신호로 만들지 않는다.
- 추천은 (a)다. 원장의 "신호는 기록한다" 원칙을 지키면서 분모를 바로잡는다. A1 효과는 0.9821 → 0.9823이다.

**Q3. 정지 예고와 가격 예고가 함께 오고 가격일 ≥ 정지일인 경우 (G5, 1건)는?**
- 가격 예고에 `parent_signal_id` = 그 정지 예고를 둔다(RESUME_SCHEDULED의 moot 규칙과 같은 구조). 정지가 실현되면 가격 예고는 `not_applicable`, 정지가 미실현(집행정지·비고 사라짐)이면 가격 예고를 그대로 판정한다. G6(동아에스티 52)은 정지가 미실현이라 지금 판정이 그대로 유지된다.
- 다만 G6은 가격일(5.4) < 정지일(8.1)이라 이 규칙의 대상이 아니다. 가격일 < 정지일이면 가격 예고를 독립적으로 판정한다. 이것도 함께 정해야 한다.

**Q4. 집행정지가 어느 처분을 막는지 날짜 순서로 일부 정해도 될까?**
- `NOTE_RULES.md` §3은 "집행정지가 막는 처분은 비고로 알 수 없다"고 정했다. 그런데 G7처럼 집행정지일 < 정지일이면 **그 정지를 막는 집행정지가 아니라는 것**은 날짜로 알 수 있다(10개 제품 모두). 이 부정 방향 판정만 규칙으로 둘지 묻는다. 현재 상태 판정 결과는 바뀌지 않는다(이미 `suspended`). 대신 G2(정지 중 stay_released 인하)를 "정지 이전 처분의 해제"로 표시할 근거가 된다.

**Q5. S6 영향 규모의 분자·분모를 맞출까?**
- 분자도 발행 달 `state_asof = 'reimbursed'`로 거를지 묻는다. Q2를 (a)로 정하면 함께 고치는 것이 맞다.

**Q6. 관측 0건인 전이 칸(§3.1)에도 합성 시험을 둘까?**
- 대상: 등재 예정 → 정지, 정지 → 산정불가, 산정불가 → 급여. 실데이터 0건이라 현재 로직이 어떻게 처리하는지 확인되지 않았다.

## 8. 산출물

- `scripts/state_transition_census.py`: 전수 집계 (읽기 전용)
- `outputs/state_census/`
  - `row_state_note_combo.csv`: §2
  - `transition_asof_x_price.csv`, `transition_eom_x_price.csv`: §3.1·3.2
  - `reimbursed_price_change_kind.csv`, `boundary_event_kind.csv`: §3.2·3.3
  - `signal_state_outcome.csv`: §4
  - `price_change_during_suspension.csv`, `price_change_during_suspension_detail.csv` (115행), `same_month_all_price_cuts.csv`, `suspension_price_change_fate.csv`: §5 G1~G3
  - `price_note_with_suspension.csv` (59행): §5 G3~G6
  - `suspension_with_stay.csv`: §5 G7
  - `misc_checks.csv`: 누락 월, unresolved, S_only, 미래 날짜 R4의 stay_flag 등
