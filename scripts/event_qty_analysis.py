"""사건(약가인하·경쟁제품 추가·대체조제 목록 추가) 전후 성분 원외 처방수량 변화 (2026-11-25 사용자 지시).

규칙은 결과를 보기 전에 정했다 (docs/DECISIONS.md 2026-11-25).
- 단위: 성분4 × 전국 × 월, 원외 청구수량. 사건 달 m = 유효일이 속한 달(대체조제 목록 추가는 처음 보인 달).
- 값 D = [ln 수량 평균(m~m+w-1) - 평균(m-w~m-1)] - [같은 두 구간의 12개월 전 차이]. 주 창 w=6.
- m-12-w ~ m+w-1 모든 달이 관측돼야 한다(0·미관측은 채우지 않고 뺀다).
- 대조군: 같은 달, 같은 조건에서 m-6 ~ m+w-1에 그 유형의 사건이 하나도 없는 성분.
- 사건 성분: m-6 ~ m-1에 같은 유형 사건 없음. 이후 추가 사건은 허용.
- 추정: D ~ 사건 + 사건 달 고정효과. 표준오차는 성분 군집과 사건 달 군집 중 큰 값.
"""
from __future__ import annotations
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "event_qty"
CUT = -0.01  # 제품 수 가중 평균 상한금액 변화 1% 이상 인하
OTHER = {"P": ["E", "S"], "E": ["P", "S"], "S": ["P", "E"], "S_ex": ["P", "E"]}


def midx(s):
    s = s.astype(str)
    return s.str[:4].astype(int) * 12 + s.str[5:7].astype(int) - 1


def calc_d(y: np.ndarray, t: int, w: int):
    """y = 월 순서 ln 수량(결측 nan), t = 사건 달 위치. 필요한 달이 하나라도 없으면 nan"""
    lo, hi = t - 12 - w, t + w
    if lo < 0 or hi > len(y) or np.isnan(y[lo:hi]).any():
        return np.nan
    return (y[t:t + w].mean() - y[t - w:t].mean()) - (y[t - 12:t - 12 + w].mean() - y[t - 12 - w:t - 12].mean())


def fit(d: pd.DataFrame, y="D", covs=(), fe="t"):
    """월(또는 월×군) 고정효과 OLS. 처치·대조가 모두 있는 fe 칸만 쓴다."""
    d = d.dropna(subset=[y])
    both = d.groupby(fe).treat.transform("nunique") == 2
    d = d[both]
    n1, n0 = int(d.treat.sum()), int((d.treat == 0).sum())
    if n1 < 10 or n0 < 10:
        return dict(n_event=n1, n_c4=d[d.treat == 1].c4.nunique(), n_control=n0)
    cols = ["treat", *covs]
    X = d[cols].astype(float)
    X = (X - X.groupby(d[fe]).transform("mean")).to_numpy()
    yy = (d[y] - d.groupby(fe)[y].transform("mean")).to_numpy()
    xi = np.linalg.pinv(X.T @ X)
    b = xi @ X.T @ yy
    u = X * (yy - X @ b)[:, None]
    ses, gs = [], []
    for g in ("c4", "t"):
        s = pd.DataFrame(u).groupby(d[g].to_numpy()).sum().to_numpy()
        G = len(s)
        if G < 10:
            continue
        ses.append(np.sqrt((xi @ (s.T @ s) @ xi)[0, 0] * G / (G - 1)))
        gs.append(G)
    k = int(np.argmax(ses))
    se, q = ses[k], stats.t.ppf(.975, gs[k] - 1)
    pc = lambda v: (np.exp(v) - 1) * 100
    return dict(n_event=n1, n_c4=d[d.treat == 1].c4.nunique(), n_months=d[d.treat == 1].t.nunique(), n_control=n0,
                pct=pc(b[0]), lo=pc(b[0] - q * se), hi=pc(b[0] + q * se),
                p=2 * stats.t.sf(abs(b[0] / se), gs[k] - 1), mde80=pc(2.8 * se),
                median_gap=pc((d[d.treat == 1][y] - d[d.treat == 1].t.map(d[d.treat == 0].groupby("t")[y].median())).median()))


def load(con):
    q = lambda s: con.execute(s).df()
    panel = q("select c4, month, claim_qty from fc_panel where observed and claim_qty > 0")
    panel["t"] = midx(panel.month)
    first, last = 2018 * 12, 2025 * 12 + 11
    Y = np.log(panel.pivot(index="c4", columns="t", values="claim_qty").reindex(columns=range(first, last + 1)))
    st = q("select product_code p, list_month, c4, trim(firm) firm, in_substitute_list s from state_month_v5")
    st["t"] = midx(st.list_month)
    npd = st.groupby(["c4", "t"]).p.nunique()
    nsub = st[st.s.fillna(False).astype(bool)].groupby(["c4", "t"]).p.nunique()
    firms = set(zip(st.c4, st.t, st.firm))
    ev = q("""select event_type, kind, product_code p, market_c4 c4, strftime(effective_date, '%Y-%m') m, price_before pb, price_after pa
              from event_ledger_v5 where retracted_month is null and event_type in ('PRICE_CHANGE', 'LISTING')""")
    ev["t"] = midx(ev.m)
    # P
    pr = ev[ev.event_type == "PRICE_CHANGE"].copy()
    pr["n"] = pd.MultiIndex.from_arrays([pr.c4, pr.t - 1]).map(npd.to_dict())
    pr = pr[pr.n.notna() & (pr.pb > 0)]
    pr["dk"] = np.log(pr.pa / pr.pb) / pr.n
    dk = pr.groupby(["c4", "t"]).dk.sum()
    P_any = pr[pr.pa < pr.pb].groupby(["c4", "t"]).size()
    # E: 실질 신규 등재 중 그 성분에 전달까지 없던 업체의 제품
    en = ev[(ev.event_type == "LISTING") & (ev.kind == "new_listing")].copy()
    prod_firm = st.sort_values("t").drop_duplicates("p").set_index("p").firm
    en["firm"] = en.p.map(prod_firm)
    en["newfirm"] = [(c, t - 1, f) not in firms for c, t, f in zip(en.c4, en.t, en.firm)]
    en_any = en.groupby(["c4", "t"]).size()
    en = en[en.newfirm]
    E = en.groupby(["c4", "t"]).size()
    # S: 대체조제 목록에 창 안에서 처음 보인 제품 (코드 이어받기 제외)
    suc = set(q("select new from succession_v5").new)
    s = st[st.s.fillna(False).astype(bool)].groupby("p").t.min().rename("t").reset_index()
    s = s[(s.t > 2022 * 12) & ~s.p.isin(suc)]
    c4at = st.set_index(["p", "t"]).c4
    s["c4"] = [c4at.get((p, t)) for p, t in zip(s.p, s.t)]
    ben = set(zip(st.p, st.t))
    s["existing"] = [(p, t - 1) in ben for p, t in zip(s.p, s.t)]
    S = s.groupby(["c4", "t"]).size()
    Sx = s[s.existing].groupby(["c4", "t"]).size()
    return dict(Y=Y, first=first, npd=npd, nsub=nsub, dk=dk, P=dk[dk <= CUT], P_any=P_any, E=E, E_any=en_any, S=S, S_ex=Sx,
                cls=q("select c4, cls from fc_c4_class").set_index("c4").cls)


def near(setc, c, t, a, b):
    return any((c, k) in setc for k in range(t + a, t + b + 1))


def build(L, typ, w=6, shift=0, min_prod=2, qty_sum=None):
    """typ = P/E/S/S_ex. shift = 결과 창을 앞으로 옮긴 거짓 사건 시점(개월). 반환: 사건·대조 칸과 제외 집계"""
    ev = set(L[typ].index)
    anyset = set(L[{"P": "P_any", "E": "E_any", "S": "S", "S_ex": "S"}[typ]].index)
    others = {o: set(L[o].index) for o in OTHER[typ]}
    npd = L["npd"].to_dict()
    nsub = L["nsub"].to_dict()
    rows, drop = [], {}
    arr = {c: r.to_numpy() for c, r in L["Y"].iterrows()}
    cells = [c for c in L["npd"].index.get_level_values(0).unique()]
    for t in range(2022 * 12 + 1, 2025 * 12 + 12 - w + 1):
        for c in cells:
            tr = (c, t) in ev
            n = npd.get((c, t - 1), 0)
            why = None
            if n == 0:
                why = "전달 목록 제품 없음"
            elif typ != "P" and n < min_prod:
                why = "전달 제품 2개 미만"
            elif tr and near(ev, c, t, -6, -1):
                why = "직전 6개월에 같은 유형 사건"
            elif not tr and near(anyset, c, t, -6, w - 1):
                continue
            d = calc_d(arr[c], t - L["first"] - shift, w) if c in arr else np.nan
            if why is None and np.isnan(d):
                why = "필요한 달에 원외 미관측(또는 계열 시작 전)"
            if tr:
                L.setdefault('audit', []).append((typ, w, shift, c, t, why)) if why else None
                drop[why or "사용"] = drop.get(why or "사용", 0) + 1
            if why:
                continue
            r = dict(c4=c, t=t, treat=int(tr), D=d, n_prod=n, n_sub=nsub.get((c, t - 1), 0), grp=L["cls"].get(c),
                     dk=L["dk"].get((c, t), 0.0), size=L[typ].get((c, t), 0))
            for o in others:
                r["o_" + o] = int(near(others[o], c, t, -6, w - 1))
            rows.append(r)
    return pd.DataFrame(rows), drop


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    L = load(con)
    res, drops, cells_out = [], [], []
    for typ in ("P", "E", "S", "S_ex"):
        d, drop = build(L, typ)
        drops += [dict(type=typ, reason=k, n=v) for k, v in drop.items()]
        add = lambda name, dd, **k: res.append(dict(type=typ, spec=name, **fit(dd, **k)))
        add("주: w=6, 사건 달 고정효과", d)
        ocs = [c for c in d if c.startswith("o_")]
        add("다른 유형 사건 통제", d, covs=ocs)
        add("다른 유형 사건 없는 칸만", d[(d[ocs].sum(axis=1) == 0)])
        d["g"] = d.t.astype(str) + "_" + d.grp.astype(str).str[:2]
        add("같은 달×약효 두 자리 안 비교", d, fe="g")
        d["r"] = pd.cut(d.n_prod, [0, 4, 19, 1e9], labels=["2~4", "5~19", "20+"], right=True).astype(str)
        d["gr"] = d.t.astype(str) + "_" + d.r
        add("같은 달×제품 수 구간 안 비교", d, fe="gr")
        add("제품 2개 이상 (P는 조건 추가)", d[d.n_prod >= 2])
        lo, hi = d.D.quantile([.01, .99])
        add("양끝 1% 절사값 대체", d.assign(D=d.D.clip(lo, hi)))
        for lab in ("2~4", "5~19", "20+"):
            add(f"제품 수 {lab}", d[d.r == lab])
        if typ == "P":
            for nm, m in (("2023-09", 2023 * 12 + 8), ("2024-07", 2024 * 12 + 6)):
                add(f"{nm}만", d[d.t == m])
            add("2023-09·2024-07 제외", d[~d.t.isin([2023 * 12 + 8, 2024 * 12 + 6])])
            for nm, a, b in (("인하폭 1~5%", -.05, -.01), ("인하폭 5% 초과", -9, -.05)):
                add(nm, d[(d.treat == 0) | ((d.dk > a) & (d.dk <= b))] if a > -9 else d[(d.treat == 0) | (d.dk <= b)])
        else:
            add("사건 크기 1개", d[(d.treat == 0) | (d["size"] == 1)])
            add("사건 크기 2개 이상", d[(d.treat == 0) | (d["size"] >= 2)])
        if typ in ("S", "S_ex"):
            add("대체조제 제품 이미 있음", d[d.n_sub >= 1])
            add("대체조제 제품 처음", d[(d.n_sub == 0)])
        for w in (3, 12):
            add(f"창 w={w}", build(L, typ, w=w)[0])
        add("거짓 시점: 사건 6개월 전", build(L, typ, shift=6)[0])
        cells_out.append(d.assign(type=typ).drop(columns=["g", "gr"]))
    r = pd.DataFrame(res)
    main_ = r.spec.str.startswith("주:") & r.type.isin(["P", "E", "S"])
    order = r[main_].p.sort_values()
    h = np.maximum.accumulate([min(1, (len(order) - i) * p) for i, p in enumerate(order)])
    r.loc[order.index, "p_holm"] = h
    r.to_csv(OUT / "estimates.csv", index=False)
    pd.DataFrame(drops).to_csv(OUT / "event_drops.csv", index=False)
    au = pd.DataFrame(L["audit"], columns=["type", "w", "shift", "c4", "t", "reason"])
    au = au[(au.w == 6) & (au["shift"] == 0)].assign(month=lambda x: (x.t // 12).astype(str) + "-" + (x.t % 12 + 1).astype(str).str.zfill(2))
    au[["type", "month", "c4", "reason"]].drop_duplicates().to_csv(ROOT / "outputs/audit/dropped_event_qty.csv", index=False)
    a = pd.concat(cells_out)
    a[a.treat == 1].sort_values(["type", "t", "c4"]).to_csv(OUT / "event_cells.csv", index=False)
    pd.set_option("display.width", 220, "display.max_columns", 20, "display.max_rows", 99)
    print(pd.DataFrame(drops).to_string()); print(r.round(3).to_string())


if __name__ == "__main__":
    main()
