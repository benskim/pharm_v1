"""P2 v2 대상 선정·가중치·커버리지 (PLAN_P2_v2 §5-1, 사용자 결정 2026-11-20).

- 대상 = 2018-01 ~ 2022-07 55개월 모두 원외 관측된 성분4, 그리고 2018-12 ~ 2019-11 원외 청구금액 합 > 0.
- 가중치 w_i = 성분 i의 2018-12 ~ 2019-11 원외 청구금액 ÷ 대상 합. 보조 A18 가중치 = 2021-08 ~ 2022-07.
- 분류 = 2022-07 급여목록에서 그 성분4의 고유 제품코드가 가장 많은 분류, 같으면 작은 번호, 없으면 0('000').
- 쓰는 사용통계·목록은 모두 a₀ = 2022-07 이하 (`timeaxis.panel_upto`, 목록은 list_month = 2022-07 하나).
- 예측 오차는 계산하지 않는다.
사용: python -m pbd.forecast_v2.select  (make p2v2-select)
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from pbd.common import AUDIT, DB_PATH, ROOT, connect, load_yaml, sql_files, usage_files
from pbd.forecast_v2.timeaxis import PRE_TAG_MAX, mrange, panel_upto

CFG = load_yaml("p2v2.yaml")
S = CFG["selection"]
OUT = ROOT / "outputs/p2v2"


def c4_class(con, list_month: str = S["class_list_month"]) -> pd.DataFrame:
    """성분4 → 분류 (list_month 급여목록 제품 행, 고유 제품코드 수 최다, 같으면 작은 번호)"""
    return con.execute("""
        with n as (select c4, cast(class_code as int) cls, count(distinct product_code) np
                   from stg_benefit where list_month = ? and row_kind = 'product_candidate'
                        and class_code is not null and c4 is not null group by 1, 2)
        select c4, arg_max(cls, np * 10000 - cls) cls from n group by 1""", [list_month]).df()


def select_target(panel: pd.DataFrame, cls: pd.DataFrame, s: dict = S) -> tuple[pd.DataFrame, list[dict], pd.DataFrame]:
    """(대상 표, 탈락 단계, 탈락 성분 audit). panel은 a₀ 이하만 받는다"""
    months = mrange(s["obs_start"], s["a0"])
    assert len(months) == s["n_months"], (len(months), s["n_months"])
    assert panel.month.max() <= s["a0"], "선정에는 a₀ 이하 자료만 넘긴다"
    p = panel[panel.month.isin(months)]
    obs = p.groupby("c4")["observed"].sum()

    def period_amt(per):
        q = p[p.month.isin(mrange(*per)) & p["observed"]]
        return q.groupby("c4").claim_amt.sum()

    amt = period_amt(s["weight_period"]).reindex(obs.index).fillna(0.0)
    amt_alt = period_amt(s["weight_period_alt"]).reindex(obs.index).fillna(0.0)
    step1 = obs[obs > 0].index
    step2 = obs[obs == s["n_months"]].index
    step3 = [c for c in step2 if amt[c] > 0]
    steps = [dict(step=f"① {s['obs_start']} ~ {s['a0']} 원외 관측 1개월 이상", n_c4=len(step1)),
             dict(step=f"② {s['n_months']}개월 모두 원외 관측", n_c4=len(step2)),
             dict(step=f"③ {s['weight_period'][0]} ~ {s['weight_period'][1]} 원외 청구금액 합 > 0", n_c4=len(step3))]
    for i in range(1, len(steps)):
        steps[i]["dropped"] = steps[i - 1]["n_c4"] - steps[i]["n_c4"]
    steps[0]["dropped"] = None
    drop = [dict(c4=c, reason=f"원외 관측 {int(obs[c])}/{s['n_months']}개월") for c in step1 if c not in set(step2)]
    drop += [dict(c4=c, reason=f"가중치 기간 원외 청구금액 합 {amt[c]:.0f} ≤ 0") for c in step2 if amt[c] <= 0]
    t = pd.DataFrame({"c4": step3})
    t = t.merge(cls, on="c4", how="left")
    t["cls"] = t.cls.fillna(0).astype(int)
    t["cls3"] = t.cls.map(lambda v: f"{v:03d}")
    t["in_h"] = t.cls.isin(s["subgroup_h"])
    t["amt_w"] = t.c4.map(amt)
    t["amt_alt"] = t.c4.map(amt_alt)
    t["w"] = t.amt_w / t.amt_w.sum()
    t["w_alt"] = t.amt_alt / t.amt_alt.sum()
    t = t.sort_values("c4").reset_index(drop=True)
    return t, steps, pd.DataFrame(drop, columns=["c4", "reason"])


def eff_n(w: pd.Series) -> float:
    return float(1.0 / (w ** 2).sum())


def coverage(panel: pd.DataFrame, t: pd.DataFrame, s: dict = S) -> list[dict]:
    """커버리지(오차 아님). 비중 = 대상 원외 청구금액 ÷ 공개(관측) 원외 청구금액 전체"""
    tg = set(t.c4)
    obs = panel[panel["observed"] & (panel.month <= s["a0"])]
    rows = []

    def share(name, months):
        q = obs[obs.month.isin(months)]
        rows.append(dict(item=f"대상 금액 비중 {name}", value=float(q[q.c4.isin(tg)].claim_amt.sum() / q.claim_amt.sum())))

    rows.append(dict(item="대상 성분 수", value=len(t)))
    share(f"가중치 기간 {s['weight_period'][0]} ~ {s['weight_period'][1]}", mrange(*s["weight_period"]))
    share(f"A18 기간 {s['weight_period_alt'][0]} ~ {s['weight_period_alt'][1]}", mrange(*s["weight_period_alt"]))
    for y in range(int(s["obs_start"][:4]), int(s["a0"][:4]) + 1):
        ms = [m for m in mrange(s["obs_start"], s["a0"]) if m.startswith(str(y))]
        share(f"{y}" + ("" if len(ms) == 12 else f" ({ms[0]} ~ {ms[-1]})"), ms)
    for col, name in (("w", "주 가중치"), ("w_alt", "A18 가중치")):
        rows.append(dict(item=f"유효 성분 수 1/Σw² ({name})", value=eff_n(t[col])))
        rows.append(dict(item=f"상위 10 성분 가중치 몫 ({name})", value=float(t[col].nlargest(10).sum())))
        rows.append(dict(item=f"최대 성분 가중치 ({name})", value=float(t[col].max())))
    rows.append(dict(item="주·A18 가중치 순위 상관(Spearman)", value=float(t.w.rank().corr(t.w_alt.rank()))))
    rows.append(dict(item="하위 집단 H 성분 수", value=int(t.in_h.sum())))
    rows.append(dict(item="하위 집단 H 가중치 몫 (주 가중치)", value=float(t.loc[t.in_h, "w"].sum())))
    rows.append(dict(item="분류 없음('000') 성분 수", value=int((t.cls == 0).sum())))
    return rows


def panel_check(con, years=(2018, 2022)) -> dict:
    """패널 재사용 확인: 원본 CSV(years 연도 파일만)를 다시 읽은 성분4 × 월 원외 수량·금액 = fc_panel 관측 칸.
    2022-12 뒤 파일은 열지 않는다"""
    assert years[1] <= int(PRE_TAG_MAX[:4])
    raw = con.execute(f"""
        select lpad(WK_COMPN_4, 4, '0') c4, substr(STD_YYYYMM, 1, 4) || '-' || substr(STD_YYYYMM, 5, 2) as month,
               sum(cast(PRSCRPTN_TOT_USE as double)) qty, sum(cast(PRSCRPTN_AMT as double)) amt
        from read_csv({sql_files(usage_files(years))}, header = true, all_varchar = true)
        where OUT_IN_HOS = 'OUT' group by 1, 2""").df()
    m = panel_upto(con, f"{years[1]}-12")
    m = m[m["observed"] & (m.month >= f"{years[0]}-01")]
    j = raw.merge(m, on=["c4", "month"], how="outer", indicator=True)
    both = j[j._merge == "both"]
    bad = both[(np.abs(both.qty - both.claim_qty) > 1e-6 * np.maximum(1, both.qty.abs()))
               | (np.abs(both.amt - both.claim_amt) > 1e-6 * np.maximum(1, both.amt.abs()))]
    return dict(only_raw=int((j._merge == "left_only").sum()), only_panel=int((j._merge == "right_only").sum()),
                both_cells=len(both), diff_cells=len(bad))


def sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    con = connect(DB_PATH, read_only=True)
    chk = panel_check(con)
    print("패널 재사용 확인 (원본 2018 ~ 2022 재집계 대 fc_panel):", chk)
    assert chk["only_raw"] == 0 and chk["only_panel"] == 0 and chk["diff_cells"] == 0, chk
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([chk]).to_csv(OUT / "panel_check.csv", index=False)
    panel = panel_upto(con, S["a0"])
    cls = c4_class(con)
    t, steps, drop = select_target(panel, cls)
    cov = coverage(panel, t)
    OUT.mkdir(parents=True, exist_ok=True)
    AUDIT.mkdir(parents=True, exist_ok=True)
    cols = ["c4", "cls3", "in_h", "amt_w", "w", "amt_alt", "w_alt"]
    t[cols].to_csv(OUT / "target.csv", index=False, float_format="%.12g")
    pd.DataFrame(steps)[["step", "n_c4", "dropped"]].to_csv(OUT / "target_steps.csv", index=False)
    pd.DataFrame(cov).to_csv(OUT / "coverage.csv", index=False, float_format="%.6g")
    drop.to_csv(AUDIT / "dropped_p2v2_select.csv", index=False)
    by_cls = (t.groupby("cls3").agg(n_c4=("c4", "size"), w=("w", "sum"), w_alt=("w_alt", "sum"))
              .reset_index().sort_values("w", ascending=False))
    by_cls.to_csv(OUT / "coverage_by_class.csv", index=False, float_format="%.6g")
    assert np.isclose(t.w.sum(), 1.0) and np.isclose(t.w_alt.sum(), 1.0)
    print("탈락 단계:", *[f"{r['step']}: {r['n_c4']}" for r in steps], sep="\n  ")
    print("커버리지:", *[f"{r['item']}: {r['value']:.4g}" for r in cov], sep="\n  ")
    print("target.csv SHA-256:", sha256(OUT / "target.csv"))
    print("config/p2v2.yaml SHA-256:", sha256(ROOT / "config/p2v2.yaml"))


if __name__ == "__main__":
    main()
