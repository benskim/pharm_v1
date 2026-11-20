"""P2 v2 Stage 4 실행 순서 (사전등록 r1 §9, Q1만, p값 없음). 판정 논리는 잠금 파일 src/pbd/forecast_v2/stage4.py에 있다.
이 스크립트는 순서만 부른다(잠금 대상 아님).

  python -I scripts/stage4_p2v2.py manifest   : 1·2단계 파일 기록 + 형식 점검 → outputs/p2v2/stage4/manifest_2026.{csv,json}, schema_check.csv
  python -I scripts/stage4_p2v2.py forecast   : 3단계 예측(입력 사용통계 ≤ 2025-12) → forecasts.csv, forecasts_seal.json(SHA-256)
     (그 뒤 봉인: seal json 커밋 + 태그 p2v2-stage4-forecasts)
  python -I scripts/stage4_p2v2.py score      : 4·5단계 정답 적재(2026-01 ~ 06 파일만) + 잠정 채점 → actuals.csv, score.csv, score.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from pbd.common import DB_PATH, connect, load_yaml, usage_files  # noqa: E402
from pbd.forecast_v2 import gate as G  # noqa: E402
from pbd.forecast_v2 import stage4 as S4  # noqa: E402
from pbd.forecast_v2.timeaxis import panel_full  # noqa: E402

_C = sorted((ROOT / "data/raw/usage").glob("cmpn_stat_2026*"))
assert len(_C) == 1, f"2026 사용통계 폴더가 하나가 아니다: {_C}"
FOLDER = _C[0]
OUT = ROOT / "outputs/p2v2/stage4"


def file_month(name: str) -> str | None:
    m = re.match(r"MEDICINE_(\d{4})(\d{2})", name)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def manifest() -> int:
    u = S4.guard("manifest")
    OUT.mkdir(parents=True, exist_ok=True)
    mf = S4.manifest(u, FOLDER)
    mf["month"] = mf.path.map(lambda p: file_month(Path(p).name))
    mf["use"] = mf.month.isin(S4.TARGET_MONTHS)
    mf.to_csv(OUT / "manifest_2026.csv", index=False)
    early = mf[mf.month.notna() & (mf.month < "2026-01")]
    late = mf[mf.month.notna() & (mf.month > S4.TARGET_MONTHS[-1])]
    missing = sorted(set(S4.TARGET_MONTHS) - set(mf.month.dropna()))
    info = dict(folder=str(FOLDER.relative_to(ROOT)), files=len(mf), use=int(mf.use.sum()), months=sorted(mf.month.dropna().unique()),
                early_files=early.path.tolist(), late_files_not_read=late.path.tolist(), missing_target_months=missing,
                other_files=mf[mf.month.isna()].path.tolist())
    print(json.dumps(info, ensure_ascii=False, indent=1))
    if len(early):
        print("멈춤: 2025년 이하 파일이 2026 폴더에 있다 — 기본 로더(2018 ~ 2025)가 함께 읽어 같은 달을 두 번 더한다", file=sys.stderr)
        return 3
    if missing:
        print(f"멈춤: 대상월 파일 없음 {missing}", file=sys.stderr)
        return 3
    con = connect(None)
    ref = usage_files((2025, 2025))[0]
    expected = [c[0] for c in con.execute(f"describe select * from read_csv('{ref}', header = true, all_varchar = true)").fetchall()]
    files = [str(FOLDER / p) for p in mf[mf.use].path]
    sc = S4.schema_check(u, con, files, expected)
    sc.to_csv(OUT / "schema_check.csv", index=False)
    bad = sc[(sc.new_columns != "") | (sc.missing_columns != "") | (sc.new_out_in_hos != "")]
    info["schema_reference"] = Path(ref).name
    info["schema_problems"] = bad.file.tolist()
    info["std_yyyymm_by_file"] = dict(zip(sc.file, sc.std_yyyymm))
    (OUT / "manifest_2026.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(sc[["file", "new_columns", "missing_columns", "std_yyyymm", "out_in_hos"]].to_string(index=False))
    if len(bad):
        print("멈춤: 새 칸·코드 값 — 확인 필요", file=sys.stderr)
        return 3
    return 0


def forecast() -> int:
    u = S4.guard("forecast")
    con = connect(DB_PATH, read_only=True)
    panel = panel_full(con, G.unlock())
    t = pd.read_csv(ROOT / "outputs/p2v2/target.csv", dtype={"c4": str, "cls3": str})
    f = S4.forecast(u, panel, t, load_yaml("params.yaml")["seed"])
    OUT.mkdir(parents=True, exist_ok=True)
    f.to_csv(OUT / "forecasts.csv", index=False, float_format="%.10g")
    seal = dict(file="outputs/p2v2/stage4/forecasts.csv", sha256=G.sha256_file(OUT / "forecasts.csv"), rows=len(f),
                models=sorted(f.model.unique()), target_months=S4.TARGET_MONTHS, input_usage_max="2025-12",
                code_hash=u.gate.work_hash, prereg_tag=G.TAG)
    (OUT / "forecasts_seal.json").write_text(json.dumps(seal, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(seal, ensure_ascii=False, indent=1))
    return 0


def score() -> int:
    u = S4.guard("actuals")
    seal = json.loads((OUT / "forecasts_seal.json").read_text(encoding="utf-8"))
    assert G.sha256_file(OUT / "forecasts.csv") == seal["sha256"], "봉인 뒤 예측 파일이 바뀌었다"
    mf = pd.read_csv(OUT / "manifest_2026.csv")
    files = [str(FOLDER / p) for p in mf[mf.use].path]
    for p in files:                                            # 파일 기록 뒤 바뀌지 않았는지
        assert G.sha256_file(Path(p)) == mf.set_index("path").loc[str(Path(p).relative_to(FOLDER)), "sha256"]
    act = S4.load_actuals(u, connect(None), files)
    act.to_csv(OUT / "actuals.csv", index=False)
    S4.guard("score")
    t = pd.read_csv(ROOT / "outputs/p2v2/target.csv", dtype={"c4": str, "cls3": str})
    v = json.loads((ROOT / "outputs/p2v2/backtest/verdict.json").read_text(encoding="utf-8"))
    bt = {b: dict(verdict=v["q1_overall"]["individual"][b]["verdict"], diff=v["q1"][b]["diff"]) for b in ("B1", "B2", "B3")}
    f = pd.read_csv(OUT / "forecasts.csv", dtype={"c4": str})
    s = S4.score(f, act, t, bt)
    s.to_csv(OUT / "score.csv", index=False, float_format="%.6g")
    (OUT / "score.json").write_text(s.to_json(orient="records", force_ascii=False, indent=1), encoding="utf-8")
    print(s.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit({"manifest": manifest, "forecast": forecast, "score": score}[sys.argv[1]]())
