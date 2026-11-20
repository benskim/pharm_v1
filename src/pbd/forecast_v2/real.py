"""실제 자료 백테스트 진입점 (PLAN_P2_v2 §7-4 ~ §7-6).

python -m pbd.forecast_v2.real --check      : 잠금 상태·해시만 보고(사용통계를 읽지 않는다)  (make p2v2-prereg-check)
python -m pbd.forecast_v2.real              : 태그 p2v2-prereg-r1과 잠금 파일이 같을 때만 주 결과 실행 → outputs/p2v2/backtest/
python -m pbd.forecast_v2.real --deviation  : 태그는 있으나 잠금 파일이 다를 때 '사전등록 이탈' 판 → outputs/p2v2/deviation_<해시 8자리>/
태그가 없으면 어떤 판도 실행하지 않는다. 평가 기간 사용통계는 gate.unlock() 뒤 timeaxis.panel_full로만 읽는다.
"""
from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pandas as pd

from pbd.common import DB_PATH, ROOT, connect, load_yaml
from pbd.forecast_v2 import gate as G
from pbd.forecast_v2 import rho as RHO
from pbd.forecast_v2.list_info import load_db
from pbd.forecast_v2.select import c4_class, select_target
from pbd.forecast_v2.timeaxis import mrange, panel_full, panel_upto

OUT = ROOT / "outputs/p2v2"


def status() -> dict:
    g = G.check()
    t = OUT / "target.csv"
    r = RHO.OUT
    have = set(pd.read_csv(r, dtype={"M": str}).M) if r.exists() else set()
    need = RHO.needed_months()
    return dict(gate_ok=g.ok, deviation_only=g.deviation_only, reason=g.reason, head=g.head,
                code_hash=g.work_hash, tag_hash=g.tag_hash, locked_files=G.working_files(),
                verification_only_sha256=G.verification_hashes(),
                config_sha256=G.sha256_file(ROOT / "config/p2v2.yaml"),
                target_sha256=G.sha256_file(t) if t.exists() else None,
                rho_sha256=G.sha256_file(r) if r.exists() else None,
                rho_missing=sorted(set(need) - have))


def inputs_for(con, u: G.Unlock):
    cfg = load_yaml("p2v2.yaml")
    S = cfg["selection"]
    t_new, _, _ = select_target(panel_upto(con, S["a0"]), c4_class(con))
    t = pd.read_csv(OUT / "target.csv", dtype={"c4": str, "cls3": str})
    assert t.c4.tolist() == t_new.c4.tolist() and np.allclose(t.w, t_new.w), "target.csv가 다시 고른 대상과 다르다"
    rho = RHO.load()
    miss = sorted(set(RHO.needed_months()) - set(rho))
    assert not miss, f"ρ_M 없음: {miss}"
    panel = panel_full(con, u)
    obs = panel.groupby("c4")["observed"].sum()
    full = set(obs[obs == len(mrange("2018-01", "2025-12"))].index)
    w96 = np.where(t.c4.isin(full), t.w, 0.0)
    w96 = w96 / w96.sum() if w96.sum() > 0 else None
    a22 = panel[panel["observed"] & panel.month.str.startswith("2022")].groupby("c4").claim_amt.sum()
    w22 = t.c4.map(a22).fillna(0.0).to_numpy(float)
    w22 = w22 / w22.sum()
    batch = set(con.execute("select observed_month from batch_month_v5 where batch").df().observed_month)
    return cfg, panel, t, load_db(con), rho, w96, w22, batch


def main(argv: list[str]) -> int:
    if "--check" in argv:
        s = status()
        (OUT / "prereg_check.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(s, ensure_ascii=False, indent=1))
        return 0
    deviation = "--deviation" in argv
    try:
        u = G.unlock(deviation)
    except PermissionError as e:
        print(f"실행 거부: {e}", file=sys.stderr)
        return 2
    from pbd.forecast_v2.run import run_all
    con = connect(DB_PATH, read_only=True)
    cfg, panel, t, ld, rho, w96, w22, batch = inputs_for(con, u)
    out = OUT / ("backtest" if not deviation else f"deviation_{u.gate.work_hash[:8]}")
    seed = load_yaml("params.yaml")["seed"]
    run_all(panel, t, ld, lambda M: rho[M], cfg, out, seed, batch_months=batch, w96=w96, w2022=w22)
    subprocess.run([sys.executable, "-I", str(ROOT / "scripts/p2v2_verdict.py"), str(out)], check=True)
    G.run_log("python -m pbd.forecast_v2.real" + (" --deviation" if deviation else ""), u,
              {"target.csv": G.sha256_file(OUT / "target.csv"), "rho_asof.csv": G.sha256_file(RHO.OUT),
               "config": G.sha256_file(ROOT / "config/p2v2.yaml"),
               "db": f"size={DB_PATH.stat().st_size},mtime={int(DB_PATH.stat().st_mtime)}"})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
