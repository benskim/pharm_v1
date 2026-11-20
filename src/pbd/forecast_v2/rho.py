"""ρ_M 캐시 (PLAN_P2_v2 §5-6, DECISIONS 2026-11-20 4단계): origin M마다 build_asof(M) 원장(목록 ≤ M만)에서
약가 예고 정시 실현 비율. 사용통계는 읽지 않는다(build_asof는 목록·비고·2026 속성표만 복사한다).

필요한 M = Q2-d 학습 origin(a′ ≥ 2022-01 → M′ ≥ 2022-06) ~ 평가 마지막 origin(t = 2025-12 → M = 2025-11).
출력 outputs/p2v2/rho_asof.csv(M, rho, n_realized, n_denominator). 이미 있는 M은 다시 계산하지 않는다.
사용: python -m pbd.forecast_v2.rho  (make p2v2-rho)
"""
from __future__ import annotations

import pandas as pd

from pbd.common import ROOT, load_yaml
from pbd.forecast_v2.timeaxis import madd, mrange

OUT = ROOT / "outputs/p2v2/rho_asof.csv"


def needed_months(cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_yaml("p2v2.yaml")
    lag = load_yaml("params.yaml")["usage_lag_months"]
    return mrange(madd("2022-01", lag), madd(cfg["eval"]["end"], -cfg["eval"]["h"] + lag))


def compute(M: str) -> dict:
    from pbd.ledger.s6_report import build_asof
    con = build_asof(M)
    try:
        r = con.execute("""select count(*) filter (where outcome = 'REALIZED'),
                   count(*) filter (where outcome in ('REALIZED', 'REALIZED_LATE', 'REALIZED_CHANGED', 'NOT_REALIZED')),
                   max(list_month) from signal_v5, (select max(list_month) list_month from stg_benefit)
                   where signal_type = 'PRICE_NOTICE' and check_month <= ?""", [M]).fetchone()
        assert r[2] is None or r[2] <= M, "build_asof가 M 뒤 목록을 담았다"
    finally:
        con.close()
    return dict(M=M, rho=1.0 if not r[1] else r[0] / r[1], n_realized=int(r[0]), n_denominator=int(r[1]))


def load() -> dict:
    d = pd.read_csv(OUT, dtype={"M": str})
    return dict(zip(d.M, d.rho))


def main() -> None:
    done = pd.read_csv(OUT, dtype={"M": str}) if OUT.exists() else pd.DataFrame(columns=["M", "rho", "n_realized", "n_denominator"])
    rows = done.to_dict("records")
    for M in needed_months():
        if M in set(done.M):
            continue
        r = compute(M)
        rows.append(r)
        pd.DataFrame(rows).sort_values("M").to_csv(OUT, index=False, float_format="%.10g")
        print(r, flush=True)
    d = pd.DataFrame(rows).sort_values("M")
    assert d.M.tolist() == needed_months(), "필요한 M이 빠졌다"
    print(f"ρ_M {len(d)}개: {d.M.iloc[0]} ~ {d.M.iloc[-1]}, 범위 {d.rho.min():.4f} ~ {d.rho.max():.4f}")


if __name__ == "__main__":
    main()
