"""P2 v2 전 과정 실행 (PLAN_P2_v2 §5 ~ §7). 합성 자료 dry run(dryrun.py)과 실제 백테스트가 같은 함수를 쓴다.

run_all(panel, target, ld, rho, cfg, out, seed, extra) → 결과 dict, out 폴더에 csv·json.
- 실제 자료 실행(main)은 태그 p2v2-prereg-r1과 잠금 파일이 같을 때만 한다(사전등록 전 평가 기간 정답 미사용, PLAN §7-4).
- 판정 문구는 evaluate.py가 내고, scripts/p2v2_verdict.py가 verdict.json에서 요약 문장을 만든다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pbd.forecast_v2 import dm as DM
from pbd.forecast_v2 import q2 as Q2
from pbd.forecast_v2.backtest import Backtest, long_table, matrices, run_q1
from pbd.forecast_v2.evaluate import block_bootstrap, compare, q1_overall
from pbd.forecast_v2.metrics import decompose
from pbd.forecast_v2.timeaxis import madd, mrange

MAIN_B = ("B1", "B2", "B3")


def _win(months: list[str], per) -> slice:
    return slice(months.index(per[0]), months.index(per[1]) + 1)


def _wape_table(y, preds: dict, w, mask, label: str) -> list[dict]:
    """금액 가중 WAPE와 편향(Σ_i w_i Σ_t (ŷ − y) / Y_i). 모델 칸이 없는 칸(B0 등)은 그 모델에서만 뺀다"""
    rows = []
    for m, p in preds.items():
        mm = mask & np.isfinite(p)
        Yi = np.where(mm, y, 0).sum(1)
        k = np.divide(w, Yi, out=np.zeros_like(w, dtype=float), where=Yi > 0)
        rows.append(dict(analysis=label, model=m, wape=float(decompose(y, p, w, mm).sum()),
                         bias=float((np.where(mm, p - y, 0) * k[:, None]).sum())))
    return rows


def _strip(c: dict) -> dict:
    return {k: v for k, v in c.items() if k not in ("d_a", "d_b", "D")}


def run_all(panel: pd.DataFrame, target: pd.DataFrame, ld, rho, cfg: dict, out: Path, seed: int,
            batch_months: set | None = None, n_perm: int = 999, n_boot: int = 9999, w96: np.ndarray | None = None,
            w2022: np.ndarray | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    E, V = cfg["eval"], cfg["verdict"]
    h, dq1, dq2 = E["h"], V["delta_q1"], V["delta_q2"]
    Y, A, C, months, audit = matrices(panel, target, "2018-01", E["end"])
    audit.to_csv(out / "dropped_cells.csv", index=False)
    bt = Backtest(Y, A, months, target, seed, C)
    w = target.w.to_numpy(float)
    walt = target.w_alt.to_numpy(float)
    all_t = mrange("2022-01", E["end"])                 # 2022 회복기 + 평가 기간 (Q2-d 학습에도 씀)
    pred = run_q1(bt, all_t, h)
    long_table(bt, all_t, h, pred, ("B0", "B1", "B2", "B3", "G")).to_csv(out / "predictions.csv", index=False)
    yall = np.stack([Y[:, bt.col(t)] for t in all_t], axis=1)
    E_all = np.isfinite(yall) & np.all([np.isfinite(pred[m]) for m in ("B1", "B2", "B3", "G")], axis=0)
    ev = _win(all_t, (E["start"], E["end"]))
    y, mask = yall[:, ev], E_all[:, ev]
    P = {m: pred[m][:, ev] for m in ("B0", "B1", "B2", "B3", "G")}
    em = all_t[ev]
    r1, r2 = _win(em, E["r1"]), _win(em, E["r2"])
    res: dict = dict(n_target=len(target), eval_cells=int(mask.sum()), eval_months=len(em),
                     b3_fallback_cells=int((pred["B3_fallback"][:, ev] & mask).sum()))
    # ---- Q1 주
    comps = {b: compare(y, P["G"], P[b], w, mask, r1, r2, dq1, h) for b in MAIN_B}
    n_i = mask.sum(1)
    fb_share = float((w * np.divide((pred["B3_fallback"][:, ev] & mask).sum(1), n_i, out=np.zeros_like(w),
                                    where=n_i > 0)).sum())
    res["b3_fallback_weight_share"] = fb_share
    if fb_share > 0.10:
        comps["B3"]["verdict"] = DM.HOLD
        comps["B3"]["note"] = f"B3 대체 칸 가중치 몫 {fb_share:.1%} > 10% → 판단 보류"
    res["q1"] = {b: _strip(c) for b, c in comps.items()}
    res["q1_overall"] = q1_overall(comps, dq1)
    res["q1_wape"] = {m: float(decompose(y, P[m], w, mask).sum()) for m in P}
    # ---- Q2 주: 단가
    lc = Q2.ListCache(ld, target.c4, rho)
    UP, U0, U1 = Q2.unit_prices(bt, em, h, lc)
    m2 = mask & np.isfinite(UP) & np.isfinite(U0) & np.isfinite(U1) & (UP > 0) & (U0 > 0) & (U1 > 0)
    res["q2_cells"] = int(m2.sum())
    q2m = compare(UP, U1, U0, w, m2, r1, r2, dq2, h, log_error=True)
    res["q2_main"] = _strip(q2m)
    fakes = {}
    for k in (7, 17):
        sub = [i for i, t in enumerate(em) if madd(t, -h - k) >= "2022-01"]
        if len(sub) < 3:
            fakes[k] = dict(note="부분 기간 3개월 미만")
            continue
        _, _, Uf = Q2.unit_prices(bt, [em[i] for i in sub], h, lc, shift=k)
        ms = m2[:, sub] & np.isfinite(Uf) & (Uf > 0)
        fakes[k] = _strip(compare(UP[:, sub], U1[:, sub], Uf, w, ms, None, None, dq2, h, use_r=False, log_error=True))
        fakes[k]["months"] = f"{em[sub[0]]} ~ {em[sub[-1]]}"
    res["q2_fake"] = fakes
    res["q2_fake_statement"] = ("" if all(isinstance(f.get("verdict"), str) and f["verdict"].startswith(DM.BETTER)
                                          for f in fakes.values()) else "시점 정렬의 기여 확인 안 됨")
    dk = Q2.dk_matrix(bt, em, h, lc)
    upa = np.stack([A[:, bt.col(t) - h] / Y[:, bt.col(t) - h] for t in em], axis=1)

    def q2metric(UPx, U):
        n = m2.sum(1)
        e = np.where(m2, np.abs(np.log(U) - np.log(UPx)), 0).sum(1)
        return float((w * np.divide(e, n, out=np.zeros_like(w), where=n > 0)).sum())

    res["q2_perm_p"] = Q2.permutation_p(UP, U0, dk, upa, w, m2, n_perm, seed, q2metric)
    res["q2_perm_statement"] = "성분별 정렬의 기여 확인 안 됨" if res["q2_perm_p"] >= 0.05 else ""
    # ---- Q2-d 보정층
    dper = ("2024-01", E["end"])
    dmon = mrange(*dper)
    di = [all_t.index(t) for t in dmon]
    F0, F1, clog = Q2.correction(bt, pred["G"], all_t, dmon, lc)
    clog.to_csv(out / "q2d_log.csv", index=False)
    md = E_all[:, di] & np.isfinite(F0) & np.isfinite(F1)
    yd = yall[:, di]
    q2d = compare(yd, F1, F0, w, md, None, None, dq1, h, use_r=False)
    res["q2d"] = _strip(q2d)
    # ---- 주입 시험
    fd = ld.first_drop(E["end"]).reindex(target.c4).to_numpy(object)
    base_f0 = q2d["metric_b"]
    inj = {}
    for d in (0.05, 0.10, 0.20):
        Y2, A2 = Q2.inject(Y, A, months, fd, d)
        bt2 = Backtest(Y2, A2, months, target, seed, C)
        g2 = run_q1(bt2, all_t[all_t.index("2022-07"):], h, models=("G",))["G"]
        gfull = np.full_like(pred["G"], np.nan)
        gfull[:, all_t.index("2022-07"):] = g2
        f0, f1, _ = Q2.correction(bt2, gfull, all_t, dmon, Q2.ListCache(ld, target.c4, rho))
        y2 = np.stack([Y2[:, bt2.col(t)] for t in dmon], axis=1)
        c = compare(y2, f1, f0, w, md & np.isfinite(f0) & np.isfinite(f1), None, None, dq1, h, use_r=False)
        den = c["metric_b"] - base_f0
        inj[str(d)] = dict(_strip(c), recovery=float((c["metric_b"] - c["metric_a"]) / den) if den else None)
    res["q2d_injection"] = inj
    res["q2d_statement"] = ("" if inj["0.2"]["verdict"].startswith(DM.BETTER) else
                            "효과를 찾을 힘이 없음 — 실제 결과의 '개선 없음'을 해석하지 않음")
    # ---- 보조 분석 (판정에 쓰지 않음)
    aux = []
    aux += _wape_table(y, P, w, mask, "주 기간")
    for yr in sorted({t[:4] for t in all_t}):
        cols = [i for i, t in enumerate(all_t) if t.startswith(yr)]
        lab = f"A1 {yr}" + (" (회복기, 대상·가중치가 평가월 이후 자료로 정해짐)" if yr == "2022" else "")
        aux += _wape_table(yall[:, cols], {m: pred[m][:, cols] for m in P}, w, E_all[:, cols], lab)
    for b in MAIN_B:                                                   # A2
        wi = lambda p: np.where(mask, np.abs(y - p), 0).sum(1) / np.where(mask, y, 0).sum(1)
        rr = wi(P["G"]) / wi(P[b])
        rr = rr[np.isfinite(rr) & (rr > 0)]
        aux.append(dict(analysis="A2 비가중 상대 오차", model=f"G/{b}", geo_mean=float(np.exp(np.log(rr).mean())),
                        median=float(np.median(rr)), share_lt1=float((rr < 1).mean())))
    hmask = target.in_h.to_numpy(bool)
    if hmask.any():                                                   # A4
        wh = np.where(hmask, w, 0) / w[hmask].sum()
        aux += _wape_table(y, P, wh, mask & hmask[:, None], "A4 하위 집단 H")
    for lab, ww in (("A9 2022 가중치", w2022), ("A8 96개월 관측 부분집합", w96)):
        if ww is not None:
            aux += _wape_table(y, P, ww, mask & (ww > 0)[:, None], lab)
    # A5 예측 거리 곡선
    curve_t = None
    for hh in range(1, 13):
        ts = [madd(a, hh) for a in mrange("2022-12", "2024-12")]
        pc = run_q1(bt, ts, hh, models=("B1", "B2", "B3", "G"))
        yc = np.stack([Y[:, bt.col(t)] for t in ts], axis=1)
        mc = np.isfinite(yc) & np.all([np.isfinite(pc[m]) for m in ("B1", "B2", "B3", "G")], axis=0)
        for m in ("B1", "B2", "B3", "G"):
            aux.append(dict(analysis="A5 예측 거리 곡선", model=m, h=hh, wape=float(decompose(yc, pc[m], w, mc).sum())))
    # A6 목표 금액·건수 (G 직접)
    for tg in ("amt", "cnt"):
        V_ = A if tg == "amt" else C
        if not np.isfinite(V_).any():
            continue
        pt = run_q1(bt, em, h, models=("B1", "B2", "G"), target=tg)
        yt = np.stack([V_[:, bt.col(t)] for t in em], axis=1)
        mt = np.isfinite(yt) & np.all([np.isfinite(pt[m]) for m in ("B1", "B2", "G")], axis=0)
        aux += _wape_table(yt, {m: pt[m] for m in ("B1", "B2", "G")}, w, mt, f"A6 목표 {tg}")
        if tg == "amt":
            amt_pred, amt_y, amt_mask = pt, yt, mt
    # A10 Q2-a, A11 Q2-b, A13 F★
    F1K = P["G"] * upa * np.exp(dk)
    F0A = P["G"] * upa
    ma = m2 & np.isfinite(F1K) & np.isfinite(F0A) & amt_mask
    aux.append(dict(analysis="A10 Q2-a 회계 점검", **{k: v for k, v in _strip(compare(amt_y, F1K, F0A, w, ma, r1, r2, dq1, h)).items()
                                                  if k in ("metric_a", "metric_b", "diff")}))
    cand = {"G_A": amt_pred["G"], "B2_amt": amt_pred["B2"]}
    best = min(cand, key=lambda k: decompose(amt_y, cand[k], w, ma & np.isfinite(cand[k])).sum())
    q2b = compare(amt_y, F1K, cand[best], w, ma & np.isfinite(cand[best]), r1, r2, dq1, h)
    res["q2b"] = dict(_strip(q2b), against=best)
    Fstar = P["G"] * UP
    aux.append(dict(analysis="A13 상한 진단 F★ (금액, 일부러 미래)", model="G×UP_t",
                    wape=float(decompose(amt_y, Fstar, w, ma).sum())))
    # A12 as-of 전가율
    beta = np.array([Q2.passthrough(bt, lc, bt.months[bt.col(t) - h]) for t in em])
    U1c = upa * np.exp(beta[None, :] * dk)
    c12 = compare(UP, U1c, U0, w, m2 & np.isfinite(U1c), r1, r2, dq2, h, log_error=True)
    aux.append(dict(analysis="A12 Q2-c as-of 전가율", beta_median=float(np.median(beta)),
                    **{k: v for k, v in _strip(c12).items() if k in ("metric_a", "metric_b", "diff", "p")}))
    # A14 목록 s = M − 1
    lc14 = Q2.ListCache(ld, target.c4, rho, s_offset=-1)
    _, _, U1_14 = Q2.unit_prices(bt, em, h, lc14)
    c14 = compare(UP, U1_14, U0, w, m2 & np.isfinite(U1_14) & (U1_14 > 0), r1, r2, dq2, h, log_error=True)
    aux.append(dict(analysis="A14 목록 s = M − 1 (Q2 주)", **{k: v for k, v in _strip(c14).items()
                                                            if k in ("metric_a", "metric_b", "diff", "p", "verdict")}))
    # A15 in_month_dated 제외 (Q2-d)
    _, F1_15, _ = Q2.correction(bt, pred["G"], all_t, dmon, Q2.ListCache(ld, target.c4, rho, exclude_in_month=True))
    c15 = compare(yd, F1_15, F0, w, md & np.isfinite(F1_15), None, None, dq1, h, use_r=False)
    aux.append(dict(analysis="A15 in_month_dated 제외 (Q2-d)", **{k: v for k, v in _strip(c15).items()
                                                                if k in ("metric_a", "metric_b", "diff", "p", "verdict")}))
    # A16 일괄 인하 달 여부별 Q2
    if batch_months is not None:
        for flag in (True, False):
            sel = np.array([(t in batch_months) == flag for t in em])
            if sel.any():
                ms = m2 & sel[None, :]
                aux.append(dict(analysis="A16 일괄 인하 달" + (" 포함" if flag else " 아님"), model="U1",
                                metric=q2metric_mask(UP, U1, w, ms)))
                aux.append(dict(analysis="A16 일괄 인하 달" + (" 포함" if flag else " 아님"), model="U0",
                                metric=q2metric_mask(UP, U0, w, ms)))
    # A7 블록 부트스트랩, A17 구간 폭
    for b in MAIN_B:
        lo, hi = block_bootstrap(comps[b]["D"], n_boot, 12, seed)
        aux.append(dict(analysis="A7 블록 부트스트랩 95%", model=f"G − {b}", lo95=lo, hi95=hi))
        aux.append(dict(analysis="A17 구간 폭 환산", model=f"G − {b}", halfwidth90=(comps[b]["hi90"] - comps[b]["lo90"]) / 2))
    lo, hi = block_bootstrap(q2m["D"], n_boot, 12, seed)
    aux.append(dict(analysis="A7 블록 부트스트랩 95%", model="U1 − U0", lo95=lo, hi95=hi))
    # A18 가중치 민감도
    sens = []
    for b in MAIN_B:
        ca = compare(y, P["G"], P[b], walt, mask, r1, r2, dq1, h)
        differs = (ca["verdict"] != comps[b]["verdict"]) or (np.sign(ca["diff"]) != np.sign(comps[b]["diff"]))
        sens.append(differs)
        aux.append(dict(analysis="A18 가중치 2021-08 ~ 2022-07", model=f"G − {b}", diff=ca["diff"], p=ca["p"],
                        verdict=ca["verdict"], main_verdict=comps[b]["verdict"], differs=bool(differs)))
    res["a18_statement"] = "가중치 선택에 민감함" if any(sens) else ""
    pd.DataFrame(aux).to_csv(out / "aux.csv", index=False, float_format="%.6g")
    pd.DataFrame(bt.ets_log).to_csv(out / "ets_fallback.csv", index=False)
    (out / "verdict.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return dict(res=res, pred=pred, all_t=all_t, mask=mask, y=y, P=P, UP=UP, U0=U0, U1=U1, m2=m2, dk=dk,
                F0=F0, F1=F1, md=md, aux=pd.DataFrame(aux), bt=bt)


def q2metric_mask(UP, U, w, m):
    n = m.sum(1)
    e = np.where(m, np.abs(np.log(U) - np.log(UP)), 0).sum(1)
    return float((w * np.divide(e, n, out=np.zeros_like(w), where=n > 0)).sum())
