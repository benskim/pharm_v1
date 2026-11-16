"""A6 수작업 검증의 정밀도 (docs/PLAN_P1.md §5 A6). 사용자가 라벨을 채운 뒤 실행한다.

입력: outputs/validation/sample_60.user_reviewed.csv (최종 라벨, 저장소에 있음)
  label_type_correct  : Y / N / 모름 (기준은 outputs/validation/LABELING.md)
  label_date_correct  : Y / N / 모름, 날짜가 없는 행은 비움
  label_note          : 판정 근거
  labeler             : claude (1차 라벨) / user_reviewed (사용자가 검토한 행)
  claude_label_type_correct, claude_label_date_correct : 1차(claude) 라벨. 사용자가 검토하며 label_*를 고쳐도 남긴다
  label_source        : raw (원본만으로 판정) / external (원본으로 판단할 수 없는 부분을 공식 고시·공지·보도로 판정)
출력: outputs/validation/precision.csv (전체, record_type별, 층별, date_source별)
      × scope(all = external_included / user_reviewed / raw_only),
      outputs/validation/label_review_diff.csv (1차 라벨과 검토 라벨이 다른 행), 화면 출력
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


LABELERS = {"claude", "user_reviewed"}
SOURCES = {"raw", "external"}
LABELS = ("label_type_correct", "label_date_correct")


def _norm(v):
    v = (v or "").strip()
    return v.upper() if v.upper() in YES | NO else v


def review_diff(rows):
    """user_reviewed 행에서 1차(claude_label_*)와 검토 라벨(label_*)이 다른 행. 1차 라벨이 없는 행은 비교하지 않는다.
    → (비교한 행 수, 칸별 다른 수 dict, 다른 행 목록)"""
    rev = [r for r in rows if (r.get("labeler") or "").strip() == "user_reviewed"
           and any((r.get(f"claude_{lab}") or "").strip() for lab in LABELS)]
    n_diff = {lab: 0 for lab in LABELS}
    out = []
    for r in rev:
        d = [lab for lab in LABELS if _norm(r.get(f"claude_{lab}")) != _norm(r.get(lab))]
        for lab in d:
            n_diff[lab] += 1
        if d:
            out.append({"sample_id": r["sample_id"], "record_id": r.get("record_id", ""), "stratum": r["stratum"],
                        "changed": "+".join(d),
                        **{f"claude_{lab}": r.get(f"claude_{lab}", "") for lab in LABELS},
                        **{lab: r.get(lab, "") for lab in LABELS}, "label_note": r.get("label_note", "")})
    return len(rev), n_diff, out


def main(path=None):
    path = Path(path) if path else ROOT / "outputs/validation/sample_60.user_reviewed.csv"
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
    has_labeler = any("labeler" in r for r in rows)
    if has_labeler:
        badl = [r["sample_id"] for r in rows if (r.get("labeler") or "").strip() not in LABELERS]
        if badl:
            print(f"labeler가 claude / user_reviewed가 아닌 행: sample_id {', '.join(badl[:20])}")
            return 1
    has_source = any("label_source" in r for r in rows)
    if has_source:
        bads = [r["sample_id"] for r in rows if (r.get("label_source") or "").strip() not in SOURCES]
        if bads:
            print(f"label_source가 raw / external이 아닌 행: sample_id {', '.join(bads[:20])}")
            return 1
    scopes = [("all", rows)]
    if has_labeler:
        scopes.append(("user_reviewed", [r for r in rows if r["labeler"].strip() == "user_reviewed"]))
    if has_source:
        # 원본만: 외부 근거로 판정한 행을 뺀다. 외부 포함 = all
        scopes.append(("raw_only", [r for r in rows if r["label_source"].strip() == "raw"]))
        scopes.append(("external_included", rows))
    res = [(sc, *x) for sc, rs in scopes for x in summarize(rs)]
    out = path.parent / "precision.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scope", "group", "key", "label", "n", "correct", "unknown", "precision", "wilson_lo", "wilson_hi",
                    "precision_weighted", "weighted_lo", "weighted_hi", "n_eff"])
        w.writerows(res)
    for r in res:
        if r[1] in ("all", "record_type"):
            print(r)
    if has_labeler:
        n_rev, n_diff, diff = review_diff(rows)
        n_user = sum(1 for r in rows if r["labeler"].strip() == "user_reviewed")
        print(f"labeler: claude {len(rows) - n_user}, user_reviewed {n_user} (그중 1차 라벨과 비교한 행 {n_rev})")
        if has_source:
            n_ext = sum(1 for r in rows if r["label_source"].strip() == "external")
            print(f"label_source: raw {len(rows) - n_ext}, external {n_ext}")
        print(f"1차 라벨과 검토 라벨이 다른 행 {len(diff)} (type {n_diff['label_type_correct']}, date {n_diff['label_date_correct']})")
        dpath = path.parent / "label_review_diff.csv"
        cols = ["sample_id", "record_id", "stratum", "changed", *[f"claude_{l}" for l in LABELS], *LABELS, "label_note"]
        with dpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(diff)
        print(f"→ {dpath}")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
