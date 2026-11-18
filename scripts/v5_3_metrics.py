"""v5.3 개정 전후 비교용 지표 (읽기 전용). 사용: PYTHONPATH=src python -I scripts/v5_3_metrics.py <이름표>
출력 outputs/v5/metrics_<이름표>.csv (급여중지 예고 A·B·집행정지율, 이벤트 건수), outputs/v5/gap_months_<이름표>.csv (gap_months 분포)"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

from pbd.common import DB_PATH  # noqa: E402
from pbd.ledger.v5_rules import rates  # noqa: E402

OUT = ROOT / "outputs/v5"


def main(tag: str) -> None:
    c = duckdb.connect(str(DB_PATH), read_only=True)
    rows = []
    for st in ("SUSPEND_NOTICE", "PRICE_NOTICE"):
        rs = [dict(outcome=o, reason=r, stay_blocked=b, current_status=s) for o, r, b, s in c.execute(
            "select outcome, reason, stay_blocked, current_status from signal_v5 where signal_type = ?", [st]).fetchall()]
        x = rates(rs)
        for k in ("A", "B", "on_time", "stay"):
            a, n, p, lo, hi = x[k]
            rows.append(dict(item=f"{st} {k}", k=a, n=n, value=round(p, 4), lo=round(lo, 4), hi=round(hi, 4)))
    for name, cond in (("양도양수", "event_type = 'SUCCESSION' and kind = 'transfer'"), ("코드 변경", "event_type = 'SUCCESSION' and kind = 'recode'"),
                       ("교체 등재", "event_type = 'SUCCESSION' and kind = 'swap'"), ("급여삭제", "event_type = 'DELISTING' and kind = 'delisting'"),
                       ("급여삭제(규격)", "event_type = 'DELISTING' and kind = 'delisting_spec'"),
                       ("급여삭제 as_known 뒤 철회", "event_type = 'DELISTING' and retracted_month is not null")):
        tb = "event_ledger_v5" if "철회" in name else "ev_v5"
        rows.append(dict(item=name, k=c.execute(f"select count(*) from {tb} where {cond}").fetchone()[0]))
    m = pd.DataFrame(rows)
    m.to_csv(OUT / f"metrics_{tag}.csv", index=False)
    g = c.execute("""select kind, link_basis, case when gap_months = 0 then '0' when gap_months <= 3 then '1~3' when gap_months <= 12 then '4~12'
                            else '13+' end gap_bin, count(*) n from succession_v5 group by all order by 1, 2, 3""").df()
    g.to_csv(OUT / f"gap_months_{tag}.csv", index=False)
    print(m.to_string(index=False))
    print(g.pivot_table(index=["kind", "link_basis"], columns="gap_bin", values="n", fill_value=0).to_string())


if __name__ == "__main__":
    main(sys.argv[1])
