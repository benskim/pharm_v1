# sample_60.csv 라벨 기준 (P1 S4 A6)

`sample_60.csv`의 60행마다 원장이 주장한 것(`ledger_claim`)이 원자료와 맞는지 적는다. 원자료는 `data/raw/benefit/`·`data/raw/substitute/`의 스냅샷이다. 각 행의 `month`·`look_month`·`product_code`로 찾고, `notes_3m`(확인 달까지 3개월의 비고 원문, B 급여목록 / S 대체조제)과 `prices_3m`(상한금액)을 먼저 본다.

다 채운 뒤 `make p1-s4-precision`을 실행하면 `precision.csv`가 나온다. `label_type_correct`가 하나라도 비어 있으면 계산하지 않는다.

## 라벨 작성자 (`labeler`)
| 칸 | 뜻 |
|---|---|
| `labeler` | `claude` (1차 라벨) / `user_reviewed` (사용자가 원자료와 대조해 검토한 행). 모든 행에 둘 중 하나 |
| `claude_label_type_correct`, `claude_label_date_correct` | 1차 라벨. 1차에 `label_*`와 같은 값을 쓰고, 검토할 때는 **고치지 않는다** |
| `label_type_correct`, `label_date_correct` | 최종 라벨. 검토에서 다르게 보면 이 칸을 고친다 |
| `label_note` | 판정 근거 (어느 달·어느 목록의 비고·금액을 봤는지, N이면 무엇이 다른지) |

검토 흐름: 행을 검토하면 `labeler`를 `user_reviewed`로 바꾸고, 1차와 판단이 다르면 `label_*`와 `label_note`를 고친다. 1차 라벨 없이 처음부터 채워도 된다 (그 행은 1차와 비교하지 않는다).
`make p1-s4-precision`은 정밀도를 전체(all)와 검토한 행(user_reviewed)으로 따로 내고, 1차와 검토 라벨이 다른 행을 `label_review_diff.csv`에 쓴다.

## 근거 출처 (`label_source`, 2026-11-15 사용자 결정)
| 값 | 뜻 |
|---|---|
| `raw` | 원본(`data/raw`)만으로 판정 |
| `external` | 원본으로 판단할 수 없는 부분을 공식 고시·공지나 보도로 판정. 출처(문서 번호, 날짜, 파일 경로·링크)를 `label_note`에 적는다 |

- 원본으로 판단할 수 없고 외부로도 확인되지 않으면 "모름".
- 정밀도는 원본만(`raw_only`)과 외부 포함(`external_included` = 전체)으로 따로 나온다.
- 외부 문서 파일은 `docs/evidence/`에 둔다 (목록 `docs/evidence/README.md`).

## 라벨 3칸

| 칸 | 뜻 | Y | N | 모름 |
|---|---|---|---|---|
| `label_type_correct` | 원장의 **유형·종류**가 맞는가. 이벤트 행은 event_type과 kind(예: PRICE_CHANGE unsignaled, DELISTING exit), 신호 행은 outcome과 사유(예: not_realized / delisted) | 원자료로 같은 결론이 나온다 | 원자료와 다르다 (무엇이 다른지 `label_note`에) | 원자료만으로 판단할 수 없다 (외부 자료가 필요하거나, 원본 행이 이상함) |
| `label_date_correct` | 원장의 **날짜**가 맞는가. 이벤트는 유효일, 신호는 신호 날짜와 확인 달 | 날짜가 맞다. **구간으로 기록된 행(삭제)은 실제 삭제일이 구간 안에 있으면 Y** | 날짜가 다르다 (맞는 날짜를 `label_note`에) | 실제 날짜를 원자료로 알 수 없다 |
| `label_note` | 메모 (선택) | | | |

- 날짜를 판정할 것이 없는 행은 `label_date_correct`를 비워 둔다. 비운 칸은 분모에 들어가지 않는다. **ATTR_CHANGE도 날짜를 판정한다** (사용자 결정 2026-11-15): 비고 없이 바뀐 행의 유효일은 그 스냅샷이 속한 달의 1일이다 (제품명 변경 포함).
- **전달 파일의 예고가 이번 파일에서 실현된 경우**(`signal_confirmed`)는 이번 행에 비고가 없어도 예고일을 유효일로 쓴다 (사용자 확인 2026-11-15). 금액이 예고와 다르면(realized_partial) 그 달 1일.
- "모름"은 분모에서 빼고 따로 센다. Y·N·모름 외의 값이 있으면 스크립트가 멈춘다.
- **구간 길이**: 삭제 행은 `interval_days`(구간 lo~hi의 일수)가 있다. 맞아도 구간이 지나치게 넓으면(예: 빠진 달을 건너뛴 경우) 쓸모가 적으니, 60일을 넘으면 `label_note`에 적는다.

## 예시 (지금 sample_60.csv의 행)

**`label_type_correct` — sample_id 28** `PRICE_CHANGE:643105170:2024-11`, 원장: `PRICE_CHANGE unsignaled 유효일 2024-11-01 (snapshot_first)`
- `prices_3m` = 2024-09:893 | 2024-10:893 | 2024-11:858, `notes_3m`의 비고는 세 달 모두 빈칸.
- 상한금액이 2024-11에 바뀌었고 예고 비고가 없다 → 유형 PRICE_CHANGE, 종류 unsignaled가 맞으면 **Y**.

**`label_date_correct` (구간) — sample_id 7** `DELISTING:645604500:2025-01`, 원장: `DELISTING exit 유효일 2024-12-02~2025-01-01 (interval)`, `interval_days` = 31
- `prices_3m` = 2024-11:415 | 2024-12:415 | 2025-01:없음 → 2024-12 스냅샷에는 있고 2025-01 스냅샷에는 없다.
- 원장이 주장한 것은 "2024-12-02 ~ 2025-01-01 사이에 삭제"다. 원본 파일로 이 구간 밖의 삭제 근거(예: 2024-12 파일 비고의 다른 삭제일)가 없으면 **Y**.

**`label_type_correct`·`label_date_correct` (신호) — sample_id 52** `SUSPEND_SCHEDULED:658603520:2022-09-08`, 원장: `SUSPEND_SCHEDULED 2022-09-08 → realized_partial`
- `notes_3m` = 2022-09 B:`급여정지(22.9.8.)` | 2022-10 B:`급여정지(22.9.9.)` S:`급여정지(22.9.9.)`.
- 9/8 예고 뒤 첫 스냅샷(2022-10)에 정지 비고가 있고 정지일이 하루 다르다 → 결과(realized_partial)가 맞으면 `label_type_correct` **Y**. 신호 날짜 9/8과 확인 달 2022-10이 맞으면 `label_date_correct` **Y**.

## 정밀도 계산 (`scripts/s4_precision.py`)
- 비가중: Y / (Y + N), Wilson 95%.
- 층 크기 가중: 층 h의 라벨 행마다 가중치 (층 크기 N_h) / (그 층의 Y·N 행 수 n_h). 구간은 유효 표본 크기 n_eff = (Σw)² / Σw²로 Wilson 95%.
- 표본은 50개 층에 최소 1건씩 배분해서 큰 층(예: 비고 없는 가격 변경 9,412건)은 표본이 적다. 비가중 정밀도는 작은 층 쪽으로 쏠리므로 두 값을 함께 본다.
- 파일 (2026-11-16 사용자 결정): 최종 라벨 `sample_60.user_reviewed.csv`, 1차(Claude) 라벨 `sample_60.claude_labeled.csv`. 둘 다 저장소에 있다. S4 시험·정밀도 계산(`make p1-s4-precision`)은 user_reviewed 파일을 읽는다. `make p1-s4`는 이 파일을 덮어쓰지 않고, 같은 기록으로 다시 채운 표본을 `sample_60.new.csv`에 (최종 라벨 파일이 없으면 새 표본을 `sample_60.generated.csv`에) 쓴다. 아래의 `sample_60.csv`는 지금 user_reviewed 파일을 가리킨다.

## 1차 라벨(claude)에서 쓴 판단 기준 (2026-11-15, 검토 대상)
- 원자료 = `data/raw/benefit/`·`data/raw/substitute/`의 parquet와 `data/raw/master/` code_map을 **직접** 조회했다 (파이프라인 표 stg_*·event_ledger를 쓰지 않음). 근거는 행마다 `label_note`.
- **`snapshot_first`(관측월 1일) 날짜는 Y** (사용자 결정 2026-11-15): 급여목록은 매월 1일 적용이라 비고 없는 변화는 그 달 1일 적용분에 들어 있다. 추정이 아니라 목록 규칙이므로 **이 16행(sample 16·17·18·20·24·27·28·29·30·31·41·42·43·58·59·60)의 날짜 라벨 Y는 확정**이다 (사용자 확인 2026-11-15). 월중에 수정된 항목은 비고에 날짜가 남아 비고 날짜 규칙(유효일 규칙 2)으로 잡힌다. 기준만 정했고 행별 검토는 아니라 `labeler = claude`.
- **비고 날짜가 "고시"일인 행**(sample 25·44: `25.11.20. 고시`): 원장은 고시일을 유효일로 쓰지만 원본에 적용일이 없어 날짜를 "모름"으로 했다.
- **외부 근거로 정한 사유**(sample 51, `reason_source = external`): 미실현은 원본으로 맞지만 사유를 원본으로 판단할 수 없어 유형을 "모름"으로 했다.
- **2차 점검** (같은 날): 원본에서 규칙(F7 재분류 순서, 확인 스냅샷 = 신호 날짜 이후 첫 스냅샷(같은 날 포함), 유효일 규칙 1~7)을 다시 적용하는 점검 스크립트로 60행을 다시 계산했다. 60행 모두 원장 주장과 같았고 라벨은 바뀌지 않았다. 신호 15행은 확인 달·발행 달(비고가 원본에 처음 나타난 달)도 대조했다.
- **적용 기준일은 항상 그 달 1일** (사용자 결정 2026-11-15). 원본 기준일자 칸(2023-04-08, 2023-09-05, 2025-03-04)과 파일명 수정일은 참고로만 적고 판정 근거로 쓰지 않는다 (29·49행).

## 사용자 검토 반영 (2026-11-15)
| sample | 1차 → 최종 | labeler / label_source | 근거 |
|---|---|---|---|
| 21 | Y/Y 유지 | user_reviewed / raw | 외부 확인(항소 포기로 집행정지 종료, 7/15 인하, Dailypharm 2022-07-18). signaled와 stay_released가 겹치면 규칙상 signaled. STAY_END:626700122:2022-07 있음 (누락 아님) |
| 25, 44 | 날짜 모름 → Y | user_reviewed / external | 보건복지부 고시 제2025-187호(2025.11.20.), 대한병원협회 보험 제2025-423호, 시행일 2025.11.20. (`docs/evidence/보험_제2025-423_1_고시.pdf`) |
| 51 | 유형 모름 → Y | user_reviewed / raw | 원장 수정: 주장이 `not_realized / stay` → `not_realized / note_gone`. 외부 원인은 `reason_external` |
| 28·29·31 | Y/Y 유지 | user_reviewed / raw | 비고에 별도 예고 날짜가 없으므로 시행일은 인하 금액이 기록된 달의 1일 (`review10_evidence.md`로 검토) |
| 7·9 | Y/Y 유지 | user_reviewed / raw | 목록에서 빠진 달의 1일이 시행일 = 원장 구간의 끝 날짜 |
| 16 | Y/Y 유지 | user_reviewed / raw | 목록에 등록된 달의 1일이 시행일 |
| 22·35 | Y/Y 유지 | user_reviewed / raw | 예고일이 있어 그날 시행 |
| 2 | Y/(비움→Y) | user_reviewed / raw | 업체명만 다름: 양도양수가 아니라 회사 이름 개명 |
| 59 | Y/Y 유지 | user_reviewed / raw | 같은 품목, 함량 표기 방식만 바뀜 (6.39mg 에스시탈로프람옥살산염 실제 질량 = 5mg 에스시탈로프람 환산 질량) |

## 정답 칸 (2026-11-18 사용자 지시, `scripts/v5_labels.py`)
라벨이 원장 버전에 묶이지 않도록 행마다 정답을 둔다. `label_*`은 원장 주장(`ledger_v5_*`)과 정답을 비교해 다시 계산한 값이다. 개정 전 원장(v4)과 비교한 값은 `label_*_v4`, 이전 라벨은 `prev_*`에 남긴다.
| 칸 | 뜻 |
|---|---|
| `truth_type` | 이벤트 `EVENT_TYPE\|kind` (예: `SUCCESSION\|transfer`, `DELISTING\|delisting_spec`), 신호 `SIGNAL_TYPE\|FINAL_OUTCOME[:사유]` + `\|blocked`(집행정지로 막힘), 기록이 없어야 하면 `NONE`. `*` = 결과값은 규칙대로 (사용자는 막힘만 판정) |
| `truth_date` | 유효일 ISO, 삭제 구간은 `lo~hi`, 신호는 `신호일;확인 달` |
| `truth_note` | 근거 |
| `labeler` | `user_reviewed` (사용자 판정 26행) / `rule_check` (`scripts/rule_check.py`가 data/raw만으로 RULES를 적용, 35행) |
v4 주장은 RULES §0 대응으로 이름을 바꿔 비교한다 (`docs/DECISIONS.md` 2026-11-18). 정밀도: `outputs/validation/precision_v5.csv`.
