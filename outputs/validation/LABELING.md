# sample_60.csv 라벨 기준 (P1 S4 A6)

`sample_60.csv`의 60행마다 원장이 주장한 것(`ledger_claim`)이 원자료와 맞는지 적는다. 원자료는 `data/raw/benefit/`·`data/raw/substitute/`의 스냅샷이다. 각 행의 `month`·`look_month`·`product_code`로 찾고, `notes_3m`(확인 달까지 3개월의 비고 원문, B 급여목록 / S 대체조제)과 `prices_3m`(상한금액)을 먼저 본다.

다 채운 뒤 `make p1-s4-precision`을 실행하면 `precision.csv`가 나온다. `label_type_correct`가 하나라도 비어 있으면 계산하지 않는다.

## 라벨 3칸

| 칸 | 뜻 | Y | N | 모름 |
|---|---|---|---|---|
| `label_type_correct` | 원장의 **유형·종류**가 맞는가. 이벤트 행은 event_type과 kind(예: PRICE_CHANGE unsignaled, DELISTING exit), 신호 행은 outcome과 사유(예: not_realized / delisted) | 원자료로 같은 결론이 나온다 | 원자료와 다르다 (무엇이 다른지 `label_note`에) | 원자료만으로 판단할 수 없다 (외부 자료가 필요하거나, 원본 행이 이상함) |
| `label_date_correct` | 원장의 **날짜**가 맞는가. 이벤트는 유효일, 신호는 신호 날짜와 확인 달 | 날짜가 맞다. **구간으로 기록된 행(삭제)은 실제 삭제일이 구간 안에 있으면 Y** | 날짜가 다르다 (맞는 날짜를 `label_note`에) | 실제 날짜를 원자료로 알 수 없다 |
| `label_note` | 메모 (선택) | | | |

- 날짜를 판정할 것이 없는 행(예: ATTR_CHANGE의 종류만 보는 경우)은 `label_date_correct`를 비워 둔다. 비운 칸은 분모에 들어가지 않는다.
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
- `sample_60.csv`는 `make p1-s4`로 다시 만들 수 있다 (시드 고정, 같은 60행). **라벨을 채운 파일은 덮어쓰지 않도록 따로 복사해 두고**, 다시 만든 뒤에는 라벨을 옮겨 붙인다.
