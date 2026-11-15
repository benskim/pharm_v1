"""A6 수작업 검증의 정밀도 (docs/PLAN_P1.md §5 A6). 사용자가 라벨을 채운 뒤 실행한다.

입력: outputs/validation/sample_60.csv
  label_type_correct  : Y / N / 모름 (기준은 outputs/validation/LABELING.md)
  label_date_correct  : Y / N / 모름, 날짜가 없는 행은 비움
  label_note          : 메모 (선택)
출력: outputs/validation/precision.csv (전체, record_type별, 층별, date_source별), 화면 출력
- 비가중 정밀도: Y / (Y + N). 모름과 빈칸은 분모에서 빼고 따로 센다.
- 가중 정밀도: 층 크기(stratum_size)로 가중. 층 h의 라벨 행마다 가중치 N_h / n_h (n_h = 그 층의 Y·N 행 수).
  구간은 유효 표본 크기 n_eff = (Σw)² / Σw² (Kish)로 Wilson 95%.
라벨 label_type_correct가 하나라도 비어 있으면 계산하지 않고 비어 있는 sample_id를 알려 준다.
사용: python -I scripts/s4_precision.py [csv 경로]
"""
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return None, None
    return wilson_p(k / n, n, z)


def wilson_p(p, n, z=1.959963984540054):
    """비율 p, (유효) 표본 크기 n의 Wilson 95% 구간"""
    if not n:
        return None, None
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


YES, NO, UNKNOWN = {"Y"}, {"N"}, {"모름", "?"}


def yn(v):
    v = (v or "").strip().upper()
    return True if v in YES else False if v in NO else None


def _r(x):
    return None if x is None else round(x, 4)


def summarize(rows):
    """rows: dict 목록 → [(group, key, label, n, correct, unknown, precision, lo, hi, precision_w, lo_w, hi_w, n_eff)]"""
    groups = defaultdict(list)
    for r in rows:
        ds = r["stratum"].split("|")[-1] if r["record_type"] == "event" else "-"
        for g, k in (("all", "all"), ("record_type", r["record_type"]), ("stratum", r["stratum"]), ("date_source", ds)):
            groups[(g, k)].append(r)
    out = []
    for (g, k), rs in sorted(groups.items()):
        for label in ("label_type_correct", "label_date_correct"):
            lab = [(r, yn(r.get(label))) for r in rs]
            vals = [v for _, v in lab if v is not None]
            unknown = sum(1 for r, v in lab if v is None and (r.get(label) or "").strip().upper() in UNKNOWN)
            n, c = len(vals), sum(vals)
            lo, hi = wilson(c, n)
            # 층 크기 가중
            pw = lw = hw = neff = None
            if n and all((r.get("stratum_size") or "").strip() for r, v in lab if v is not None):
                by = defaultdict(list)
                for r, v in lab:
                    if v is not None:
                        by[r["stratum"]].append((int(r["stratum_size"]), v))
                ws = [(size / len(vs), v) for vs in by.values() for size, v in vs]
                sw = sum(w for w, _ in ws)
                pw = sum(w for w, v in ws if v) / sw
                neff = sw * sw / sum(w * w for w, _ in ws)
                lw, hw = wilson_p(pw, neff)
            out.append((g, k, label, n, c, unknown, None if n == 0 else _r(c / n), _r(lo), _r(hi),
                        _r(pw), _r(lw), _r(hw), _r(neff)))
    return out


def main(path=None):
    path = Path(path) if path else ROOT / "outputs/validation/sample_60.csv"
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    missing = [r["sample_id"] for r in rows if (r.get("label_type_correct") or "").strip() == ""]
    if missing:
        print(f"라벨이 비어 있는 행 {len(missing)}개: sample_id {', '.join(missing[:20])}{' …' if len(missing) > 20 else ''}")
        return 1
    bad = [r["sample_id"] for r in rows for lab in ("label_type_correct", "label_date_correct")
           if (r.get(lab) or "").strip().upper() not in {"", *YES, *NO, *UNKNOWN}]
    if bad:
        print(f"Y / N / 모름이 아닌 라벨: sample_id {', '.join(sorted(set(bad)))}")
        return 1
    res = summarize(rows)
    out = path.parent / "precision.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "key", "label", "n", "correct", "unknown", "precision", "wilson_lo", "wilson_hi",
                    "precision_weighted", "weighted_lo", "weighted_hi", "n_eff"])
        w.writerows(res)
    for r in res:
        if r[0] in ("all", "record_type", "date_source"):
            print(r)
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
