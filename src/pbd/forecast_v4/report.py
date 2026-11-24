"""P2 v4 평가·저장·보고서 (PLAN_P2_v4 §0·§6·§7). 숫자는 모두 이번 실행의 계산값"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pbd.forecast_v2 import dm as DM
from pbd.forecast_v2 import evaluate as EV
from pbd.forecast_v2.calendar_kr import workdays
from pbd.forecast_v2.metrics import decompose
from pbd.forecast_v2.timeaxis import madd
from pbd.forecast_v4 import interval as IV


def common_bias(y, p, w, mask):
    """월 공통 오차 = Σ v y log(ŷ/y) ÷ Σ v y, v = w ÷ 성분 정답 합"""
    ys = np.where(mask, y, 0.0)
    Yi = ys.sum(1)
    k = np.divide(w, Yi, out=np.zeros(len(w)), where=Yi > 0)[:, None] * ys
    e = np.where(mask, np.log(np.where(mask, p, 1) / np.where(mask, y, 1)), 0.0)
    return (k * e).sum(0) / k.sum(0)


def bounds(R, ctr, kind, l):
    x = np.log(np.where(R["mask"], R["pred"][ctr], np.nan))
    q = R["half"][ctr, kind, l]
    return x - q, x + q


def verdict_c2(D, r1, r2) -> dict:
    r = DM.ewc(D)
    v = str(DM.verdict_single(r, D[r1].mean(), D[r2].mean(), 0.0)[()])
    return dict(diff=float(D.mean()), p=float(r["p"]), lo90=float(r["lo90"]), hi90=float(r["hi90"]),
                r1=float(D[r1].mean()), r2=float(D[r2].mean()), verdict=v)


def evaluate(R: dict, cfg: dict) -> dict:
    em, y, w, mask, P = R["ev_t"], R["y"], R["w"], R["mask"], R["pred"]
    sl = lambda q: slice(em.index(q[0]), em.index(q[1]) + 1)
    r1, r2 = sl(cfg["r1"]), sl(cfg["r2"])
    cmp = lambda a, b, m=mask: EV.compare(y, P[a], P[b], w, m, r1, r2, cfg["delta"], cfg["h"])
    main = cmp("W", "B3")
    has = mask.any(1)
    n = mask.sum(1)
    fb = float((np.divide((R["fb"] & mask).sum(1), n, out=np.zeros(len(n)), where=n > 0) * w)[has].sum() / w[has].sum())
    if fb > cfg["fallback_max"]:
        main["verdict"] = DM.HOLD
    dw = np.array([workdays(t) - workdays(madd(t, -12)) for t in em], float)
    cb = {m: common_bias(y, P[m], w, mask) for m in ("W", "W_all", "Wg", "B3", "G", "B1")}
    r = {m: float(np.corrcoef(v, dw)[0, 1]) for m, v in cb.items()}
    S1 = bool(main["diff"] < 0 and main["r1_mean"] < 0 and main["r2_mean"] < 0)
    S2 = bool(abs(r["W"]) <= cfg["s2_max_abs_r"] and abs(r["W"]) < abs(r["B3"]))
    center = "W" if S1 and S2 else "B3"
    years = sorted({t[:4] for t in em})
    yix = {yr: [i for i, t in enumerate(em) if t[:4] == yr] for yr in years}
    levels = cfg["interval"]["levels"]
    x = np.log(np.where(mask, y, np.nan))
    cov, d = {}, {}
    H = R["tgt"].in_h.astype(str).str.lower().eq("true").to_numpy()
    for ctr in ("W", "B3", "B1"):
        for kind in ("I0", "I1"):
            for l in levels:
                lo, hi = bounds(R, ctr, kind, l)
                with np.errstate(invalid="ignore"):
                    hit = mask & (x >= lo) & (x <= hi)
                k = np.divide(w, n, out=np.zeros(len(w)), where=n > 0)[:, None]
                cov[ctr, kind, l] = dict(all=hit.sum() / mask.sum(), weighted=float((hit * k).sum() / (mask * k).sum()),
                                         H=hit[H].sum() / max(mask[H].sum(), 1), width=float((np.where(mask, hi - lo, 0)).sum() / mask.sum()),
                                         **{yr: hit[:, ix].sum() / mask[:, ix].sum() for yr, ix in yix.items()},
                                         month=hit.sum(0) / mask.sum(0))
                d[ctr, kind, l] = IV.decompose_mean(np.where(mask, IV.iscore(x, lo, hi, l), 0.0), w, mask)
    T, L = len(em), levels[0]
    c2 = {ctr: verdict_c2(T * (d[ctr, "I1", L] - d[ctr, "I0", L]), r1, r2) for ctr in ("W", "B3")}
    inr = lambda v, b: bool(b[0] <= v <= b[1])
    cv = cov[center, "I1", L]
    C1 = inr(cv["all"], cfg["interval"]["cover_all"]) and all(inr(cv[yr], cfg["interval"]["cover_year"]) for yr in years)
    C2 = c2[center]["verdict"].startswith(DM.BETTER)
    wape = {m: float(decompose(y, P[m], w, mask).sum()) for m in ("W", "W_all", "Wg", "B3", "G", "B1")}
    sub = {}

    def wp(name, mm):                                  # 부분집합: 쓰인 성분의 가중치로 다시 정규화
        sub[name] = {m: float(decompose(y, P[m], w, mm).sum() / w[mm.any(1)].sum()) for m in ("W", "B3", "G")} if mm.any() else None
    for yr, ix in yix.items():
        mm = np.zeros_like(mask); mm[:, ix] = mask[:, ix]; wp(yr, mm)
    wp("호흡기 H", mask & H[:, None]); wp("H 밖", mask & ~H[:, None])
    for nm, q in (("|dw| = 0", dw == 0), ("|dw| = 1", abs(dw) == 1), ("|dw| ≥ 2", abs(dw) >= 2),
                  ("임시공휴일 달 제외", ~np.isin(em, [s[:7] for s in cfg["temp_holidays"]]))):
        wp(nm, mask & q[None, :])
    up = np.exp(R["half"][center, "I1", L]) - 1
    g0, g1 = cfg["grades"]
    grade = []
    lo, hi = bounds(R, center, "I1", L)
    with np.errstate(invalid="ignore"):
        hit = mask & (x >= lo) & (x <= hi)
    k = np.divide(w, n, out=np.zeros(len(w)), where=n > 0)[:, None]
    err = np.abs(np.where(mask, P[center] / np.where(mask, y, 1) - 1, 0.0))
    for nm, q in (("낮음", up < g0), ("중간", (up >= g0) & (up < g1)), ("높음", up >= g1)):
        mm = mask & q
        grade.append(dict(grade=nm, cells=int(mm.sum()), ingredients=int(mm.any(1).sum()), weight_share=float((mm * k).sum() / (mask * k).sum()),
                          mean_abs_pct_err=float(err[mm].mean()) if mm.any() else None, coverage=float(hit[mm].mean()) if mm.any() else None))
    return dict(main=main, fallback_share=fb, corr=r, cb=cb, dw=dw, S1=S1, S2=S2, center=center, cov=cov, d=d, c2=c2, C1=bool(C1), C2=bool(C2),
                wape=wape, sub=sub, grade=grade, aux={f"W_{m}": cmp("W", m) for m in ("W_all", "Wg", "G")} | {f"{m}_B3": cmp(m, "B3") for m in ("W_all", "Wg")})


def save(R, ev, lock, cfg, out, report, dropped) -> None:
    out.mkdir(parents=True, exist_ok=True)
    dropped.parent.mkdir(parents=True, exist_ok=True)
    em, c4, L = R["ev_t"], R["tgt"].c4.to_numpy(), cfg["interval"]["levels"][0]
    rows, seen = [], R["mask"].copy()
    for why, M in R["reason"].items():
        for i, j in zip(*np.nonzero(M & ~seen)):
            rows.append((c4[i], em[j], why))
        seen |= M
    pd.DataFrame(rows, columns=["c4", "month", "reason"]).to_csv(dropped, index=False)
    idx = pd.MultiIndex.from_product([c4, em], names=["c4", "t"])
    df = pd.DataFrame({m: R["pred"][m].ravel() for m in ("W", "W_all", "Wg", "B3")}, index=idx)
    df["half_I1"], df["half_I0"] = R["half"][ev["center"], "I1", L].ravel(), R["half"][ev["center"], "I0", L].ravel()
    df["eval"] = R["mask"].ravel()
    df.to_csv(out / "predictions.csv")
    pd.DataFrame([dict(t=t, mu=b["mu"], tau2=b["tau2"], b_median=float(np.median(b["b"])), **dict(zip(("beta_p05", "beta_p50", "beta_p95"), np.quantile(b["beta"], [.05, .5, .95]))),
                       fallback_to_mean=int(np.isnan(b["raw"]).sum())) for t, b in R["betas"].items()]).to_csv(out / "x2_betas.csv", index=False)
    pd.DataFrame(dict(t=em, dw=ev["dw"], **{f"bias_{m}": v for m, v in ev["cb"].items()}, **{f"cover_{kk[0]}_{kk[1]}_{kk[2]}": v["month"] for kk, v in ev["cov"].items()},
                      D_WAPE=ev["main"]["D"])).to_csv(out / "by_month.csv", index=False)
    clean = lambda q: {k: v for k, v in q.items() if not isinstance(v, np.ndarray)}
    js = dict(lock=lock, S1=ev["S1"], S2=ev["S2"], C1=ev["C1"], C2=ev["C2"], center=ev["center"], corr=ev["corr"], main=clean(ev["main"]), c2=ev["c2"],
              wape=ev["wape"], fallback_share=ev["fallback_share"], aux={k: clean(v) for k, v in ev["aux"].items()}, check=R["check"],
              cov={"|".join(map(str, k)): clean(v) for k, v in ev["cov"].items()}, grade=ev["grade"], sub=ev["sub"],
              cells=int(R["mask"].sum()), dropped=len(rows), seconds=R["seconds"], hits=R["hits"], fits=R["fits"])
    (out / "verdict.json").write_text(json.dumps(js, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    pc = lambda v: "–" if v is None else f"{100 * v:.2f}%"
    pp = lambda v: f"{100 * v:+.2f}%p"
    ok = lambda b: "통과" if b else "**불통과**"
    m, c2, ct = ev["main"], ev["c2"][ev["center"]], ev["center"]
    years = sorted({t[:4] for t in em})
    o = ["# P2 v4 결과: 근무일수 보정 ETS와 성분별 예측 구간", "",
         "> **맹검이 아니다.** 평가 기간은 P2 v1 ~ v3가 보았고 ①·②의 착안도 같은 기간 오차에서 나왔다. 결과는 낙관 쪽으로 기울 수 있다. 수정본 사용통계로 평가했다. 구간은 관측된 칸에 대한 것이고 비공개 위험은 담지 않는다. "
         "전국 성분 단위 원외 처방이며 약국 재고 구간이 아니다. 공휴일 목록은 공식 출처와 대조하지 않았다.", "",
         f"사전등록 `docs/PLAN_P2_v4.md`, 태그 `{lock['tag']}`({lock['tag_commit'][:7]}). 평가 칸 {int(R['mask'].sum()):,}, 대상월 {em[0]} ~ {em[-1]}, h = {cfg['h']}.", "",
         "## 성공 기준", "", "| 기준 | 값 | 결과 |", "|---|---|---|",
         f"| S1 W 대 B3 D̄ < 0, R1·R2 < 0 | {pp(m['diff'])}, {pp(m['r1_mean'])}, {pp(m['r2_mean'])} | {ok(ev['S1'])} |",
         f"| S2 월 공통 오차와 dw의 상관 | W {ev['corr']['W']:+.2f}, B3 {ev['corr']['B3']:+.2f} (문턱 {cfg['s2_max_abs_r']}) | {ok(ev['S2'])} |",
         f"| C1 90% 포함률 (중심 {ct}, I1) | 전체 {pc(ev['cov'][ct, 'I1', L]['all'])}, " + ", ".join(f"{yr} {pc(ev['cov'][ct, 'I1', L][yr])}" for yr in years) + f" | {ok(ev['C1'])} |",
         f"| C2 구간 점수 I1 − I0 | {c2['diff']:+.4f} (90% 구간 {c2['lo90']:+.4f} ~ {c2['hi90']:+.4f}, p {c2['p']:.3g}) 판정 {c2['verdict']} | {ok(ev['C2'])} |", "",
         f"**공식 판정 W 대 B3: {m['verdict']}** — ΔWAPE {pp(m['diff'])} (90% 구간 {pp(m['lo90'])} ~ {pp(m['hi90'])}, EWC p {m['p']:.3g}; 민감도 DM {m['p_dm']:.3g}, Bartlett 12 {m['p_b12']:.3g}). W의 대체 칸 가중치 몫 {pc(ev['fallback_share'])}.", "",
         "## 보조 (판정 아님)", "", "금액 가중 WAPE: " + ", ".join(f"{k} {pc(v)}" for k, v in ev["wape"].items()) + ".", "",
         "월 공통 오차와 dw의 상관(X1): " + ", ".join(f"{k} {v:+.2f}" for k, v in ev["corr"].items()) + ".", "",
         "| 비교 (앞 − 뒤) | ΔWAPE | 90% 구간 |", "|---|---|---|"]
    o += [f"| {k.replace('_all', '-all').replace('_', ' 대 ')} | {pp(v['diff'])} | {pp(v['lo90'])} ~ {pp(v['hi90'])} |" for k, v in ev["aux"].items()]
    o += ["", "| 부분 | W | B3 | G |", "|---|---|---|---|"] + [f"| {k} | " + " | ".join(pc(v[q]) for q in ("W", "B3", "G")) + " |" for k, v in ev["sub"].items() if v]
    o += ["", "| 중심 | 구간 | 수준 | 포함률 | 가중 | H | " + " | ".join(years) + " | 평균 폭(로그) |", "|" + "---|" * (7 + len(years))]
    o += [f"| {'작년 같은 달' if k[0] == 'B1' else k[0]} | {k[1]} | {k[2]:.0%} | {pc(v['all'])} | {pc(v['weighted'])} | {pc(v['H'])} | " + " | ".join(pc(v[yr]) for yr in years) + f" | {v['width']:.3f} |"
          for k, v in ev["cov"].items() if not (k[0] == "B1" and k[1] == "I1")]
    o += ["", "**위험 등급**(운영 제안, 경계는 가정값)", "", "| 등급 | 칸 | 성분 | 가중치 몫 | 평균 절대 상대오차 | 포함률 |", "|---|---|---|---|---|---|"]
    o += [f"| {g['grade']} | {g['cells']:,} | {g['ingredients']} | {pc(g['weight_share'])} | {pc(g['mean_abs_pct_err'])} | {pc(g['coverage'])} |" for g in ev["grade"]]
    o += ["", "## 완료 기준", "", f"- B3 재현 {R['check']['month']}: {R['check']['cells']}칸, 최대 상대 차이 {R['check']['max_rel']:.2e}, 결측 모양 같음 {R['check']['same_nan']}.",
          f"- 뺀 칸 {len(rows):,}개(사유 `{dropped.name}`). ETS 캐시 재사용 {R['hits']:,}, 새 적합 {R['fits']:,}, {R['seconds'] / 60:.1f}분.", ""]
    report.write_text("\n".join(o), encoding="utf-8")
