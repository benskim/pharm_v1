"""P2 v3 실행: 분류군별 오차·사용 패턴 표 (docs/PLAN_P2_v3.md). 사후 탐색, 판정 없음.
python -m pbd.forecast_v3.run → outputs/p2v3/
"""
from __future__ import annotations

import json
import sys

import duckdb
import numpy as np
import pandas as pd
import yaml

from pbd.common import ROOT
from pbd.forecast_v3 import group_compare as gc

OUT = ROOT / "outputs/p2v3"
V2 = ROOT / "outputs/p2v2"
# P2 v2 verdict.md의 금액 가중 WAPE (재현 확인용, 소수 둘째 자리 %)
V2_VERDICT = {"B1": 9.76, "B2": 45.30, "B3": 7.11, "G": 6.46}


def _c4(s: pd.Series) -> pd.Series:
    return s.astype(str).str.zfill(4)


def load(con) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pred = pd.read_csv(V2 / "backtest/predictions.csv", dtype={"c4": str})
    pred["c4"] = _c4(pred.c4)
    pred = pred[pred.h == 6]
    tgt = pd.read_csv(V2 / "target.csv", dtype={"c4": str, "cls3": str})
    tgt["c4"] = _c4(tgt.c4)
    drop = pd.read_csv(V2 / "backtest/dropped_cells.csv", dtype={"c4": str}).rename(columns={"month": "t"})
    drop["c4"] = _c4(drop.c4)
    panel = con.execute("select c4, month t, claim_qty y from fc_panel where observed").df()
    return pred, tgt, drop, panel


def atc_products(con) -> tuple[pd.DataFrame, dict]:
    """표준코드 → 제품코드(4~12자리) → 급여목록(2022-01 ~ 2025-12) 주성분코드. 같은 제품코드에 성분4가 둘이면 둘 다 센다."""
    raw = (ROOT / "data/raw/master/dim_drug_code_map_26.parquet").as_posix()
    con.execute(f"""create or replace temp table v3_cm as
        select substr(cast(표준코드 as varchar), 4, 9) product_code, upper(trim("국제표준코드(ATC코드)")) atc,
               length(cast(표준코드 as varchar)) len from read_parquet('{raw}')""")
    chk = con.execute("""select count(*) n, sum((len = 13)::int) n13 from v3_cm""").fetchone()
    b = con.execute("""select count(distinct product_code) n_bp,
                              count(distinct product_code) filter (where product_code in (select product_code from v3_cm)) n_hit
                       from stg_benefit where list_month = '2025-12'""").fetchone()
    prod = con.execute("""select distinct substr(b.ingredient_code, 1, 4) c4, m.product_code, m.atc
                          from v3_cm m join (select distinct product_code, ingredient_code from stg_benefit) b using (product_code)
                          where m.atc is not null""").df()
    return prod, {"code_map_rows": chk[0], "std_code_len13": chk[1], "benefit_2025_12_products": b[0],
                  "benefit_2025_12_matched_by_std": b[1]}


def main() -> int:
    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    pred, tgt, drop, panel = load(con)
    tgt = tgt.set_index("c4")

    # 평가 칸과 성분 오차, P2 v2 재현 확인
    cells = gc.eval_cells(pred, panel, drop)
    err = gc.ingredient_errors(cells)
    repro = {m: round(100 * gc.weighted_wape(err, tgt.w, m) * tgt.w.reindex(err.index).sum(), 2) for m in gc.MODELS}
    ok = all(abs(repro[m] - V2_VERDICT[m]) < 0.006 for m in gc.MODELS) and len(cells) == 16486
    print(f"평가 칸 {len(cells)}, 성분 {len(err)}, 전체 금액 가중 WAPE(%) {repro}, P2 v2 재현 {'일치' if ok else '불일치'}")
    if not ok:
        print("P2 v2 결과와 다르다. 멈춘다.")
        return 1

    # 분류군 배정
    # ATC 연결 (사용자 승인 2026-11-24): 2026 약가정보 표준코드 4~12자리 = 9자리 제품코드 → 2022~2025 급여목록 주성분코드 → 성분4
    prod, link = atc_products(con)
    atc = gc.assign_atc2(prod, tgt.index).set_index("c4")
    old = gc.assign_atc2(con.execute("""select substr(ingredient_code, 1, 4) c4, product_code, atc from attr_2026_product
                                        where ingredient_code is not null""").df(), tgt.index).set_index("c4")
    both = (atc.atc2 != gc.UNMAPPED) & (old.atc2 != gc.UNMAPPED)
    link.update({"old_path_mapped": int((old.atc2 != gc.UNMAPPED).sum()), "new_path_mapped": int((atc.atc2 != gc.UNMAPPED).sum()),
                 "both_mapped": int(both.sum()), "both_same_atc2": int((atc.atc2[both] == old.atc2[both]).sum())})
    diff = atc[both & (atc.atc2 != old.atc2)].join(old.atc2.rename("atc2_old_path"))
    name = con.execute("""select c4, arg_max(ingredient_name, n) ing_name from (
                            select substr(ingredient_code, 1, 4) c4, ingredient_name, count(distinct product_code) n
                            from stg_benefit where list_month = '2022-07' group by 1, 2) group by 1""").df().set_index("c4")

    # 사용 패턴 (창 2023-01 ~ 2025-12, 전년 동월비용 2022)
    ps = panel.set_index(["c4", "t"]).y
    pat, why = {}, {}
    win = [f"{y}-{m:02d}" for y in (2023, 2024, 2025) for m in range(1, 13)]
    prev = [f"2022-{m:02d}" for m in range(1, 13)]
    for c4 in tgt.index:
        s = ps.reindex(pd.MultiIndex.from_product([[c4], win])).droplevel(0)
        p0 = ps.reindex(pd.MultiIndex.from_product([[c4], prev])).droplevel(0)
        pat[c4] = gc.pattern_metrics(s, p0)
        if s.isna().any() or (s <= 0).any():
            why[c4] = f"창 36개월 중 미관측 {int(s.isna().sum())}, 0 이하 {int((s <= 0).sum())}"
        elif np.isnan(pat[c4]["yoy_sd"]):
            why[c4] = "2022 비교 달 미관측(전년 동월비 변동성만 빈칸)"
    pat = pd.DataFrame.from_dict(pat, orient="index")

    ing = tgt[["cls3", "w", "in_h"]].join(name).join(atc.rename(columns={"atc2": "atc2_raw"})).join(err).join(pat)
    ing["cls2_raw"] = ing.cls3.str[:2]
    ing["log_w"] = np.log(ing.w)
    ing["d_G_B3"] = ing.wape_G - ing.wape_B3
    ing["d_G_B1"] = ing.wape_G - ing.wape_B1
    ing["log_wape_G"] = np.log(ing.wape_G)
    ing["log_wape_B3"] = np.log(ing.wape_B3)
    ing["lr_G_B3"] = np.log(ing.wape_G / ing.wape_B3)
    ing["lr_G_B1"] = np.log(ing.wape_G / ing.wape_B1)
    ing["pattern_note"] = pd.Series(why).reindex(ing.index)
    ing.index.name = "c4"
    no_cells = ing[ing.n_cells.isna()]
    ing = ing[ing.n_cells.notna()].copy()
    # 성분 3개 미만 군 → 상위 기타 군 (평가 칸이 있는 성분 기준으로 셈)
    for g in ("cls2", "atc2"):
        ing[g] = gc.merge_small_groups(ing[f"{g}_raw"])

    OUT.mkdir(parents=True, exist_ok=True)
    ing.sort_values("wape_G").to_csv(OUT / "ingredient_metrics.csv", encoding="utf-8-sig", float_format="%.6g")
    tables = {}
    for gcol in ("cls2", "atc2"):
        tables[gcol] = gc.group_table(ing.assign(src_group=ing[f"{gcol}_raw"]), gcol, seed)
        tables[gcol].to_csv(OUT / f"group_{gcol}.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    kw = pd.concat([gc.group_difference_tests(ing, g) for g in ("cls2", "atc2")])
    kw.to_csv(OUT / "group_difference_tests.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    between = pd.concat([gc.between_share(ing, g) for g in ("cls2", "atc2")])
    between.to_csv(OUT / "between_group_share.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    diff.to_csv(OUT / "atc2_path_diff.csv", encoding="utf-8-sig")
    assoc = gc.pattern_error_association(ing)
    assoc.to_csv(OUT / "pattern_error_spearman.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    atc_audit = ing[["ing_name", "cls3", "atc2_raw", "atc2", "atc2_n", "atc2_top_share", "atc2_tie"]]
    atc_audit[(ing.atc2_n != 1)].to_csv(OUT / "atc2_assignment_audit.csv", encoding="utf-8-sig", float_format="%.4g")

    with pd.ExcelWriter(OUT / "p2v3_group_compare.xlsx") as xw:
        for gcol, t in tables.items():
            t.to_excel(xw, sheet_name=f"group_{gcol}", index=False)
        between.to_excel(xw, sheet_name="between_group_share", index=False)
        kw.to_excel(xw, sheet_name="group_difference_tests", index=False)
        assoc.to_excel(xw, sheet_name="pattern_error_spearman", index=False)
        ing.sort_values("wape_G").to_excel(xw, sheet_name="ingredient_metrics")
        atc_audit[(ing.atc2_n != 1)].to_excel(xw, sheet_name="atc2_assignment_audit")

    summ = {"n_cells": len(cells), "n_ingr": len(ing), "n_target_without_cells": len(no_cells), "repro_wape_pct": repro,
            "atc_link": link, "n_raw_groups": {g: int(ing[f"{g}_raw"].nunique()) for g in ("cls2", "atc2")},
            "atc2_unmapped": int((ing.atc2 == gc.UNMAPPED).sum()), "atc2_multi": int((ing.atc2_n > 1).sum()),
            "atc2_tie": int(ing.atc2_tie.sum()), "pattern_missing": int(ing.seas_strength.isna().sum()),
            "yoy_missing": int(ing.yoy_sd.isna().sum()),
            "n_groups": {g: len(t) for g, t in tables.items()},
            "n_groups_large": {g: int((~t.small_group).sum()) for g, t in tables.items()}}
    (OUT / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
