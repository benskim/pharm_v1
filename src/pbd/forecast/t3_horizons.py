"""P2 부록: 예측 대상 여섯 달 M−4 ~ M+1 (lag 5, PLAN_P2 §1·§5. 사용자 결정 2026-11-18: 주 결과는 M+1, 여섯 달은 부록).

origin M, 마지막 관측월 a = M − 5. 대상 t = M + d (d = −4 … +1), 마지막 관측월에서 h = d + 5개월 앞.
구현: rolling.gbm·m0는 '대상 = origin + 1'을 가정하므로 origin′ = M + d − 1, lag′ = 4 + d로 부른다
(available(panel, origin′, lag′) = month ≤ M − 5로 같은 자료, 대상 = M + d).
M3의 신호 특징은 이 origin′ 기준(기준 스냅샷 s = M + d − 1 ≤ M)으로 만든다. d < 1이면 origin M에 실제로 볼 수 있던 것보다
이른 스냅샷만 쓴다 (보수적, 누수 아님).
사용: python -m pbd.forecast.t3_horizons  (출력 outputs/p2/t3_horizons.csv)
"""
from __future__ import annotations

import pandas as pd

from pbd.common import DB_PATH, ROOT, connect
from pbd.forecast import rolling as R
from pbd.forecast import signals as S
from pbd.forecast.t2_baseline import ORIGINS, load

OUT = ROOT / "outputs/p2"
LAG = 5
OFFSETS = (-4, -3, -2, -1, 0, 1)


def main() -> None:
    panel, series, cls = load()
    scale = R.scale12(panel, series)
    tb = S.load_tables(connect(DB_PATH, read_only=True))
    rows, preds = [], []
    for d in OFFSETS:
        o2 = [R.madd(m, d - 1) for m in ORIGINS]
        lag2 = LAG - 1 + d
        f = S.feature_table(tb, lag2, "same", R.months(S.FIRST_SNAPSHOT, o2[-1]), series)
        for name, fn, kw in (("M0", R.m0, {}), ("M2", R.gbm, {}), ("M3", R.gbm, dict(feat=f))):
            p = R.rolling(panel, fn, o2, lag2, series, cls=cls, **kw)
            p = p.assign(origin=[R.madd(o, 1 - d) for o in p["origin"]], model=name, d=d, h=d + LAG)
            preds.append(p)
            print(f"d {d} {name}", flush=True)
    sc = R.score(pd.concat(preds, ignore_index=True), panel, scale)
    for (d, m), g in sc.groupby(["d", "model"]):
        rows.append(dict(d=d, target=f"M{d:+d}" if d else "M", h=d + LAG, model=m, **R.summary(g)))
    t = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "t3_horizons.csv", index=False)
    print(t.pivot_table(index=["d", "target", "h"], columns="model", values="mase").round(3).to_string())


if __name__ == "__main__":
    main()
