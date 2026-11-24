"""P2 v3 결과 보고서: outputs/p2v3/ 표를 읽어 reports/P2v3_group_result.md를 쓴다 (숫자는 모두 표에서 읽음).
초점: 군간 차이(군마다 다른 예측 모델을 만들 실험의 사전 자료, 사용자 2026-11-24).
python -m pbd.forecast_v3.report
"""
from __future__ import annotations

import json

import pandas as pd

from pbd.common import ROOT
from pbd.forecast_v3.group_compare import SMALL_N

OUT = ROOT / "outputs/p2v3"
REPORT = ROOT / "reports/P2v3_group_result.md"
LABEL = {"cls2": "약효분류 2자리", "atc2": "ATC 2단계"}
MNAME = {"wape_G": "G 오차", "log_wape_G": "G 오차(log)", "wape_B3": "B3 오차", "d_G_B3": "G − B3 오차 차이(원 척도, 참고)",
         "d_G_B1": "G − B1 오차 차이(원 척도, 참고)", "lr_G_B3": "log(G 오차 ÷ B3 오차)", "lr_G_B1": "log(G 오차 ÷ B1 오차)", "seas_strength": "계절성 강도", "trend_strength": "추세 강도",
         "annual_growth": "연간 증가율", "resid_sd": "잔차 sd", "winter_summer": "겨울/여름", "yoy_sd": "전년비 sd"}


def pct(x, d=1):
    return "" if pd.isna(x) else f"{100 * x:.{d}f}%"


def pp(x):
    return "" if pd.isna(x) else f"{100 * x:+.1f}%p"


def num(x, d=2):
    return "" if pd.isna(x) else f"{x:.{d}f}"


def rank_table(t: pd.DataFrame) -> str:
    h = ("| 순위 | 군 | 합친 원래 군 | 성분 수 | 가중치 몫 | G 오차 | 전체 대비 | B3 | B1 | G − B3 | 최저 모델 "
         "| 계절성 | 추세 | 연간 증가율 | 잔차 sd | 겨울/여름 | 전년비 sd | 대표 성분 |\n|" + "---|" * 18)
    rows = []
    for _, r in t.iterrows():
        g = f"{r.group}" + (" †" if r.small_group else "")
        mf = r.merged_from if str(r.group).endswith("_기타") else ""
        rows.append(f"| {r.rank_G} | {g} | {mf} | {r.n_ingr} | {pct(r.w_share)} | {pct(r.wape_G)} | {pp(r.G_minus_overall)} | "
                    f"{pct(r.wape_B3)} | {pct(r.wape_B1)} | {pp(r.G_minus_B3)} | {r.best_model} | "
                    f"{num(r.seas_strength_median)} | {num(r.trend_strength_median)} | {pct(r.annual_growth_median)} | "
                    f"{num(r.resid_sd_median, 3)} | {num(r.winter_summer_median)} | {num(r.yoy_sd_median, 3)} | "
                    f"{' '.join(str(r.top_ingredients).split())[:60]} |")
    return h + "\n" + "\n".join(rows)


def ends(t: pd.DataFrame, k: int = 4) -> tuple[str, str]:
    b = t[~t.small_group]
    f = lambda d: ", ".join(f"{r.group}({pct(r.wape_G)}, {r.n_ingr}개)" for _, r in d.iterrows())
    return f(b.head(k)), f(b.tail(k).iloc[::-1])


def main() -> None:
    s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    T = {g: pd.read_csv(OUT / f"group_{g}.csv", dtype={"group": str}) for g in ("cls2", "atc2")}
    kw = pd.read_csv(OUT / "group_difference_tests.csv").set_index(["grouping", "metric"])
    eta = pd.read_csv(OUT / "between_group_share.csv").set_index(["grouping", "metric"])
    sp = pd.read_csv(OUT / "pattern_error_spearman.csv").set_index("metric").spearman_with_wape_G
    ing = pd.read_csv(OUT / "ingredient_metrics.csv", dtype={"c4": str, "cls3": str, "cls2": str, "cls2_raw": str})
    lk = s["atc_link"]
    L = []
    a = L.append
    a("# P2 v3 결과: 분류군 사이의 예측 오차와 사용 패턴 차이 (사후 탐색)\n")
    a("> 성격: P2 v2 백테스트 주 결과에 붙이는 별도 분석. 사전등록 목록 밖의 **사후 탐색**이며 판정을 내리지 않는다. "
      "목적은 '군마다 다른 예측 모델을 만들면 오차가 줄어드는가' 실험의 사전 자료다. 이 보고서는 그 실험을 하지 않았다. "
      "분류군과 오차·패턴의 관계는 연관이고 원인이 아니다. 오차는 수정본 사용통계로 평가했고 2023–2025는 v1이 이미 본 기간이다. "
      "설계 `docs/PLAN_P2_v3.md`, 실행 `make p2v3-group`.\n")
    r = s["repro_wape_pct"]
    a("## 1. 완료 기준\n")
    a("| 기준 | 결과 |\n|---|---|")
    a(f"| P2 v2 평가 칸·전체 오차 재현 | 통과: 평가 칸 {s['n_cells']:,}, 금액 가중 WAPE G {r['G']}%, B3 {r['B3']}%, B1 {r['B1']}%, B2 {r['B2']}% (verdict.md와 같음) |")
    a(f"| ATC 연결 확대 (표준코드) | 통과: 대상 501개 중 {lk['new_path_mapped']}개 연결(이전 경로 {lk['old_path_mapped']}개). 분석 성분 {s['n_ingr']}개 중 미연결 {s['atc2_unmapped']}개 |")
    a(f"| 성분 {SMALL_N}개 미만 군 → 상위 기타 군 | 통과: 약효분류 2자리 원래 {s['n_raw_groups']['cls2']}개 → {s['n_groups']['cls2']}개 군, "
      f"ATC 2단계 원래 {s['n_raw_groups']['atc2']}개 → {s['n_groups']['atc2']}개 군. 합친 뒤에도 {SMALL_N}개 미만인 군: "
      f"약효분류 {s['n_groups']['cls2'] - s['n_groups_large']['cls2']}개, ATC {s['n_groups']['atc2'] - s['n_groups_large']['atc2']}개 |")
    a(f"| 사용 패턴 지표 | 성분 {s['n_ingr']}개 중 {s['n_ingr'] - s['pattern_missing']}개 계산, {s['pattern_missing']}개는 창 안 미관측 달이 있어 빈칸 |")
    a("| 시험 | `tests/test_p2v3_group.py` 7개 (합성 자료, 손 계산 기대값) |\n")

    a("## 2. 군간 차이 요약 (실험 설계용)\n")
    a("군간 몫 η² = 성분 값의 가중 분산 중 군 평균 차이로 설명되는 몫(가중치 w). 군이 많을수록 우연만으로도 커지므로 "
      "'우연 기준' = (군 수 − 1) ÷ (성분 수 − 1)과 비교한다. ε² = Kruskal–Wallis 순위 기준(비가중), p는 같은 시험.\n")
    a("| 지표 | 약효분류 η² | 우연 기준 | ε² | p | ATC η² | 우연 기준 | ε² | p |\n|---|---|---|---|---|---|---|---|---|")
    for m in ("log_wape_G", "wape_G", "lr_G_B3", "lr_G_B1", "d_G_B3", "d_G_B1", "seas_strength", "trend_strength", "annual_growth",
              "resid_sd", "winter_summer", "yoy_sd"):
        cells = []
        for g in ("cls2", "atc2"):
            e = eta.loc[(g, m)]
            k = kw.loc[(g, m)] if (g, m) in kw.index else None
            cells += [num(e.eta2_weighted), num(e.eta2_null_approx),
                      "" if k is None else num(k.epsilon2), "" if k is None else f"{k.kw_p:.2g}"]
        a(f"| {MNAME[m]} | " + " | ".join(cells) + " |")
    a("")
    e = lambda g, m: eta.loc[(g, m)]
    a(f"- **오차 수준은 군 사이에 크게 다르다.** G 오차(log)의 군간 몫은 약효분류 {num(e('cls2', 'log_wape_G').eta2_weighted)}, "
      f"ATC {num(e('atc2', 'log_wape_G').eta2_weighted)}로 우연 기준({num(e('cls2', 'log_wape_G').eta2_null_approx)}, "
      f"{num(e('atc2', 'log_wape_G').eta2_null_approx)})보다 훨씬 크다. 원 척도 WAPE는 꼬리가 길어 몫이 낮게 나온다.")
    a(f"- **어느 모델이 나은지도 군 사이에 다르다.** 상대 차이 log(G 오차 ÷ B3 오차)의 군간 몫은 약효분류 {num(e('cls2', 'lr_G_B3').eta2_weighted)}"
      f"(우연 기준 {num(e('cls2', 'lr_G_B3').eta2_null_approx)}), ATC {num(e('atc2', 'lr_G_B3').eta2_weighted)}"
      f"(우연 기준 {num(e('atc2', 'lr_G_B3').eta2_null_approx)}). 원 척도 차이 G − B3로 재면 극단값 몇 개가 분산을 차지해 "
      f"우연 기준 수준({num(e('cls2', 'd_G_B3').eta2_weighted)}, {num(e('atc2', 'd_G_B3').eta2_weighted)})으로 나온다 — 이 보고서의 이전 판은 이 원 척도 값으로 "
      "'덜 다르다'고 썼고, 그 문장은 고쳤다(2026-11-24).")
    for g in ("cls2", "atc2"):
        t = T[g]
        b3 = t[t.best_model != "G"]
        a(f"- {LABEL[g]}: G가 아닌 모델이 군 안 최저인 군 {len(b3)}개 / {len(t)}개, 가중치 몫 {pct(b3.w_share.sum())} "
          f"({', '.join(f'{x.group}({x.best_model})' for _, x in b3.iterrows())}).")
    a(f"- **사용 패턴도 군 사이에 다르다.** ATC 2단계가 약효분류 2자리보다 패턴을 더 크게 나눈다(계절성 강도 η² "
      f"{num(e('atc2', 'seas_strength').eta2_weighted)} 대 {num(e('cls2', 'seas_strength').eta2_weighted)}, "
      f"추세 강도 {num(e('atc2', 'trend_strength').eta2_weighted)} 대 {num(e('cls2', 'trend_strength').eta2_weighted)}). "
      "단 ATC는 군 수가 많아 우연 기준도 높다.")
    for g in ("cls2", "atc2"):
        lo, hi = ends(T[g])
        a(f"- {LABEL[g]} 오차가 가장 작은 군 {lo}. 가장 큰 군 {hi}.")
    a(f"- 성분 단위 Spearman(G의 WAPE_i와): 전년비 sd {sp['yoy_sd']:.2f}, 잔차 sd {sp['resid_sd']:.2f}, 겨울/여름 {sp['winter_summer']:.2f}, "
      f"계절성 강도 {sp['seas_strength']:.2f}, 연간 증가율 {sp['annual_growth']:.2f}, 추세 강도 {sp['trend_strength']:.2f}, log 가중치(규모) {sp['log_w']:.2f}. "
      "전년비 sd는 G·B1의 목표(작년 같은 달 대비 비율)의 변동이라 높은 상관이 일부 정의에서 나온다.")
    a("- 실험에 주는 뜻(해석, 시험하지 않음): 군마다 오차 수준이 다르다는 것만으로는 군별 모델이 낫다는 근거가 되지 않는다. "
      "근거가 될 수 있는 것은 군마다 **어느 모델 구조가 나은지가 다르다**는 점이다. B3가 나은 군에는 호흡기·항생제 계열"
      "(약효분류 22·61, ATC J01·R01·R05)이 들어 있고, 이 군들은 전년비 sd와 겨울/여름 비율이 크다(§3). "
      "묶는 방식(ATC 1단계, 패턴 군집)별 비교는 `reports/P2v3_regroup_result.md`.\n")

    a("## 3. 분류군 순위 (G 분류군 오차 오름차순, 1 = 오차가 가장 작음)\n")
    a("오차 = 군 안 금액 가중 WAPE(가중치를 군 안에서 다시 정규화). 전체 대비 = 군 G 오차 − 전체 G 오차. "
      "G − B3 음수 = G가 나음. 패턴 지표 = 군 안 성분 **중앙값**(가중 평균은 CSV), 창 2023-01 ~ 2025-12. "
      f"`X_기타` = 성분 {SMALL_N}개 미만 원래 군을 상위 분류 X(약효분류 첫 자리, ATC 1단계)로 합친 군, `전체_기타` = 그렇게 합쳐도 "
      f"{SMALL_N}개 미만인 군. † = 여전히 {SMALL_N}개 미만. 군 안 재표집 구간·사분위는 CSV에 있다. 군 이름은 대표 성분(가중치 상위 3)으로 표시한다.\n")
    for i, g in enumerate(("cls2", "atc2"), 1):
        a(f"### 3-{i}. {LABEL[g]}\n")
        a(rank_table(T[g]) + "\n")

    a("## 4. 정의\n")
    a("- 사용 패턴(성분마다, 원외 청구수량, 2023-01 ~ 2025-12 36개월 모두 관측된 성분만): 계절성·추세 강도 = log y의 STL(주기 12, robust) 분해로 "
      "max(0, 1 − Var(잔차) ÷ Var(성분 + 잔차)); 연간 증가율 = log y 직선 기울기 × 12의 exp − 1; 잔차 sd = STL 잔차 표준편차(log); "
      "겨울/여름 = 11~3월 월평균 ÷ 6~8월 월평균; 전년비 sd = sd(log(y_t ÷ y_{t−12})).")
    a("- ATC 2단계 배정: 2026 약가정보 `표준코드` 4~12자리(9자리 제품코드) → 2022-01 ~ 2025-12 급여목록의 주성분코드 → 성분4. "
      "성분4마다 서로 다른 제품코드 수가 가장 많은 ATC 2단계, 같으면 작은 코드.\n")

    a("## 5. 문서와 달랐던 점, 알아 둘 것\n")
    a(f"- 표준코드 경로 점검: 2026 약가정보 {lk['code_map_rows']:,}행 모두 13자리. 2025-12 급여목록 제품 {lk['benefit_2025_12_products']:,}개 중 "
      f"{lk['benefit_2025_12_matched_by_std']:,}개가 표준코드 4~12자리와 일치. `제품코드(개정후)` 열은 개정 뒤 새 코드라 쓰지 않았다.")
    a(f"- 이전 경로(`attr_2026_product`)와 둘 다 연결된 {lk['both_mapped']}개 성분 중 {lk['both_same_atc2']}개는 같은 ATC 2단계. "
      f"다른 {lk['both_mapped'] - lk['both_same_atc2']}개는 `outputs/p2v3/atc2_path_diff.csv`. 이전 보고의 `미연결` 군(10개)은 이제 각 ATC 군에 들어갔다.")
    a(f"- ATC 2단계가 둘 이상인 성분 {s['atc2_multi']}개, 최다가 동점이라 작은 코드를 쓴 성분 {s['atc2_tie']}개(`atc2_assignment_audit.csv`).")
    for g, t in T.items():
        for _, x in t[t.small_group].iterrows():
            a(f"- {LABEL[g]} `{x.group}`은 합친 뒤에도 성분 {x.n_ingr}개(원래 군 {x.merged_from}). 순위표에 두고 Kruskal–Wallis에서는 뺐다.")
    a(f"- 패턴 지표 빈칸 {s['pattern_missing']}개 성분(가중치 몫 {pct(ing[ing.seas_strength.isna()].w.sum())})은 창 안에 미관측(비공개) 달이 있다. 채우지 않았다.")
    a("- 군 배정에 쓴 ATC는 2026 자료다. 속성표로만 썼지만, 군별 모델 실험에서는 평가 시점에 이 배정을 알 수 있었는지 따로 적어야 한다(약효분류는 2022-07 목록 기준).")
    a("- 최근 달은 덜 집계되었을 수 있어(LIMITATIONS W4) 연간 증가율이 약간 낮게 나올 수 있다. B2 오차는 CSV에만 둔다(한 성분이 전체를 끌어올림).\n")

    a("## 6. 확인 필요\n")
    a(f"1. 합쳐도 {SMALL_N}개 미만인 `전체_기타`를 그대로 둘지, 가장 가까운 군에 넣을지.")
    a("2. 군별 모델 실험의 단위: 패턴 차이는 ATC 2단계가 더 크게 나누지만 군 수가 많아 군마다 학습 자료가 적다. 약효분류 2자리와 ATC 2단계 중 무엇으로 할지.")
    a("3. 군 이름: 약효분류·ATC 이름표 파일을 주면 대표 성분 대신 공식 이름을 붙인다.\n")

    a("## 7. 출력 파일\n")
    a("- `outputs/p2v3/p2v3_group_compare.xlsx` (아래 표 전부, 시트별)")
    a("- `outputs/p2v3/group_cls2.csv`, `group_atc2.csv`: 분류군 순위표(모든 열: 군 안 구간·사분위, 패턴 가중 평균, B2 포함)")
    a("- `outputs/p2v3/between_group_share.csv`, `group_difference_tests.csv`: 군간 몫, 군 차이 시험")
    a("- `outputs/p2v3/ingredient_metrics.csv`: 성분별 오차·편향·패턴·원래 군·합친 군")
    a("- `outputs/p2v3/pattern_error_spearman.csv`, `atc2_assignment_audit.csv`, `atc2_path_diff.csv`, `summary.json`")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"썼다: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
