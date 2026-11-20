"""급여목록 정보 (PLAN_P2_v2 §5-6). 목록 ≤ M만 쓴다. 신호(ΔN)는 예측값에 곱하지 않고 특징으로만 쓴다.

입력 표 (실제 = P1 v5.3 원장, 합성 = 손으로 만든 같은 형식):
- psm(product_code, list_month, c4, state_as_known, price_listed, firm): 그 달 목록에서 알 수 있던 상태(as-known)와 목록가
- tok(list_month, product_code, token_type, token_date, amount, is_future, in_month_dated): 그 달 목록 비고 토큰.
  R1 = 등재 예고, R2 = 가격 예고, R3 = 급여정지(기간) 예고
- stay(product_code, known_month, end_month): 집행정지 표시가 보이기 시작한 달·사라진 달(없으면 계속)

정의 (성분 c, 목록 기준월 s(주 = M, A14 = M − 1), 마지막 관측월 a, 대상월 t; P_c(m) = m월 급여(reimbursed)·목록가 > 0 제품):
- ΔK = P_c(a) ∩ P_c(s) 같은 제품코드마다 log(p(s)/p(a))의 평균. 공통 제품이 없으면 NaN(정의 안 됨)
- ΔN = s월 목록에 남아 있는 R2 토큰 중 시행일 ∈ (s 1일, t 말일], s월에 집행정지 표시가 없는 P_c(s) 제품.
  제품마다 시행일이 가장 늦은 토큰 하나, ω · log(예고 금액 / p(s)), ω = t월 안 시행이면 (t 말일 − 시행일 + 1) / t 일수, 앞이면 1.
  |P_c(s)|로 나눈 평균(예고 없는 제품 0). exclude_in_month = True(A15)면 in_month_dated 토큰을 뺀다
- S = s월 suspended ÷ (reimbursed + suspended), SN = s월 R3 예고(is_future, 시행일 ≤ t 말일, 집행정지 없음)가 있는 P_c(s) 비율,
  LN = log1p(s월 R1 예고(is_future, 시행일 ≤ t 말일) 제품 수, 성분은 s월 목록의 c4), ΔF = log(업체 수(s)/업체 수(a)),
  ΔP = log(|P_c(s)|/|P_c(a)|). 분모가 0이면 NaN
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pbd.forecast_v2.timeaxis import madd

FIRST_LIST = "2022-01"


def month_end(m: str) -> pd.Timestamp:
    y, mm = int(m[:4]), int(m[5:7])
    return pd.Timestamp(y, mm, calendar.monthrange(y, mm)[1])


@dataclass
class ListData:
    psm: pd.DataFrame
    tok: pd.DataFrame
    stay: pd.DataFrame
    _r: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        p = self.psm
        self.tok = self.tok.assign(token_date=pd.to_datetime(self.tok.token_date))
        r = p[(p.state_as_known == "reimbursed") & (p.price_listed > 0)]
        self._r = {m: g.set_index("product_code") for m, g in r.groupby("list_month")}
        self._all = {m: g.set_index("product_code") for m, g in p.groupby("list_month")}
        self._tok = {m: g for m, g in self.tok.groupby("list_month")}

    def reimb(self, m: str) -> pd.DataFrame:
        return self._r.get(m, self.psm.iloc[:0].set_index("product_code"))

    def blocked(self, m: str) -> set:
        s = self.stay
        v = s[(s.known_month <= m) & (s.end_month.isna() | (s.end_month > m))]
        return set(v.product_code)

    def dK(self, a: str, s: str) -> pd.Series:
        pa, ps = self.reimb(a), self.reimb(s)
        j = pa[["c4", "price_listed"]].join(ps[["price_listed"]], rsuffix="_s", how="inner")
        j = j[j.c4 == ps.loc[j.index, "c4"]]
        return np.log(j.price_listed_s / j.price_listed).groupby(j.c4).mean()

    def info(self, a: str, s: str, t: str, exclude_in_month: bool = False) -> pd.DataFrame:
        """성분4마다 dK, dN, dF, dP, S, SN, LN (목록 s ≤ M 만)"""
        ps, pa = self.reimb(s), self.reimb(a)
        n_s = ps.groupby("c4").size()
        out = pd.DataFrame(index=n_s.index)
        out["dK"] = self.dK(a, s)
        tend, s1 = month_end(t), pd.Timestamp(s + "-01")
        blk = self.blocked(s)
        tk = self._tok.get(s, self.tok.iloc[:0])
        if exclude_in_month:
            tk = tk[~tk.in_month_dated.astype(bool)]
        r2 = tk[(tk.token_type == "R2") & (tk.token_date > s1) & (tk.token_date <= tend)
                & tk.product_code.isin(ps.index) & ~tk.product_code.isin(blk) & (tk.amount > 0)]
        r2 = r2.sort_values(["product_code", "token_date"]).groupby("product_code").tail(1)
        if len(r2):
            ndays = tend.day
            in_t = r2.token_date.dt.strftime("%Y-%m") == t
            w = np.where(in_t, (ndays - r2.token_date.dt.day + 1) / ndays, 1.0)
            v = w * np.log(r2.amount.to_numpy() / ps.loc[r2.product_code, "price_listed"].to_numpy())
            dn = pd.Series(v, index=ps.loc[r2.product_code, "c4"].to_numpy()).groupby(level=0).sum()
            out["dN"] = (dn / n_s).reindex(out.index).fillna(0.0)
        else:
            out["dN"] = 0.0
        al = self._all.get(s)
        st = al.groupby("c4").state_as_known.agg(lambda x: (x == "suspended").sum() / max(x.isin(["reimbursed", "suspended"]).sum(), 1))
        out["S"] = st.reindex(out.index).fillna(0.0)
        r3 = tk[(tk.token_type == "R3") & tk.is_future.astype(bool) & (tk.token_date <= tend)
                & tk.product_code.isin(ps.index) & ~tk.product_code.isin(blk)]
        sn = ps.loc[r3.product_code.unique(), "c4"].value_counts() if len(r3) else pd.Series(dtype=float)
        out["SN"] = (sn / n_s).reindex(out.index).fillna(0.0)
        r1 = tk[(tk.token_type == "R1") & tk.is_future.astype(bool) & (tk.token_date <= tend) & tk.product_code.isin(al.index)]
        ln = al.loc[r1.product_code.unique(), "c4"].value_counts() if len(r1) else pd.Series(dtype=float)
        out["LN"] = np.log1p(ln.reindex(out.index).fillna(0.0))
        fa, fs = pa.groupby("c4").firm.nunique(), ps.groupby("c4").firm.nunique()
        out["dF"] = np.log(fs / fa.reindex(fs.index))
        out["dP"] = np.log(n_s / pa.groupby("c4").size().reindex(n_s.index))
        return out

    def first_drop(self, last: str) -> pd.Series:
        """성분마다 목록 가격지수가 처음 내려간 목록월 m (ΔK(m − 1 → m) < 0, m ≤ last). 주입 시험용"""
        out = {}
        m = madd(FIRST_LIST, 1)
        while m <= last:
            d = self.dK(madd(m, -1), m)
            for c in d[d < 0].index:
                out.setdefault(c, m)
            m = madd(m, 1)
        return pd.Series(out, dtype=object)


def load_db(con) -> ListData:
    """실제 자료: P1 v5.3 원장(9c7743a 코드로 만든 DB). 목록은 2022-01 ~ 2025-12(data_years)"""
    psm = con.execute("""select product_code, list_month, c4, state_as_known, price_listed, firm
                         from product_state_month_v5""").df()
    tok = con.execute("""select distinct list_month, product_code, token_type, token_date, amount, is_future, in_month_dated
                         from note_token where token_type in ('R1', 'R2', 'R3')""").df()
    st = con.execute("select product_code, first_month, end_month, target_source, stay_decision_date from stay_v5").df()
    dec = pd.to_datetime(st.stay_decision_date).dt.strftime("%Y-%m")
    ext = st.target_source.eq("external") & dec.notna()
    st["known_month"] = np.where(ext, [madd(d, 1) if isinstance(d, str) else None for d in dec], st.first_month)
    return ListData(psm, tok, st[["product_code", "known_month", "end_month"]])


def rho_from_asof(con_asof, M: str) -> float:
    """ρ_M: build_asof(M) 원장의 약가 예고 중 확인 달 ≤ M인 것의 정시 실현 비율(제품 단위).
    분모 = REALIZED·REALIZED_LATE·REALIZED_CHANGED·NOT_REALIZED, 분자 = REALIZED. 분모 0이면 1"""
    r = con_asof.execute("""select count(*) filter (where outcome = 'REALIZED'),
               count(*) filter (where outcome in ('REALIZED', 'REALIZED_LATE', 'REALIZED_CHANGED', 'NOT_REALIZED'))
        from signal_v5 where signal_type = 'PRICE_NOTICE' and check_month <= ?""", [M]).fetchone()
    return 1.0 if not r[1] else r[0] / r[1]
