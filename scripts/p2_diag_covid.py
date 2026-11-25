"""D8 코로나 기간 활용 (docs/PLAN_P2_diag.md §11). 2026 자료는 읽지 않는다. make p2-diag-vol이 p2_diag_volatile 뒤에 실행"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
import numpy as np
import pandas as pd

from p2_diag_volatile import GROUPS, boot_diff

OUT = ROOT / "outputs/p2_diag"
C0, C1 = "2020-01", "2021-12"


def covid_rows(months, h):
    """학습 대상 열 tau 중 tau · tau−12 · tau−h가 코로나인 열"""
    k = [C0 <= m <= C1 for m in months]
    return {j for j in range(len(months)) if any(j - d >= 0 and k[j - d] for d in (0, 12, h))}


def _g(arg):
    """drop에 든 학습 열을 빼고 원래 fit_predict를 실행"""
    Y, A, months, cls2, a, h, seed, drop = arg
    import pbd.forecast_v2.gbm as GB
    base = GB.target_vals
    Y, A = Y.copy(), A.copy()
    Y[:, a + 1:] = np.nan
    A[:, a + 1:] = np.nan
    GB.target_vals = lambda c, tau, target: np.full(len(Y), np.nan) if tau in drop else base(c, tau, target)
    try:
        return GB.fit_predict(GB.Ctx(Y, A, months, cls2), a, h, seed)
    finally:
        GB.target_vals = base


def _e(x):
    from pbd.forecast_v2.ets import ets_path
    return ets_path(x, 6)


def rank_partial(x, y, Z):
    r = lambda v: pd.Series(v).rank().to_numpy()
    M = np.column_stack([np.ones(len(x)), *[r(z) for z in Z]])
    res = lambda v: r(v) - M @ np.linalg.lstsq(M, r(v), rcond=None)[0]
    return float(np.corrcoef(res(x), res(y))[0, 1])


def main():
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[k] = "1"
    import duckdb
    import yaml
    from pbd.forecast_v2.backtest import matrices
    from pbd.forecast_v2.timeaxis import mrange
    from pbd.forecast_v4 import run as RUN
    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    cfg = yaml.safe_load(RUN.CFG_PATH.read_text(encoding="utf-8"))
    tgt, Y, months, v2m, _ = RUN.load(cfg, False)
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    panel = con.execute("select c4, month, observed, claim_qty, cast(claim_amt as double) claim_amt from fc_panel where month <= '2025-12' order by c4, month").df()
    Y2, A, _, months, _ = matrices(panel, tgt, "2018-01", "2025-12")
    cls2 = tgt.cls3.str[:2].astype(int).to_numpy()
    w, ids = tgt.w.to_numpy(float), list(tgt.c4)
    col = {m: j for j, m in enumerate(months)}
    v4 = pd.read_csv(ROOT / "outputs/p2v4/predictions.csv", dtype={"c4": str})
    v4["c4"] = v4.c4.str.zfill(4)
    ok = v4.assign(k=v4["eval"].astype(float)).pivot(index="c4", columns="t", values="k").reindex(index=ids, columns=mrange("2023-01", "2025-12")).to_numpy() == 1
    g = pd.read_csv(ROOT / "config/p2v3_groups.csv", dtype=str).set_index("c4").atc2_69.reindex(ids).to_numpy()
    use = ok.any(1)
    sel = {q: np.flatnonzero((g == q) & use) for q in (*GROUPS, "R01")}
    sel["나머지"] = np.flatnonzero(use & ~np.isin(g, list(sel)))
    LY = np.log(np.where(Y > 0, Y, np.nan))
    cc = lambda a, b: [col[m] for m in mrange(a, b)]
    # D8a
    mon = np.array([int(m[5:]) for m in months])
    base = np.stack([(LY[:, cc("2018-01", "2018-12")][:, k] + LY[:, cc("2019-01", "2019-12")][:, k]) / 2 for k in range(12)], 1)
    dev = LY - base[:, mon - 1]
    S, o = {}, []
    full = np.isfinite(LY).all(1)
    yy = np.array([m[:4] for m in months])
    R = LY - np.stack([LY[:, yy == m[:4]].mean(1) for m in months], 1)
    M = R - np.stack([R[:, mon == k].mean(1) for k in mon], 1)
    nr = np.where(full, M.var(1) / np.where(R.var(1) > 0, R.var(1), 1), np.nan)
    for q, ix in sel.items():
        a = ix[full[ix]]
        r = dict(group=q, n=len(a), nonrep8=np.average(nr[a], weights=w[a]))
        for nm, lo, hi in (("covid", "2020-03", "2021-12"), *((str(y), f"{y}-01", f"{y}-12") for y in range(2022, 2026))):
            r["lv_" + nm] = np.average(dev[a][:, cc(lo, hi)].mean(1), weights=w[a])
        for y in range(2018, 2025):
            r[f"h{y}/{y - 1999}"] = np.average(LY[a][:, cc(f"{y}-11", f"{y + 1}-03")].mean(1) - LY[a][:, cc(f"{y}-06", f"{y}-08")].mean(1), weights=w[a])
        o.append(r)
    pd.DataFrame(o).to_csv(OUT / "d8a.csv", index=False)
    # D8b
    depth = np.abs(dev[:, cc("2020-03", "2021-12")].mean(1))
    pre = np.std((LY[:, 12:24] - LY[:, :12]), axis=1)
    Yv = np.where(ok, Y[:, col["2023-01"]:], np.nan)
    iw = {}
    for m in ("G", "B3"):
        P = v2m[m].reindex(columns=mrange("2023-01", "2025-12")).to_numpy(float)
        iw[m] = np.nansum(np.where(ok, np.abs(P - Yv), np.nan), 1) / np.where(use, np.nansum(Yv, 1), 1)
    rng = np.random.default_rng(seed)
    o = []
    for nm, ix in (("전체", np.flatnonzero(use)), ("네 군·R01 밖", sel["나머지"]), ("네 군", np.concatenate([sel[q] for q in GROUPS])), *((q, sel[q]) for q in GROUPS)):
        for m in iw:
            k = ix[np.isfinite(depth[ix]) & np.isfinite(pre[ix]) & (iw[m][ix] > 0)]
            f = lambda z: rank_partial(depth[z], iw[m][z], [pre[z], np.log(w[z])])
            b = [f(rng.choice(k, len(k))) for _ in range(1000)] if len(k) >= 10 else [np.nan]
            o.append(dict(set=nm, model=m, n=len(k), partial=f(k) if len(k) >= 10 else np.nan, lo=np.quantile(b, .05), hi=np.quantile(b, .95),
                          spearman=float(pd.Series(depth[k]).corr(pd.Series(iw[m][k]), "spearman"))))
    pd.DataFrame(o).to_csv(OUT / "d8b.csv", index=False)
    # D8c
    h, ts = 6, mrange("2024-01", "2025-12")
    cov = covid_rows(months, h)
    arms, jobs = {}, []
    for t in ts:
        a = col[t] - h
        cand = list(range(14 + h, a + 1))
        keep = [j for j in cand if j not in cov]
        ds = [set(), cov & set(cand)]
        for s in range(3):
            ds.append(set(cand) - set(np.random.default_rng([seed, s, a]).choice(cand, len(keep), replace=False).tolist()))
        jobs += [((Y2, A, months, cls2, a, h, seed, d), t, n, len(keep)) for n, d in zip(("base", "nocov", "rnd0", "rnd1", "rnd2"), ds)]
    with ProcessPoolExecutor(7) as ex:
        rs = list(ex.map(_g, [j[0] for j in jobs], chunksize=2))
    for (_, t, n, nk), (p, _r) in zip(jobs, rs):
        arms.setdefault(n, {})[t] = p
    P = {n: np.column_stack([arms[n][t] for t in ts]) for n in arms}
    G0 = v2m["G"].reindex(columns=ts).to_numpy(float)
    e = ok[:, 12:]
    gap = np.nanmax(np.abs(P["base"] / G0 - 1)[e])
    S["d8c"] = dict(base_repro_maxrel=float(gap), keep_months={t: j for (_, t, n, j) in jobs if n == "base"})
    assert gap < 1e-6, f"G 재현 실패 {gap}"
    Yc = np.where(e, Yv[:, 12:], np.nan)
    def wp(Q, ix, c=slice(None)):
        y = Yc[ix][:, c]
        v = np.isfinite(y) & np.isfinite(Q[ix][:, c])
        den = np.where(v, y, 0).sum(1)
        u = den > 0
        return float(np.average(np.where(v, np.abs(Q[ix][:, c] - y), 0).sum(1)[u] / den[u], weights=w[ix][u]))
    o = []
    for q, ix in {**sel, "전체": np.flatnonzero(use)}.items():
        for nm, c in (("2024~25", slice(None)), ("2024", slice(0, 12)), ("2025", slice(12, 24))):
            o.append(dict(group=q, period=nm, **{n: wp(P[n], ix, c) for n in P}))
    d = pd.DataFrame(o)
    for n in ("nocov", "rnd0", "rnd1", "rnd2"):
        d["d_" + n] = d[n] - d.base
    d.to_csv(OUT / "d8c.csv", index=False)
    # D8d
    t8 = mrange("2025-06", "2025-12")
    ix8 = np.flatnonzero(use)
    xs = [(i, t) for t in t8 for i in ix8]
    with ProcessPoolExecutor(7) as ex:
        fs = list(ex.map(_e, [Y[i, col["2022-01"]:col[t] - h + 1] for i, t in xs], chunksize=32))
    Q = np.full((len(ids), 24), np.nan)
    for (i, t), (f, _why) in zip(xs, fs):
        if f is not None:
            Q[i, ts.index(t)] = f[h - 1]
    S["d8d"] = dict(fits=len(xs), failed=sum(f is None for f, _ in fs))
    B = np.where(np.isfinite(Q), v2m["B3"].reindex(columns=ts).to_numpy(float), np.nan)
    c = slice(17, 24)
    pd.DataFrame([dict(group=q, ets_2022=wp(Q, ix, c), B3=wp(B, ix, c)) for q, ix in {**sel, "전체": ix8}.items()]).to_csv(OUT / "d8d.csv", index=False)
    (OUT / "summary_covid.json").write_text(json.dumps(S, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
