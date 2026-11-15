# PLAN_P1 — 선행 신호 원장 (2026-11-14)

## 1. 목표와 주장

**무엇을 만드는가**: 공개 약가 스냅샷을 유효일 기준 원장으로 복원한다. 비고의 예정 정보는 선행 신호로 따로 기록하고, 신호가 실제 변화로 이어진 비율과 시차를 잰다. 결과는 사용통계로 검증하고, 월간 "선행 신호 모니터링 리포트"로 자동화한다.

**핵심 주장 두 가지**
1. 스냅샷에 행이 있다는 것은 "그 날짜의 목록에 실렸다"는 뜻이지 "그 날짜에 그 가격으로 급여되고 있다"는 뜻이 아니다. 비고를 무시하면 이벤트의 날짜와 종류가 틀린다.
2. 비고의 예정 정보는 시장 변화의 **선행 신호**다. 대부분 실현되지만 전부는 아니다. 신호 유형별 실현율과 시차를 알면, 약국·유통이 변화를 미리 대비할 수 있다.

| 항목 | 값 |
|---|---|
| 분석 기간 | **2022-01 ~ 2025-12** (월 스냅샷 최대 48개) |
| 검증 데이터 | 건보공단 사용통계 2022-01 ~ 2025-12 (원외 단가 = 청구금액 ÷ 청구수량) |
| 설계에 쓴 표본 | 2024-01 ~ 2025-12. 이 문서의 숫자는 표본에서 나왔다 |
| 제외 | 2026-01 이후 변동 (`BACKLOG.md`) |
| 제출처 | 바로팜 데이터 사이언티스트 (마감 2026-11-30). 올리브영 데이터 분석가에도 사용 |

## 2. 표본에서 확인한 것 (가설)

재현 스크립트는 `reference/precheck_v3.py`. 전체 기간에서 다시 센다.

| # | 발견 | 표본 숫자 |
|---|---|---|
| F1 | 제품코드 0 누락 | 원본 고유 25,767 → 0 채움 후 24,597 |
| F3 | 등재일 비고는 급여목록에 일부만 있고 대체조제 목록이 보완 | 진입 1,697건 중 급여목록 비고 197건. 2025-10 진입은 급여목록 비고 0건, 대체조제 목록 비고 59건 |
| F4 | 가격 예고는 대부분 다음 스냅샷에서 실현되지만 예외가 있다 | 예고 4,565행 중 다음 스냅샷 금액 일치 4,552, 다음 스냅샷에 없음 6, 금액 불일치 7 (2025-07 예고 1건 포함). 실현된 월중 예고의 성분 단가는 예고 날짜가 속한 달에 움직였다 (시타글립틴 6942: 2024-09 −23.1%, 2024-10 0.0%) |
| F5 | 집행정지 중에도 가격은 바뀐다 | 301개 제품. 정지 유지 중 가격 변화 150건(149건 인하) |
| F6 | 급여정지는 퇴출 전 단계이거나 단기 처분 | 창 시작 전부터 정지였던 73개 중 50개가 정지 상태로 삭제 |
| F7 | 스냅샷상 진입의 16.3%, 퇴출의 9.1%는 실질이 아니다 | 진입 1,697 → 실질 신규 1,421 |
| F8 | 목록 등장과 시장 반응 사이 시차 | 엠파글리플로진(6282): 2025-10 업체 1 → 33곳, 단가는 12월에 하락. **단, 사용통계의 비공개 규칙 때문에 전체 자료에서는 진입 전 구간이 안 보일 수 있다** |
| F9 | 비고 없는 가격 변화는 관측월 1일 적용 | 14건 중 12건 |
| F10 | 대체조제 그룹 내 가격 차 | 2025-12: 제품 2개 이상 그룹 668개, 최고/최저 중앙값 1.18 |
| F11 | 목록은 시행 전 품목을 미리 싣는다 | 2025-11 `'25.11.23 등재` 27행 = 처음 나타난 제품 17 + 이미 급여 중이던 제품 10 |

## 3. 신호와 이벤트의 구분

| 구분 | 정의 | 예 | 기록 위치 |
|---|---|---|---|
| **신호** | 스냅샷 기준일보다 **미래** 날짜를 가진 비고 토큰. 아직 일어나지 않은 일 | `24.9.2., 453원` (기준일 2024-09-01), `'25.11.23 등재` (기준일 2025-11-01) | `signal_ledger` |
| **상태 근거** | 기준일 이전 날짜를 가진 비고. 이미 일어난 일의 기록 | `급여정지(22.10.20.)`, `집행정지(24.9.30.)` | `product_state_month` |
| **이벤트** | 스냅샷 사이에서 관측된 변화 | 가격이 바뀜, 제품이 나타남·사라짐 | `event_ledger` |

**신호 유형** (1차)
| signal_type | 출처 비고 | 실현 판정 |
|---|---|---|
| `PRICE_PENDING` | R2 가격 예고 | 신호 날짜 이후 첫 스냅샷의 금액 = 예고 금액. 금액이 바뀌었지만 다르면 `realized_partial`. 그대로면 `not_realized`, 사유 delisted / suspended / `price_kept_stay_note`(관측만, 집행정지와 연결하지 않음) / `note_gone_price_kept` 등 (S3) |
| `LISTING_SCHEDULED` | R1 등재 예정 (처음 관측된 제품) | 신호 날짜 이후 첫 스냅샷에 제품이 있고 정지·삭제가 아님 |
| `LISTING_NOTE_EXISTING` | R1 등재 예정 (이미 급여 중인 제품) | 상태 변화 없음. 실현율 계산에서 따로 보고 |
| `STAY_RELEASE_PRICE` | R5 집행정지 해제 및 인하 (U6 변형 포함) | 해제 후 첫 스냅샷의 인하 여부. 이벤트 쌍(STAY_END + PRICE_CHANGE)은 확인 뒤에 만든다 |
| `SUSPEND_SCHEDULED` | R3 급여정지의 미래 날짜, R3b 기간 명시 정지의 미래 시작일. 유형을 나누지 않고 `source_rule`(R3 / R3b)로 구분 (9차) | 신호 날짜 이후 첫 스냅샷에서 같은 제품의 **정지 비고(R3·R3b)가 확인됨** (2026-11-14 8차). 같은 정지일이면 `realized`: SUSPEND 이벤트 날짜 = 신호 날짜 (`signal_confirmed`), 시작 달의 `exposure_frac`을 고쳐 쓴다. 정지일이 다르면 `realized_partial`(날짜 차이 기록, 예: 2022-09 39건 +1일): SUSPEND 이벤트 날짜 = 확인된 비고의 정지일 (`note_benefit`/`note_substitute`). **예고 날짜는 어떤 이벤트에도 쓰지 않는다.** `not_realized` 사유에는 출처 `reason_source`(data / external)와 근거 `evidence`를 둔다. external은 외부 자료로 정한 사유(링크)이고, 실현율 표에서 따로 센다. `not_realized` 사유: **집행정지**(확인 스냅샷에 정지 비고 없이 집행정지 비고) / **삭제**(확인 스냅샷에 제품 없음) / **비고 사라짐**(제품은 있고 정지·집행정지 비고 없음). R3b의 종료일이 확인 스냅샷 기준일보다 앞서면 `unverifiable`. S2 근거 표 `s2_suspend_signal_check` (`NOTE_RULES.md` §2) |
| `RESUME_SCHEDULED` | R3b 기간 명시 정지의 미래 종료일 | 종료일 이후 첫 스냅샷에 제품이 있고, 새 정지 비고나 삭제가 없음. RESUME 이벤트 날짜는 종료일 다음 날 (`note_benefit` / `note_substitute`) |
| `DELISTING_SCHEDULED` | R9 급여삭제 예정 | 신호 날짜 이후 첫 스냅샷에서 제품이 사라짐. **F7 재분류(코드 교체·양도양수)를 먼저 거친다**. 1건이라 A1에서 건수로만 보고 |

신호 날짜가 미래가 아닌 R2·R5 비고(이미 적용된 날짜)는 신호가 아니라 이벤트의 날짜 근거다.

**두 목록의 중복 신호 (P4)**: 같은 제품·같은 유형·같은 신호 날짜가 급여목록과 대체조제 목록에 함께 나오면 신호 하나로 합치고 `source_list`에 두 목록을 모두 적는다. 실현율 분모는 합친 신호로 센다.

**신호 결과** (`signal_outcome`)
| outcome | 뜻 |
|---|---|
| `realized` | 판정 기준을 만족 |
| `realized_partial` | 실현됐지만 금액·날짜가 다름 (금액 차이와 날짜 차이를 기록) |
| `not_realized` | 추적 기간 안에 실현되지 않음 (사유: 비고 사라짐·가격 유지 / 정지 / 삭제 / 집행정지) |
| `censored` | 분석 끝(2025-12)까지 추적 기간이 차지 않음 |
| `moot` | **전제 신호가 실현되지 않은 신호** (일반 규칙, 2026-11-15). 신호가 다른 신호의 실현을 전제할 때(`parent_signal_id`, 예: RESUME_SCHEDULED의 전제는 같은 제품·같은 시작일의 SUSPEND_SCHEDULED) 전제가 `not_realized`이면 `moot`. 전제가 `unverifiable`·`censored`이면 같은 값. **실현율 분모에서 빼고 건수를 따로 보고한다** |
| `unverifiable` | 확인할 스냅샷이 오기 전에 신호의 기간이 끝나 실현 여부를 판정할 수 없음 (R3b 종료일 < 확인 스냅샷 기준일). `censored`와 구분한다. **실현율 분모에서 빼고 건수를 따로 보고한다** (2026-11-14 8차) |

추적 기간은 `config/params.yaml`의 `signal_followup_months` (기본 3개월).

## 4. 데이터 모델

```
L1 정규화
 ├ snapshot_inventory   스냅샷별: 출처 파일, 기준일, 행 수, 컬럼 목록, 누락 월
 ├ stg_benefit          코드 9자리, 월 키, 공통 컬럼명
 ├ stg_substitute
 ├ stg_usage            원본 단위 그대로 (월×성분4×연령×시도×기관종별×원내외) + observed
 ├ note_token           (제품, 월, 출처목록, 순번, type, date, amount, raw, is_future)
 └ attr_2026            2026 약가정보 2종 + 오렌지북 (제품코드·품목기준코드 기준 속성)

L2 원장
 ├ product_state_month  제품×월: state_asof(기준일 시점), state_eom(월말), price_listed, price_pending, pending_date, stay_flag,
 │                      exposure_frac, exposure_revised, state_revised, resume_unconfirmed,
 │                      in_substitute_list, product_role (reference / bioequiv / NULL), product_role_carried, role_source,
 │                      is_bioequiv, role_conflict
 ├ product_attr_scd2    업체명·제품명·주성분코드의 유효 구간
 ├ bridge_product_item  제품코드 → 품목기준코드
 ├ bridge_code_recode   옛 제품코드 → 새 제품코드 (목록정비전코드)
 ├ signal_ledger        signal_id, signal_type, source_rule, product_code, market_c4, issued_month,
 │                      signal_date, amount, source_list, raw
 ├ signal_outcome       signal_id, outcome, parent_signal_id, realized_month, lag_days, amount_gap, date_gap_days, reason, reason_source, evidence,
 │                      realized_after_followup (참고: 미실현 가격 예고가 뒤에 예고 금액으로 나타난 첫 달, 판정에 쓰지 않음)
 └ event_ledger         event_id, event_type, product_code, market_c4, observed_month, notice_date(참고), exit_reversed(사후 분석용),
                        late_realization_of(사후 참고: 늦게 예고 금액이 된 미실현 가격 예고의 signal_id), reason_source(외부 근거 표시),
                        effective_date, effective_date_lo/hi, date_source, kind, signal_id, evidence

L3 분석용
 ├ market_month         성분4자리×월: 유효 업체 수, 가격 지수, 신호 노출, 사용통계, observed
 └ subst_group_month    대체조제 그룹×월: 제품 수, 최저·최고가, 대조약 가격
```

### state (제품×월)
| state (`state_asof`·`state_eom` 공통 값) | 뜻 | 판정 |
|---|---|---|
| `reimbursed` | 급여 적용 중 | 목록에 있고 아래에 해당하지 않음 |
| `scheduled_listing` | 목록에 실렸지만 시행 전 | 처음 관측된 제품 + `LISTING_SCHEDULED` 신호의 날짜 전 |
| `suspended` | 급여정지 | 급여정지 비고 날짜 이후. 기간 명시 정지(R3b)는 비고 유무가 아니라 시작일~종료일로 판정 |
| `unpriced` | 산정불가 | 비고 또는 금액 칸의 `산정불가` |
| `delisted` | 삭제 | 다음 스냅샷에 없음 (코드 교체·양도양수 제외) |

- 집행정지는 state가 아니라 `stay_flag`. 급여는 정상이다. **`stay_flag`는 기준일 시점 값이다** (그 달 비고에 R4가 있고 기준일 이전 R5 해제가 없음). 늦게 보인 R4·R5 날짜로 고쳐 쓰지 않는다.
- `scheduled_delisting`은 state로 두지 않는다. 미래 삭제일 비고(R9, 전체 기간 1건)는 `DELISTING_SCHEDULED` 신호로만 기록한다.
- **state는 두 컬럼이다** (2026-11-14 7차). `state_asof`: 스냅샷 기준일 시점의 상태, 그 스냅샷까지의 정보만 쓴다 (뒤의 스냅샷을 지워도 같다). `state_eom`: 월말 상태, 이후 스냅샷으로 확인된 것만 반영한다. 한 컬럼 안에서 판정 시점을 섞지 않는다. `exposure_frac`(급여된 일수 비율)은 `state_eom` 기준이다. 다음 스냅샷에서 처음 보인 지난 날짜(R6 해제일, R3 정지일, R3b 시작일)가 직전 스냅샷의 달 안이면 그 달의 `state_eom`·`exposure_frac`을 고쳐 쓴다 (`late_note_revised`). `SUSPEND_SCHEDULED`처럼 다음 스냅샷에서 확인된 뒤 고쳐 쓴 행은 `exposure_revised = true`.
- 분석 창 시작(2022-01) 전에 시작된 집행정지는 STAY_START 이벤트를 만들지 않고 `stay_flag`만 켠다.
- 종료일 없는 급여정지(R3)의 복귀: (a) R6 해제 비고, 또는 (b) 두 목록 어디에도 그 정지 토큰이 없는 달이 2개월 연속. (b)의 RESUME 유효일은 토큰이 처음 사라진 달의 1일(`snapshot_first`). 첫 달은 `suspended`를 유지하고, 둘째 달에 확정되면 첫 달 행을 고쳐 쓴다(`state_revised = true`). 한 달 공백 뒤 같은 정지일로 다시 나타나면 이벤트 없음. 분석 끝 한 달 공백은 `resume_unconfirmed = true` (`NOTE_RULES.md` R3).
- **대체조제 목록의 의미**: 대체조제는 처방약 대신 성분·함량·제형이 같고 동등성이 인정된 다른 제품으로 조제하는 것이다. 목록에 실렸다는 것은 그 제품의 역할(대조약인지, 생동인정 품목인지)을 말해 줄 뿐, 복제약 여부도 저가약 여부도 말해 주지 않는다. "저가약"은 제품 속성으로 쓰지 않고, 같은 그룹 안 그 달 상한금액 비교로만 쓴다. 원본 목록 이름("저가약 대체조제 장려금 대상 약제목록")은 그대로 둔다.
- **1차에서는 오리지널과 복제약을 구분하지 않는다.** 초기 설계의 `is_original`은 쓰지 않는다. 컬럼 이름·값·표 이름에 "오리지널", "제네릭"을 쓰지 않는다 (한계 절과 사례 설명의 문장에서는 쓸 수 있다). **예외**: 출처 원본의 칸 값을 그대로 보존하는 컬럼은 출처 접두어를 붙여 원본 칸 이름을 따를 수 있다 (예: 오렌지북 `제네릭의약품` → `ob_generic`). 이런 컬럼은 판정에 쓰지 않는다 (`DECISIONS.md` 2026-11-14 4차).
- **출처별 원본 값 보존**: 대체조제 목록의 `생동대조약 여부`·`생동인정여부`·`의약동등`, 오렌지북의 `공고대조약` 표시와 `제네릭의약품` 표시를 각각 별도 컬럼으로 보존한다. 서로 다른 속성이다. 오렌지북의 `제네릭의약품` 표시를 대체조제 목록의 생동인정과 같은 것으로 보지 않는다.
- **`product_role`** (`reference` / `bioequiv` / NULL)과 `role_source`:
  - 대체조제 목록에 있는 제품: `생동대조약 여부` = ○이면 `reference`. 대조약이 아니고 생동인정이면 `bioequiv`. `role_source = substitute`.
  - **제품×월로 판정한다.** 그 달 대체조제 목록 행의 값을 쓴다 (`의약동등`이 기간 중 바뀐 제품 3개). 대체조제 목록에서 빠진 달은 관측값 컬럼(`sub_*`, `is_bioequiv`)을 NULL로 둔다. `product_role_carried`는 과거 달의 대체조제 값만 앞으로 이어 쓰고(뒤의 값을 앞 달로 가져오지 않음), 없으면 오렌지북 공고대조약으로 채운다. `role_source` = observed / carried / orangebook (`product_role_carried`의 출처).
  - 대조약 여부와 생동인정 여부를 서로 배타적으로 강제하지 않는다. 대조약이면서 생동인정 정보도 있으면(`의약동등 = 6`) `product_role = reference`, 생동인정은 `is_bioequiv`(출처와 함께)에 보존한다.
  - 대체조제 목록에 없는 제품: 오렌지북 `공고대조약` = O이면 `reference`, `role_source = orangebook`. 그 밖에는 NULL.
  - 오렌지북의 `제네릭의약품` 표시만으로는 `product_role`을 채우지 않는다.
  - 오렌지북 연결 경로: 1) `code_map`(제품코드 → 품목기준코드), 2) 대체조제 목록 `②품목기준코드`. 경로 2는 대체조제 목록에 실린 제품에만 있으므로 연결률이 경로별로 다르다. 경로를 기록한다.
- **`role_conflict`**: 두 출처에서 같은 개념은 대조약 여부 하나다 (대체조제 `생동대조약 여부` ↔ 오렌지북 `공고대조약`). 둘이 다를 때만 `role_conflict = true`로 두고 대체조제 목록 값을 채택한다(`role_source`에 채택 출처). `의약동등 = 6`처럼 대조약 ○와 생동인정 ○가 함께 붙은 것은 충돌이 아니다. S1 점검: 오렌지북에 연결된 대체조제 목록 제품 9,350개에서 대조약 여부가 다른 제품 0개.
- 한계: 대조약이 항상 오리지널은 아니다. 1차는 오리지널을 구분하지 않는다.

### event_type
LISTING, DELISTING, PRICE_CHANGE (kind: signaled / unsignaled / stay_released), SUSPEND, RESUME, STAY_START, STAY_END, TRANSFER (kind: same_item_recode / transfer_by_name / same_firm_swap / list_recode), ATTR_CHANGE.

### 유효일 규칙
1. 이벤트와 짝지어진 신호가 `realized`이면 신호 날짜 (`date_source = signal_confirmed`).
2. 이벤트의 비고에 기준일 이전 날짜가 있으면 그 날짜 (`note_benefit` / `note_substitute`).
3. 둘 다 없으면 관측월 1일 (`snapshot_first`).
4. 삭제는 `effective_date_lo`(직전 스냅샷 다음 날) ~ `effective_date_hi`(사라진 스냅샷 기준일).
5. 빠진 달을 건너 생긴 변화는 `date_source = gap`, 날짜는 구간.
6. 실현되지 않은 신호의 날짜는 어떤 이벤트에도 쓰지 않는다.
7. 집행정지·해제의 날짜(R4 집행정지일, R5 해제·고시일)는 STAY_START, STAY_END, `stay_released` 가격 변경 이벤트의 유효일로 쓴다. 늦게 보인 R4·R5 날짜도 이벤트 유효일에만 쓰고 `stay_flag`(기준일 시점 값)는 고쳐 쓰지 않는다 (2026-11-14 8차).

### 진입·퇴출 재분류 (F7)
1. 같은 품목기준코드의 다른 제품코드가 ±1개월 안에 사라짐 → `same_item_recode`
2. 같은 주성분코드, 다른 업체, 같은 제품명(밑줄 앞부분)이 ±1개월 안에 사라짐 → `transfer_by_name`
3. 같은 주성분코드, 같은 업체가 ±1개월 안에 사라짐 → `same_firm_swap`
4. `목록정비전코드`로 이어지는 옛 코드가 있음 → `list_recode`
5. 같은 품목기준코드가 이미 등재됨 → `new_pack_existing_item`
6. 품목기준코드 연결 불가 → `unlinked`
7. 나머지 → `new_item` (실질 신규)

좌측 절단: 2022-01 스냅샷에 이미 있던 제품은 진입 이벤트를 만들지 않는다.

## 5. 분석

### A. 신호의 실현율과 시차 (본론)
- **A1 실현율 표**: 신호 유형 × 연도별 신호 수, `realized` / `realized_partial` / `not_realized` / `censored` 건수와 비율. 실현율은 `censored`·`unverifiable`·`moot`를 뺀 분모로 계산하고(세 건수는 따로 적는다) Wilson 95% 구간을 붙인다. **실현율은 하나만 낸다** (출처별 실현율을 따로 내지 않는다). `not_realized` 건수는 `reason_source`(data / external / data+external)별로 따로 적는다 (10차). `DELISTING_SCHEDULED`(1건)는 실현율 없이 건수로만 보고한다.
  - **보조 표**: 분해는 대조약 / 생동인정 / 모름 (`product_role` = `reference` / `bioequiv` / NULL). 역할 근거가 대체조제 목록에 실린 제품에만 충분히 있으므로 본 표에 넣지 않는다. 표 앞에 역할별 제품 수와 비율을 연도별·삭제 여부별·`role_source`별로 적는다. `role_conflict` 제품을 포함한 결과를 쓰고, 뺀 결과와의 차이를 한 줄로 적는다. "대조약 = 오리지널"로 쓰지 않는다.
- **A2 시차 분포**: 신호 발행 월 → 신호 날짜 → 실현 확인 스냅샷. 일수 분포와 "몇 번째 스냅샷에서 확인됐나".
- **A3 실현되지 않은 신호**: 사유별 건수와 사례 표. 숨기지 않는다.
- **A4 사용통계 검증**: 실현된 가격 신호에서 성분 원외 단가(청구금액 ÷ 청구수량)가 (a) 신호 날짜가 속한 달과 (b) 스냅샷 관측월 중 언제 움직였는지 비교한다. 월중 적용은 `exposure_frac`으로 부분 월 변화까지 맞는지 본다. 대상은 성분 내 제품의 절반 이상이 같은 신호를 가진 경우로 한정하고, 대상 수·제외 수(비공개로 빠진 성분 포함)를 먼저 보고한다.
- **A5 원장 대 스냅샷 차이 방식 대조표**: 유형별 이벤트 건수, 날짜가 달라진 건수, 종류가 달라진 건수, 시행 전·급여정지로 빠진 활성 제품 수. 연도별. 퇴출 건수는 전체와 `exit_reversed`(나중에 같은 제품코드로 돌아옴) 제외 두 가지로 낸다. 비고 없는 가격 변경(unsignaled)은 전체와 `late_realization_of` 제외 두 가지로 낸다.
- **A6 수작업 검증**: 층화 표본 60건 (층: `date_source` × `signal_type`/`kind`, 연도 분산). 사용자가 원자료와 대조해 라벨을 채운다. 정밀도와 Wilson 95% 구간. **654700150(2024-01, 대체조제 목록에만 남은 정지 비고)은 반드시 넣는다** (2026-11-14 7차).

### B. 시장 반응 (축소판)
- B1 전가율: 목록 가격 변화(제품 수 가중) 대비 실제 원외 단가 변화의 기울기. 1에서 벗어나는 부분은 구성 변화이며 "잔차"로만 말한다.
- B2 진입 군집 사례: 한 달에 새 업체 5곳 이상 들어온 성분×월. 오리지널 인하와 제네릭 등재가 같은 시기에 와서 분리하지 못한다는 점, 비공개 규칙으로 진입 전이 안 보일 수 있다는 점을 결과보다 먼저 쓴다.
- 이벤트 스터디(B3)는 하지 않는다.

### C. 대체조제 그룹 (표 1개)
그룹(①주성분 및 투여제형코드 × 주성분코드)별 최저가·최고가·대조약 가격과 성분 원외 금액. 리포트에 순위표로 넣는다. 절감액은 계산하지 않는다.

### P2로 넘기는 것
`market_month`의 신호 특징: 예고 인하 폭 × 해당 제품 비중 × 노출 일수 비율, 등재 예정 업체 수, 급여정지 제품 수, 신호 유형별 과거 실현율. **예측 시점에 볼 수 있던 스냅샷만으로** 만든다. 나중에 고쳐 쓴 `exposure_frac`(`exposure_revised = true`)은 쓰지 않고, 예측 시점에 있던 `signal_ledger` 행만 쓴다.

## 6. 단계와 완료 기준

공통: `make <단계>`가 오류 없이 실행, 해당 테스트 통과, `reports/<단계>_result.md` 작성, 버린 행 기록.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **S0** 자료 점검 (P1·P2 공통) | `data/raw/` 전수 조사 | `docs/DATA.md` §5의 6개 항목 보고. `python reference/precheck_v3.py data/sample` 출력이 `reference/precheck_v3_output.md`와 일치. Makefile 골격과 `config/schema_map.yaml` 초안. **구현 없이 점검만** |
| **S1** 적재·비고 파서 | L1 테이블 | 비고가 있는 모든 행이 type을 가짐 (`unresolved` 포함, 건수 보고). R1~R7·R3b·R9·P1~P4 문형 테스트 통과 (U1~U9 변형 포함: U1→R4, U4·U5→R2, U6→R5, U9→R1, U2·U3·U8→R3b 시작일·종료일, U7→R9). `is_future` 판정 테스트 (기간 명시 정지는 시작일·종료일 각각). 표본 구간 유형별 행 수가 reference F2와 일치. 원본 행 수 = stg 행 수. stg_usage 재집계 = 가공본 (S0에서 확인한 범위) |
| **S2** 상태·속성·연결 | `product_state_month`, `product_attr_scd2`, 두 bridge, 오렌지북 연결 | 제품×월 행 수 = 원본 행 수 − `non_product` 행 수 (S1 `row_kind`). 표본 구간 2025-11의 27행이 `state_asof` 기준 `scheduled_listing` 17 / `reimbursed` 10. `state_asof`는 뒤의 스냅샷을 지워도 같다 (시험). 집행정지 구간 수가 reference F5와 일치. 급여정지 구간은 reference 정의(급여목록 비고 문자열)로 세면 F5와 일치하고, `state_eom` 기준과의 차이는 (a) 확인되지 않은 미래 정지 비고, (b) 대체조제 목록에만 남은 정지 비고 두 규칙으로 모두 설명된다 (2026-11-14 7차). 기간 명시 정지는 종료일 뒤 비고가 남아도 복귀 (합성 시험). 종료일 없는 정지의 복귀 규칙 합성 시험: 1개월 공백 후 재등장(이벤트 없음, 실데이터 3건 644914120·644914130·644914140 포함), 2개월 연속 공백(첫 달 고쳐 쓰기, RESUME 유효일 = 첫 공백 달 1일), 한 목록에만 남은 토큰(복귀 아님), 분석 끝 1개월 공백(`resume_unconfirmed`). 창 시작 전 집행정지는 `stay_flag`만 (합성 시험). 품목·오렌지북 연결률을 연도별·삭제 여부별·경로별로 보고. `product_role`(`reference`/`bioequiv`/NULL)·`role_source`·`is_bioequiv`·`role_conflict`, 출처별 원본 값 컬럼. 역할별 제품 수(연도별·삭제 여부별·`role_source`별), `role_conflict` 건수와 사례 보고. 합성 시험: `의약동등 = 6` → `reference` + `is_bioequiv`, 오렌지북 `제네릭의약품` 표시만 있는 제품 → NULL, 대조약 값 불일치 → 대체조제 값 채택 |
| **S3** 신호·이벤트 원장 | `signal_ledger`, `signal_outcome`, `event_ledger`, 대조표 | 표본 구간에서 F4·F7 숫자 재현. 모든 신호가 outcome 하나를 가짐. 실현되지 않은 신호의 날짜가 이벤트에 쓰이지 않음(테스트). `event_id`·`signal_id` 결정적. 스냅샷을 한 달씩 추가한 결과 = 한 번에 만든 결과 (`exposure_frac`·복귀 첫 달 `state` 고쳐 쓰기 포함). 새 신호 3개 합성 시험: `SUSPEND_SCHEDULED` 확인/미확인, `RESUME_SCHEDULED` 실현/새 정지로 미실현, `DELISTING_SCHEDULED` 삭제/코드 교체(F7)로 미실현. U6: 신호 달에는 이벤트 없음, 다음 달 금액 확인 뒤 STAY_END+PRICE_CHANGE. 두 목록 중복 신호가 하나로 합쳐지고 분모가 한 번만 세짐 |
| **S4** 분석 A | A1~A6 | A1 실현율 표(연도별, 구간 포함), A4 대상·제외 수 먼저. `outputs/validation/sample_60.csv` (라벨 비움). **사용자 라벨 후** 정밀도 계산 |
| **S5** 분석 B·C | B1, B2, C 순위표 | 한계 절이 결과보다 먼저. 표본 탈락 단계표 |
| **S6** 월간 리포트 | `make report MONTH=YYYY-MM` | 내용: 이번 달 신호(유형별, 과거 실현율 병기), 지난 신호의 결과, 급여정지, 삭제, 양도양수, 대체조제 그룹 순위. 같은 입력 → 같은 파일 해시. 스냅샷 없는 달은 "자료 없음". 대시보드용 CSV |
| **S7** 문서 | README, 2쪽 요약, 한계 문서 | 새 환경 `make all` 재현 로그. 요약은 §7의 공고 문구별 근거 링크 포함 |

**우선순위**: S0 → S1 → S2 → S3 → S4가 본론. 시간이 부족하면 S5를 B1만 남기고 줄인다.

## 7. 바로팜 공고와의 대응

| 공고 문구 | 근거 산출물 |
|---|---|
| 의약품 시장 전반에 대한 분석, 보고서 설계 및 자동화 | S6 월간 선행 신호 리포트 |
| 가설 검증을 통해 잠재적 이슈를 계량화하고 해결 방안을 도출 | A1·A4: "목록 수록 ≠ 급여 중", "예정 ≠ 실현"을 실현율과 사용통계로 계량화, 원장으로 해결 |
| 심평원 빅데이터 및 처방 데이터 활용 (우대) | 심평원 급여목록 × 건보공단 사용통계 결합 |
| SQL, Python | DuckDB SQL과 Python, 테스트 포함 |
| Tableau, Power BI (우대) | S6 추출 CSV로 대시보드 1개 |

유통·커머스 직무용 번역: 등재 = 입점, 등재 예정 = 입점 예고, 삭제 = 단종, 급여정지 = 판매 중지, 가격 예고 = 가격 변경 공지, 신호 실현율 = 공지 이행률, 관측일/유효일 분리 = 상품 마스터 변경 이력 관리.

## 8. 사용자가 할 일
1. `data/raw/` 배치 (`docs/DATA.md` §1).
2. S0에서 나온 새 비고 문형의 규칙 결정.
3. S4의 60건 라벨링.
4. `생동인정여부(공고번호)` 괄호 숫자의 의미 확인. 확인 전에는 ○ 여부만 쓴다.
