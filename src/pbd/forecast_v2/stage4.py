"""Stage 4 최소판 (PLAN_P2_v2 §8, 사용자 결정 2026-11-20: p값 없이 부호 일치만).

잠금(guard):
 - 'manifest'·'forecast': 태그 p2v2-prereg-r1과 잠금 파일 일치 + reports/P2V2_backtest_result.md가 HEAD에 커밋됨
 - 'actuals'·'score': 위 + 예측 봉인 태그 p2v2-stage4-forecasts
 CLAUDE.md '하지 않을 것'의 Stage 4 예외(2026 자료 처리)는 이 조건 아래에서만 쓴다.
기능:
 - manifest: 2026 파일의 경로·크기·SHA-256·수정 시각(내용은 해석하지 않음). schema_check: 헤더·코드 칸 값만(청구 값 안 읽음)
 - forecast: Q1 모델(B1·B2·B3·G), a = 2025-07 ~ 12, h = 6, 대상월 2026-01 ~ 06. 사용통계는 data/raw 2018–2025 판만
 - load_actuals: 2026 사용통계 원외 × 성분4 × 월 합(정답으로만)
 - score: 비교(G 대 B)마다 달별 d_t, 6개월 합 ΔWAPE, 백테스트 방향과 부호가 같은 달 수, 판정.
   백테스트 판정이 '나음'(방향 −) 또는 '나쁨'(방향 +)인 비교: 합의 부호가 방향과 같고 6개월 중 4개월 이상 같으면
   '반대 방향 아님', 아니면 '반대 방향'. 그 밖의 비교는 백테스트 D̄ 부호와의 점추정 일치만 보고. **p값은 내지 않는다.**
 - 확정 채점(2027-11 이후 판)은 같은 score를 다른 정답으로 다시 부른다.
 Q2(단가)의 Stage 4는 이번 범위에서 하지 않는다(사용자 결정 2026-11-20: 2026 목록 처리·접근 예외 미승인, 후속 과제).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from pbd.common import ROOT
from pbd.forecast_v2 import gate as G
from pbd.forecast_v2.metrics import decompose

SEAL_TAG = "p2v2-stage4-forecasts"
REPORT = "reports/P2V2_backtest_result.md"
TARGET_MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
NOT_OPPOSITE, OPPOSITE = "반대 방향 아님", "반대 방향"


class Stage4Unlock:
    def __init__(self, step: str, gate: G.Gate):
        self.step, self.gate = step, gate


def guard(step: str, root: Path = ROOT) -> Stage4Unlock:
    g = G.check(G.TAG, root)
    if not g.ok:
        raise PermissionError(f"Stage 4 {step} 거부: {g.reason}")
    if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"HEAD:{REPORT}"], capture_output=True).returncode != 0:
        raise PermissionError(f"Stage 4 {step} 거부: {REPORT}가 HEAD에 커밋되지 않음")
    if step in ("actuals", "score"):
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify", f"refs/tags/{SEAL_TAG}"], capture_output=True)
        if r.returncode != 0:
            raise PermissionError(f"Stage 4 {step} 거부: 예측 봉인 태그 {SEAL_TAG} 없음")
    return Stage4Unlock(step, g)


def manifest(u: Stage4Unlock, folder: Path) -> pd.DataFrame:
    assert isinstance(u, Stage4Unlock) and u.step == "manifest"
    rows = [dict(path=p.relative_to(folder).as_posix(), size=p.stat().st_size, sha256=G.sha256_file(p),
                 mtime=int(p.stat().st_mtime)) for p in sorted(folder.rglob("*")) if p.is_file()]
    return pd.DataFrame(rows)


def schema_check(u: Stage4Unlock, con, files: list[str], expected_cols: list[str]) -> pd.DataFrame:
    """사용통계 형식 점검(PLAN §8 순서 3): 파일마다 헤더와 코드 칸(STD_YYYYMM·OUT_IN_HOS)의 값 목록만.
    청구 값(수량·금액·건수)은 읽지도 합하지도 않는다. 새 칸·코드 값이면 new_* 표시(확인 필요로 멈춤)"""
    assert isinstance(u, Stage4Unlock) and u.step == "manifest"
    rows = []
    for f in files:
        cols = [c[0] for c in con.execute(f"describe select * from read_csv('{f}', header = true, all_varchar = true)").fetchall()]
        codes = con.execute(f"""select string_agg(distinct STD_YYYYMM, ','), string_agg(distinct OUT_IN_HOS, ',')
                                from read_csv('{f}', header = true, all_varchar = true)""").fetchone()
        rows.append(dict(file=Path(f).name, columns=",".join(cols), new_columns=",".join(sorted(set(cols) - set(expected_cols))),
                         missing_columns=",".join(sorted(set(expected_cols) - set(cols))), std_yyyymm=codes[0],
                         out_in_hos=codes[1], new_out_in_hos=",".join(sorted(set((codes[1] or "").split(",")) - {"IN", "OUT"}))))
    return pd.DataFrame(rows)


def forecast(u: Stage4Unlock, panel: pd.DataFrame, target: pd.DataFrame, seed: int, h: int = 6) -> pd.DataFrame:
    """panel은 2025-12 이하(data/raw 판)만. 대상월 2026-01 ~ 06 예측"""
    assert isinstance(u, Stage4Unlock) and u.step == "forecast"
    assert panel.month.max() <= "2025-12", "Stage 4 예측 입력은 2025-12 이하 사용통계만"
    from pbd.forecast_v2.backtest import Backtest, long_table, matrices, run_q1
    p = panel.copy()
    Y, A, C, months, _ = matrices(p, target, "2018-01", TARGET_MONTHS[-1])
    bt = Backtest(Y, A, months, target, seed, C)
    pred = run_q1(bt, TARGET_MONTHS, h, models=("B1", "B2", "B3", "G"))
    return long_table(bt, TARGET_MONTHS, h, pred, ("B1", "B2", "B3", "G"))


def load_actuals(u: Stage4Unlock, con, files: list[str]) -> pd.DataFrame:
    """2026 사용통계 원본 CSV → (c4, month, qty, observed). 정답으로만 쓴다"""
    assert isinstance(u, Stage4Unlock) and u.step == "actuals"
    from pbd.common import sql_files
    d = con.execute(f"""select lpad(WK_COMPN_4, 4, '0') c4, substr(STD_YYYYMM, 1, 4) || '-' || substr(STD_YYYYMM, 5, 2) month,
                               sum(cast(PRSCRPTN_TOT_USE as double)) qty
                        from read_csv({sql_files(files)}, header = true, all_varchar = true)
                        where OUT_IN_HOS = 'OUT' group by 1, 2""").df()
    assert d.month.isin(TARGET_MONTHS).all(), "2026-01 ~ 06 밖의 달이 있다"
    return d.assign(observed=True)


def score(forecasts: pd.DataFrame, actuals: pd.DataFrame, target: pd.DataFrame, backtest: dict,
          months: list[str] = TARGET_MONTHS) -> pd.DataFrame:
    """forecasts: long_table 형식(c4, t, model, yhat). actuals: (c4, month, qty). backtest: {'B1': {'verdict', 'diff'}, …}.
    p값을 내지 않는다. 미관측·0 정답과 모델 하나라도 예측이 없는 칸은 모든 모델에서 뺀다"""
    w = target.w.to_numpy(float)
    act = actuals.pivot(index="c4", columns="month", values="qty").reindex(index=target.c4, columns=months).to_numpy(float)
    act = np.where(act > 0, act, np.nan)
    P = {m: g.pivot(index="c4", columns="t", values="yhat").reindex(index=target.c4, columns=months).to_numpy(float)
         for m, g in forecasts.groupby("model")}
    mask = np.isfinite(act) & np.all([np.isfinite(P[m]) for m in ("B1", "B2", "B3", "G")], axis=0)
    rows = []
    for b, bv in backtest.items():
        dG, dB = decompose(act, P["G"], w, mask), decompose(act, P[b], w, mask)
        D = dG - dB
        total = float(D.sum())
        v = bv["verdict"]
        direction = -1 if v.startswith("나음") else (1 if v.startswith("나쁨") else None)
        ref = direction if direction is not None else int(np.sign(bv["diff"]))
        same = int((np.sign(D) == ref).sum())
        if direction is not None:
            verdict = NOT_OPPOSITE if (np.sign(total) == direction and same >= 4) else OPPOSITE
        else:
            verdict = f"(백테스트 '{v}') 점추정 부호 일치: {'예' if np.sign(total) == ref else '아니오'}"
        rows.append(dict(comparison=f"G 대 {b}", backtest_verdict=v, sum_dwape=total, months_same_sign=same,
                         verdict=verdict, **{f"d_{m}": float(x) for m, x in zip(months, D)}, cells=int(mask.sum())))
    return pd.DataFrame(rows)
