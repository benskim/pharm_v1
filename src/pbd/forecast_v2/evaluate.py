"""비교·판정 (PLAN_P2_v2 §5-7·§5-8, 사전등록 변경 이력 B1 ~ B6).

compare(): 평가 칸 mask에서 모델 A 대 B.
 d_t = Σ_i w_i |e_it| / Y_i (metrics.decompose), D_t = T(d_t^A − d_t^B), D̄ = ΔWAPE.
 주 검정 = EWC ν = round(0.4·T^{2/3}), 90% 구간·동등도 EWC. 판정 = dm.verdict_single (Q2-d·부분 기간은 use_r = False).
 민감도(판정에 쓰지 않음): DM 가중 없음 + HLN(h), Bartlett 12 → 각 p와 그 p로 낸 판정 문구를 함께 낸다.
q1_overall(): 세 비교 모두 '나음'이면 "단순 예측보다 나음"(개별 문장은 원 p). 아니면 개별 문장은 Holm(3) p로 다시 판정,
 종합 = 가장 강한 기준(전체 기간 지표가 가장 작은 B)과의 Holm(3) 판정이 동등 → "가장 강한 기준과 동등", 나쁨 → "가장 강한 기준보다 나쁨",
 그 밖 "판단 보류".
"""
from __future__ import annotations

import numpy as np

from pbd.forecast_v2 import dm as DM
from pbd.forecast_v2.metrics import decompose


def compare(y, ya, yb, w, mask, r1: slice | None, r2: slice | None, delta: float, h: int = 6,
            use_r: bool = True, alpha: float = 0.05, log_error: bool = False) -> dict:
    """log_error = True면 |log ŷ − log y|의 금액 가중 평균(Q2 단가 지표). 아니면 금액 가중 WAPE"""
    if log_error:
        ea, eb = np.abs(np.log(ya) - np.log(y)), np.abs(np.log(yb) - np.log(y))
        n = mask.sum(1)
        k = np.divide(w, n, out=np.zeros_like(w, dtype=float), where=n > 0)
        da = (np.where(mask, ea, 0) * k[:, None]).sum(0)
        db = (np.where(mask, eb, 0) * k[:, None]).sum(0)
    else:
        da, db = decompose(y, ya, w, mask), decompose(y, yb, w, mask)
    T = len(da)
    D = T * (da - db)
    r = DM.ewc(D)
    m1 = D[r1].mean() if use_r else D.mean()
    m2 = D[r2].mean() if use_r else D.mean()
    v = DM.verdict_single(r, m1, m2, delta, alpha, use_r)[()]
    s = DM.dm(D, h, 5, 5, 12)
    out = dict(metric_a=float(da.sum()), metric_b=float(db.sum()), diff=float(D.mean()), T=T, nu=int(r["nu"]),
               p=float(r["p"]), lo90=float(r["lo90"]), hi90=float(r["hi90"]), invalid=bool(r["invalid"]),
               verdict=str(v), r1_mean=float(m1), r2_mean=float(m2),
               p_dm=float(s["p"]), p_b12=float(s["p_robust"]), dm_fallback=bool(s["fallback"]))
    if out["verdict"] == DM.BETTER and out["hi90"] > -delta:
        out["verdict"] = DM.BETTER + "(실무 여유 안)"
    for name, pk in (("dm", "p_dm"), ("b12", "p_b12")):
        rs = dict(mean=np.array(out["diff"]), p=np.array(out[pk]), lo90=np.array(np.nan), hi90=np.array(np.nan))
        out[f"verdict_{name}"] = str(DM.verdict_single(rs, m1, m2, delta, alpha, use_r)[()])
    out["d_a"], out["d_b"], out["D"] = da, db, D
    return out


def rejudge(c: dict, p: float, delta: float, use_r: bool = True, alpha: float = 0.05) -> str:
    r = dict(mean=np.array(c["diff"]), p=np.array(p), lo90=np.array(c["lo90"]), hi90=np.array(c["hi90"]))
    v = str(DM.verdict_single(r, c["r1_mean"], c["r2_mean"], delta, alpha, use_r)[()])
    if v == DM.BETTER and c["hi90"] > -delta:
        v = DM.BETTER + "(실무 여유 안)"
    return v


def holm(ps: list[float]) -> list[float]:
    n = len(ps)
    order = np.argsort(ps)
    adj = np.empty(n)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (n - rank) * ps[i])
        adj[i] = min(1.0, run)
    return adj.tolist()


def q1_overall(comps: dict, delta: float) -> dict:
    """comps: {'B1': compare(G, B1), 'B2': …, 'B3': …}"""
    better = all(c["verdict"].startswith(DM.BETTER) for c in comps.values())
    names = list(comps)
    if better:
        indiv = {b: dict(p=comps[b]["p"], verdict=comps[b]["verdict"]) for b in names}
        text = "단순 예측보다 나음"
        strongest = min(names, key=lambda b: comps[b]["metric_b"])
    else:
        adj = holm([comps[b]["p"] for b in names])
        indiv = {b: dict(p=adj[k], verdict=rejudge(comps[b], adj[k], delta)) for k, b in enumerate(names)}
        strongest = min(names, key=lambda b: comps[b]["metric_b"])
        vs = indiv[strongest]["verdict"]     # 개별 문장과 같은 Holm(3) 판정 (가장 강한 기준은 결과로 고른 것, 2026-11-20 dry run에서 발견)
        text = ("가장 강한 기준과 동등" if vs == DM.EQUIV else
                "가장 강한 기준보다 나쁨" if vs == DM.WORSE else "판단 보류")
    return dict(text=text, strongest=strongest, individual=indiv, holm=not better)


def block_bootstrap(D: np.ndarray, B: int, block: int, seed: int) -> tuple[float, float]:
    """원형 이동 블록 부트스트랩 95% 구간 (A7)"""
    rng = np.random.default_rng(seed)
    T = len(D)
    nb = int(np.ceil(T / block))
    starts = rng.integers(0, T, size=(B, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % T
    m = D[idx.reshape(B, -1)[:, :T]].mean(1)
    return float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))
