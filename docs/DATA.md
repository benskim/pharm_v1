# DATA — 입력 데이터

## 1. 폴더와 내용

| 폴더 | 내용 | 기간 | 쓰는 곳 | 비고 |
|---|---|---|---|---|
| `data/raw/benefit/` | 약제급여목록 월 스냅샷 | 2022-01 ~ 2025-12 | P1, P2 특징 | 병합 parquet 2개. `_original_check/`: 받은 원본 xlsx 4개월·CSV 2개월(대조용, 적재 안 함). `_superseded/`: 교체 전 파일(회귀 표본용) |
| `data/raw/substitute/` | 대체조제 목록 월 스냅샷 (원본 이름 "저가약 대체조제 장려금 대상 약제목록". 복제약 목록이 아니다. `PLAN_P1.md` §4) | 2022-01 ~ 2025-12 | P1 | 같음 |
| `data/raw/usage/` | 건보공단 의약품 사용통계 **원본 집계파일** | 2018-01 ~ 2025-12 | P1 검증, P2 목표 변수 | 받은 형식 그대로 |
| `data/raw/master/` | 2026 약가정보 2종. `dim_drug_code_map_26` (심평원 버전 = 설계 때의 `dim_drug_master_20260831`과 같은 파일), `dim_drug_info_26` (의약품안전나라 버전 = 설계 때의 `dim_drug_info`) | 최신본 | P1 연결, 속성표 | 시계열로 쓰지 않음 |
| `data/raw/price_2026/` | 비어 있음. 약가정보 2종은 `master/`에 있다 | | | |
| `data/raw/orangebook/` | 오렌지북(복제약) 2026 최신. `dim_orange_book` (= 설계 때의 `dim_generic_master`) | 최신본 | 공고대조약 표시 확인 (속성표). 1차는 오리지널을 구분하지 않는다 | 시계열로 쓰지 않음 |
| `data/sample/` | 설계에 쓴 표본 parquet 8개 (설계 때 파일명) | 2024-01 ~ 2025-12 | 회귀 시험 | `fact_cmpn_stat_2023~2025`는 사용자가 원본을 가공한 집계본 |

표본 파일: `benefit_snapshot_table_2425.parquet`, `substitute_snapshot_table_2425.parquet`, `fact_cmpn_stat_2023.parquet`, `fact_cmpn_stat_2024.parquet`, `fact_cmpn_stat_2025.parquet`, `dim_drug_master_20260831.parquet`, `dim_drug_info.parquet`, `dim_generic_master.parquet`. `precheck_v3.py`는 파일명 키워드(`benefit_snapshot`, `substitute_snapshot`, `dim_drug_master`, `fact_cmpn_stat_`)로 찾으므로 표본 폴더에서는 설계 때 파일명을 쓴다.

**원본 파일명 대응** (2026-11-14 배치 기준)
| 설계 때 이름 | `data/raw/` 실제 파일 |
|---|---|
| `dim_drug_master_20260831` | `master/dim_drug_code_map_26.parquet` |
| `dim_drug_info` | `master/dim_drug_info_26.parquet` |
| `dim_generic_master` | `orangebook/dim_orange_book.parquet` |
| 급여목록 스냅샷 | `benefit/benefit_snapshot_table_2223.parquet`, `_2425.parquet` |
| 대체조제 스냅샷 | `substitute/substitute_snapshot_table_2223.parquet`, `_2425.parquet` |
| 사용통계 원본 | `usage/cmpn_stat_YYYY(공개일)/MEDICINE_YYYYMM (공개일).csv` 96개 + 스키마 `usage/cmpn_scheme(2025.2.4.).xlsx` |

## 2. 사용통계 (건보공단)

**원본 집계 단위**: 처방 연월 × 성분 4자리 × 연령(10세 단위) × **성별** × 시도 × 요양기관종별 × 원내외. 값은 청구건수, 환자수, 청구수량, 청구금액.

**원본 컬럼** (`cmpn_scheme(2025.2.4.).xlsx`, 96개 파일 헤더 동일, UTF-8 CSV)
| 컬럼 | 뜻 | 값 |
|---|---|---|
| `STD_YYYYMM` | 처방년월 | YYYYMM |
| `WK_COMPN_4` | 주성분코드 (성분 4자리) | 문자열 4자리 |
| `AGE_G` | 연령대 | 0, 10, …, 90 (10세 단위) |
| `SEX_TYPE` | 성별 | 남성 1, 여성 2 |
| `SIDO` | 시도코드 (요양기관 주소) | 11 서울, 26 부산, 27 대구, 28 인천, 29 광주, 30 대전, 31 울산, 36 세종, 41 경기, 42 강원도, 43 충북, 44 충남, 45 전라북도, 46 전남, 47 경북, 48 경남, 50 제주, 51 강원특별자치도, 52 전북특별자치도 |
| `YOYANG_CLSFC_CD_ADJ` | 요양기관그룹 | 병원 이상 1, **의원 3**, 보건기관 7 |
| `OUT_IN_HOS` | 원내원외구분 | IN, OUT |
| `PRSCRPTN_TNDN_CNT` | 총 처방 건수 (명세서 건수) | |
| `PRSCRPTN_AMT` | 총 금액 (원) | |
| `PATIENT_CNT` | 총 환자 수 (칸 안에서 중복 제거) | 칸끼리 더하면 중복이 생긴다 |
| `PRSCRPTN_TOT_USE` | 총 처방량 | **생산·유통 단위(제품 개수)**. 투약 단위가 아니다 (사용자 확인 2026-11-14). 성분 안에서 규격이 다른 제품이 섞이면 수량끼리 단순 비교할 때 주의 |

**시도 코드 변경**: 강원 42(~2023-12) → 51(2024-01~), 전북 45(~2024-12) → 52(2025-01~). 행정단위 명칭 변경일 뿐 같은 지역이므로 **하나의 시도로 잇는다** (51→42, 52→45로 묶은 `sido_std`). 원본 코드는 `sido_raw`로 보존한다.

**공개 시차** (2026-11-16 사용자 확인, 앞의 같은 날 점검 문단을 대체): 사용통계는 1년 묶음 공개가 아니다. **발생 후 5개월 뒤 초본이 공개되고**(예: 11월 1일에 6월 초본), 이후 매달 과거 자료 수정본과 새 초본이 함께 공개된다. `data/raw` 사용통계(2018~2025)는 **2026-11-01에 받은 수정본**이다. 폴더명 날짜(2018~2020 → 2024.12.30., 2021 → 2025.04.29., 2022 → 2026.04.27., 2023~2025 → 2026.10.28.)는 그 판의 공개일일 뿐이다. `config/params.yaml` `usage_lag_months = 5` (P1 S6, P2 공통). 초본과 수정본의 차이는 판이 하나라 측정할 수 없다 (BACKLOG X11).

**비공개 규칙**: 처방 연월 기준 품목 수 또는 업체 수가 2개 이하인 성분(4자리)은 결과에서 제외되어 있다.

이 규칙이 만드는 문제와 대응:
| 문제 | 대응 |
|---|---|
| 오리지널 단독·2개사 시기가 없다. 첫 제네릭 진입 직전 구간을 볼 수 없다 | 첫 제네릭 진입은 사용통계로 검증하지 않는다. 목록 안에서만 분석한다 |
| 업체 수가 3곳을 넘는 달에 성분이 "나타난다" | 수요 급증으로 읽지 않는다. `first_observed_month`를 두고, 첫 관측월은 예측 학습·평가에서 뺀다 |
| 독과점 성분이 빠져 표본이 치우친다 | 모든 결과 앞에 커버리지(성분 수 기준, 목록 기준 금액 비중 기준)를 보고한다 |
| 세부 칸(시도×연령 등)에서 추가 비식별 처리가 있을 수 있다 | S0에서 확인한다 |

**가공본 `fact_cmpn_stat` 컬럼** (표본, 월 × 성분 4자리 × 원내외): `STD_YYYYMM`, `WK_COMPN_4`, `OUT_IN_HOS`, `PRSCRPTN_TNDN_CNT`, `PRSCRPTN_AMT`, `PATIENT_CNT`, `PRSCRPTN_TOT_USE`.
- 원본을 같은 단위로 다시 집계한 결과가 가공본과 같아야 한다 (S0 회귀 시험). 환자수는 칸끼리 더하면 중복이 생기므로 비교에서 제외하거나 별도로 표시한다.
- **설계 때의 가공본은 남아 있지 않다.** `data/sample/fact_cmpn_stat_2023~2025`는 2026-11-14 `scripts/build_sample.py`로 원본에서 다시 만들었다. 따라서 원본 대 가공본 대조는 예전 가공 과정의 검증이 아니라 재집계 코드의 일관성 확인이다. 독립 기준은 `reference/precheck_v3_output.md`의 숫자다.

## 3. 스냅샷 컬럼 (표본 기준)
- 급여목록: 연번, 투여, 분류, 주성분코드, 제품코드, 제품명, 업체명, 규격, 단위, 상한금액, 전일, 비고, 목록정비전코드, 주성분명, 소스파일명, 기준일자, 식약분류, 주성분개수
- 대체조제: 연번(급여목록상), 투여, 분류, 주성분코드, ①주성분 및 투여제형코드, 제품코드, ②대표코드, 목록정비전코드, 제품명, 업체명, 규격, 단위, 상한금액, 전일, 정렬순서, ②품목기준코드, 생동인정여부(공고번호), 생동대조약 여부, 생동인정 및 생동대조약 적용일, 의약동등, 비고(급여정지), 주성분명, 기준일자, 소스파일명
  - `의약동등` 값 (사용자 제공 2026-11-14): 1 = 식약처 생동인정 공고 품목, 4 = 생동대조약 공고 품목, 6 = 생동인정 및 생동대조약 공고 품목(중복)
  - `생동인정여부(공고번호)`의 괄호 숫자 (사용자 확인 2026-11-16): **식약처 생물학적동등성 인정품목 목록의 순번**이다 (출처: 식약처 "생물학적동등성 인정품목 26년9월.xlsx", 2026-10-28 등록, https://www.mfds.go.kr/brd/m_76/view.do?seq=16393). 순번이라 목록 버전마다 바뀐다. **키나 시점 간 비교에 쓰지 않고 ○ 여부만 쓴다** (`sub_bioeq`). 원본 값은 `sub_bioeq_raw`에 그대로 둔다.
    - 원장 점검 (`scripts/check_bioeq_number.py` → `outputs/s2/bioeq_number_*.csv`): 값이 있는 512,663행 모두 `○(숫자)` 형식. 제품 13,095개 중 2개월 이상 나온 13,053개의 **12,883개(98.7%)는 숫자가 한 번 이상 바뀌었다** (예: 651900330 루나팜정 2022-10 ○(8702) → 2024-01 ○(8064)). 바뀐 달은 매년 3·9월에 몰린다 (이어진 제품의 99.9% 이상이 바뀜). 2022-05, 2022-11 ~ 2023-04에는 일부만 바뀌었다. 같은 달 안에서 한 품목기준코드는 숫자 하나만 가지고, 같은 숫자를 가진 제품끼리는 모두 같은 품목기준코드다 (3,728쌍). 순번 해석과 맞는다.

### 급여목록 원본 형식 (2026-11-14 원본 대조, `scripts/check_benefit_originals.py`, 결과 `outputs/original_check/`)
**2022~2025-10 (원본 xlsx 형식. 원본으로 확인한 달: 2022-01, 2022-03, 2022-09, 2023-11)**
- 시트 1개, 열 13개: 연번, 투여, 분류, 주성분코드, 제품코드, 제품명, 업체명, 규격, 단위, 상한금액, 전일, 비고, 목록정비전코드. **주성분명 컬럼이 없다.**
- 주성분마다 **묶음 머리 행**이 있고, 그 아래에 그 성분의 제품 행이 이어진다. 머리 행은 연번·투여·분류·주성분코드가 있고, **제품코드 칸**에 영문 주성분명과 함량(예: `chloral hydrate   9.5g(0.1g/mL)`)이 있으며, 제품명·업체명·규격·단위·상한금액·전일·비고는 비어 있다. 엑셀에서는 녹색 음영이다 (2022: indexed 42, 2023-11: rgb `FFCCFFCC`).
  - openpyxl로 읽으면 병합 셀은 0개다. 주성분명은 병합 범위가 아니라 제품코드 칸 하나에 들어 있다.
  - **복합 성분은 성분마다 머리 행이 하나씩 있다** (한 묶음에 머리 행이 최대 33개).
- 머리 행 판별은 구조로 한다: 업체명·상한금액이 비어 있고 제품코드 칸이 9자리 숫자가 아님. 4개월 모두 녹색 음영과 100% 일치 (불일치 0행). 2022-01의 흰색 채움(theme 0) 제품 행 7개는 음영이 아니다.
- 연번은 머리 행과 제품 행에 함께 매긴다. parquet은 머리 행을 뺀 파일이라 연번에 빈 번호가 있다 (월 7,000여 개).
- `산정불가`는 원본에서 **상한금액 칸**에 있고 비고는 비어 있다. parquet은 비고 칸에 `산정불가`, 상한금액 NULL로 옮겨 두었다 (2022-01 63행 등. 값의 뜻은 같다).
- 2023-11 원본에는 같은 연번이 두 번 나오는 경우가 4개 있다 (머리 행·제품 행 1쌍, 제품 행·제품 행 3쌍).

**2025-11~ (원본 CSV 형식. 원본으로 확인한 달: 2025-11, 2025-12)**
- 열 14개: 연번, 투여, 분류, 주성분코드, 주성분개수, 주성분명, 제품코드, 제품명, 업체명, 규격, 단위, 상한금액, 전일, 비고. 헤더 `주성분개수` 안에 줄바꿈이 있다. UTF-8(BOM).
- 머리 행이 없다. 연번이 1~행 수로 빈 번호가 없고, 업체명이 빈 행 0, 제품코드는 모두 9자리.
- 복합 성분의 주성분명은 한 칸에 쉼표로 이어져 있다. parquet은 첫 성분만 있다 (2025-11 4,846행, 2025-12 4,858행).

**형식이 바뀐 달 (추정)**: parquet의 월별 연번 빈 번호는 2022-01~2025-10에 매달 7,047~7,643개, 2025-11·12에 0개다. `주성분개수` 값도 2025-11부터 있다. 원본으로 확인하지 못한 달(2022-02~2025-10 중 3개월을 뺀 달)은 이 빈 번호로 추정한 것이다.

## 4. 알려진 위험
- **컬럼이 파일마다 다르다.** 표본에서도 `목록정비전코드`는 2024년과 2025-09~10에만, `식약분류`는 2025-10에만, `주성분개수`는 2025-11~12에만 있었다. 적재기는 `config/schema_map.yaml`로 동작한다.
- **2022~2023 스냅샷은 표본에 없던 구간이다.** 새 비고 문형과 컬럼이 나올 수 있다.
- **제품코드 폭.** 설계 때 파일에서 2025-11, 2025-12는 앞자리 0이 빠져 있었다(8자리 2,357행). 2026-11-14 사용자가 준 원본 CSV로 확인한 결과 원본은 9자리이고 0 채움 값과 2,357행 모두 같았다. `benefit_snapshot_table_2425.parquet`을 복원한 파일로 바꿨고, 교체 전 파일은 `data/raw/benefit/_superseded/`에 두어 회귀 표본(`data/sample/`)에만 쓴다. 적재기의 규칙(숫자 8자리만 0 채움, 7자리 이하는 채우지 않음)은 그대로 둔다(교체 후 8자리 0행). 0 채움 후 숫자 9자리가 아니면 `row_kind = non_product`로 stg에 두고 note_token·원장에서 뺀다 (전체 기간 1행: 2022-09 `243001ACS`).
- **원본 기준일자 칸이 1일이 아닌 달이 있다** (급여목록 2023-04-08, 2023-09-05, 2025-03-04). 월 키는 YYYY-MM. **적용 기준일은 항상 그 달 1일이다** (사용자 결정 2026-11-15, NOTE_RULES §2). 원본 값은 `base_date_raw`로만 보존한다.
- **파일의 공개·수정 시점은 알 수 없다** (사용자 결정 2026-11-15). 사례: 2025-06 파일은 5/20 공개(6/1 적용), 6/16 수정("665000520 엘도코프캡슐 등 23품목에 대한 급여중지 사항을 반영", 사용자 제공 공지). `data/raw`의 2025-06 급여목록에는 이 수정분이 들어 있다 (경보제약 23개 제품 `급여정지(25.6.24.)`, 파일명은 `약제급여목록25.6.1.xlsx`). 아래 9차 문단의 "파일명 수정일"은 참고 정보이고 판정·근거에 쓰지 않는다.
- (2026-11-14 9차, 참고) **기준일은 파일명에서 온 명목 날짜다** (사용자 확인 2026-11-14). 파일은 그 달 안에 고쳐 배포될 수 있고 파일명의 기준일은 바뀌지 않는다. 스냅샷에는 기준일보다 뒤에 알려진 정보가 들어 있을 수 있다 (예: 2025-06 경보제약 정지 비고. 처분 공시 06-12, 복지부 안내 06-16). 급여목록 2022-01~2023-11 중 20개월은 파일명에 수정일이 있다(`_1.21.수정`, `snapshot_inventory.filename_revision_date`). 14개월은 수정일이 기준일보다 3~20일 뒤다. `is_future`·`state_asof`·`stay_flag`는 명목 기준일로 판정한다 (LIMITATIONS L4).
- **목록정비로 제품코드가 바뀐 시기**가 2022~2023에 있을 수 있다. `목록정비전코드`로 옛 코드와 새 코드를 잇는다.
- **2026 최신본은 생존 편향이 있다.** 그사이 삭제된 제품은 없다. 연결률을 보고한다.
- **분석 끝자락의 신호는 결과를 아직 모른다.** 2025년 말 신호는 추적 기간이 부족하다 (`PLAN_P1.md` §5).
- **코로나 시기 (2020~2021)** 는 P2 평가에서 따로 보고한다.

## 5. S0에서 확인할 것
1. 월별 스냅샷 보유표 (2022-01~2025-12): 있는 달, 없는 달, 파일별 컬럼·행 수·기준일·제품코드 길이 분포
2. 사용통계 원본: 파일 형식, 컬럼명, 코드값(시도, 요양기관종별, 연령, 원내외), 월 연속성, 키 중복, 비공개 칸의 표현 방식(행 없음 / 특수값)
3. 원본 → 가공본 재집계 대조: 행 수, 청구금액·청구건수·청구수량 합계. `PRSCRPTN_TOT_USE`가 청구수량에 해당하는지
4. 성분별 관측 연속성: 96개월 내내 관측된 성분 수, 중간에 나타난 성분 수, 사라진 성분 수
5. 2026 약가정보 2종과 오렌지북: 키 컬럼, 행 수, 급여목록 제품코드와의 연결률
6. 전체 기간 비고 문형 빈도표와 `NOTE_RULES.md`에 없는 문형

<!-- 원본 파일 목록 시작 (scripts/data_manifest.py 생성) -->

## 원본 파일 목록 (재현 대조용)

데이터는 저장소에 없다. 아래 표의 이름·크기·SHA-256으로 받은 파일을 대조한다(`python -I scripts/data_manifest.py --check`). 출처 URL은 저장소에 기록된 것만 적었고, 기록이 없는 것은 '확인 필요'다. 받은 날짜는 기록된 날짜, 없으면 파일 수정 시각이다(받은 날로 확인 필요).

| 폴더 | 출처 | 공식 URL | 받은 날짜 | 파일 수 | 크기 합 (바이트) |
|---|---|---|---|---|---|
| `data/raw/benefit` | 건강보험심사평가원 약제급여목록 월별 파일 (사용자가 병합한 parquet + 대조용 원본) | 확인 필요 | 파일 수정 시각 2026-11-14 ~ 2026-11-14 (확인 필요) | 11 | 59,354,013 |
| `data/raw/master` | 2026 약가정보 2종 (심평원 코드 대응표, 의약품안전나라 품목 정보) | 확인 필요 | 파일 수정 시각 2026-11-14 ~ 2026-11-14 (확인 필요) | 3 | 14,023,012 |
| `data/raw/orangebook` | 식품의약품안전처 의약품 동등성 인정 품목(오렌지북) 2026 | 확인 필요 | 파일 수정 시각 2026-11-14 ~ 2026-11-14 (확인 필요) | 2 | 683,586 |
| `data/raw/price_2026` | (비어 있음) |  | 파일 수정 시각 2026-11-14 ~ 2026-11-14 (확인 필요) | 1 | 88 |
| `data/raw/substitute` | 건강보험심사평가원 저가약 대체조제 장려금 대상 약제목록 월별 파일 (사용자가 병합한 parquet) | 확인 필요 | 파일 수정 시각 2026-11-14 ~ 2026-11-14 (확인 필요) | 3 | 9,703,615 |
| `data/raw/usage` | 국민건강보험공단 의약품 사용통계 (공공데이터포털) | https://www.data.go.kr/biz/dcat/metadata/15128709.do | 2026-11-01 (docs/DATA.md §2, 2026-10-28 공개 판 등) | 98 | 2,411,303,961 |
| `data/sample` | 설계에 쓴 표본 parquet (사용자 제공, 회귀 시험용) | 사용자 제공 | 파일 수정 시각 2026-11-14 ~ 2026-11-14 (확인 필요) | 9 | 33,738,587 |

| 파일 | 크기 (바이트) | SHA-256 |
|---|---|---|
| `data/raw/benefit/README.md` | 628 | `30ef6da0ab535dd8ff6f46fa5f6fd302ac0d3cfd71af62cc6fd048fecd60d811` |
| `data/raw/benefit/_original_check/1_2025-12-01_약제급여목록.csv` | 4,125,447 | `14f4de7a68e20250a55350f5742a7227a06ecf291a44dfc1f000b246a08ae9f6` |
| `data/raw/benefit/_original_check/2_2025-11-01_약제급여목록.csv` | 4,112,400 | `11d7991f35eb61b9fea9cb67262c63740c742d39a641c6f806da15044c224543` |
| `data/raw/benefit/_original_check/약제급여목록및급여상한금액표_(2022.1.1.)(25,054)_1.21.수정.xlsx` | 2,844,813 | `dd5b8aeb17635fc812fe5f5a14fd7e9c14595384894025b173064b15ed1bb9d5` |
| `data/raw/benefit/_original_check/약제급여목록및급여상한금액표_(2022.3.1.)(25,076)_3.18.수정.xlsx` | 2,783,003 | `92711b338a7a08b0b90ef9f29b3fa15ed914865ea546a8ff166c304ed1987433` |
| `data/raw/benefit/_original_check/약제급여목록및급여상한금액표_(2022.9.1.)(24,643)_공개_9.19.수정.xlsx` | 2,676,054 | `b6adba1eb7c3bacf6f9d17a78905235be1ac8a35b9b971604c1fb6c10fb20160` |
| `data/raw/benefit/_original_check/약제급여목록및급여상한금액표_(2023.11.1.)(22,906)_공개용_11.1.수정.xlsx` | 2,565,541 | `ac742820f76592ed091b5810460187b92b1cee673f573671d8b3b4a670192803` |
| `data/raw/benefit/_superseded/README.md` | 601 | `eb3ccbb00d89a8ee232a2560ef6c8a3c2873b3ca14b2d18df374cce04ce09352` |
| `data/raw/benefit/_superseded/benefit_snapshot_table_2425.parquet` | 13,141,536 | `dfc97730c62847c35ee6429340b6c241c81406c6fcb2d137db07b2acd16fd10d` |
| `data/raw/benefit/benefit_snapshot_table_2223.parquet` | 13,968,126 | `07a5494cf3e1cfb809f69a5423d77c9ae0da59641a8d9789de06edda3a9c4bc8` |
| `data/raw/benefit/benefit_snapshot_table_2425.parquet` | 13,135,864 | `bcf7e8c4f95db3f99b197018bfeb8018545707be98a0c42f87ac92b513d39a83` |
| `data/raw/master/README.md` | 88 | `9ccfe7b94f5d82fffff9a50331928c4f0696f6efddc0a5c189084fcf042cee5d` |
| `data/raw/master/dim_drug_code_map_26.parquet` | 9,082,601 | `548bd88de14031cc95d1910db456150368a3ec64788083b98048cf1e92e548e7` |
| `data/raw/master/dim_drug_info_26.parquet` | 4,940,323 | `12ee3b50a44bb0b474a0339991c3f418fb7f1f292362bc22e6ec87fa65463444` |
| `data/raw/orangebook/README.md` | 88 | `9ccfe7b94f5d82fffff9a50331928c4f0696f6efddc0a5c189084fcf042cee5d` |
| `data/raw/orangebook/dim_orange_book.parquet` | 683,498 | `4873c57a5d13239ea664bcb5ec120c29c864490bc3535f5c1adf93f85ed7951c` |
| `data/raw/price_2026/README.md` | 88 | `9ccfe7b94f5d82fffff9a50331928c4f0696f6efddc0a5c189084fcf042cee5d` |
| `data/raw/substitute/README.md` | 88 | `9ccfe7b94f5d82fffff9a50331928c4f0696f6efddc0a5c189084fcf042cee5d` |
| `data/raw/substitute/substitute_snapshot_table_2223.parquet` | 5,037,237 | `3515c17fdcc0573dede4bba643244da3c472102b3fa954dbe3f781e7b7228b28` |
| `data/raw/substitute/substitute_snapshot_table_2425.parquet` | 4,666,290 | `18c6304528cc5991ec6cd388aadde46916a22b4502f4eafecab918b46a1b1109` |
| `data/raw/usage/README.md` | 88 | `9ccfe7b94f5d82fffff9a50331928c4f0696f6efddc0a5c189084fcf042cee5d` |
| `data/raw/usage/cmpn_scheme(2025.2.4.).xlsx` | 12,163 | `8835e13a56e1130b0e9d10ce93d0139a28702274cc2f57182d2f7536a7ad9e1b` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201801 (2024.12.30.).csv` | 26,369,398 | `3a44426658a28dda0745bd63a94d0c7f66207f221dd5e31ec1443074bc52c41e` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201802 (2024.12.30.).csv` | 25,557,538 | `39bbe1d74caf18ea995b1ee37987653a1bdb67962a7288c3ca94c57eb3120d4d` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201803 (2024.12.30.).csv` | 25,124,822 | `4673e3d1d07e59ee216050ef5ac7f579fe3b23145b4a1cfc4c8d78e01fb3c307` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201804 (2024.12.30.).csv` | 25,029,442 | `81b197c0eda9ba2abcdc434b7f50c6d5286b1ea8df50661d988f5c728e9d8be9` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201805 (2024.12.30.).csv` | 25,206,446 | `e2d46b4dfa826d4e411bbc907502985f32136521a12ae99cd7ccd3ec3e247944` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201806 (2024.12.30.).csv` | 24,897,211 | `8aee349011ae6121ee58a1a4436360a8d164c6bfe195ac176c89b961579efe4f` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201807 (2024.12.30.).csv` | 25,081,200 | `b0bc6e57dcd38792868f9365b25dcc9d1278a0a6be4d1d223c2154bb14493b90` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201808 (2024.12.30.).csv` | 25,056,485 | `081b6e26007bdb3920f76de70a6ff137d1cb039242b1578b778be961433e7b84` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201809 (2024.12.30.).csv` | 24,812,681 | `d50cdf646c330d38367e1ff87eb46d5b4a37a8f591822483ffe7c13046a4079c` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201810 (2024.12.30.).csv` | 24,970,881 | `7fa786d68141e1c3183cbff3098728d4d200ec5c3bb6de2fe1131fe25bc10c07` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201811 (2024.12.30.).csv` | 25,008,530 | `86eda899b65553f44500a1f29d3502ccb8c45ad7f5d12258dae3a2da9caa22c5` |
| `data/raw/usage/cmpn_stat_2018(2024.12.30.)/MEDICINE_201812 (2024.12.30.).csv` | 25,079,320 | `8d6f573d347540286b94639e0117c39073d70a5d436217d7dea7bc63ece13f14` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201901 (2024.12.30.).csv` | 25,336,706 | `e80f8592f05b2405f627ac01186c6194132e6b6770cb5fe4434263fd3ed6f737` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201902 (2024.12.30.).csv` | 24,581,028 | `479698403458e6fe4e3aaf46888ea874f2c19106af414670b0f07fa2625637a4` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201903 (2024.12.30.).csv` | 25,119,363 | `528ae4d206d0c986b3e627878d47f6aca59890eed7718653ed00ed9c614057fc` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201904 (2024.12.30.).csv` | 25,416,800 | `eca9c60ed47d5a886f2587b202750c2bce58299e04ed0f3e6e8229c7ee43a096` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201905 (2024.12.30.).csv` | 25,502,123 | `80861f7fbc40d4b63dbd09d5c240ce7dd1d91cc3be7901d8da7bacc0116bf4e5` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201906 (2024.12.30.).csv` | 25,054,867 | `cca715c7dc627aba86d53f11e1db07c12cb29b0ae8a0e8bdeb04ccd53804fc29` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201907 (2024.12.30.).csv` | 25,417,906 | `e0670d729442b6cc5c89fc22cda76d2211b824412dd90e4725bb62ff75e4297e` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201908 (2024.12.30.).csv` | 25,369,476 | `2be3005d7d6d17cbff68b4ef47cd4f9398cb1468a040ff858652ece890867cad` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201909 (2024.12.30.).csv` | 25,268,713 | `87e336f6df0f07aad62ad6f6598a03250a1c04066b80f3831bb530667c4bae23` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201910 (2024.12.30.).csv` | 25,277,532 | `6c39519efc85ada5820b098a544d3f30f76b4becbed9455698d671ab55a3d6cd` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201911 (2024.12.30.).csv` | 25,231,467 | `c8e53a1f98f7a5168789b78dddb6f688ddb79a1a3f667e33dd27cc1a5aef1070` |
| `data/raw/usage/cmpn_stat_2019(2024.12.30.)/MEDICINE_201912 (2024.12.30.).csv` | 25,367,997 | `efdc2cab8c48caa3101be078afaa8565093751dfb3d4c14489a91bd47c2ae209` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202001 (2024.12.30.).csv` | 25,397,730 | `be93621b60c60c80174ceaede464f40867aa8d62f73c56b5a5e17afea95a5be2` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202002 (2024.12.30.).csv` | 24,621,204 | `5f4adbe5794c0c7d1e19dc70f867f85d8a43f0ceb41aa5e15e5e03017f8de2bd` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202003 (2024.12.30.).csv` | 24,007,458 | `7f7439c9df1e888c407d98cde02944e11fe81433952ff0fba024d7c7af654c80` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202004 (2024.12.30.).csv` | 23,937,454 | `bbd35b3c0776d3f45e9d84a0ed37e821444687d3e8768e1a794accd988f9083a` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202005 (2024.12.30.).csv` | 24,142,120 | `ebe73dabe1fc5612c984c442f43a692b34e4a8c900e91b48965a42ad58b27e52` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202006 (2024.12.30.).csv` | 24,346,058 | `8b18898873ec22840c09853aaea0e406f81162c531dabd161f779fc7c3d6e7b0` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202007 (2024.12.30.).csv` | 24,569,959 | `eafe0b042ac96abae41770ce8ca3e6219ed248188b1276895b54d36fb5c5f2c2` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202008 (2024.12.30.).csv` | 24,623,890 | `7249a890cac9f048dc8ba8aa3e6a371b47e7c9cfa48d0c21fea4426e9b6b9cb9` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202009 (2024.12.30.).csv` | 24,674,291 | `8d42c76007cc872189dc5187197d46f8ff34befa71bb71419cda652f2935ffaa` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202010 (2024.12.30.).csv` | 24,567,165 | `b8f7d61f73b349c12d4a5b2fa69d5b6f384c592a701e9030ed00cf1b905c9d54` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202011 (2024.12.30.).csv` | 24,496,944 | `5212ef008712fb40b505ae510cd8942517891b5cd13021cb6f4c35edb9f60e6d` |
| `data/raw/usage/cmpn_stat_2020(2024.12.30.)/MEDICINE_202012 (2024.12.30.).csv` | 24,443,965 | `6cef6e2723c5db94a990b9e25824e5019a23b4ef9f7c687677cafc99904dc42c` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202101 (2025.04.24.).csv` | 24,164,159 | `8e3c0b006b3298575dec2bc9f0318f7ccd2f58161411cee9b93648a77a26cc5b` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202102 (2025.04.24.).csv` | 23,932,071 | `2b3f54d68864843ff5cb8b345752bee767df6b8101e7cc7131a7ea37c9531bef` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202103 (2025.04.24.).csv` | 24,512,966 | `d889e86a7ed719f8312dce9fcd1421178494d1da97da37f5ead4f30d2ee981da` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202104 (2025.04.24.).csv` | 24,534,588 | `53b487819cc5777e80d5df4f931ad0e985c8590b62b069e7a1389954ff8886b9` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202105 (2025.04.24.).csv` | 24,449,576 | `991aac5589357b095d4f107b0cc148008a68f530edaff4cf95c740ccb89d6f57` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202106 (2025.04.24.).csv` | 24,622,589 | `281bc0612dcf910215c8a3ba663e31074331d91bda1f8e7b59133b24c4c08280` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202107 (2025.04.24.).csv` | 24,624,096 | `5c340e70bac2a5eaa0d08a6b1a2912dd228db9908149f53a4659717a9cc35580` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202108 (2025.04.24.).csv` | 24,479,458 | `796e479ce2205ce672fe74bc8dbde2ab0cb036f08ed0a2c3cdfc8a2cdefa7c72` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202109 (2025.04.24.).csv` | 24,568,494 | `6cac4ae90d0d5f5c62ba4db79dd1626adfb607d8fbe6a6ca474a6aa21ee9386e` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202110 (2025.04.24.).csv` | 24,622,082 | `7ebbe3b866f8fa5b0f62f2a2915cbaf93e0e5224d7c13e31a3a9eda1d7daf5c7` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202111 (2025.04.24.).csv` | 24,639,229 | `427e36b0280e8af527c5ad5e1791295e48d9f10e71c1f287aaac4e514fc4999c` |
| `data/raw/usage/cmpn_stat_2021(2025.04.29.)/MEDICINE_202112 (2025.04.24.).csv` | 24,384,628 | `cf19b7d916fe5a249ac406241c2b45991196a36ce2ea912f3ac06fb894b80ea8` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202201 (2026.04.27.).csv` | 24,270,952 | `c03a5d27632aa567e100822da9165bd818c7357c1812ee81134025fb135e31f1` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202202 (2026.04.27.).csv` | 23,950,773 | `42a9ac076fd19712e20ad95a3c1fec2964a8e955065489f95ebcdc8a61131a82` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202203 (2026.04.27.).csv` | 24,494,109 | `45245126970347cf575309a518a81e84361e503b7349b01476c4361918f960fb` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202204 (2026.04.27.).csv` | 24,557,898 | `3468a1c771cb220317eba5c865f819b1e6e5f7eeb65a0f0b7dc945510dfe7b13` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202205 (2026.04.27.).csv` | 24,565,213 | `789f0b8972127453998b1427cc03f420978c70128ff3eb043b5b0476da07981e` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202206 (2026.04.27.).csv` | 24,487,045 | `f2b7f98c97c78c3495ac2ece5cbe750668c71bce89fdd8394b75cf589098efc8` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202207 (2026.04.27.).csv` | 24,799,794 | `de1317e9bcfebbecd7f1575d745bc290147a71b934d76164330dab2301e117f4` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202208 (2026.04.27.).csv` | 25,037,601 | `87ea2e288f927bdca276561024318e4725a323844aaf7f741c96ab5d9fef1df2` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202209 (2026.04.27.).csv` | 24,642,151 | `ff40b6bf7e9942e24928640a7d731bda01379f67cda7ea2956afdbe43b5c5599` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202210 (2026.04.27.).csv` | 24,591,705 | `693262cc9857de64222b8a81874747ea20757d05d67b71a4aec6d79d5ed83c12` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202211 (2026.04.27.).csv` | 24,836,413 | `4edef77dee4f3d2a1492ba9ea81b8948e622ca43e220b03ffce500b342cfc9b6` |
| `data/raw/usage/cmpn_stat_2022(2026.04.27.)/MEDICINE_202212 (2026.04.27.).csv` | 24,943,551 | `1a23d2c53a8a6d9ef0ef28a6cea2f06177822b3daac870796e414e5215f4d6de` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202301 (2026.10.28.).csv` | 24,906,915 | `2fea8cc69b4162ded016ed764c4e46a568bfe66462bac2ebd3e1ec3a844ffd22` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202302 (2026.10.28.).csv` | 24,545,625 | `4ea9e69dc4f32b53fece467bd9b9cf3e057e313989ade421be3ce55a285282aa` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202303 (2026.10.28.).csv` | 24,888,766 | `6167c0c087eede816d104c8eecc542cabe4e1f4970106e72d02db06acab46f65` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202304 (2026.10.28.).csv` | 25,000,557 | `0f3131b36a26d1f3e050fabee4855e397f89e07e5b8c024943c5321c901119f4` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202305 (2026.10.28.).csv` | 25,539,316 | `ea1545840eaedb6d0c074ebffe9cb3fb64735882169e3ad4139a39dfdc08f79c` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202306 (2026.10.28.).csv` | 25,384,593 | `ca3ef5721fc91c8e82c03d2653204c4e68e85ca08aa97c8bf5d446f3d6632972` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202307 (2026.10.28.).csv` | 25,401,001 | `3cf6e7a92077689106c9ac016de128a294c89b70296d9519c7f7fc411fcadf6f` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202308 (2026.10.28.).csv` | 25,491,939 | `420b1a9f87bcf21310f39d341d96c6de34c7e70e873b18c5e8d10090f4b80de2` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202309 (2026.10.28.).csv` | 25,238,281 | `64bdf4fdf4745b34035b01f3dd0c46a9bc7e3379b56a292938134509540a86ac` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202310 (2026.10.28.).csv` | 25,505,893 | `1d354794496520104b19f78a5b136d206be5256e71cd01599d834cf4f892a881` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202311 (2026.10.28.).csv` | 25,645,088 | `22754bc12fe9639d84ef387268d7baf1a9cc9d5d2f99a194571b0a464e45ce54` |
| `data/raw/usage/cmpn_stat_2023(2026.10.28.)/MEDICINE_202312 (2026.10.28.).csv` | 25,644,954 | `29da63bf83ce2e33ac49ae744f0ce470d85b41d44641f0cdb9faffae1bb8e574` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202401 (2026.10.28.).csv` | 25,962,270 | `f982180e706403cbb4c12c79b1bb6b19529d49c2304c84026ac89adea4d18e54` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202402 (2026.10.28.).csv` | 25,629,670 | `f32b2885963cccc1312160abb8f322432dee283aa34604e7a8b7700676717d75` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202403 (2026.10.28.).csv` | 25,663,289 | `9af3eff6315b2199345ddfec2fb4347c2a61bdae089fcf5af1bbb3bb42cf319a` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202404 (2026.10.28.).csv` | 25,882,138 | `dca186092869e3902d5b74b5357947e1b4acc28d3ff6f2acbd56c11b0cab925b` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202405 (2026.10.28.).csv` | 25,996,666 | `65715d5f168670d965da56df889afbc4ee89bacc6108e2e2858da7f557a741d9` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202406 (2026.10.28.).csv` | 25,400,095 | `cb2caf40e2b103bc72a23c1f35ecff163c24207b9b878e179341ef89b6651104` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202407 (2026.10.28.).csv` | 25,819,731 | `053141052e343441fbf0c2b4ef13e5c1eef5ec34239a76b555d5692f5b548257` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202408 (2026.10.28.).csv` | 25,773,222 | `0b694fc64cb3011e9844aa1de4a77cf8a0796c4dbc0f191c904ebb0eac38bad8` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202409 (2026.10.28.).csv` | 25,507,521 | `53fa90847fa702ae14959bd776c966b6eb5797ec3ae82d6cc31fd3627e824270` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202410 (2026.10.28.).csv` | 25,670,840 | `660471804a77551d7da8c5aabaea4e55ae1f8542ccc16207d785898c6584c057` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202411 (2026.10.28.).csv` | 25,729,649 | `cc82092488b1788ec9bbabb86500a3483f45375860e65df672820f505ab41f5c` |
| `data/raw/usage/cmpn_stat_2024(2026.10.28.)/MEDICINE_202412 (2026.10.28.).csv` | 25,845,870 | `688c20e8b75c62519d5c45d073f8ca0091739aaa02050a8aa17fa883b171655e` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202501 (2026.10.28.).csv` | 25,778,085 | `f7a55fd325890635ea120eef10e0eadfbdef78b3a1a5bfe69900aef3385828cb` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202502 (2026.10.28.).csv` | 25,610,133 | `3c030e19ad610ae618b6a8ff4ac06901b0c2a0678b131b96e45604c335678d50` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202503 (2026.10.28.).csv` | 25,675,315 | `4a298ec6d5c205985622ae712e292c7b33d15cfff6dea6d1e0b355f3120fa3e7` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202504 (2026.10.28.).csv` | 25,950,125 | `45b56e93d849388588588e24e54f9f093edb6742680ad82639a1c74070e75355` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202505 (2026.10.28.).csv` | 26,124,709 | `86fef2e99d22b1110642edbffcdd0022a429fa78de25ac0d4135368a85577cc3` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202506 (2026.10.28.).csv` | 25,805,780 | `3ffbff62bdbe80c21bb33863a3fb4ed503e845539451ad6d32d1101d8c40043d` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202507 (2026.10.28.).csv` | 26,190,333 | `ac9c773aacf5cccce4a62bdb702ce6296e1e781d1632e8f1e967a2f8fd5862d6` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202508 (2026.10.28.).csv` | 26,033,829 | `d6f0f4520932d6e956fe24289346288b358a737247201ecd66adf8867038aa03` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202509 (2026.10.28.).csv` | 26,189,657 | `8ccd5e15cfc18c9047acc44d4680d8e979e72f4e201bb284a96e7b7b7b7dcd72` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202510 (2026.10.28.).csv` | 25,929,961 | `c6474b30ddfd91a21c1ae9eecfe103b3725d1363674a24ce6acd2ff47b7748f9` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202511 (2026.10.28.).csv` | 26,083,213 | `7559566c55623974fdff273449fe915c7de63f0d1578bfa436dfa43fe2a8ba0a` |
| `data/raw/usage/cmpn_stat_2025(2026.10.28.)/MEDICINE_202512 (2026.10.28.).csv` | 26,169,370 | `8b04292ef80f60fdbb30cac4e199165fd40c0a482d69064ffefd846b70ec1155` |
| `data/sample/README.md` | 101 | `9b4ae7a31248d516758c55f544f3f5f40a1649d717e9492587423f7e789c38a5` |
| `data/sample/benefit_snapshot_table_2425.parquet` | 13,141,536 | `dfc97730c62847c35ee6429340b6c241c81406c6fcb2d137db07b2acd16fd10d` |
| `data/sample/dim_drug_info.parquet` | 4,940,323 | `12ee3b50a44bb0b474a0339991c3f418fb7f1f292362bc22e6ec87fa65463444` |
| `data/sample/dim_drug_master_20260831.parquet` | 9,082,601 | `548bd88de14031cc95d1910db456150368a3ec64788083b98048cf1e92e548e7` |
| `data/sample/dim_generic_master.parquet` | 683,498 | `4873c57a5d13239ea664bcb5ec120c29c864490bc3535f5c1adf93f85ed7951c` |
| `data/sample/fact_cmpn_stat_2023.parquet` | 396,496 | `e79539a800c495f2d734e84c08ad2fb4f1a775d585fea67c0cae9ab59a84defc` |
| `data/sample/fact_cmpn_stat_2024.parquet` | 411,724 | `c59ea0887ba1c5dbab9f80abcfb0f8c21af10c1d57ab0f53df46cd1575e8dba2` |
| `data/sample/fact_cmpn_stat_2025.parquet` | 416,018 | `cdd7f4b1618b2e4b9702f0302967610de4ca6911e7f8da0ff8eb242b48441462` |
| `data/sample/substitute_snapshot_table_2425.parquet` | 4,666,290 | `18c6304528cc5991ec6cd388aadde46916a22b4502f4eafecab918b46a1b1109` |

<!-- 원본 파일 목록 끝 -->
