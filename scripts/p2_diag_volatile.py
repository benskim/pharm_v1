"""S01·R03·J01·R05 오차의 구성 (docs/PLAN_P2_diag.md, 사후 탐색). 저장된 예측만 쓰고 새로 적합하지 않는다. 2026 자료는 읽지 않는다.
실행: make p2-diag-vol → outputs/p2_diag/*.csv, summary.json"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import numpy as np
import pandas as pd

GROUPS = ("S01", "R03", "J01", "R05")
YRS = np.repeat([2023, 2024, 2025], 12)
N_PB = 499


# ---------- 핵심 계산 (행 = 성분, 열 = 대상월 36개, 평가 밖 칸 = NaN) ----------
def wape_by(E, Y, w, cols=None):
    """성분 WAPE의 금액 가중 평균. 해별 값은 그 해 정답 합을 분모로 씀"""
    c = slice(None) if cols is None else cols
    e, y = E[:, c], Y[:, c]
    ok = np.isfinite(e) & np.isfinite(y)
    num = np.where(ok, np.abs(np.expm1(np.where(ok, e, 0))) * np.where(ok, y, 0), 0).sum(1)
    den = np.where(ok, y, 0).sum(1)
    u = den > 0
    return float((w[u] * num[u] / den[u]).sum() / w[u].sum())


def wmedian(E, w):
    """열마다 가중 중앙값(누적 가중치가 절반을 처음 넘는 값)"""
    out = np.full(E.shape[1], np.nan)
    for j in range(E.shape[1]):
        k = np.isfinite(E[:, j])
        if k.any():
            o = np.argsort(E[k, j])
            cw = np.cumsum(w[k][o])
            out[j] = E[k, j][o][np.searchsorted(cw, cw[-1] / 2)]
    return out


def loo_factor(E, w):
    return np.vstack([wmedian(np.delete(E, i, 0), np.delete(w, i)) for i in range(len(w))])


def level(E):
    """같은 해 나머지 달의 평균 로그오차 (두 달 미만이면 0)"""
    out = np.zeros_like(E)
    for y in np.unique(YRS):
        c = YRS == y
        k = np.isfinite(E[:, c])
        n = k.sum(1, keepdims=True)
        s = np.nansum(E[:, c], 1, keepdims=True)
        out[:, c] = np.where(k & (n > 1), (s - np.where(k, E[:, c], 0)) / np.maximum(n - 1, 1), 0)
    return out


def xfit(E, Xs, w):
    """e를 Xs에 회귀(성분 고정효과, 가중 w). 두 해로 계수, 남은 해에 b·X. (보정, 전체 기간 계수)"""
    def fit(c):
        k = np.isfinite(E) & c[None, :]
        for X in Xs:
            k &= np.isfinite(X)
        W = np.where(k, w[:, None], 0)
        s = W.sum(1, keepdims=True)
        dm = lambda Z: np.where(k, np.where(k, Z, 0) - (W * np.where(k, Z, 0)).sum(1, keepdims=True) / np.where(s > 0, s, 1), 0)
        M = np.column_stack([dm(X).ravel() for X in Xs])
        sw = np.sqrt(W.ravel())
        return np.linalg.lstsq(M * sw[:, None], dm(E).ravel() * sw, rcond=None)[0]
    out = np.zeros_like(E)
    for y in np.unique(YRS):
        b = fit(YRS != y)
        c = YRS == y
        out[:, c] = sum(bi * np.where(np.isfinite(X[:, c]), X[:, c], 0) for bi, X in zip(b, Xs))
    return out, fit(np.ones(36, bool))


def gains(E, C, Y, w):
    """오차 감소 몫 [전체, 2023, 2024, 2025]"""
    C = np.where(np.isfinite(C), C, 0)
    r = []
    for c in (None, *(YRS == y for y in (2023, 2024, 2025))):
        a = wape_by(E, Y, w, c)
        r.append((a - wape_by(E - C, Y, w, c)) / a)
    return np.array(r)


def label(S, beats, yr, hi=.25, lo=.10):
    pos = yr > 0
    if beats and pos.all():
        return "큰 구성" if S >= hi else "일부 구성" if S >= lo else "근거 약함"
    return "한 해의 사건" if beats and pos.sum() == 1 else "근거 약함"


def judge(g, pbs):
    """pbs: 가짜 보정 표본(행 × 4)들의 목록. 평균·95% 분위는 가짜 종류 중 큰 쪽"""
    base = np.max([p.mean(0) for p in pbs], 0)
    q95 = max(np.quantile(p[:, 0], .95) for p in pbs)
    S = g - base
    beats = bool(g[0] > q95)
    r = dict(gain=g[0], placebo_mean=base[0], placebo_q95=q95, S=S[0], S2023=S[1], S2024=S[2], S2025=S[3], beats=beats, label=label(S[0], beats, S[1:]))
    for d in (-.05, .05):
        r[f"label{d:+.2f}"] = label(S[0], beats, S[1:], .25 + d, .10 + d)
    return r


def analyse(E, Y, w, Ea=None, A12=None, Aa=None, A9=None, pool=None, rng=None):
    """한 군·한 모델의 D2·D2x·D3·D4. Ea = 기준월 열의 로그오차(대상월 = a), pool = 짝 성분 (E, w)"""
    rng = rng or np.random.default_rng(0)
    roll = lambda Z, k: np.roll(Z, k, 1)
    res, cor = {}, {}
    F = loo_factor(E, w)
    pb = [np.array([gains(E, roll(F, k), Y, w) for k in range(1, 36)])]
    if pool is not None and len(pool[1]) > len(w):
        p = []
        for _ in range(N_PB):
            ix = rng.choice(len(pool[1]), len(w) - 1 if len(w) > 1 else 1, replace=False)
            p.append(gains(E, np.tile(wmedian(pool[0][ix], pool[1][ix]), (len(w), 1)), Y, w))
        pb.append(np.array(p))
    res["D2"], cor["D2"] = judge(gains(E, F, Y, w), pb), F
    if Ea is not None:
        Fa = loo_factor(Ea, w)
        C, b = xfit(E, [Fa], w)
        res["D2x"] = judge(gains(E, C, Y, w), [np.array([gains(E, xfit(E, [roll(Fa, k)], w)[0], Y, w) for k in range(1, 36)])])
        res["D2x"]["b_F"], cor["D2x"] = float(b[0]), C
    L = level(E)
    p = []
    for _ in range(N_PB):
        Z = E * rng.choice([-1, 1], E.shape)
        p.append(gains(Z, level(Z), Y, w))
    res["D3"], cor["D3"] = judge(gains(E, L, Y, w), [np.array(p)]), L
    if A12 is not None:
        C, b = xfit(E, [A12, Aa], w)
        res["D4"] = judge(gains(E, C, Y, w), [np.array([gains(E, xfit(E, [roll(A12, k), roll(Aa, k)], w)[0], Y, w) for k in range(1, 36)])])
        res["D4"].update(b_echo=float(b[0]), b_anchor=float(b[1]), echo_only_gain=gains(E, xfit(E, [A12], w)[0], Y, w)[0],
                         anchor_only_gain=gains(E, xfit(E, [Aa], w)[0], Y, w)[0], b_echo_only=float(xfit(E, [A12], w)[1][0]),
                         b_anchor_only=float(xfit(E, [Aa], w)[1][0]))
        if A9 is not None:
            res["D4"]["lag9_gain"] = gains(E, xfit(E, [A9, Aa], w)[0], Y, w)[0]
        cor["D4"] = C
    # 두 순서: 앞 단계 보정을 뺀 오차에서 다음 구성을 다시 계산
    def seq(order):
        Z, o, W0 = E, {}, wape_by(E, Y, w)
        for nm in order:
            c = loo_factor(Z, w) if nm == "D2" else level(Z) if nm == "D3" else xfit(Z, [A12, Aa], w)[0]
            Z2 = Z - np.where(np.isfinite(c), c, 0)
            o[nm] = (wape_by(Z, Y, w) - wape_by(Z2, Y, w)) / W0
            Z = Z2
        return o
    if A12 is not None:
        f, b = seq(["D4", "D2", "D3"]), seq(["D3", "D2", "D4"])
        for nm in f:
            res[nm].update(order_fwd=f[nm], order_rev=b[nm], joint_fwd=sum(f.values()), joint_rev=sum(b.values()))
    return res, cor


def boot_diff(a, wa, b, wb, seed, n=2000):
    """가중 평균 차이 a − b의 성분 재표집 90% 구간"""
    rng = np.random.default_rng(seed)
    def m(v, w):
        ix = rng.integers(0, len(v), (n, len(v)))
        return (v[ix] * w[ix]).sum(1) / w[ix].sum(1)
    return (float((a * wa).sum() / wa.sum()), float((b * wb).sum() / wb.sum()), *map(float, np.quantile(m(a, wa) - m(b, wb), [.05, .95])))


def main():
    import duckdb
    import yaml
    from pbd.forecast_v2.calendar_kr import workdays
    from pbd.forecast_v2.timeaxis import madd, mrange
    from pbd.forecast_v4 import run as RUN
    from pbd.forecast_v4 import workday as WD
    from pbd.forecast_v4.etscache import EtsStore

    OUT = ROOT / "outputs/p2_diag"
    OUT.mkdir(parents=True, exist_ok=True)
    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    cfg = yaml.safe_load(RUN.CFG_PATH.read_text(encoding="utf-8"))
    tgt, Yall, months, _, _ = RUN.load(cfg, False)
    assert max(months) <= "2025-12"
    ids, w = list(tgt.c4), tgt.w.to_numpy(float)
    col = {m: j for j, m in enumerate(months)}
    ev = mrange("2023-01", "2025-12")
    Yp = np.where(Yall > 0, Yall, np.nan)
    pr = pd.read_csv(ROOT / "outputs/p2v2/backtest/predictions.csv", dtype={"c4": str})
    pr["c4"] = pr.c4.str.zfill(4)
    mat = lambda d, v, ms: d.pivot(index="c4", columns="t", values=v).reindex(index=ids, columns=ms).to_numpy(float)
    v4 = pd.read_csv(ROOT / "outputs/p2v4/predictions.csv", dtype={"c4": str})
    v4["c4"] = v4.c4.str.zfill(4)
    ok = v4.assign(k=v4["eval"].astype(float)).pipe(mat, "k", ev) == 1
    wide = {m: mat(pr[pr.model == m], "yhat", mrange("2022-01", "2025-12")) for m in ("G", "B3", "B1")}
    lo22 = col["2022-01"]
    le = {m: np.log(np.where(P > 0, P, np.nan)) - np.log(Yp[:, lo22:lo22 + 48]) for m, P in wide.items()}
    Y = np.where(ok, Yp[:, col["2023-01"]:col["2025-12"] + 1], np.nan)
    E = {m: np.where(ok, v[:, 12:], np.nan) for m, v in le.items()}
    E["W"] = np.where(ok, np.log(mat(v4, "W", ev)) - np.log(Y), np.nan)
    A = -le["B3"]                                                 # 열 0 = 2022-01
    A12, Aa, A9 = A[:, 0:36], A[:, 6:42], A[:, 3:39]
    g = pd.read_csv(ROOT / "config/p2v3_groups.csv", dtype=str).set_index("c4").atc2_69.reindex(ids).to_numpy()
    use = ok.any(1)
    L = np.log(Yp[:, :col["2022-07"] + 1])
    with np.errstate(all="ignore"):
        import warnings
        warnings.simplefilter("ignore")
        vol = np.nanstd(L[:, 12:] - L[:, :-12], axis=1)
    sel = {q: np.flatnonzero((g == q) & use) for q in (*GROUPS, "R01")}
    # 짝 성분
    cand = np.flatnonzero(use & ~np.isin(g, list(sel)) & np.isfinite(vol))
    Z = np.column_stack([np.log(w), vol])
    sd, mu = Z[cand].std(0), Z[cand].mean(0)
    Z = (Z - mu) / sd
    pool = {}
    for q, ix in sel.items():
        ix = ix[np.isfinite(vol[ix])]
        pool[q] = np.unique(np.concatenate([cand[np.argsort(((Z[cand] - Z[i]) ** 2).sum(1))[:3]] for i in ix]))
    S, rows = dict(pool={q: len(p) for q, p in pool.items()}, sizes={q: len(p) for q, p in sel.items()}), []
    # 재현
    S["overall"] = {m: float(wape_by(E[m][use], Y[use], w[use])) for m in E}
    ref = pd.read_csv(ROOT / "outputs/p2v3/group_atc2.csv", encoding="utf-8-sig").set_index("group")
    for q in GROUPS:
        for m in ("G", "B3"):
            assert abs(wape_by(E[m][sel[q]], Y[sel[q]], w[sel[q]]) - ref.loc[q, f"wape_{m}"]) < 1e-5, (q, m)
    # D2·D2x·D3·D4
    for q, ix in sel.items():
        for m in ("G", "B3"):
            pe = (E[m][pool[q]], w[pool[q]])
            res, _ = analyse(E[m][ix], Y[ix], w[ix], le[m][ix][:, 6:42], A12[ix], Aa[ix], A9[ix], pe, np.random.default_rng(seed))
            rows += [dict(group=q, model=m, wape=wape_by(E[m][ix], Y[ix], w[ix]), part=k, **r) for k, r in res.items()]
            if q == "S01":
                nx = np.array([i for i in ix if ids[i] != "5300"])
                r2, _ = analyse(E[m][nx], Y[nx], w[nx], le[m][nx][:, 6:42], A12[nx], Aa[nx], A9[nx], pe, np.random.default_rng(seed))
                rows += [dict(group="S01(5300 제외)", model=m, wape=wape_by(E[m][nx], Y[nx], w[nx]), part=k, **r) for k, r in r2.items()]
    # 결과를 본 뒤 추가 (§10 밖): 짝 성분 구성, 달 돌리기만의 기준, 짝을 ATC R·J 밖으로 좁힌 기준
    ph = []
    for q in GROUPS:
        ix, pl = sel[q], pool[q]
        nr = pl[~np.isin([str(x)[0] for x in g[pl]], ["R", "J"])]
        for m in ("G", "B3"):
            e, y, ww = E[m][ix], Y[ix], w[ix]
            F = loo_factor(e, ww)
            gn = gains(e, F, y, ww)
            rl = np.array([gains(e, np.roll(F, k, 1), y, ww) for k in range(1, 36)])
            rg = np.random.default_rng(seed)
            f = lambda z: np.array([gains(e, np.tile(wmedian(E[m][r], w[r]), (len(ix), 1)), y, ww) for r in (rg.choice(z, min(len(ix) - 1, len(z) - 1), replace=False) for _ in range(N_PB))])
            pa, pn = f(pl), f(nr)
            # 전체 밖 성분(R·J·S01 제외) 공통 오차
            oth = np.flatnonzero(use & ~np.isin([str(x)[0] for x in g], ["R", "J"]) & (g != "S01"))
            ph.append(dict(group=q, model=m, gain=gn[0], g2023=gn[1], g2024=gn[2], g2025=gn[3], roll_mean=rl[:, 0].mean(), roll_q95=np.quantile(rl[:, 0], .95),
                           pool_mean=pa[:, 0].mean(), pool_q95=np.quantile(pa[:, 0], .95), n_pool_notRJ=len(nr), poolNR_mean=pn[:, 0].mean(), poolNR_q95=np.quantile(pn[:, 0], .95),
                           outside=gains(e, np.tile(wmedian(E[m][oth], w[oth]), (len(ix), 1)), y, ww)[0],
                           pool_atc=" ".join(f"{k}:{v}" for k, v in pd.Series(g[pl]).value_counts().head(6).items())))
    pd.DataFrame(ph).to_csv(OUT / "posthoc_d2.csv", index=False)
    comp = pd.DataFrame(rows)
    comp.to_csv(OUT / "components.csv", index=False)
    # D0·D3 부속
    o, oi, win = [], [], []
    names = pd.read_csv(ROOT / "outputs/p2v3/ingredient_metrics.csv", dtype={"c4": str}, encoding="utf-8-sig").set_index("c4").ing_name
    for q, ix in sel.items():
        full = np.array([ok[i].all() for i in ix])
        for m in ("G", "B3"):
            e, y, ww = E[m][ix], Y[ix], w[ix]
            iw = np.array([wape_by(e[[i]], y[[i]], ww[[i]]) for i in range(len(ix))])
            cb = ww * iw / ww.sum()
            od = np.argsort(-cb)
            pv = np.array([wape_by(E[m][[i]], Y[[i]], w[[i]]) for i in pool[q]]) / vol[pool[q]]
            fin = np.isfinite(vol[ix])
            rel = boot_diff((iw / vol[ix])[fin], ww[fin], pv, w[pool[q]], seed)
            drop = lambda k: wape_by(np.delete(e, od[:k], 0), np.delete(y, od[:k], 0), np.delete(ww, od[:k]))
            yr = {f"wape{t}": wape_by(e, y, ww, YRS == t) for t in (2023, 2024, 2025)}
            o.append(dict(group=q, model=m, wape=cb.sum(), **yr, bias=float(np.average([np.nansum(np.expm1(a) * b) / np.nansum(b) for a, b in zip(e, y)], weights=ww)), full_only=wape_by(e[full], y[full], ww[full]),
                          to_2025_06=wape_by(e, y, ww, np.arange(36) < 30), n_eff=1 / ((cb / cb.sum()) ** 2).sum(), top1=ids[ix[od[0]]],
                          top1_share=cb[od[0]] / cb.sum(), drop1=drop(1), drop3=drop(3), n=len(ix),
                          rel_to_vol=rel[0], rel_pool=rel[1], rel_lo=rel[2], rel_hi=rel[3]))
            oi += [dict(group=q, model=m, c4=ids[ix[i]], name=names.get(ids[ix[i]]), wape=iw[i], share=cb[i] / cb.sum()) for i in od[:5]]
            F = wmedian(e, ww)
            for nm, lo, hi in (("2023-01~03", 0, 3), ("2023/24", 10, 15), ("2024/25", 22, 27), ("2025-11~12", 34, 36)):
                win.append(dict(group=q, model=m, winter=nm, F_mean=float(F[lo:hi].mean())))
    pd.DataFrame(o).to_csv(OUT / "d0_d3.csv", index=False)
    pd.DataFrame(oi).to_csv(OUT / "top_ingredients.csv", index=False)
    pd.DataFrame(win).to_csv(OUT / "winter_factor.csv", index=False)
    # D1 비반복 몫
    cs = [j for j, t in enumerate(months) if t[:4] in ("2018", "2019", "2022", "2023", "2024", "2025")]
    ly, yy, mm = np.log(Yp[:, cs]), np.array([months[j][:4] for j in cs]), np.array([months[j][5:] for j in cs])
    nonrep = np.full(len(ids), np.nan)
    full = np.isfinite(ly).all(1)
    R = ly[full] - sum(np.where(yy == y, ly[full][:, yy == y].mean(1, keepdims=True), 0) for y in np.unique(yy))
    M = R - sum(np.where(mm == x, R[:, mm == x].mean(1, keepdims=True), 0) for x in np.unique(mm))
    nonrep[full] = M.var(1) / R.var(1)
    o = []
    for q, ix in sel.items():
        a, b = [k[np.isfinite(nonrep[k])] for k in (ix, pool[q])]
        r = boot_diff(nonrep[a], w[a], nonrep[b], w[b], seed)
        hh = {}
        for nm, y0 in (("2018/19", 2018), ("2022/23", 2022), ("2023/24", 2023), ("2024/25", 2024)):
            wi = [col[t] for t in mrange(f"{y0}-11", f"{y0 + 1}-03")]
            su = [col[t] for t in mrange(f"{y0}-06", f"{y0}-08")]
            hh["h" + nm] = float(np.average(np.log(Yp[a][:, wi]).mean(1) - np.log(Yp[a][:, su]).mean(1), weights=w[a]))
        o.append(dict(group=q, n=len(a), nonrep=r[0], pool=r[1], lo=r[2], hi=r[3], **hh))
    pd.DataFrame(o).to_csv(OUT / "d1.csv", index=False)
    # D5
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    allix = np.unique(np.concatenate([*sel.values(), *pool.values()]))
    con.register("k", pd.DataFrame({"c4": [ids[i] for i in allix]}))
    u = con.execute("""select c4, month, case when age_band::int < 10 then '0-9' when age_band::int < 20 then '10-19' when age_band::int < 60 then '20-59' else '60+' end age,
        sido, inst_type, sum(claim_cnt) n, sum(claim_qty) v from stg_usage join k using (c4)
        where in_out = 'OUT' and month between '2022-01' and '2025-12' group by all""").df()
    m5 = mrange("2022-01", "2025-12")
    def cube(key):
        z = u.groupby(["c4", key, "month"])[["n", "v"]].sum().reset_index()
        return z
    nat = u.groupby(["c4", "month"])[["n", "v"]].sum().reset_index()
    N, V = [nat.pivot(index="c4", columns="month", values=x).reindex(index=ids, columns=m5).to_numpy(float) for x in ("n", "v")]
    d = allix
    chk = np.isfinite(Y[d]) & np.isfinite(V[d][:, 12:])
    assert np.allclose(V[d][:, 12:][chk], Y[d][chk], rtol=1e-9), "원본 재집계 ≠ 패널"
    with np.errstate(all="ignore"):
        dn, dv = [np.log(x[:, 12:] / x[:, :-12]) for x in (N, V)]
        dq = np.log((V / N)[:, 12:] / (V / N)[:, :-12])
    dn, dv, dq = [np.where(ok & np.isfinite(dv), x, np.nan) for x in (dn, dv, dq)]
    S["d5_identity"] = float(np.nanmax(np.abs(dv - dn - dq)[d]))
    assert S["d5_identity"] < 1e-9
    def cshare(part, tot):
        """성분마다 (Cov, Var). 군 몫은 가중합의 비"""
        k = np.isfinite(tot) & np.isfinite(part)
        out = np.zeros((len(tot), 2))
        for i in range(len(tot)):
            if k[i].sum() >= 6:
                t, p = tot[i, k[i]], part[i, k[i]]
                out[i] = ((p - p.mean()) * (t - t.mean())).sum() / len(t), t.var()
        return out
    def ratio_boot(c, ix, jx, n=2000):
        rng = np.random.default_rng(seed)
        f = lambda z, r: (w[z][r, None] * c[z][r]).sum(-2) if r.ndim == 1 else (w[z][r][..., None] * c[z][r]).sum(1)
        a, b = [f(z, rng.integers(0, len(z), (n, len(z)))) for z in (ix, jx)]
        p = lambda z: (lambda t: t[0] / t[1])(f(z, np.arange(len(z))))
        return p(ix), p(jx), *np.quantile(a[:, 0] / a[:, 1] - b[:, 0] / b[:, 1], [.05, .95])
    cq = cshare(dq, dv)
    parts, base = {}, {}
    for key in ("age", "inst_type"):
        z = cube(key)
        for v in sorted(z[key].unique()):
            K = z[z[key] == v].pivot(index="c4", columns="month", values="n").reindex(index=ids, columns=m5).fillna(0).to_numpy(float)  # 세부 칸 미관측은 합에 없는 행(전국 합도 같은 행들의 합)
            with np.errstate(all="ignore"):
                r = N[:, 12:] - N[:, :-12]
                lm = np.where(r == 0, N[:, 12:], r / np.log(N[:, 12:] / N[:, :-12]))
                parts[key, v] = cshare(np.where(np.isfinite(dn), (K[:, 12:] - K[:, :-12]) / lm, np.nan), dn)
            base[key, v] = np.nansum(K[:, 12:], 1) / np.nansum(N[:, 12:], 1)
    sd_ = cube("sido")
    sync = np.full(len(ids), np.nan)
    pos = {c: i for i, c in enumerate(ids)}
    for c4, z in sd_.groupby("c4"):
        K = z.pivot(index="sido", columns="month", values="n").reindex(columns=m5).to_numpy(float)
        with np.errstate(all="ignore"):
            dk = np.log(K[:, 12:] / K[:, :-12])
        dk = dk[np.isfinite(dk).all(1)]
        if len(dk) >= 10:
            sync[pos[c4]] = np.corrcoef(dk)[np.triu_indices(len(dk), 1)].mean()
    o, oa = [], []
    for q, ix in sel.items():
        r = ratio_boot(cq, ix, pool[q])
        a, b = [k[np.isfinite(sync[k])] for k in (ix, pool[q])]
        s = boot_diff(sync[a], w[a], sync[b], w[b], seed)
        o.append(dict(group=q, qty_per_claim_share=r[0], pool=r[1], lo=r[2], hi=r[3], claims_share=1 - r[0], sync=s[0], sync_pool=s[1], sync_lo=s[2], sync_hi=s[3], sync_n=len(a)))
        for (key, v), c in parts.items():
            f = lambda z: (w[z, None] * c[z]).sum(0)
            x, y = f(ix), f(pool[q])
            oa.append(dict(group=q, key=key, value=v, share=x[0] / x[1], claim_share=np.average(np.nan_to_num(base[key, v][ix]), weights=w[ix]), pool_share=y[0] / y[1],
                           pool_claim_share=np.average(np.nan_to_num(base[key, v][pool[q]]), weights=w[pool[q]])))
    pd.DataFrame(o).to_csv(OUT / "d5.csv", index=False)
    pd.DataFrame(oa).to_csv(OUT / "d5_parts.csv", index=False)
    # D6
    evs = con.execute("""select market_c4 c4, event_type k, coalesce(strftime(effective_date::date, '%Y-%m'), observed_month) m, count(*) n from ev_v5
        where retracted_month is null and event_type in ('LISTING','DELISTING','SUSPEND','STAY_START','PRICE_CHANGE') group by all""").df()
    key = set(zip(evs.c4.astype(str).str.zfill(4), evs.k, evs.m))
    S["d6_null_date"] = int(con.execute("select count(*) from ev_v5 where effective_date is null and event_type in ('LISTING','DELISTING','SUSPEND','STAY_START','PRICE_CHANGE')").fetchone()[0])
    o = []
    rng = np.random.default_rng(seed)
    for q in sel:
        for m in ("G", "B3"):
            ix = sel[q]
            e, y = E[m][ix], Y[ix]
            c = np.abs(np.expm1(e)) * y * (w[ix] / np.nansum(y, 1))[:, None]
            top = c >= np.nanquantile(c, .9)
            for k in ("LISTING", "DELISTING", "SUSPEND", "STAY_START", "PRICE_CHANGE"):
                H = np.array([[any((ids[i], k, madd(t, -x)) in key for x in range(0, 9)) for t in ev] for i in ix])
                a = np.isfinite(c)
                tab = np.array([[(H[i] & top[i]).sum(), top[i].sum(), (H[i] & a[i] & ~top[i]).sum(), (a[i] & ~top[i]).sum()] for i in range(len(ix))])
                f = lambda t: t[..., 0] / t[..., 1] - t[..., 2] / t[..., 3]
                with np.errstate(all="ignore"):
                    bt = f(tab[rng.integers(0, len(ix), (2000, len(ix)))].sum(1))
                t = tab.sum(0)
                lo, hi = np.nanquantile(bt, [.05, .95])
                o.append(dict(group=q, model=m, event=k, top_cells=t[1], rate_top=t[0] / t[1], rate_rest=t[2] / t[3], diff=f(t), lo=lo, hi=hi))
    pd.DataFrame(o).to_csv(OUT / "d6.csv", index=False)
    # D7
    st = EtsStore(ROOT / cfg["ets_cache"])
    from pbd.forecast_v4.etscache import key as ckey
    wd = np.array([workdays(t) for t in months], float)
    t7 = mrange("2023-07", "2025-06")
    P = np.full((12, len(ids), 24), np.nan)
    miss = 0
    for a in mrange("2022-07", "2025-05"):
        B = WD.betas(Yall, wd, col[a], cfg["beta"]["huber_k"], cfg["beta"]["min_rows"])
        X = RUN.adjusted(Yall, B["beta"], wd, col[a], cfg["center_wd"])
        for i, x in enumerate(X):
            r = st._get(ckey(x, st.ver))
            if r is None:
                miss += 1
                continue
            for h in range(1, 13):
                t = madd(a, h)
                if t in t7:
                    P[h - 1, i, t7.index(t)] = r[0][h - 1] * np.exp(B["beta"][i] * (WD.wd_known(t, cfg["temp_holidays"]) - cfg["center_wd"]))
    W6 = mat(v4, "W", t7)
    k = np.isfinite(P[5]) & np.isfinite(W6)
    assert np.allclose(P[5][k], W6[k], rtol=1e-9), "W h=6 재현 실패"
    Y7 = Y[:, 6:30]
    val = np.isfinite(P).all(0) & np.isfinite(Y7)
    S["d7"] = dict(cache_missing=miss, cells=int(val.sum()), cells_dropped=int((np.isfinite(Y7) & ~val).sum()), w6_checked=int(k.sum()))
    o = []
    for q, ix in {**sel, "나머지": np.flatnonzero(use & ~np.isin(g, list(sel)))}.items():
        for h in range(1, 13):
            e7 = np.where(val, np.log(np.where(val, P[h - 1], 1)) - np.log(np.where(val, Y7, 1)), np.nan)
            o.append(dict(group=q, h=h, wape=wape_by(e7[ix], Y7[ix], w[ix])))
        # h=1 ÷ h=6 성분 재표집
    d7 = pd.DataFrame(o).pivot(index="group", columns="h", values="wape")
    d7.to_csv(OUT / "d7.csv")
    def num(h, ix):
        e7 = np.where(val, np.abs(P[h - 1] - Y7), 0)[ix].sum(1)
        y7 = np.where(val, Y7, 0)[ix].sum(1)
        return np.where(y7 > 0, w[ix] * e7 / np.where(y7 > 0, y7, 1), 0)
    rest = np.flatnonzero(use & ~np.isin(g, list(sel)))
    o = []
    for q, ix in sel.items():
        rg = np.random.default_rng(seed)
        bs = []
        for z in (ix, rest):
            r = rg.integers(0, len(z), (2000, len(z)))
            bs.append(num(1, z)[r].sum(1) / num(6, z)[r].sum(1))
        o.append(dict(group=q, r16=num(1, ix).sum() / num(6, ix).sum(), rest=num(1, rest).sum() / num(6, rest).sum(), lo=np.quantile(bs[0] - bs[1], .05), hi=np.quantile(bs[0] - bs[1], .95)))
    pd.DataFrame(o).to_csv(OUT / "d7_ratio.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(S, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
