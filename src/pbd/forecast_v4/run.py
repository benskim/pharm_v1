"""P2 v4: 근무일수 보정 ETS(W)와 성분별 예측 구간 (docs/PLAN_P2_v4.md).
python -m pbd.forecast_v4.run --smoke : 잠금 전 동작 확인(대상월 2022, ATC 1단계 P·R). 오차 숫자를 내지 않는다
python -m pbd.forecast_v4.run         : 본 실행. 태그의 잠금 파일과 작업 파일이 같을 때만
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import yaml

from pbd.common import ROOT
from pbd.forecast_v2 import baselines as BL
from pbd.forecast_v2.backtest import matrices
from pbd.forecast_v2.calendar_kr import workdays
from pbd.forecast_v2.timeaxis import madd, mrange
from pbd.forecast_v4 import interval as IV
from pbd.forecast_v4.etscache import EtsStore
from pbd.forecast_v4.workday import betas, wd_known

CFG_PATH = ROOT / "config/p2v4.yaml"
LOCKED = ("docs/PLAN_P2_v4.md", "config/p2v4.yaml", "config/params.yaml", "tests/test_p2v4.py",
          *(f"src/pbd/forecast_v4/{f}.py" for f in ("run", "workday", "interval", "etscache", "report")),
          *(f"src/pbd/forecast_v2/{f}.py" for f in ("backtest", "baselines", "calendar_kr", "dm", "ets", "evaluate", "gbm",
                                                    "metrics", "timeaxis")))


def lock_check(tag: str) -> dict:
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True)
    if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode != 0:
        raise PermissionError(f"태그 {tag}가 없다 — 본 실행 거부")
    diff = []
    for p in LOCKED:
        r = git("show", f"{tag}:{p}")
        if r.returncode != 0 or not (ROOT / p).exists() or r.stdout != (ROOT / p).read_bytes():
            diff.append(p)
    if diff:
        raise PermissionError(f"잠금 파일이 태그 {tag}와 다르다: {diff}")
    return {"tag": tag, "tag_commit": git("rev-list", "-n", "1", tag).stdout.decode().strip(), "locked": list(LOCKED)}


def check_hash(path: str, sha: str):
    h = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    if h != sha:
        raise RuntimeError(f"{path} 해시가 다르다: {h}")


def guard_months(months) -> None:
    if max(months) > "2025-12":
        raise RuntimeError(f"2026 처방월이 입력에 있다: {max(months)}")


def adjusted(Y: np.ndarray, beta: np.ndarray, c: np.ndarray, a: int, center: float) -> np.ndarray:
    """a까지의 근무일수 효과를 뺀 수량. 열 a 이하만 읽는다"""
    return Y[:, :a + 1] * np.exp(-beta[:, None] * (c[None, :a + 1] - center))


def restore(path_h: np.ndarray, beta: np.ndarray, c_t: float, center: float) -> np.ndarray:
    return path_h * np.exp(beta * (c_t - center))


def window_scores(Y: np.ndarray, P: dict, a: int, window: int, first: int) -> np.ndarray:
    """보정 자료 |log(y/ŷ)|, 대상 열 τ ∈ [max(a − window + 1, first), a]"""
    taus = [t for t in range(max(a - window + 1, first), a + 1) if t in P]
    y = np.stack([Y[:, t] for t in taus], 1)
    p = np.stack([P[t] for t in taus], 1)
    ok = np.isfinite(y) & (y > 0) & np.isfinite(p) & (p > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok, np.abs(np.log(np.where(ok, y, 1) / np.where(ok, p, 1))), np.nan)


def load(cfg: dict, smoke: bool):
    import duckdb
    check_hash(cfg["target_file"], cfg["target_sha256"])
    check_hash(cfg["b3"]["cache"], cfg["b3"]["cache_sha256"])
    tgt = pd.read_csv(ROOT / cfg["target_file"], dtype={"c4": str, "cls3": str})
    tgt["c4"] = tgt.c4.str.zfill(4)
    end = (cfg["smoke"] if smoke else cfg)["eval"][1]
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    panel = con.execute("""select c4, month, observed, claim_qty, cast(claim_amt as double) claim_amt
                           from fc_panel where month <= ? order by c4, month""", [end]).df()
    guard_months(panel.month)
    Y, _, _, months, audit = matrices(panel, tgt, "2018-01", end)
    v2 = pd.read_csv(ROOT / cfg["b3"]["cache"], dtype={"c4": str})
    v2["c4"] = v2.c4.str.zfill(4)
    v2 = v2[v2.h == cfg["h"]]
    v2m = {m: v2[v2.model == m].pivot(index="c4", columns="t", values="yhat").reindex(index=tgt.c4) for m in ("B1", "B2", "B3", "G")}
    return tgt, Y, months, v2m, audit


def run(cfg: dict, seed: int, smoke: bool) -> dict:
    t0 = time.time()
    tgt, Y, months, v2m, audit = load(cfg, smoke)
    h, cen = cfg["h"], cfg["center_wd"]
    ev_t = mrange(*(cfg["smoke"] if smoke else cfg)["eval"])
    all_t = mrange(cfg["first_tau"], ev_t[-1])
    col = {m: j for j, m in enumerate(months)}
    c = np.array([workdays(m) for m in months], float)
    keep = np.ones(len(tgt), bool)
    if smoke:
        g = pd.read_csv(ROOT / cfg["smoke"]["groups_file"], dtype=str).set_index("c4").reindex(tgt.c4)
        keep = g.atc1.isin(cfg["smoke"]["atc1"]).to_numpy()
    rows = np.flatnonzero(keep)                       # β는 항상 501개로 추정(동작 확인의 적합 캐시를 본 실행에서 다시 쓴다)
    st = EtsStore(ROOT / cfg["ets_cache"])
    B, jobs = {}, []
    for t in all_t:
        a = col[t] - h
        B[t] = betas(Y, c, a, cfg["beta"]["huber_k"], cfg["beta"]["min_rows"])
        jobs += [("W", t, i) for i in rows]
        if t >= ev_t[0]:
            jobs += [("Wg", t, i) for i in rows]
        if t <= cfg["b3"]["fresh_until"]:
            jobs += [("B3", t, i) for i in rows]
    adj = {}
    for t in all_t:
        a = col[t] - h
        adj["W", t] = adjusted(Y, B[t]["beta"], c, a, cen)
        adj["Wg", t] = adjusted(Y, np.full(len(Y), B[t]["mu"]), c, a, cen)
        adj["B3", t] = Y[:, :a + 1]
    res = st.many([adj[m, t][i] for m, t, i in jobs], recheck=cfg["cache_recheck"], seed=seed)
    path = {}
    for (m, t, i), (f, _) in zip(jobs, res):
        path.setdefault((m, t), np.full(len(Y), np.nan))[i] = f[h - 1]
    P = {m: {} for m in ("W", "W_all", "Wg", "B3", "B1")}
    fb = {}
    for t in all_t:
        j = col[t]
        a = j - h
        Vc = Y.copy()
        Vc[:, a + 1:] = np.nan
        b2 = BL.b2(Vc, j, h)
        ck, ca = wd_known(t, cfg["temp_holidays"]), workdays(t)
        p = path["W", t]
        fb[t] = ~np.isfinite(p)
        P["W"][j] = np.where(fb[t], b2, restore(p, B[t]["beta"], ck, cen))
        P["W_all"][j] = np.where(fb[t], b2, restore(p, B[t]["beta"], ca, cen))
        if t >= ev_t[0]:
            p = path["Wg", t]
            P["Wg"][j] = np.where(np.isfinite(p), restore(p, np.full(len(Y), B[t]["mu"]), ck, cen), b2)
        if t <= cfg["b3"]["fresh_until"]:
            p = path["B3", t]
            P["B3"][j] = np.where(np.isfinite(p), p, b2)
        else:
            P["B3"][j] = v2m["B3"][t].to_numpy(float)
        P["B1"][j] = BL.b1(Vc, j, h)
    cm = cfg["b3"]["check_month"]
    n_, o_ = P["B3"][col[cm]][rows], v2m["B3"][cm].to_numpy(float)[rows]
    f = np.isfinite(n_) & np.isfinite(o_)
    chk = dict(month=cm, cells=int(f.sum()), same_nan=bool((np.isfinite(n_) == np.isfinite(o_)).all()),
               max_rel=float(np.max(np.abs(n_[f] / o_[f] - 1))))
    if not (chk["same_nan"] and chk["max_rel"] <= 1e-9):
        raise RuntimeError(f"B3 새 적합과 캐시가 다르다: {chk}")
    # ---- 구간 (보정은 origin 열 a 이하 정답만)
    ivc, levels = cfg["interval"], cfg["interval"]["levels"]
    first = col[cfg["first_tau"]]
    Yk = Y[rows]
    cal = {}
    for ctr in ("W", "B3", "B1"):
        Pk = {j: v[rows] for j, v in P[ctr].items()}
        for t in ev_t:
            cal[ctr, t] = IV.fit(window_scores(Yk, Pk, col[t] - h, ivc["window"], first), levels, ivc["min_obs"])
    js = [col[t] for t in ev_t]
    S = lambda D: np.stack([D[j][rows] for j in js], 1)
    pred = {m: S(P[m]) for m in P}
    for m in ("G", "B2"):
        pred[m] = v2m[m].reindex(columns=ev_t).to_numpy(float)[rows]
    y = np.stack([Y[rows, j] for j in js], 1)
    half = {}
    for ctr in ("W", "B3", "B1"):
        for l in levels:
            half[ctr, "I0", l] = np.stack([np.full(len(rows), cal[ctr, t]["q0"][l]) for t in ev_t], 1)
            half[ctr, "I1", l] = np.stack([cal[ctr, t]["q1"][l] * cal[ctr, t]["sigma"] for t in ev_t], 1)
    mask = np.isfinite(y) & (y > 0)
    reason = {"정답 없음": ~mask}
    for m in ("B1", "B2", "B3", "G"):
        M = v2m[m].reindex(columns=ev_t).to_numpy(float)[rows]
        reason[f"P2 v2 {m} 예측 없음"] = ~np.isfinite(M)
    for m in ("W", "W_all", "Wg"):
        reason[f"{m} 예측 없음"] = ~np.isfinite(pred[m])
    for k, v in half.items():
        reason[f"구간 {k[0]} {k[1]} 없음"] = ~np.isfinite(v)
    for v in reason.values():
        mask &= ~v
    T = tgt.iloc[rows].reset_index(drop=True)
    return dict(tgt=T, w=T.w.to_numpy(float), ev_t=ev_t, y=y, pred=pred, half=half, mask=mask, reason=reason, betas=B, cal=cal,
                fb=np.stack([fb[t][rows] for t in ev_t], 1), check=chk, hits=st.hits, fits=st.fits, seconds=time.time() - t0,
                workers=st.workers, n_beta=len(tgt))


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
    from pbd.forecast_v4.report import evaluate, save
    R = run(cfg, seed, smoke)
    if smoke:
        import tempfile
        from pathlib import Path
        sc = {**cfg, "r1": cfg["smoke"]["r1"], "r2": cfg["smoke"]["r2"]}
        ev = evaluate(R, sc)
        with tempfile.TemporaryDirectory() as d:
            save(R, ev, {"tag": "smoke", "tag_commit": "0" * 7}, sc, Path(d), Path(d) / "r.md", Path(d) / "dropped.csv")
        print(json.dumps(dict(smoke="ok", ingredients=len(R["tgt"]), beta_ingredients=R["n_beta"], eval_months=len(R["ev_t"]),
                              eval_cells=int(R["mask"].sum()), b3_check=R["check"], ets_cache_hits=R["hits"], ets_fits=R["fits"],
                              workers=R["workers"], seconds=round(R["seconds"], 1)), ensure_ascii=False))
        return 0
    out = ROOT / "outputs/p2v4"
    save(R, evaluate(R, cfg), lock, cfg, out, ROOT / "reports/P2v4_result.md", ROOT / "outputs/audit/dropped_p2v4_cells.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
