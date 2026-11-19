"""처분 단위 집계 (2026-11-19 사용자 결정, 검토 A·B 반영 묶음 ②).

처분 단위 = (업체, 처분 종류, 예정일 또는 기간). 업체는 신호가 처음 보인 달의 목록 업체명.
비고 원문은 쓰지 않는다(약가 예고는 제품마다 금액이 달라 원문으로 나누면 처분이 갈라진다).
외부 근거로 넣은 집행정지(경보제약 23개 제품)는 이 키로 처분 1건이 되는지 확인한다(assert).

출력 outputs/v5/disposition_units.csv (처분 단위 표), outputs/v5/disposition_summary.csv (요약).
제품 단위 정시 실현율(v5.3)과 비고 미기재 약가 변경의 일괄 인하 달 몫도 같이 낸다.
금액 기준 비중은 계산하지 않는다: 제품별 판매량(처방량)이 데이터에 없다.
사용: python -I scripts/p1_disposition.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pbd.ledger.v5_rules import REALIZED_FAMILY, rates  # noqa: E402

OUT = ROOT / "outputs/v5"
KEY = ["firm", "signal_type", "signal_date", "period_end"]


def units(sig: pd.DataFrame) -> pd.DataFrame:
    """제품 단위 신호 → 처분 단위. 판정 제외(EXCLUDED)는 처분 안에서 세지 않는다.
    막힘: 판정 대상 제품 가운데 하나라도 집행정지로 막힘. 전부 막힘도 따로 센다.
    결과가 난 제품(미결·판정 제외 아님)이 하나도 없으면 처분 결과는 '미결'."""
    s = sig.copy()
    s["period_end"] = s["period_end"].fillna("")
    s["judged"] = s["outcome"] != "EXCLUDED"
    s["blocked"] = s["stay_blocked"].fillna(False).astype(bool) & s["judged"]
    s["resolved"] = s["judged"] & (s["outcome"] != "CENSORED")
    s["fam"] = s["outcome"].isin(REALIZED_FAMILY) & s["resolved"]
    s["on_time"] = (s["outcome"] == "REALIZED") & s["resolved"]
    g = s.groupby(KEY, dropna=False)
    u = g.agg(n_products=("product_code", "nunique"), n_judged=("judged", "sum"), n_blocked=("blocked", "sum"),
              n_resolved=("resolved", "sum"), n_realized_family=("fam", "sum"), n_on_time=("on_time", "sum"),
              external=("stay_target_source", lambda x: bool((x == "external").any()))).reset_index()
    u = u[u["n_judged"] > 0]
    u["any_blocked"] = u["n_blocked"] > 0
    u["all_blocked"] = u["n_blocked"] == u["n_judged"]
    return u.sort_values(["signal_type", "n_products"], ascending=[True, False]).reset_index(drop=True)


def load(con) -> pd.DataFrame:
    return con.execute("""
        select s.signal_id, s.signal_type, s.source_rule, s.product_code, s.outcome, s.reason, s.stay_blocked, s.current_status,
               s.stay_target_source, strftime(s.signal_date, '%Y-%m-%d') signal_date, strftime(s.period_end, '%Y-%m-%d') period_end,
               p.firm
        from signal_v5 s left join product_state_month_v5 p on p.product_code = s.product_code and p.list_month = s.first_month
        where (s.signal_type = 'PRICE_NOTICE' and s.source_rule = 'R2') or s.signal_type = 'SUSPEND_NOTICE'""").df()


def main() -> None:
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    sig = load(con)
    assert sig["firm"].notna().all(), "업체명이 없는 신호가 있다"
    u = units(sig)
    # 외부 근거 집행정지(경보제약) = 처분 1건
    ext = u[u["external"] & (u["signal_type"] == "SUSPEND_NOTICE")]
    assert (ext["firm"].str.contains("경보").sum(), int(ext.loc[ext["firm"].str.contains("경보"), "n_products"].sum())) == (1, 23), ext
    rows = []
    for t, name in (("SUSPEND_NOTICE", "급여중지 예고"), ("PRICE_NOTICE", "약가 예고 (급여목록 비고)")):
        x = u[u["signal_type"] == t]
        r = rates([dict(outcome=o, reason=a, stay_blocked=b, current_status=c) for o, a, b, c in
                   sig.loc[sig["signal_type"] == t, ["outcome", "reason", "stay_blocked", "current_status"]].itertuples(index=False)])
        rows.append(dict(kind=name, n_products_judged=int(x["n_judged"].sum()), n_units=len(x), n_firms=x["firm"].nunique(),
                         units_any_blocked=int(x["any_blocked"].sum()), units_all_blocked=int(x["all_blocked"].sum()),
                         firms_blocked=x.loc[x["any_blocked"], "firm"].nunique(),
                         # 처분 단위 실현율 (2026-11-19): 결과가 난 제품이 있는 처분 중, 결과가 난 제품이 모두 실현 계열인 처분
                         units_resolved=int((x["n_resolved"] > 0).sum()),
                         units_all_realized=int(((x["n_resolved"] > 0) & (x["n_realized_family"] == x["n_resolved"])).sum()),
                         units_all_on_time=int(((x["n_resolved"] > 0) & (x["n_on_time"] == x["n_resolved"])).sum()),
                         products_blocked=int(x["n_blocked"].sum()), max_unit_products=int(x["n_products"].max()),
                         product_on_time_k=r["on_time"][0], product_on_time_n=r["on_time"][1], product_on_time=r["on_time"][2],
                         product_B=r["B"][2], product_stay=r["stay"][2]))
    sm = pd.DataFrame(rows)
    # 비고 미기재 약가 변경 가운데 일괄 인하 달의 몫 (제품 수 기준)
    u_all, u_batch = con.execute("""select count(*), count(*) filter (where batch) from ev_v5
                                    where event_type = 'PRICE_CHANGE' and kind = 'unobserved_notice'""").fetchone()
    m2309 = con.execute("""select count(*) from ev_v5 where event_type = 'PRICE_CHANGE' and kind = 'unobserved_notice'
                           and strftime(effective_date, '%Y-%m') = '2023-09'""").fetchone()[0]
    sm["unannounced_n"], sm["unannounced_batch_n"], sm["unannounced_2023_09_n"] = u_all, u_batch, m2309
    OUT.mkdir(parents=True, exist_ok=True)
    u.to_csv(OUT / "disposition_units.csv", index=False)
    sm.to_csv(OUT / "disposition_summary.csv", index=False)
    pd.set_option("display.width", 200)
    print(sm.T.to_string())
    print(u[(u["signal_type"] == "SUSPEND_NOTICE") & u["any_blocked"]].head(20).to_string())


if __name__ == "__main__":
    main()
