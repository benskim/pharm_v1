"""P2 v4 호흡기(H) 성능 저하 분석 — 사후 탐색, 판정 아님. 2026 자료를 읽지 않는다. P2 v4 코드·결과는 고치지 않고 불러 쓴다.
ETS는 outputs/p2v4/cache의 적합을 다시 쓴다(없으면 같은 식으로 새로 적합). H의 ETS 모수만 새로 적합해 outputs/p2v4_resp/ets_params_h.csv에 남긴다.
실행: make p2v4-resp → outputs/p2v4_resp/*.csv, summary.json

결과를 보기 전에 정한 규칙 (2026-11-24)
[예측 네 가지] B3 / W_pre = 보정 자료 ETS 예측(근무일수 22일 기준, 되돌리기 전) / W = 되돌린 예측 / 실제 y
[분해] W_neu = W_pre · exp(β(c̄ − 22)), c̄ = origin까지 같은 달 근무일수 평균(B3의 계절이 담는 평균 달력에 해당)
   적합 몫 = WAPE(W_neu) − WAPE(B3), 달력 몫 = WAPE(W) − WAPE(W_neu). 순서를 바꾼 판: B3cal = B3·exp(β(c_t − c̄)),
   달력 몫' = WAPE(B3cal) − WAPE(B3), 적합 몫' = WAPE(W) − WAPE(B3cal). 두 순서의 부호가 다르면 '갈리지 않음'
[집중] Δ(W − B3)를 성분별 기여로 나눔. 기여가 큰 성분 3개를 빼서 부호가 바뀌면 '일부 성분에 집중'
[세 갈래] 군마다(성분 5개 이상) Δ_2023·Δ_2024·Δ_2025와 성분 재표집 2,000회 90% 구간(예측 고정)
   A 그대로 적용: 세 해 모두 Δ < 0이고 90% 구간 상한 < 0
   B 별도 보정·모델 검토: 세 해 모두 Δ > 0이고 90% 구간 하한 > 0이고 나빠진 성분 60% 이상
   C 판단하기 어려움: 나머지
[수요 변동 칸] 실제 전년 동월비 |log(y_t/y_{t−12})| : < 0.1 / 0.1 ~ 0.3 / ≥ 0.3 (예외 플래그 30%와 맞춘 값). 사후 구분이라 예측 때 알 수 없음
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import numpy as np
import pandas as pd
import yaml

from pbd.forecast_v2 import dm as DM
from pbd.forecast_v2.calendar_kr import workdays
from pbd.forecast_v2.timeaxis import madd, mrange
from pbd.forecast_v4 import run as RUN
from pbd.forecast_v4 import workday as WD
from pbd.forecast_v4.etscache import EtsStore, _init

OUT = ROOT / "outputs/p2v4_resp"
KEYS = ("B3", "W_neu", "B3cal", "W", "W_pre", "W_all", "Wg", "G")


def ets_params(x):
    """ets_path와 같은 선택 규칙으로 적합하고 모수를 남긴다"""
    import warnings
    from pbd.forecast_v2.ets import _fit_one
    ok = np.isfinite(x) & (x > 0)
    n = len(ok) - (np.flatnonzero(~ok)[-1] + 1 if (~ok).any() else 0)
    if n < 36:
        return dict(why="학습 36개월 미만")
    best = bt = None
    for tr in (None, "add"):
        r = _fit_one(np.log(x[-n:]), tr)
        if best is None or r.aicc < best.aicc:
            best, bt = r, tr
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g = float(np.exp(best.forecast(12))[5])
    return dict(why="", trend=bt or "none", alpha=float(best.params[0]), n=n, yhat6=g,
                gamma=float(best.smoothing_seasonal))


def build():
    cfg = yaml.safe_load(RUN.CFG_PATH.read_text(encoding="utf-8"))
    tgt, Y, months, v2m, _ = RUN.load(cfg, False)
    ev = mrange(*cfg["eval"])
    col = {m: j for j, m in enumerate(months)}
    c = np.array([workdays(m) for m in months], float)
    st, cen, h = EtsStore(ROOT / cfg["ets_cache"]), cfg["center_wd"], cfg["h"]
    B = {t: WD.betas(Y, c, col[t] - h, cfg["beta"]["huber_k"], cfg["beta"]["min_rows"]) for t in ev}
    xs = [(t, i, RUN.adjusted(Y, B[t]["beta"], c, col[t] - h, cen)[i]) for t in ev for i in range(len(tgt))]
    pre = {t: np.full(len(tgt), np.nan) for t in ev}
    for (t, i, _), (f, _w) in zip(xs, st.many([x for *_, x in xs], recheck=20, seed=1)):
        pre[t][i] = f[h - 1]
    P = pd.read_csv(ROOT / "outputs/p2v4/predictions.csv", dtype={"c4": str})
    P["c4"] = P.c4.str.zfill(4)
    rows = []
    for t in ev:
        a, j = col[t] - h, col[t]
        cm = np.mean([c[k] for k in range(a + 1) if months[k][5:] == t[5:]])
        b = B[t]["beta"]
        rows.append(pd.DataFrame(dict(c4=tgt.c4, t=t, y=Y[:, j], y12=Y[:, j - 12], beta=b, raw=B[t]["raw"], se=np.sqrt(B[t]["se2"]), b=B[t]["b"], mu=B[t]["mu"],
                                      W_pre=pre[t], cbar=cm, c_known=WD.wd_known(t, cfg["temp_holidays"]), c_act=c[j], dw=c[j] - c[j - 12],
                                      G=v2m["G"][t].to_numpy(float))))
    d = pd.concat(rows).merge(P[["c4", "t", "W", "W_all", "Wg", "B3", "eval"]], on=["c4", "t"])
    d = d[d["eval"]].drop(columns="eval").reset_index(drop=True)
    re = d.W_pre * np.exp(d.beta * (d.c_known - cen))
    ok = d.W_pre.notna()
    assert np.allclose(re[ok], d.W[ok], rtol=1e-9), "W 재현 실패"
    d["W_neu"] = np.where(ok, d.W_pre * np.exp(d.beta * (d.cbar - cen)), d.W)
    d["W_pre"] = d.W_pre.fillna(d.W)
    d["B3cal"] = d.B3 * np.exp(d.beta * (d.c_known - d.cbar))
    d["cal"] = d.beta * (d.c_known - d.cbar)
    g = pd.read_csv(ROOT / "config/p2v3_groups.csv", dtype=str)
    tt = tgt[["c4", "w", "cls3", "in_h"]].merge(g, on="c4", how="left")
    tt["H"] = tt.in_h.astype(str).str.lower().eq("true")
    d = d.merge(tt, on="c4")
    d["k"] = d.w / d.groupby("c4").y.transform("sum")
    d["yr"], d["mon"] = d.t.str[:4], d.t.str[5:].astype(int)
    for m in KEYS:
        d["e_" + m] = d.k * (d[m] - d.y).abs()
        d["le_" + m] = np.log(d[m] / d.y)
    return d, Y, c, months, tgt, B, cfg


def wape(g, m):
    """군 WAPE(군에 든 성분 가중치로 다시 정규화)"""
    return g["e_" + m].sum() / g.drop_duplicates("c4").w.sum()


def delta(g, a="W", b="B3"):
    return wape(g, a) - wape(g, b)


def boot(g, a="W", b="B3", n=2000, seed=0):
    s = g.groupby("c4").agg(a=("e_" + a, "sum"), b=("e_" + b, "sum"), w=("w", "first"))
    ix = np.random.default_rng(seed).integers(0, len(s), (n, len(s)))
    v = (s.a - s.b).to_numpy()[ix].sum(1) / s.w.to_numpy()[ix].sum(1)
    return np.quantile(v, [.05, .95])


def month_stat(g, a="W", b="B3"):
    """대상월 계열의 EWC (군 ΔWAPE)"""
    W = g.drop_duplicates("c4").w.sum()
    D = ((g["e_" + a] - g["e_" + b]).groupby(g.t).sum() / W).reindex(sorted(g.t.unique())).to_numpy()
    r = DM.ewc(len(D) * D)
    return dict(diff=float(r["mean"]), p=float(r["p"]), lo90=float(r["lo90"]), hi90=float(r["hi90"]), months_worse=int((D > 0).sum()), months=len(D))


def verdict(g, seed):
    ys = {y: delta(x) for y, x in g.groupby("yr")}
    lo, hi = boot(g, seed=seed)
    s = g.groupby("c4")[["e_W", "e_B3"]].sum()
    worse = float((s.e_W > s.e_B3).mean())
    v = ("A" if all(x < 0 for x in ys.values()) and hi < 0 else
         "B" if all(x > 0 for x in ys.values()) and lo > 0 and worse >= .6 else "C")
    return dict(n=len(s), wshare=g.drop_duplicates("c4").w.sum(), B3=wape(g, "B3"), W=wape(g, "W"), d=delta(g), lo=lo, hi=hi,
                **{"d" + y: x for y, x in ys.items()}, worse=worse, group_verdict=v)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d, Y, c, months, tgt, B, cfg = build()
    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    S = {}
    H, N = d[d.H], d[~d.H]
    S["cells"] = dict(all=len(d), H=len(H), H_ingr=H.c4.nunique(), N_ingr=N.c4.nunique())
    S["wape"] = {n: {m: wape(g, m) for m in KEYS} for n, g in (("전체", d), ("H", H), ("H 밖", N))}
    S["month_test"] = {n: month_stat(g) for n, g in (("전체", d), ("H", H), ("H 밖", N))}
    S["verdict"] = {n: verdict(g, seed) for n, g in (("전체", d), ("H", H), ("H 밖", N))}
    # 1 집중
    s = H.groupby("c4").agg(w=("w", "first"), cls3=("cls3", "first"), atc2=("atc2_69", "first"), e_W=("e_W", "sum"), e_B3=("e_B3", "sum"), n=("t", "size"))
    s["wape_B3"], s["wape_W"] = s.e_B3 / s.w, s.e_W / s.w
    s["contrib"] = (s.e_W - s.e_B3) / s.w.sum()
    s = s.sort_values("contrib", ascending=False)
    s["cum"] = s.contrib.cumsum()
    s.to_csv(OUT / "h_ingredients.csv")
    top = list(s.index)
    S["conc"] = dict(total=s.contrib.sum(), pos=s.contrib[s.contrib > 0].sum(), neg=s.contrib[s.contrib < 0].sum(), worse_share=float((s.contrib > 0).mean()),
                     geo_ratio=float(np.exp(np.log(s.wape_W / s.wape_B3).mean())), median_ratio=float((s.wape_W / s.wape_B3).median()),
                     drop={k: delta(H[~H.c4.isin(top[:k])]) for k in (1, 3, 5, 10)}, top=s.head(5).reset_index()[["c4", "cls3", "w", "wape_B3", "wape_W", "contrib"]].to_dict("records"))
    # 2·3 분해
    S["decomp"] = {n: dict(fit=wape(g, "W_neu") - wape(g, "B3"), cal=wape(g, "W") - wape(g, "W_neu"),
                           cal2=wape(g, "B3cal") - wape(g, "B3"), fit2=wape(g, "W") - wape(g, "B3cal")) for n, g in (("전체", d), ("H", H), ("H 밖", N))}
    for n, g in (("H", H), ("H 밖", N)):
        for y, x in g.groupby("yr"):
            S["decomp"][f"{n} {y}"] = dict(fit=wape(x, "W_neu") - wape(x, "B3"), cal=wape(x, "W") - wape(x, "W_neu"),
                                          cal2=wape(x, "B3cal") - wape(x, "B3"), fit2=wape(x, "W") - wape(x, "B3cal"))
    # 달력 신호: B3 로그오차를 달력 기대치(−cal)에 회귀. 맞으면 기울기 1
    def cal_signal(g):
        r = {}
        for nm in ("B3", "W_neu"):
            v, u, kw = -g.cal, g["le_" + nm], g.k * g.y
            m = lambda z: (kw * z).sum() / kw.sum()
            r[nm] = dict(slope=((kw * (v - m(v)) * (u - m(u))).sum() / (kw * (v - m(v)) ** 2).sum()), sd_cal=np.sqrt(m((v - m(v)) ** 2)), sd_err=np.sqrt(m((u - m(u)) ** 2)))
        return r
    S["cal_signal"] = {n: cal_signal(g) for n, g in (("H", H), ("H 밖", N))}
    S["cal_signal_yr"] = {f"{n} {y}": cal_signal(x)["B3"]["slope"] for n, g in (("H", H), ("H 밖", N)) for y, x in g.groupby("yr")}
    # 계수
    last = d[d.t == "2025-12"]
    S["beta"] = {n: dict(raw_p25=g.raw.quantile(.25), raw_med=g.raw.median(), raw_p75=g.raw.quantile(.75), used_med=g.beta.median(), se_med=g.se.median(), b_med=g.b.median(),
                         mu=g.mu.iloc[0]) for n, g in (("H", last[last.H]), ("H 밖", last[~last.H]))}
    # 4 월·계절·변동
    def tab(key, name):
        o = []
        for n, g in (("H", H), ("H 밖", N)):
            for q, x in g.groupby(key):
                o.append(dict(set=n, key=q, cells=len(x), **{m: wape(x, m) for m in ("B3", "W_neu", "W", "G")}, d=delta(x), bias_B3=(x.k * (x.B3 - x.y)).sum() / (x.k * x.y).sum(),
                              bias_W=(x.k * (x.W - x.y)).sum() / (x.k * x.y).sum(), contrib=(x.e_W - x.e_B3).sum() / g.drop_duplicates("c4").w.sum()))
        pd.DataFrame(o).to_csv(OUT / f"{name}.csv", index=False)
        return pd.DataFrame(o)
    for g in (d, H, N):
        pass
    for x in (H, N):
        r = np.log(x.y / x.y12).abs()
        x.loc[:, "shock"] = np.select([r < .1, r < .3, r >= .3], ["1 <0.1", "2 0.1~0.3", "3 ≥0.3"], "4 전년 없음") if len(x) else None
        x.loc[:, "dir"] = np.where(np.log(x.y / x.y12) > 0, "증가", "감소")
        x.loc[:, "season"] = x.mon.map(lambda m: "겨울 11~3" if m in (11, 12, 1, 2, 3) else "여름 6~8" if m in (6, 7, 8) else "그 밖")
        x.loc[:, "adw"] = x.dw.abs().clip(upper=2).astype(int).astype(str)
    for k in ("t", "yr", "season", "shock", "dir", "adw", "mon"):
        tab(k, "by_" + k)
    # 성분 변동성(2018-01 ~ 2022-07의 전년비 표준편차, 평가 전 자료)과 Δ
    a0 = months.index("2022-07")
    L = np.log(np.where(Y[:, :a0 + 1] > 0, Y[:, :a0 + 1], np.nan))
    vol = pd.Series(np.nanstd(L[:, 12:] - L[:, :-12], axis=1), index=tgt.c4)
    si = d.groupby("c4").agg(w=("w", "first"), H=("H", "first"), e_W=("e_W", "sum"), e_B3=("e_B3", "sum"))
    si["vol"] = vol
    si["lr"] = np.log(si.e_W / si.e_B3)
    si["q"] = pd.qcut(si.vol, 4, labels=["Q1", "Q2", "Q3", "Q4"]).astype(str)
    vt = si.groupby("q").apply(lambda g: pd.Series(dict(n=len(g), H=int(g.H.sum()), vol=g.vol.median(), B3=g.e_B3.sum() / g.w.sum(), W=g.e_W.sum() / g.w.sum(), improved=(g.lr < 0).mean(), geo=np.exp(g.lr.mean()))))
    vt.to_csv(OUT / "by_vol.csv")
    vh = si.groupby(["H", "q"]).apply(lambda g: pd.Series(dict(n=len(g), B3=g.e_B3.sum() / g.w.sum(), W=g.e_W.sum() / g.w.sum(), improved=(g.lr < 0).mean(), geo=np.exp(g.lr.mean()))))
    vh.to_csv(OUT / "by_vol_h.csv")
    S["vol_spearman"] = {n: float(x[["vol", "lr"]].corr("spearman").iloc[0, 1]) for n, x in (("전체", si), ("H", si[si.H]), ("H 밖", si[~si.H]))}
    # H 밖에서 변동성이 H만큼 큰 성분(H의 중앙값 이상)
    hv = si[si.H].vol.median()
    z = N[N.c4.isin(si.index[(~si.H) & (si.vol >= hv)])]
    S["nonH_highvol"] = dict(thr=float(hv), **verdict(z, seed))
    # 5 일반화: 군별
    o = []
    for lab in ("atc1", "atc2_69"):
        for q, g in d.groupby(lab):
            if g.c4.nunique() >= 5:
                o.append(dict(level=lab, group=q, **verdict(g, seed)))
    gv = pd.DataFrame(o)
    gv.to_csv(OUT / "groups.csv", index=False)
    S["gen"] = dict(improved=float((si.lr < 0).mean()), geo=float(np.exp(si.lr.mean())),
                    improved_nonH=float((si[~si.H].lr < 0).mean()),
                    improved_year={y: float((x.groupby("c4").e_W.sum() < x.groupby("c4").e_B3.sum()).mean()) for y, x in d.groupby("yr")},
                    counts={lab: gv[gv.level == lab].group_verdict.value_counts().to_dict() for lab in ("atc1", "atc2_69")},
                    loo={q: delta(d[d.atc1 != q]) for q in sorted(d.atc1.unique())},
                    size={q: verdict(d[d.c4.isin(ix)], seed) for q, ix in si.groupby(pd.qcut(si.w, 4, labels=["작음", "중하", "중상", "큼"]).astype(str)).groups.items()})
    tot = (si.e_W - si.e_B3).sum()
    best = (si.e_W - si.e_B3).sort_values()
    S["gen"]["top10_share"] = float(best.head(10).sum() / tot)
    S["gen"]["drop_top10"] = delta(d[~d.c4.isin(best.head(10).index)])
    S["h_sub"] = {q: verdict(g, seed) for q, g in H.groupby("atc2_69") if g.c4.nunique() >= 3}
    # 6 ETS 모수(H만, B3 자료와 보정 자료)
    f = OUT / "ets_params_h.csv"
    if not f.exists():
        ids, xs = [], []
        pos = {v: i for i, v in enumerate(tgt.c4)}
        for t in sorted(x for x in H.t.unique() if x[5:] in ("01", "04", "07", "10")):   # 분기별 12개 대상월
            a = months.index(t) - 6
            for q in sorted(H.c4.unique()):
                i = pos[q]
                for nm, x in (("B3", Y[i, :a + 1]), ("W", RUN.adjusted(Y[i:i + 1], B[t]["beta"][i:i + 1], c, a, cfg["center_wd"])[0])):
                    ids.append((q, t, nm)); xs.append(x)
        _init()
        with ProcessPoolExecutor(7, initializer=_init) as ex:
            rs = list(ex.map(ets_params, xs, chunksize=8))
        pd.DataFrame([dict(c4=q, t=t, m=m, **r) for (q, t, m), r in zip(ids, rs)]).to_csv(f, index=False)
    e = pd.read_csv(f, dtype={"c4": str})
    e = e[e.why.isna() | (e.why == "")]
    chk = e.merge(H[["c4", "t", "B3", "W_pre"]], on=["c4", "t"])
    ref = np.where(chk.m == "B3", chk.B3, chk.W_pre)
    assert np.allclose(chk.yhat6, ref, rtol=1e-6), "ETS 모수 적합이 P2 v4 예측과 다르다"
    S["ets_check_cells"] = len(chk)
    S["ets"] = {m: dict(trend_share=float((g.trend == "add").mean()), alpha_med=float(g.alpha.median()), alpha_hi=float((g.alpha > .9).mean()), gamma_med=float(g.gamma.median())) for m, g in e.groupby("m")}
    pv = e.pivot(index=["c4", "t"], columns="m", values=["trend", "alpha"]).dropna()
    hh = H.set_index(["c4", "t"])
    hh = hh.loc[hh.index.intersection(pv.index)].copy()
    hh["sw"] = (pv["trend"]["B3"] + "→" + pv["trend"]["W"]).reindex(hh.index)
    hh["dalpha"] = (pv["alpha"]["W"].astype(float) - pv["alpha"]["B3"].astype(float)).reindex(hh.index)
    hh = hh.reset_index()
    tw = H.drop_duplicates("c4").w.sum()
    S["switch"] = {q: dict(cells=len(g), contrib=float((g.e_W - g.e_B3).sum() / tw), fit=float((g.e_W_neu - g.e_B3).sum() / tw), cal=float((g.e_W - g.e_W_neu).sum() / tw)) for q, g in hh.groupby("sw")}
    hh["ab"] = pd.cut(hh.dalpha, [-2, -.1, .1, 2], labels=["낮아짐", "비슷", "높아짐"]).astype(str)
    S["alpha_change"] = {q: dict(cells=len(g), contrib=float((g.e_W - g.e_B3).sum() / tw)) for q, g in hh.groupby("ab")}
    # ---- 추가 점검 (1차 결과를 본 뒤 정함: H의 원추정 β가 작고 줄임이 공통 값으로 당긴다는 관찰에서)
    hs = set(H.c4)
    o = []
    for t in ("2022-07", *sorted(H.t.unique())[::6], "2025-12"):              # origin별(대상월 t의 origin) 원추정·사용 β. 2022-07 열은 대상월
        b = B.get(t) or WD.betas(Y, c, months.index(t) - 6, cfg["beta"]["huber_k"], cfg["beta"]["min_rows"])
        for n, q in (("H", tgt.c4.isin(hs).to_numpy()), ("H 밖", tgt.c4.isin(set(N.c4)).to_numpy())):
            o.append(dict(t=t, set=n, raw_med=np.nanmedian(b["raw"][q]), used_med=np.median(b["beta"][q]), b_med=np.median(b["b"][q]), raw_pos=float(np.nanmean(b["raw"][q] > 0))))
    pd.DataFrame(o).to_csv(OUT / "beta_by_origin.csv", index=False)
    # 달 단위: 성분들의 u 중앙값(달마다 하나)을 v에 회귀. 같은 달 성분이 함께 움직이는 문제를 피함. 기간별
    u, v = WD.diff_rows(Y, c, len(months) - 1)
    um = months[13:]
    o = []
    for n, q in (("H", tgt.c4.isin(hs).to_numpy()), ("H 밖", tgt.c4.isin(set(N.c4)).to_numpy())):
        z = np.nanmedian(u[q], axis=0)
        for nm, lo, hi in (("전체", "2019-02", "2025-12"), ("코로나 전", "2019-02", "2019-12"), ("코로나", "2020-01", "2021-12"), ("회복기", "2022-01", "2022-12"), ("평가 2023~2025", "2023-01", "2025-12"),
                           ("첫 origin까지", "2019-02", "2022-07")):
            k = np.array([lo <= m <= hi for m in um]) & np.isfinite(z)
            b, e = z[k] @ v[k] / (v[k] @ v[k]), None
            r = z[k] - b * v[k]
            # 이중 차분의 음의 자기상관을 고려한 Bartlett 2 표준오차
            g = v[k] * r
            om = (g @ g) + 2 * sum((1 - l / 3) * (g[l:] @ g[:-l]) for l in (1, 2))
            o.append(dict(set=n, period=nm, months=int(k.sum()), beta=b, se=np.sqrt(max(om, 0)) / (v[k] @ v[k]), resid_sd=r.std()))
    pd.DataFrame(o).to_csv(OUT / "beta_monthly.csv", index=False)
    # 보정 강도 λ: log ŷ = log B3 + λ(log W − log B3) (λ = 0 → B3, 1 → W). 근사 반사실, 고르는 데 쓰지 않음
    o = []
    for n, g in (("H", H), ("H 밖", N), *((f"H {q}", x) for q, x in H.groupby("yr"))):
        for lam in (-.5, 0, .1, .2, .3, .5, .75, 1, 1.25):
            p = g.B3 * (g.W / g.B3) ** lam
            o.append(dict(set=n, lam=lam, wape=(g.k * (p - g.y).abs()).sum() / g.drop_duplicates("c4").w.sum()))
    pd.DataFrame(o).pivot(index="set", columns="lam", values="wape").to_csv(OUT / "strength.csv")
    # alpha ≈ 1이면 W/B3 ≈ exp(β[(c_t − c̄_t) − (c_a − c̄_a)]). 근사가 맞는지
    ca = {t: (c[months.index(t) - 6] - np.mean([c[k] for k in range(months.index(t) - 5) if months[k][5:] == months[months.index(t) - 6][5:]])) for t in H.t.unique()}
    q = H[(H.W > 0)].copy()
    q["pred"] = q.beta * (q.c_known - q.cbar - q.t.map(ca))
    q["act"] = np.log(q.W / q.B3)
    S["approx"] = dict(corr=float(q[["pred", "act"]].corr().iloc[0, 1]), corr_calonly=float(np.corrcoef(q.cal, q.act)[0, 1]), abs_act=float(q.act.abs().mean()))
    mm = (H.e_W - H.e_B3).groupby(H.t).sum() / tw
    S["month_conc"] = dict(total=float(mm.sum()), top5=mm.sort_values(ascending=False).head(5).to_dict(), low5=mm.sort_values().head(5).to_dict(), drop_top3=float(mm.sort_values(ascending=False).iloc[3:].sum()))
    H.drop(columns=["in_h"]).to_csv(OUT / "h_cells.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(S, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
