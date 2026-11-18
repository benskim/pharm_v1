"""P2 T3 선행 신호 특징 (v5.3 원장, RULES_2026-11-17). 성분4 × origin.

시점 규칙 (PLAN_P2 §3, 사용자 결정 2026-11-16, 사용자 지시 2026-11-18):
- 기준 스냅샷 s: 주 정의 s = M (M월 목록은 전달 하순 공개, 기준일 M 1일 = origin), 보수적 민감도 s = M − 1 (9차 규칙).
- s 시점에 보이는 것만 쓴다: 신호는 first_month ≤ s인 행의 처음 보인 값(신호일·금액·제품), 제품 상태는 s월의
  state_as_known, 가격은 신호가 처음 보인 달의 목록가. 신호의 결과(실현·미실현·미결), 확인 달 같은 뒤 스냅샷 칸은 읽지 않는다.
- 집행정지: s월 목록에 집행정지 표시가 보이는 제품(first_month ≤ s < 표시가 처음 사라진 달)의 신호는 "실현될 인하·중지"로
  넣지 않는다. 외부 근거로만 넣은 집행정지(경보제약)는 결정일(stay_decision_date)의 다음 달 스냅샷부터 보인 것으로 친다.
  원장의 '미결(집행정지 중)'은 뒤 스냅샷으로 정해지는 값이라 쓰지 않는다. origin 시점에 보이는 막힘만 쓴다.
- 창: 신호일이 a+1월 ~ t월 (a = M − lag 마지막 관측월, t = M+1 예측 대상). 이미 시행됐지만 사용통계에 아직 안 잡힌 변화도 포함.

특징 (제품 판매량이 없어 제품은 같은 무게):
- n_prod: s월 급여 중(reimbursed) 제품 수 (log1p)
- cut_frac: 창 안 인하 예고(막히지 않음)가 있는 급여 중 제품 비율
- price_ratio: 창 안 약가 예고의 log(예고 금액 / 처음 보인 달 목록가)를 급여 중 제품 수로 나눈 평균 (예고 없는 제품 = 0)
- susp_frac: 창 안 급여중지 예고(막히지 않음)가 있는 급여 중 제품 비율
- listing_n: 창 안 등재 예고 수 (log1p)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pbd.forecast.rolling import madd, months

FEATURES = ("n_prod", "cut_frac", "price_ratio", "susp_frac", "listing_n")
FIRST_SNAPSHOT = "2022-01"


def load_tables(con) -> dict[str, pd.DataFrame]:
    sig = con.execute("""select signal_type, product_code, first_month, signal_date, amount, market_c4
                         from signal_v5 where signal_type in ('PRICE_NOTICE', 'SUSPEND_NOTICE', 'LISTING_NOTICE')""").df()
    sig["sig_month"] = pd.to_datetime(sig["signal_date"]).dt.strftime("%Y-%m")
    stay = con.execute("""select product_code, first_month, end_month, target_source, stay_decision_date from stay_v5""").df()
    dec = pd.to_datetime(stay["stay_decision_date"]).dt.strftime("%Y-%m")
    ext = stay["target_source"].eq("external") & dec.notna()
    stay["known_month"] = np.where(ext, [madd(d, 1) if isinstance(d, str) else None for d in dec], stay["first_month"])
    psm = con.execute("""select product_code, list_month, c4, state_as_known, price_listed from product_state_month_v5""").df()
    return dict(sig=sig, stay=stay, psm=psm)


def visible_signals(sig: pd.DataFrame, s: str) -> pd.DataFrame:
    """s월 스냅샷까지 보인 신호"""
    return sig[sig["first_month"] <= s]


def blocked_products(stay: pd.DataFrame, s: str) -> set[str]:
    """s월에 집행정지 표시가 보이는 제품"""
    v = stay[(stay["known_month"] <= s) & (stay["end_month"].isna() | (stay["end_month"] > s))]
    return set(v["product_code"])


def features(tb: dict[str, pd.DataFrame], s: str, a: str, t: str) -> pd.DataFrame:
    """기준 스냅샷 s, 마지막 관측월 a, 예측 대상 t의 성분4별 특징"""
    psm = tb["psm"]
    cur = psm[(psm["list_month"] == s) & (psm["state_as_known"] == "reimbursed")]
    n = cur.groupby("c4")["product_code"].nunique()
    reimb = set(cur["product_code"])
    sig = visible_signals(tb["sig"], s)
    lo = madd(a, 1)
    w = sig[(sig["sig_month"] >= lo) & (sig["sig_month"] <= t)]
    blk = blocked_products(tb["stay"], s)
    out = pd.DataFrame(index=n.index)
    out["n_prod"] = np.log1p(n)
    pr = w[(w["signal_type"] == "PRICE_NOTICE") & w["product_code"].isin(reimb) & ~w["product_code"].isin(blk)]
    if len(pr):
        p0 = psm[["product_code", "list_month", "price_listed"]].rename(columns={"list_month": "first_month"})
        pr = pr.merge(p0, on=["product_code", "first_month"], how="left")
        pr = pr[(pr["price_listed"] > 0) & (pr["amount"] > 0)]
        pr = pr.assign(r=np.log(pr["amount"] / pr["price_listed"]))
        # 한 제품에 예고가 여럿이면 신호일이 가장 늦은 것 (그 창 끝의 예고 가격)
        pr = pr.sort_values(["product_code", "signal_date"]).groupby("product_code").tail(1)
        c4p = cur.drop_duplicates("product_code").set_index("product_code")["c4"]
        pr = pr.assign(c4=pr["product_code"].map(c4p))
        out["cut_frac"] = (pr[pr["r"] < 0].groupby("c4")["product_code"].nunique() / n).reindex(out.index).fillna(0)
        out["price_ratio"] = (pr.groupby("c4")["r"].sum() / n).reindex(out.index).fillna(0)
    else:
        out["cut_frac"] = 0.0
        out["price_ratio"] = 0.0
    su = w[(w["signal_type"] == "SUSPEND_NOTICE") & w["product_code"].isin(reimb) & ~w["product_code"].isin(blk)]
    c4p = cur.drop_duplicates("product_code").set_index("product_code")["c4"]
    out["susp_frac"] = (su.assign(c4=su["product_code"].map(c4p)).groupby("c4")["product_code"].nunique() / n
                        ).reindex(out.index).fillna(0)
    li = w[w["signal_type"] == "LISTING_NOTICE"]
    out["listing_n"] = np.log1p(li.groupby("market_c4").size().reindex(out.index).fillna(0))
    return out.reset_index().rename(columns={"index": "c4"})


def feature_table(tb: dict[str, pd.DataFrame], lag: int, mode: str, origins: list[str], c4s: list[str]) -> pd.DataFrame:
    """(c4, origin) 특징표. mode = 'same' (s = M, 주) / 'prev' (s = M − 1, 민감도). 스냅샷이 없는 origin은 행 없음(NaN)"""
    rows = []
    for m in origins:
        s = m if mode == "same" else madd(m, -1)
        if s < FIRST_SNAPSHOT:
            continue
        f = features(tb, s, madd(m, -lag), madd(m, 1))
        f = f[f["c4"].isin(c4s)].assign(origin=m)
        rows.append(f)
    d = pd.concat(rows, ignore_index=True)
    return d[["c4", "origin", *FEATURES]]


def all_origins(lo: str = FIRST_SNAPSHOT, hi: str = "2025-11") -> list[str]:
    return months(lo, hi)
