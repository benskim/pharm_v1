"""P2 v3 실험 r2 (docs/PLAN_P2_v3.md §13): 분류군 차이를 쓴 두 방식 대 전역 G.
 E1  = 군별 G·ETS(B3) 선형 결합, 가중치 α_g는 origin마다 직전 12개월(τ ≤ a) 성적으로, 부분 풀링으로 줄임
 E1g = 전역 결합(α 하나, 대조)
 E2  = 전역 G + 군 특징(ATC 범주, 2022-07 이전 군 패턴, 자기 성분을 뺀 같은 군 동조 흐름), E2a·E2b·E2c = 특징 묶음 하나씩

python -m pbd.forecast_v3.exp --smoke : 잠금 전 동작 확인. 평가 기간 밖(2022), 일부 성분. 오차 숫자를 출력·저장하지 않는다
python -m pbd.forecast_v3.exp         : 본 실행. 태그의 잠금 파일과 작업 파일이 같을 때만 → outputs/p2v3/exp/
P2 v2 코드(src/pbd/forecast_v2/)는 고치지 않고 가져다 쓴다. G 학습은 v2 gbm.fit_predict와 같은 식을 특징 확장만 열어 옮겼다(시험으로 같음 확인).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import yaml

from pbd.common import ROOT
from pbd.forecast_v2.backtest import Backtest, matrices
from pbd.forecast_v2.gbm import CATS, FEATS, PARAMS, Ctx, _pos, features, target_vals
from pbd.forecast_v2.timeaxis import mrange
from pbd.forecast_v3.pooling import eb_fit

CFG_PATH = ROOT / "config/p2v3_exp.yaml"
OUT = ROOT / "outputs/p2v3/exp"
LOCKED = ("docs/PLAN_P2_v3.md", "config/p2v3_exp.yaml", "config/p2v3_groups.csv", "config/p2v3_group_features.csv",
          "src/pbd/forecast_v3/exp.py", "src/pbd/forecast_v3/exp_report.py", "src/pbd/forecast_v3/pooling.py",
          "src/pbd/forecast_v2/backtest.py", "src/pbd/forecast_v2/gbm.py", "src/pbd/forecast_v2/ets.py",
          "src/pbd/forecast_v2/baselines.py", "src/pbd/forecast_v2/evaluate.py", "src/pbd/forecast_v2/dm.py",
          "src/pbd/forecast_v2/metrics.py")
ALPHAS = np.round(np.arange(0, 1.0001, 0.1), 1)
E2_SETS = {"E2": ("a", "b", "c"), "E2a": ("a",), "E2b": ("b",), "E2c": ("c",)}


# ---------------------------------------------------------------- 잠금
def lock_check(tag: str) -> dict:
    """태그가 있고 잠금 파일이 태그와 같아야 한다. 아니면 PermissionError"""
    ok = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], cwd=ROOT, capture_output=True)
    if ok.returncode != 0:
        raise PermissionError(f"태그 {tag}가 없다 — 본 실행 거부")
    diff = []
    for p in LOCKED:
        r = subprocess.run(["git", "show", f"{tag}:{p}"], cwd=ROOT, capture_output=True)
        if r.returncode != 0 or r.stdout != (ROOT / p).read_bytes():
            diff.append(p)
    if diff:
        raise PermissionError(f"잠금 파일이 태그 {tag}와 다르다: {diff}")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    tagc = subprocess.run(["git", "rev-list", "-n", "1", tag], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"tag": tag, "tag_commit": tagc, "head": head, "locked": list(LOCKED)}


# ---------------------------------------------------------------- E2: 특징 확장 G
class Groups:
    """성분 순서대로 군 정보. a: ATC 범주 코드, b: 군 패턴(고정), c: 동조 흐름용 군 번호"""

    def __init__(self, grp: pd.DataFrame, gfeat: pd.DataFrame):
        self.atc1 = pd.factorize(grp.atc1, sort=True)[0]
        self.atc2 = pd.factorize(grp.atc2_54, sort=True)[0]
        gf = gfeat.set_index("atc2_54").reindex(grp.atc2_54)
        self.seas, self.ws = gf.grp_seas.to_numpy(float), gf.grp_ws.to_numpy(float)


def comove(Y: np.ndarray, g: np.ndarray, ap: int) -> tuple[np.ndarray, np.ndarray]:
    """기준 열 ap에서 자기 성분을 뺀 같은 군의 전년 대비 흐름. co1 = 1개월, co3 = 3개월 합. ap 이하 열만 읽는다.
    j는 필요한 달이 모두 관측(> 0)된 같은 군 성분. 그런 j가 자기 말고 없으면 NaN."""
    Yp = _pos(Y)
    out = []
    for cur, prev in (([ap], [ap - 12]), ([ap - 2, ap - 1, ap], [ap - 14, ap - 13, ap - 12])):
        if min(prev) < 0:
            out.append(np.full(Y.shape[0], np.nan))
            continue
        ok = np.isfinite(Yp[:, cur + prev]).all(1)
        sc = np.where(ok, Yp[:, cur].sum(1), 0.0)
        sp = np.where(ok, Yp[:, prev].sum(1), 0.0)
        K = g.max() + 1
        Sc, Sp, N = (np.bincount(g, weights=x, minlength=K) for x in (sc, sp, ok.astype(float)))
        oc, op, on = Sc[g] - sc, Sp[g] - sp, N[g] - ok
        with np.errstate(invalid="ignore", divide="ignore"):
            out.append(np.where((on > 0) & (oc > 0) & (op > 0), np.log(oc / op), np.nan))
    return out[0], out[1]


def features_ext(c: Ctx, tau: int, h: int, G: Groups | None, sets) -> pd.DataFrame:
    f = features(c, tau, h)
    if G is None or not sets:
        return f
    if "a" in sets:
        f["atc1"], f["atc2"] = G.atc1, G.atc2
    if "b" in sets:
        f["grp_seas"], f["grp_ws"] = G.seas, G.ws
    if "c" in sets:
        f["co1"], f["co3"] = comove(c.Y, G.atc2, tau - h)
    return f


def ext_cols(sets) -> tuple[list[str], list[str]]:
    feats, cats = list(FEATS), list(CATS)
    if "a" in sets:
        feats += ["atc1", "atc2"]
        cats += ["atc1", "atc2"]
    if "b" in sets:
        feats += ["grp_seas", "grp_ws"]
    if "c" in sets:
        feats += ["co1", "co3"]
    return feats, cats


def fit_predict_ext(c: Ctx, a: int, h: int, seed: int, G: Groups | None = None, sets=()) -> tuple[np.ndarray, int, dict]:
    """v2 gbm.fit_predict와 같은 식(행 선택·범주 처리·하이퍼파라미터). 추가 특징의 결측은 LightGBM 결측 처리(행을 빼지 않음).
    반환 (ŷ_t, 학습 행 수, 특징 중요도 gain)"""
    import lightgbm as lgb
    feats, cats = ext_cols(sets)
    lo = 14 + h
    X, z = [], []
    for tau in range(lo, a + 1):
        f = features_ext(c, tau, h, G, sets)
        y = target_vals(c, tau, "qty")
        ok = f[["g1", "g3", "seas", "size"]].notna().all(1).to_numpy() & np.isfinite(y)
        if ok.any():
            X.append(f[ok])
            z.append(y[ok])
    t = a + h
    ft = features_ext(c, t, h, G, sets)
    base = _pos(c.Y)[:, t - 12]
    out = np.full(c.Y.shape[0], np.nan)
    if not X:
        return out, 0, {}
    X = pd.concat(X, ignore_index=True)
    z = np.concatenate(z)
    for k in cats:
        X[k] = pd.Categorical(X[k].astype(int), categories=sorted(set(X[k].astype(int)) | set(ft[k].astype(int))))
    m = lgb.LGBMRegressor(random_state=seed, **PARAMS)
    m.fit(X[feats], z, categorical_feature=cats)
    okt = ft[["g1", "g3", "seas", "size"]].notna().all(1).to_numpy() & np.isfinite(base)
    if okt.any():
        Xt = ft[okt].copy()
        for k in cats:
            Xt[k] = pd.Categorical(Xt[k].astype(int), categories=X[k].cat.categories)
        out[okt] = base[okt] * np.exp(m.predict(Xt[feats]))
    imp = dict(zip(feats, m.booster_.feature_importance("gain").tolist()))
    return out, len(z), imp


# ---------------------------------------------------------------- E1: 군별 결합 가중치
def wape_rows(y: np.ndarray, p: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """성분별 WAPE (창 안 ok 칸). 칸이 없으면 NaN"""
    Y = np.where(ok, y, 0.0).sum(1)
    E = np.where(ok, np.abs(y - p), 0.0).sum(1)
    return np.divide(E, Y, out=np.full(len(Y), np.nan), where=Y > 0)


def group_loss(wr: np.ndarray, w: np.ndarray, lab: np.ndarray, K: int) -> np.ndarray:
    """군 금액 가중 WAPE(군 안 가중치 재정규화). wr: 성분 × α. 성분이 없는 군 = NaN"""
    okr = np.isfinite(wr).all(1)
    W = np.bincount(lab[okr], weights=w[okr], minlength=K)
    out = np.full((K, wr.shape[1]), np.nan)
    for k in range(wr.shape[1]):
        S = np.bincount(lab[okr], weights=w[okr] * wr[okr, k], minlength=K)
        out[:, k] = np.divide(S, W, out=np.full(K, np.nan), where=W > 0)
    return out


def pick_alpha(L: np.ndarray) -> int:
    """손실 곡선에서 최소 α 번호(같으면 작은 α = G 쪽). 곡선이 모두 NaN이면 0(G)"""
    if not np.isfinite(L).any():
        return 0
    m = np.nanmin(L)
    return int(np.flatnonzero(np.isclose(L, m, rtol=1e-9, atol=1e-12))[0])   # 부동소수 오차 안의 같음도 같음


def shrunk_alpha(Lg: np.ndarray, L1: np.ndarray, L0: np.ndarray, b: float, B: float) -> int:
    """L*_1 = B·L1 + (1 − B)·L0, L*_g = b·Lg + (1 − b)·L*_1. 군·상위 곡선이 NaN이면 상위 곡선을 쓴다"""
    L1s = L0 if not np.isfinite(L1).all() else B * L1 + (1 - B) * L0
    Lgs = L1s if not np.isfinite(Lg).all() else b * Lg + (1 - b) * L1s
    return pick_alpha(Lgs)


def e1_weights(Y, Gp: dict, Bp: dict, a: int, w: np.ndarray, lower: np.ndarray, window: int, min_cells: int) -> dict:
    """origin a에서 성분별 α(E1)와 전역 α(E1g). 창 τ ∈ [a − window + 1, a], 그 τ를 예측한 이전 origin의 예측만, τ ≤ a 정답만"""
    taus = [tau for tau in range(a - window + 1, a + 1) if tau in Gp and tau in Bp]
    y = np.stack([Y[:, t] for t in taus], 1)
    g = np.stack([Gp[t] for t in taus], 1)
    b3 = np.stack([Bp[t] for t in taus], 1)
    ok = np.isfinite(y) & (y > 0) & np.isfinite(g) & (g > 0) & np.isfinite(b3) & (b3 > 0)
    use = ok.sum(1) >= min_cells
    wr = np.stack([wape_rows(y, (1 - al) * g + al * b3, ok) for al in ALPHAS], 1)
    wr[~use] = np.nan
    g2 = pd.Index(sorted(set(lower)))
    l2 = g2.get_indexer(lower)
    top2 = pd.factorize(pd.Series(g2.str[0]), sort=True)[0]
    l1 = top2[l2]
    Lg = group_loss(wr, w, l2, len(g2))
    L1 = group_loss(wr, w, l1, top2.max() + 1)
    L0 = group_loss(wr, w, np.zeros(len(w), dtype=int), 1)[0]
    # 줄임 계수: 창 안 성분별 log(WAPE_G ÷ WAPE_B3)에 DL 경험적 베이즈
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.log(wr[:, 0] / wr[:, -1])
    fin = use & np.isfinite(lr)
    _, _, _, dg = eb_fit(lr[fin], w[fin], l2[fin], top2, "dl")
    # 군 안 분산 σ² = 0이면 eb_fit의 계수가 NaN이 된다 → 잡음 없음 = 군 값 그대로(1)
    bg = np.where(np.isfinite(dg["b_atc2"]), dg["b_atc2"], 1.0)
    B1 = np.where(np.isfinite(dg["B_atc1"]), dg["B_atc1"], 1.0)
    ag = np.array([shrunk_alpha(Lg[k], L1[top2[k]], L0, bg[k], B1[top2[k]]) for k in range(len(g2))])
    a0 = pick_alpha(L0)
    return {"alpha_i": ALPHAS[ag[l2]], "alpha_global": float(ALPHAS[a0]), "alpha_group": dict(zip(g2, ALPHAS[ag].tolist())),
            "b_group": dict(zip(g2, np.asarray(bg, dtype=float).tolist())), "n_used": int(use.sum()), "taus": len(taus)}


# ---------------------------------------------------------------- 실행
def run(cfg: dict, seed: int, smoke: bool) -> dict:
    import duckdb

    h = cfg["h"]
    ev = cfg["smoke"]["eval"] if smoke else cfg["eval"]
    tgt = pd.read_csv(ROOT / "outputs/p2v2/target.csv", dtype={"c4": str, "cls3": str})
    tgt["c4"] = tgt.c4.str.zfill(4)
    grp = pd.read_csv(ROOT / cfg["groups_file"], dtype=str).set_index("c4").reindex(tgt.c4)
    gfeat = pd.read_csv(ROOT / cfg["group_features_file"], dtype={"atc2_54": str})
    assert grp.notna().all().all() and set(grp.atc2_54) <= set(gfeat.atc2_54)
    if smoke:
        keep = grp.atc1.isin(cfg["smoke"]["atc1"]).to_numpy()
        tgt, grp = tgt[keep].reset_index(drop=True), grp[keep]
    GR = Groups(grp, gfeat)
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    panel = con.execute("""select c4, month, observed, claim_qty, cast(claim_amt as double) claim_amt,
                                  cast(claim_cnt as double) claim_cnt from fc_panel where month <= ? order by c4, month""",
                        [ev[1]]).df()
    Y, A, C, months, audit = matrices(panel, tgt, "2018-01", ev[1])
    bt = Backtest(Y, A, months, tgt, seed, C)
    w = tgt.w.to_numpy(float)
    win = cfg["e1"]["window"]
    ev_t = mrange(*ev)
    base_t = mrange(months[bt.col(ev_t[0]) - h - win + 1], ev[1])   # E1 창에 필요한 G·B3 대상월부터
    t0 = time.time()
    cache = b3_cache(cfg["b3"], tgt.c4)
    Gp, Bp, b3chk, timing = {}, {}, None, {"G": 0.0, "B3_fresh": 0.0, "E2": 0.0, "E1": 0.0}
    for t in base_t:
        j = bt.col(t)
        a = j - h
        s0 = time.time()
        Gp[j], _, _ = fit_predict_ext(bt.ctx(a), a, h, seed)
        timing["G"] += time.time() - s0
        if t <= cfg["b3"]["fresh_until"]:
            s0 = time.time()
            Bp[j] = bt.predict(t, h, models=("B3",))["B3"]
            timing["B3_fresh"] += time.time() - s0
            if t == cfg["b3"]["check_month"]:
                b3chk = same_pred(Bp[j], cache[t].to_numpy(float), t)
                if not b3chk["ok"]:
                    raise RuntimeError(f"B3 새 적합과 캐시가 다르다: {b3chk}")
        else:
            Bp[j] = cache[t].to_numpy(float)
    E2p = {k: {} for k in E2_SETS}
    imp = {k: [] for k in E2_SETS}
    E1p, E1gp, winfo = {}, {}, {}
    lower = grp[cfg["e1"]["lower"]].to_numpy()
    for t in ev_t:
        j = bt.col(t)
        a = j - h
        s0 = time.time()
        for k, sets in E2_SETS.items():
            E2p[k][j], _, im = fit_predict_ext(bt.ctx(a), a, h, seed, GR, sets)
            imp[k].append(im)
        timing["E2"] += time.time() - s0
        s0 = time.time()
        wi = e1_weights(Y, Gp, Bp, a, w, lower, win, cfg["e1"]["min_cells"])
        timing["E1"] += time.time() - s0
        al = wi["alpha_i"]
        E1p[j] = (1 - al) * Gp[j] + al * Bp[j]
        E1gp[j] = (1 - wi["alpha_global"]) * Gp[j] + wi["alpha_global"] * Bp[j]
        winfo[t] = {k: v for k, v in wi.items() if k != "alpha_i"} | {"a": months[a]}
    cols = [bt.col(t) for t in ev_t]
    st = lambda D: np.stack([D[j] for j in cols], 1)
    pred = {"G": st(Gp), "B3": st(Bp), "E1": st(E1p), "E1g": st(E1gp), **{k: st(v) for k, v in E2p.items()}}
    y = np.stack([Y[:, j] for j in cols], 1)
    return dict(tgt=tgt, grp=grp, months=months, ev_t=ev_t, base_t=base_t, y=y, w=w, pred=pred, Gall=Gp, Ball=Bp, bt=bt,
                winfo=winfo, imp=imp, seconds=time.time() - t0, b3_check=b3chk, timing=timing)


def b3_cache(c: dict, c4s: pd.Series) -> pd.DataFrame:
    """P2 v2 백테스트 B3 예측(성분 × 대상월). 파일 해시가 설정과 같아야 한다"""
    import hashlib
    path = ROOT / c["cache"]
    hsh = hashlib.sha256(path.read_bytes()).hexdigest()
    if hsh != c["cache_sha256"]:
        raise RuntimeError(f"B3 캐시 해시가 다르다: {hsh}")
    v2 = pd.read_csv(path, dtype={"c4": str})
    v2["c4"] = v2.c4.str.zfill(4)
    return v2[v2.model == "B3"].pivot(index="c4", columns="t", values="yhat").reindex(index=c4s)


def same_pred(new: np.ndarray, old: np.ndarray, label: str) -> dict:
    same = bool((np.isfinite(new) == np.isfinite(old)).all())
    f = np.isfinite(new) & np.isfinite(old)
    rel = float(np.max(np.abs(new[f] - old[f]) / np.abs(old[f]))) if f.any() else float("nan")
    return {"months": label, "cells": int(f.sum()), "same_nan_pattern": same, "max_rel_diff": rel, "ok": same and f.any() and rel <= 1e-9}


def eval_mask(R: dict, smoke: bool) -> np.ndarray:
    """평가 칸 = 정답 유한(관측·> 0·재등장 첫 달 아님) ∩ E1·E2 예측 ∩ (본 실행) P2 v2 B1·B2·B3·G 예측"""
    m = np.isfinite(R["y"])
    for k in ("G", "E1", "E1g", *E2_SETS):
        m &= np.isfinite(R["pred"][k])
    if not smoke:
        v2 = pd.read_csv(ROOT / "outputs/p2v2/backtest/predictions.csv", dtype={"c4": str})
        v2["c4"] = v2.c4.str.zfill(4)
        piv = v2[v2.model.isin(["B1", "B2", "B3", "G"])].pivot_table(index=["c4", "t"], columns="model", values="yhat")
        for b in ("B1", "B2", "B3", "G"):
            M = piv[b].unstack("t").reindex(index=R["tgt"].c4, columns=R["ev_t"]).to_numpy()
            m &= np.isfinite(M)
    return m


def reproduction(R: dict) -> dict:
    """다시 계산한 G = P2 v2 G(대상월 2022-01 ~). B3는 확인 달의 새 적합 = 캐시(run에서 확인)"""
    v2 = pd.read_csv(ROOT / "outputs/p2v2/backtest/predictions.csv", dtype={"c4": str})
    v2["c4"] = v2.c4.str.zfill(4)
    ts = [t for t in R["base_t"] if t >= "2022-01"]
    res = {}
    old = v2[v2.model == "G"].pivot(index="c4", columns="t", values="yhat").reindex(index=R["tgt"].c4, columns=ts).to_numpy()
    new = np.stack([R["Gall"][R["bt"].col(t)] for t in ts], 1)
    res["G"] = same_pred(new, old, f"{ts[0]} ~ {ts[-1]}")
    res["B3 (새 적합 대 캐시)"] = R["b3_check"]
    return res


def evaluate(R: dict, mask: np.ndarray, cfg: dict) -> dict:
    from pbd.forecast_v2 import evaluate as EV
    from pbd.forecast_v2.metrics import decompose

    em = R["ev_t"]
    sl = lambda p: slice(em.index(p[0]), em.index(p[1]) + 1)
    r1, r2 = sl(cfg["r1"]), sl(cfg["r2"])
    y, w, P = R["y"], R["w"], R["pred"]
    cmp = lambda a, b: EV.compare(y, P[a], P[b], w, mask, r1, r2, cfg["delta"], cfg["h"])
    main = {a: cmp(a, "G") for a in cfg["arms_main"]}
    adj = EV.holm([main[a]["p"] for a in cfg["arms_main"]])
    for k, a in enumerate(cfg["arms_main"]):
        main[a]["p_holm"] = adj[k]
        main[a]["verdict_holm"] = EV.rejudge(main[a], adj[k], cfg["delta"])
    aux = {"E1g_vs_G": cmp("E1g", "G"), "E1_vs_E1g": cmp("E1", "E1g"),
           **{f"{k}_vs_G": cmp(k, "G") for k in ("E2a", "E2b", "E2c")}}
    success = {"E1": str(main["E1"]["verdict_holm"]).startswith("나음") and aux["E1_vs_E1g"]["diff"] < 0,
               "E2": str(main["E2"]["verdict_holm"]).startswith("나음")}
    wape = {k: float(decompose(y, P[k], w, mask).sum()) for k in P}
    by_year = {}
    for yr in sorted({t[:4] for t in em}):
        ix = [i for i, t in enumerate(em) if t.startswith(yr)]
        by_year[yr] = {k: float(decompose(y[:, ix], P[k][:, ix], w, mask[:, ix]).sum()) for k in P}
    return dict(main=main, aux=aux, success=success, wape=wape, by_year=by_year)


def main(argv: list[str]) -> int:
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    seed = yaml.safe_load((ROOT / "config/params.yaml").read_text(encoding="utf-8"))["seed"]
    smoke = "--smoke" in argv
    lock = None
    if not smoke:
        try:
            lock = lock_check(cfg["tag"])
        except PermissionError as e:
            print(f"실행 거부: {e}", file=sys.stderr)
            return 2
    R = run(cfg, seed, smoke)
    mask = eval_mask(R, smoke)
    from pbd.forecast_v3.exp_report import save
    if smoke:
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        scfg = {**cfg, "r1": cfg["smoke"]["r1"], "r2": cfg["smoke"]["r2"]}
        ev = evaluate(R, mask, scfg)
        rep = reproduction(R)                      # G는 코드 경로만(일부 성분이라 P2 v2 G와 같을 수 없다). B3는 성분별 적합이라 같아야 한다
        assert R["b3_check"]["ok"]
        m_full = eval_mask(R, smoke=False)         # 본 실행의 평가 칸 경로(P2 v2 예측은 2022에도 있다)
        assert m_full.shape == mask.shape and (m_full <= mask).all()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            save(R, mask, ev, rep, {"tag": "smoke", "tag_commit": "0000000", "locked": list(LOCKED)}, scfg,
                 out=Path(d), report=Path(d) / "r.md")
        print(json.dumps({"smoke": "ok", "ingredients": len(R["tgt"]), "eval_months": len(R["ev_t"]), "base_months": len(R["base_t"]),
                          "eval_cells": int(mask.sum()), "arms": sorted(R["pred"]), "main": sorted(ev["main"]),
                          "aux": sorted(ev["aux"]), "e1_n_used_min": min(v["n_used"] for v in R["winfo"].values()),
                          "b3_check": R["b3_check"], "timing_s": {k: round(v, 1) for k, v in R["timing"].items()},
                          "seconds": round(R["seconds"], 1)}, ensure_ascii=False))
        return 0
    rep = reproduction(R)
    ev = evaluate(R, mask, cfg)
    save(R, mask, ev, rep, lock, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
