"""합성 자료 전 과정 시험 (dry run, PLAN_P2_v2 §7-2). 실제 자료를 읽지 않는다.

합성 세계 (성분 24개, 2018-01 ~ 2025-12):
- 수량 y_im = b_i · g_i^{m/12} · s(달), s(달) = 1 + 0.1·sin(2π달/12). 잡음 없음 → B2는 정확(오차 0), B1 = y/g_i, B0 = y_a.
- 단가 UP_im = u_i · d_i^{1[m ≥ L_i]}(L_i = 그 성분 목록가가 내린 달, 없을 수 있음, d_i ∈ [0.80, 0.95] 성분마다 다름 →
  순열 시험이 퇴화하지 않음). 금액 = y · UP.
- 목록: 성분마다 제품 3개, 목록가 = p_ij · d_i^{1[m ≥ L_i]}, 2022-01 ~ 2025-12 모두 급여. L_i − 1월 목록에 R2 토큰
  (시행일 L_i 1일, 금액 d_i·p_ij). 성분 S002는 L − 1월에 집행정지 표시(그 달만) → 그 달 ΔN = 0.
- 불규칙: S003은 2024-03 미관측(→ 2024-04 재등장 첫 달), S004는 2023-08 수량 0.
손 계산 기대값은 위 생성식에서 직접 계산한다(파이프라인 예측값을 쓰지 않는다).
사용: python -m pbd.forecast_v2.dryrun  (make p2v2-dryrun) → outputs/p2v2/dryrun/
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from pbd.common import ROOT, load_yaml
from pbd.forecast_v2.list_info import ListData
from pbd.forecast_v2.run import run_all
from pbd.forecast_v2.select import select_target
from pbd.forecast_v2.timeaxis import madd, mnum, mrange

OUT = ROOT / "outputs/p2v2/dryrun"
MONTHS = mrange("2018-01", "2025-12")
N = 24
MISS = ("S003", "2024-03")
ZERO = ("S004", "2023-08")
BLOCK = "S002"
RHO = 0.9


def world(seed: int = 7):
    rng = np.random.default_rng(seed)
    c4 = [f"S{i:03d}" for i in range(1, N + 1)]
    b = rng.uniform(1e4, 1e5, N)
    g = rng.uniform(0.95, 1.10, N)
    u = rng.uniform(50, 500, N)
    drops = mrange("2022-04", "2025-10")
    L = [None if i % 4 == 3 else drops[int(rng.integers(len(drops)))] for i in range(N)]
    L[1] = "2023-05"                         # S002 (집행정지 성분)
    cls = rng.choice([119, 141, 214, 218, 222, 232, 618], N)
    d = rng.uniform(0.80, 0.95, N)
    return dict(c4=c4, b=b, g=g, u=u, L=L, cls=cls, d=d)


def qty(W, i, m):
    j = mnum(m) - mnum(MONTHS[0])
    mo = int(m[5:7])
    return W["b"][i] * W["g"][i] ** (j / 12) * (1 + 0.1 * math.sin(2 * math.pi * mo / 12))


def up(W, i, m):
    L = W["L"][i]
    return W["u"][i] * (W["d"][i] if (L is not None and m >= L) else 1.0)


def panel(W) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(W["c4"]):
        for m in MONTHS:
            q, p = qty(W, i, m), up(W, i, m)
            obs = (c, m) != MISS
            if (c, m) == ZERO:
                q = 0.0
            rows.append((c, m, obs, q if obs else None, q * p if obs else None, round(q / 10) if obs else None))
    return pd.DataFrame(rows, columns=["c4", "month", "observed", "claim_qty", "claim_amt", "claim_cnt"])


def lists(W) -> ListData:
    psm, tok = [], []
    for i, c in enumerate(W["c4"]):
        for k in range(3):
            pc = f"{i:03d}{k:06d}"
            p0 = W["u"][i] * (0.8 + 0.2 * k)
            for m in mrange("2022-01", "2025-12"):
                L = W["L"][i]
                psm.append((pc, m, c, "reimbursed", p0 * (W["d"][i] if (L and m >= L) else 1.0), f"F{k}"))
            if W["L"][i]:
                tok.append((madd(W["L"][i], -1), pc, "R2", pd.Timestamp(W["L"][i] + "-01"), p0 * W["d"][i], True, False))
    psm = pd.DataFrame(psm, columns=["product_code", "list_month", "c4", "state_as_known", "price_listed", "firm"])
    tok = pd.DataFrame(tok, columns=["list_month", "product_code", "token_type", "token_date", "amount", "is_future",
                                     "in_month_dated"])
    i = W["c4"].index(BLOCK)
    m = madd(W["L"][i], -1)
    stay = pd.DataFrame([(f"{i:03d}{k:06d}", m, W["L"][i]) for k in range(3)],
                        columns=["product_code", "known_month", "end_month"])
    return ListData(psm, tok, stay)


def usable(W, i, m):
    c = W["c4"][i]
    if (c, m) in (MISS, ZERO) or (c, madd(m, -1)) == MISS:
        return False
    return True


def hand(W, target: pd.DataFrame, cfg) -> dict:
    """생성식만으로 계산한 기대값"""
    idx = {c: W["c4"].index(c) for c in target.c4}
    wp = mrange(*cfg["selection"]["weight_period"])
    amt = np.array([sum(qty(W, idx[c], m) * up(W, idx[c], m) for m in wp) for c in target.c4])
    w = amt / amt.sum()
    em = mrange(cfg["eval"]["start"], cfg["eval"]["end"])
    h = cfg["eval"]["h"]
    num = {"B0": np.zeros(len(w)), "B1": np.zeros(len(w))}
    den = np.zeros(len(w))
    cells = 0
    q_u0, q_u1, n_q = np.zeros(len(w)), np.zeros(len(w)), np.zeros(len(w))
    for k, c in enumerate(target.c4):
        i = idx[c]
        for t in em:
            a = madd(t, -h)
            need = [t, madd(t, -12)] + mrange(madd(a, -23), a)
            if not all(usable(W, i, m) for m in need):
                continue
            cells += 1
            y = qty(W, i, t)
            den[k] += y
            num["B1"][k] += abs(qty(W, i, madd(t, -12)) - y)
            num["B0"][k] += abs(qty(W, i, a) - y)
            if a >= "2022-01":
                L, M = W["L"][i], madd(a, 5)
                upa, upb, upt = up(W, i, a), up(W, i, madd(a, -12)), up(W, i, t)
                dk = math.log(W["d"][i]) if (L and a < L <= M) else 0.0
                q_u1[k] += abs(math.log(upa) + dk - math.log(upt))
                q_u0[k] += abs(math.log(upa * (upa / upb) ** (h / 12)) - math.log(upt))
                n_q[k] += 1
    wape = {m: float((w * num[m] / den).sum()) for m in num}
    q2 = {"U1": float((w * q_u1 / n_q).sum()), "U0": float((w * q_u0 / n_q).sum())}
    dn = {}
    for c in target.c4:
        i = idx[c]
        if W["L"][i]:
            dn[c] = 0.0 if c == BLOCK else math.log(W["d"][i])
    return dict(w=w, wape=wape, cells=cells, q2=q2, dn=dn)


def main() -> None:
    cfg = load_yaml("p2v2.yaml")
    seed = load_yaml("params.yaml")["seed"]
    W = world()
    pn = panel(W)
    cls = pd.DataFrame({"c4": W["c4"], "cls": W["cls"]})
    t, steps, _ = select_target(pn[pn.month <= cfg["selection"]["a0"]], cls)
    ld = lists(W)
    H = hand(W, t, cfg)
    R = run_all(pn, t, ld, lambda M: RHO, cfg, OUT, seed, batch_months={"2024-01"}, n_perm=199, n_boot=999)
    res = R["res"]
    chk = []

    def add(name, expected, got, tol=1e-9):
        ok = (expected == got) if isinstance(expected, (str, bool, int)) and not isinstance(expected, float) \
            else abs(float(expected) - float(got)) <= tol * max(1.0, abs(float(expected)))
        chk.append(dict(check=name, expected=expected, got=got, ok=bool(ok)))

    add("대상 성분 수", N, len(t))
    add("가중치 최대 차이(손 계산)", 0.0, float(np.abs(t.w.to_numpy() - H["w"]).max()))
    add("평가 칸 수", H["cells"], res["eval_cells"])
    add("B1 금액 가중 WAPE", H["wape"]["B1"], res["q1_wape"]["B1"])
    add("B0 금액 가중 WAPE", H["wape"]["B0"], res["q1_wape"]["B0"])
    add("B2 금액 가중 WAPE(정확 = 0)", 0.0, res["q1_wape"]["B2"])
    add("Q2 U1 지표", H["q2"]["U1"], res["q2_main"]["metric_a"])
    add("Q2 U0 지표", H["q2"]["U0"], res["q2_main"]["metric_b"])
    lc_info = {c: ld.info(madd(W["L"][W["c4"].index(c)], -6), madd(W["L"][W["c4"].index(c)], -1),
                          W["L"][W["c4"].index(c)]).loc[c, "dN"] for c in H["dn"]}
    add("ΔN 최대 차이(손 계산, 집행정지 성분 0)", 0.0, max(abs(lc_info[c] - H["dn"][c]) for c in H["dn"]))
    fd = ld.first_drop("2025-12")
    add("주입 첫 하락 달 = L_i", True, all(fd.get(c) == W["L"][W["c4"].index(c)] for c in W["c4"] if W["L"][W["c4"].index(c)]))
    add("판정 문구 있음(Q1 종합)", True, isinstance(res["q1_overall"]["text"], str) and len(res["q1_overall"]["text"]) > 0)
    add("Q2-d 주입 3판 결과", 3, len(res["q2d_injection"]))
    add("순열 p ∈ (0, 1]", True, 0 < res["q2_perm_p"] <= 1)
    add("순열 p < 0.05 (인하 폭이 성분마다 달라 정렬이 기여)", True, res["q2_perm_p"] < 0.05)
    add("EWC ν (T = 36)", 4, res["q1"]["B1"]["nu"])
    add("EWC ν (Q2-d T = 24)", 3, res["q2d"]["nu"])
    add("G 대 B2: B2 지표 0", 0.0, res["q1"]["B2"]["metric_b"])
    ck = pd.DataFrame(chk)
    ck.to_csv(OUT / "dryrun_check.csv", index=False)
    pd.DataFrame(steps).to_csv(OUT / "target_steps.csv", index=False)
    print(ck.to_string(index=False))
    print("Q1 종합:", res["q1_overall"]["text"], "| 가장 강한 기준", res["q1_overall"]["strongest"])
    for b in ("B1", "B2", "B3"):
        q = res["q1"][b]
        print(f"  G − {b}: ΔWAPE {q['diff']:+.4f}, EWC p {q['p']:.3g}, 판정 {q['verdict']} | DM p {q['p_dm']:.3g} ({q['verdict_dm']}), B12 p {q['p_b12']:.3g}")
    print("Q2 주:", res["q2_main"]["verdict"], "| 가짜:", {k: v.get("verdict") for k, v in res["q2_fake"].items()},
          "| 순열 p", res["q2_perm_p"])
    print("Q2-d:", res["q2d"]["verdict"], "| 주입:", {k: (v["verdict"], round(v["recovery"], 3) if v["recovery"] is not None else None)
                                                   for k, v in res["q2d_injection"].items()})
    assert ck.ok.all(), "dry run 기대값 불일치"


if __name__ == "__main__":
    main()
