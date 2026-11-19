"""사용통계 두 판본 비교 (P2 추가 확인 A-c, 2026-11-19 사용자 지시). 지금은 합성 자료 시험만, 실행은 판본이 생긴 뒤.

같은 처방 연월을 다른 날 받은 두 판본의 원본 CSV(MEDICINE_YYYYMM*.csv)를 받아, 성분4 × 월과 약효분류군 × 월 단위로
원외 청구수량·금액의 차이(새 − 기준, 절대·%)를 계산하고 기준 판본의 공개 후 경과 개월별로 묶는다.
기준 판본 기본값 = data/raw의 2026-10-28 판본, 새 판본 = 사용자가 다시 받은 MEDICINE_202509 ~ 202512.

사용:
  python -I scripts/p2_usage_vintage_diff.py --new "data/raw/usage_vintage/2026-12-01/MEDICINE_*.csv" --new-pub 2026-12-01
  (기준 판본: --base 글롭, --base-pub 날짜. 새 판본 파일이 없으면 아무것도 하지 않고 끝난다)
출력: outputs/p2/vintage_diff_c4.csv, vintage_diff_class.csv, vintage_diff_by_elapsed.csv
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE_GLOB = str(ROOT / "data/raw/usage/cmpn_stat_*(2026.10.28.)/MEDICINE_*.csv")
BASE_PUB = "2026-10-28"


def read_out(con, pattern: str) -> pd.DataFrame:
    """원본 CSV의 원외(OUT) 합: 성분4 × 처방 연월"""
    return con.execute(f"""
        select lpad(WK_COMPN_4, 4, '0') c4, substr(STD_YYYYMM, 1, 4) || '-' || substr(STD_YYYYMM, 5, 2) as month,
               sum(cast(PRSCRPTN_TOT_USE as double)) qty, sum(cast(PRSCRPTN_AMT as double)) amt
        from read_csv('{pattern}', header = true, all_varchar = true) where OUT_IN_HOS = 'OUT' group by 1, 2""").df()


def elapsed(month: str, pub: str) -> int:
    """처방 연월 → 판본 공개일까지 경과 개월 수 (공개 달 − 처방 달)"""
    return (int(pub[:4]) * 12 + int(pub[5:7])) - (int(month[:4]) * 12 + int(month[5:7]))


def diff(base: pd.DataFrame, new: pd.DataFrame, base_pub: str, new_pub: str, key: str) -> pd.DataFrame:
    """두 판본에 같이 있는 달만 비교 (한쪽에만 있는 칸은 only 칸으로 표시)"""
    months = sorted(set(base["month"]) & set(new["month"]))
    b, n = base[base["month"].isin(months)], new[new["month"].isin(months)]
    d = b.merge(n, on=[key, "month"], how="outer", suffixes=("_base", "_new"), indicator=True)
    d["only"] = d["_merge"].map({"both": "", "left_only": "기준 판본에만", "right_only": "새 판본에만"})
    d = d.drop(columns="_merge")
    for v in ("qty", "amt"):
        d[f"{v}_diff"] = d[f"{v}_new"] - d[f"{v}_base"]
        d[f"{v}_pct"] = d[f"{v}_diff"] / d[f"{v}_base"]
    d["elapsed_base"] = d["month"].map(lambda m: elapsed(m, base_pub))
    d["elapsed_new"] = d["month"].map(lambda m: elapsed(m, new_pub))
    return d.sort_values([key, "month"]).reset_index(drop=True)


def by_class(d_c4: pd.DataFrame, cls: pd.DataFrame) -> pd.DataFrame:
    """성분4 차이를 약효분류군으로 더한다 (두 판본에 모두 있는 성분만)"""
    x = d_c4[d_c4["only"] == ""].merge(cls, on="c4", how="inner")
    g = x.groupby(["cls", "month", "elapsed_base", "elapsed_new"], as_index=False)[["qty_base", "qty_new", "amt_base", "amt_new"]].sum()
    for v in ("qty", "amt"):
        g[f"{v}_diff"] = g[f"{v}_new"] - g[f"{v}_base"]
        g[f"{v}_pct"] = g[f"{v}_diff"] / g[f"{v}_base"]
    return g


def by_elapsed(d: pd.DataFrame) -> pd.DataFrame:
    x = d[d["only"] == ""] if "only" in d else d
    return x.groupby("elapsed_base").agg(n=("qty_pct", "size"), qty_pct_median=("qty_pct", "median"),
                                         qty_total_pct=("qty_diff", lambda s: s.sum()), amt_pct_median=("amt_pct", "median")).reset_index()


def run(base_glob: str, base_pub: str, new_glob: str, new_pub: str, cls: pd.DataFrame | None, out: Path) -> dict[str, pd.DataFrame]:
    con = duckdb.connect()
    d = diff(read_out(con, base_glob), read_out(con, new_glob), base_pub, new_pub, "c4")
    res = {"vintage_diff_c4": d}
    if cls is not None:
        res["vintage_diff_class"] = by_class(d, cls)
    e = by_elapsed(d)
    tot = d[d["only"] == ""].groupby("elapsed_base")[["qty_base", "qty_diff"]].sum()
    e["qty_total_pct"] = (tot["qty_diff"] / tot["qty_base"]).reindex(e["elapsed_base"]).to_numpy()
    res["vintage_diff_by_elapsed"] = e
    out.mkdir(parents=True, exist_ok=True)
    for k, v in res.items():
        v.to_csv(out / f"{k}.csv", index=False)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=False, default="", help="새 판본 원본 CSV 글롭")
    ap.add_argument("--new-pub", default="", help="새 판본 공개일 YYYY-MM-DD")
    ap.add_argument("--base", default=BASE_GLOB)
    ap.add_argument("--base-pub", default=BASE_PUB)
    a = ap.parse_args()
    if not a.new or not glob.glob(a.new):
        print("새 판본 파일이 없어 건너뜀 (MEDICINE_202509 ~ 202512를 다시 받아 --new로 넣는다)")
        return
    if not a.new_pub:
        raise SystemExit("--new-pub(새 판본 공개일)가 필요하다")
    cls = None
    db = ROOT / "data/interim/pbd.duckdb"
    if db.exists():
        cls = duckdb.connect(str(db), read_only=True).execute("select c4, cls from fc_c4_class").df()
    res = run(a.base, a.base_pub, a.new, a.new_pub, cls, ROOT / "outputs/p2")
    print(res["vintage_diff_by_elapsed"].to_string(index=False))


if __name__ == "__main__":
    main()
