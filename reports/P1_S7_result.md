# P1 S7 문서 결과 (2026-11-16)

범위 (PLAN_P1 §6 S7): README, 2쪽 요약, 한계 문서, 새 환경 `make all` 재현 로그. 이 단계는 새로 계산하지 않았다. 문서의 숫자는 `reports/`의 결과 보고서에서 가져왔고, 출처 절을 괄호로 달았다.

## 완료 기준
| 기준 | 결과 | 근거 |
|---|---|---|
| README | **완료** | `README.md`: 소개, 폴더, 데이터 배치, 설치·실행, 시험, 예시 리포트, 문서 지도 |
| 2쪽 요약 (공고 문구별 근거 링크) | **완료** | `docs/SUMMARY.md` (약 800어절), §7 공고 문구별 근거 표 |
| 한계 문서 | **완료** | `docs/LIMITATIONS.md`: 자료 / 규칙·판정 / 검증 / 리포트 / 사용통계 시차 5개 주제, 17항목. 항목마다 영향과 보고서 위치. L12~L17은 S6 보고·DATA.md·README에 이미 있던 한계를 옮긴 것이다 |
| 새 환경 `make all` 재현 로그 | **실패 기록** | `docs/REPRODUCE_LOG.md`. 깨끗한 클론에서 `make all`이 p1-s4에서 멈췄다. 지시대로 고치지 않았다 |
| 통과하지 못한 검정·실현되지 않은 신호를 요약에 넣는다 (STAGES S7) | **완료** | 요약 §1: 급여정지 예고 실현율 38.4%와 미실현 사유. §2: 정밀도가 독립 검증이 아니라는 점. §3: 같은 방향 비율 대 대조군 기준선 |

## 재현 결과 요약
- `make all` 337초, **p1-s4에서 실패**: `test_sample_60` (60 ≠ 61). 라벨을 채운 검증 표본 `outputs/validation/sample_60.csv`가 `.gitignore`의 `*.csv` 규칙 때문에 저장소에 없다.
- `make test` 217초: 2 실패 / 211 통과. 실패는 위의 것과, p1-s5가 돌지 않아 생긴 `test_s5` FileNotFoundError.
- `make report MONTH=2024-09` 25초, 성공. 리포트 해시가 커밋된 파일과 같다 (`91538cc5…`).
- S2·S3·S4 출력 CSV 33개와 precheck 출력이 작업 환경과 바이트 단위로 같다.
- `all`에 아직 구현하지 않은 P2 목표가 있어서, 위 문제를 고쳐도 p1-s6 다음에 멈춘다 (Makefile에서 확인).

## 문서와 달랐던 점
- LIMITATIONS L4의 "P2 신호는 M+1 origin부터(9차 유지)"와 L9의 기울기 문장을 이후 결정(2026-11-16)에 맞게 고쳤다. 새 한계는 아니다.

## 확인 필요
1. **라벨 표본 CSV**: `outputs/validation/sample_60.csv`를 저장소에 넣을까요 (`.gitignore` 예외)? 아니면 원본 데이터처럼 따로 받는 입력으로 README·DATA.md에 적을까요?
2. **`make all`의 P2 목표**: P2 구현 전까지 `all`에서 빼거나 `p1-all`을 따로 둘까요?

---

# S7 반영 (2026-11-17)

- 라벨 표본을 저장소에 넣었다: `outputs/validation/sample_60.user_reviewed.csv`(최종), `sample_60.claude_labeled.csv`(1차). S4 시험·정밀도는 user_reviewed를 읽고, `make p1-s4`는 그 파일을 덮어쓰지 않는다 (`sample_60.new.csv` / `sample_60.generated.csv`).
- Makefile `p1-all`, `all = p1-all`. LIMITATIONS L16 영향 칸 수정.
- SUMMARY.md 수정 (첫 문장을 가치부터, 실현율 표 기간 표시, A4 6묶음 한계, 누가 어떻게 쓰나, 공고 표 P2 행). 약 890어절이라 외부 확인 사례는 줄이지 않았다.
- **깨끗한 클론 재현 2차 (커밋 `116ea57`) 성공**: `make all` 613초, `make test` 213개 통과, `make report MONTH=2024-09` 성공. 리포트 2개와 S2~S4 출력은 작업 환경과 바이트 동일. S5는 23개 중 `b1_pairs_effective.csv` 1개가 3행의 소수 17번째 자리만 다르다 (REPRODUCE_LOG 확인 필요).

## 확인 필요
1. `b1_pairs_effective.csv`의 부동소수점 끝자리 차이를 없앨까요 (쌍별 값 반올림 또는 해당 집계 단일 스레드)? 요약 숫자는 같습니다.
