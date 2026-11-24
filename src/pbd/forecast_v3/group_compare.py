"""P2 v3: 분류군(약효분류 2자리, ATC 2단계)별 예측 오차와 사용 패턴 비교 (사후 탐색, 판정 없음).
설계: docs/PLAN_P2_v3.md. P2 v2 예측·대상·가중치는 읽기만 한다.
실행: make p2v3-group → outputs/p2v3/
"""
from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd

MODELS = ("G", "B3", "B1", "B2")
EVAL_START, EVAL_END = "2023-01", "2025-12"
PATTERN_COLS = ("seas_strength", "trend_strength", "annual_growth", "resid_sd", "winter_summer", "yoy_sd")
ATC2_RE = re.compile(r"^[A-Z][0-9]{2}")
UNMAPPED = "미연결"
SMALL_N = 3
# 군간 비교 지표: G 오차, 모델 차이(G − B3, G − B1: 군마다 우열이 다른지), 패턴
# lr_ = log(WAPE_G ÷ WAPE_기준), 모델 우열의 상대 크기. d_ = 원 척도 차이(극단값에 끌림, 참고)
BETWEEN_COLS = ("wape_G", "wape_B3", "lr_G_B3", "lr_G_B1", "d_G_B3", "d_G_B1") + PATTERN_COLS


# ---------------------------------------------------------------- 분류군 배정
def assign_atc2(products: pd.DataFrame, c4s) -> pd.DataFrame:
    """products: c4, product_code, atc. 성분4마다 서로 다른 제품코드 수가 가장 많은 ATC 2단계(같으면 작은 코드).
    형식이 맞지 않는 ATC는 세지 않는다. 연결 없는 성분은 '미연결'."""
    p = products.copy()
    p["atc"] = p["atc"].astype("string").str.strip().str.upper()
    p = p[p["atc"].fillna("").str.match(ATC2_RE)]
    p["atc2"] = p["atc"].str[:3]
    cnt = p.groupby(["c4", "atc2"]).product_code.nunique().rename("n").reset_index()
    tot = cnt.groupby("c4").n.sum().rename("n_tot")
    cnt = cnt.sort_values(["c4", "n", "atc2"], ascending=[True, False, True])
    top = cnt.drop_duplicates("c4").set_index("c4")
    out = pd.DataFrame(index=pd.Index(list(c4s), name="c4"))
    out["atc2"] = top.atc2.reindex(out.index).fillna(UNMAPPED)
    out["atc2_n"] = cnt.groupby("c4").size().reindex(out.index).fillna(0).astype(int)
    out["atc2_top_share"] = (top.n / tot).reindex(out.index)
    # 최다가 동점이었는지 (작은 코드 규칙이 실제로 쓰였는지)
    second = cnt.groupby("c4").n.apply(lambda s: s.iloc[1] if len(s) > 1 else np.nan)
    out["atc2_tie"] = (second.reindex(out.index) == top.n.reindex(out.index)).fillna(False)
    return out.reset_index()


def merge_small_groups(g: pd.Series, min_n: int = None) -> pd.Series:
    """성분 min_n개 미만 군 → '{상위}_기타' (상위 = 코드 첫 글자: 약효분류 첫 자리, ATC 1단계).
    합친 기타 군도 min_n개 미만이면 '전체_기타'. '미연결'은 합치지 않는다."""
    min_n = SMALL_N if min_n is None else min_n
    g = g.astype(str)
    n = g.map(g.value_counts())
    out = g.where((n >= min_n) | (g == UNMAPPED), g.str[0] + "_기타")
    n2 = out.map(out.value_counts())
    return out.where(~out.str.endswith("_기타") | (n2 >= min_n), "전체_기타")


# ---------------------------------------------------------------- 평가 칸과 성분 오차
def eval_cells(pred: pd.DataFrame, actual: pd.DataFrame, dropped: pd.DataFrame,
               start: str = EVAL_START, end: str = EVAL_END) -> pd.DataFrame:
    """pred: c4, t, model, yhat (h = 6). actual: c4, t, y (관측 칸만). dropped: c4, t.
    E = 대상월 start ~ end(기본 2023-01 ~ 2025-12), 정답 > 0, G·B1·B2·B3 모두 예측, dropped 아님 (PLAN_P2_v2 §5-7)."""
    p = pred[(pred.t >= start) & (pred.t <= end) & pred.model.isin(MODELS)]
    w = p.pivot_table(index=["c4", "t"], columns="model", values="yhat").reset_index()
    w = w.merge(actual[["c4", "t", "y"]], on=["c4", "t"])
    w = w.dropna(subset=list(MODELS) + ["y"])
    w = w[w.y > 0]
    key = pd.MultiIndex.from_frame(dropped[["c4", "t"]])
    w = w[~pd.MultiIndex.from_frame(w[["c4", "t"]]).isin(key)]
    return w.reset_index(drop=True)


def ingredient_errors(cells: pd.DataFrame) -> pd.DataFrame:
    """성분별 WAPE_i, bias_i (모델마다), 평가 칸 수"""
    Y = cells.groupby("c4").y.sum()
    out = pd.DataFrame({"n_cells": cells.groupby("c4").size()})
    for m in MODELS:
        out[f"wape_{m}"] = (cells[m] - cells.y).abs().groupby(cells.c4).sum() / Y
        out[f"bias_{m}"] = (cells[m] - cells.y).groupby(cells.c4).sum() / Y
    return out


def weighted_wape(err: pd.DataFrame, w: pd.Series, model: str) -> float:
    """Σ w_i WAPE_i ÷ Σ w_i (평가 칸이 있는 성분만)"""
    ww = w.reindex(err.index)
    return float((ww * err[f"wape_{model}"]).sum() / ww.sum())


# ---------------------------------------------------------------- 사용 패턴
def pattern_metrics(series: pd.Series, prev_year: pd.Series | None = None) -> dict:
    """series: 창(36개월) 원외 청구수량, index = 'YYYY-MM' 정렬, 모두 > 0이어야 한다.
    prev_year: 창 직전 12개월(전년 동월비용). 계산할 수 없으면 NaN."""
    from statsmodels.tsa.seasonal import STL

    s = series.sort_index()
    out = dict.fromkeys(PATTERN_COLS, np.nan)
    if len(s) < 24 or s.isna().any() or (s <= 0).any():
        return out
    ly = np.log(s.to_numpy(dtype=float))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = STL(ly, period=12, robust=True).fit()
    R, S, T = r.resid, r.seasonal, r.trend
    vr = np.var(R)
    out["seas_strength"] = max(0.0, 1 - vr / np.var(S + R)) if np.var(S + R) > 0 else 0.0
    out["trend_strength"] = max(0.0, 1 - vr / np.var(T + R)) if np.var(T + R) > 0 else 0.0
    x = np.arange(len(ly))
    out["annual_growth"] = float(np.exp(12 * np.polyfit(x, ly, 1)[0]) - 1)
    out["resid_sd"] = float(np.std(R, ddof=1))
    mon = np.array([int(k[5:7]) for k in s.index])
    win, summ = s.to_numpy()[np.isin(mon, [11, 12, 1, 2, 3])], s.to_numpy()[np.isin(mon, [6, 7, 8])]
    out["winter_summer"] = float(win.mean() / summ.mean())
    if prev_year is not None:
        full = pd.concat([prev_year, s]).sort_index()
        full = full[~full.index.duplicated()]
        if len(full) == len(s) + 12 and not full.isna().any() and (full > 0).all():
            lf = np.log(full.to_numpy(dtype=float))
            out["yoy_sd"] = float(np.std(lf[12:] - lf[:-12], ddof=1))
    return out


# ---------------------------------------------------------------- 분류군 표
def _wmean(v: pd.Series, w: pd.Series) -> float:
    m = v.notna()
    return float((v[m] * w[m]).sum() / w[m].sum()) if m.any() and w[m].sum() > 0 else np.nan


def group_table(ing: pd.DataFrame, gcol: str, seed: int, n_boot: int = 2000) -> pd.DataFrame:
    """ing: 성분 단위 표(index c4; w, ing_name, wape_*, bias_*, n_cells, 패턴 지표, gcol).
    G 분류군 오차 오름차순 순위(1 = 오차 가장 작음)."""
    rng = np.random.default_rng(seed)
    overall = {m: _wmean(ing[f"wape_{m}"], ing.w) for m in MODELS}
    rows = []
    for g, d in ing.groupby(gcol, sort=True):
        d = d.sort_values("w", ascending=False)
        w = d.w
        r = {"group": g, "n_ingr": len(d), "w_share": w.sum(), "n_cells": int(d.n_cells.sum()),
             "top_ingredients": " / ".join(d["ing_name"].fillna(d.index.to_series()).astype(str).head(3)),
             "top1_w_share_in_group": w.iloc[0] / w.sum()}
        for m in MODELS:
            r[f"wape_{m}"] = _wmean(d[f"wape_{m}"], w)
        r["best_model"] = min(MODELS, key=lambda m: r[f"wape_{m}"])
        r["G_minus_overall"] = r["wape_G"] - overall["G"]
        r["G_minus_B3"] = r["wape_G"] - r["wape_B3"]
        r["G_minus_B1"] = r["wape_G"] - r["wape_B1"]
        r["merged_from"] = ", ".join(sorted(d["src_group"].astype(str).unique())) if "src_group" in d else ""
        r["G_gain_vs_B1"] = 1 - r["wape_G"] / r["wape_B1"]
        r["G_gain_vs_B3"] = 1 - r["wape_G"] / r["wape_B3"]
        r["bias_G"] = _wmean(d["bias_G"], w)
        q = d.wape_G.quantile([0.25, 0.5, 0.75])
        r["wape_G_p25"], r["wape_G_median"], r["wape_G_p75"] = q.iloc[0], q.iloc[1], q.iloc[2]
        if len(d) >= 2:
            e, ww = d.wape_G.to_numpy(), w.to_numpy()
            idx = rng.integers(0, len(d), size=(n_boot, len(d)))
            b = (e[idx] * ww[idx]).sum(1) / ww[idx].sum(1)
            r["wape_G_lo90"], r["wape_G_hi90"] = np.quantile(b, [0.05, 0.95])
        else:
            r["wape_G_lo90"] = r["wape_G_hi90"] = np.nan
        for c in PATTERN_COLS:
            r[f"{c}_median"] = d[c].median()
            r[f"{c}_wmean"] = _wmean(d[c], w)
        r["n_pattern"] = int(d[PATTERN_COLS[0]].notna().sum())
        r["small_group"] = len(d) < SMALL_N
        rows.append(r)
    t = pd.DataFrame(rows).sort_values(["wape_G", "group"]).reset_index(drop=True)
    t.insert(0, "rank_G", np.arange(1, len(t) + 1))
    return t


def group_difference_tests(ing: pd.DataFrame, gcol: str) -> pd.DataFrame:
    """성분 SMALL_N개 이상 군끼리 Kruskal–Wallis (서술용). 대상: 오차, 모델 차이, 패턴 지표"""
    from scipy.stats import kruskal

    size = ing[gcol].map(ing[gcol].value_counts())
    big = ing[(size >= SMALL_N) & (ing[gcol] != UNMAPPED)]
    rows = []
    for c in BETWEEN_COLS:
        groups = [d[c].dropna().to_numpy() for _, d in big.groupby(gcol)]
        groups = [x for x in groups if len(x) > 0]
        n = sum(len(x) for x in groups)
        H, p = kruskal(*groups)
        rows.append({"grouping": gcol, "metric": c, "n_groups": len(groups), "n_ingr": n,
                     "kw_H": H, "kw_p": p, "epsilon2": H / (n - 1)})
    return pd.DataFrame(rows)


def between_share(ing: pd.DataFrame, gcol: str) -> pd.DataFrame:
    """군간 몫 η²_w = Σ_g W_g (ē_g − ē)² ÷ Σ_i w_i (e_i − ē)² (가중치 w, 값이 있는 성분만).
    오차 꼬리가 길어 WAPE는 log에서도 계산한다. '미연결'은 뺀다."""
    d0 = ing[ing[gcol] != UNMAPPED]
    rows = []
    for c in BETWEEN_COLS + ("log_wape_G", "log_wape_B3"):
        d = d0[[gcol, "w", c]].dropna()
        if d.empty:
            continue
        w, e = d.w, d[c]
        mu = (w * e).sum() / w.sum()
        gm = (w * e).groupby(d[gcol]).sum() / w.groupby(d[gcol]).sum()
        between = (w.groupby(d[gcol]).sum() * (gm - mu) ** 2).sum()
        total = (w * (e - mu) ** 2).sum()
        rows.append({"grouping": gcol, "metric": c, "n_groups": d[gcol].nunique(), "n_ingr": len(d),
                     "eta2_weighted": between / total if total > 0 else np.nan,
                     # 군 배정이 무작위일 때 η²의 대략 기댓값 (비가중 근사). 군이 많을수록 η²는 저절로 커진다
                     "eta2_null_approx": (d[gcol].nunique() - 1) / (len(d) - 1)})
    return pd.DataFrame(rows)


def pattern_error_association(ing: pd.DataFrame) -> pd.DataFrame:
    """성분 단위 Spearman: G의 WAPE_i 대 패턴 지표 (연관, 원인 아님)"""
    rows = []
    for c in PATTERN_COLS + ("log_w",):
        x = ing[["wape_G", c]].dropna()
        rows.append({"metric": c, "n": len(x), "spearman_with_wape_G": x.wape_G.rank().corr(x[c].rank())})
    return pd.DataFrame(rows)
