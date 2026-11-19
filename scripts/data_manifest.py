"""원본 파일 목록 (2026-11-19 사용자 지시: 데이터는 git에 없으므로 파일별 출처·받은 날짜·파일명·크기·SHA-256을 docs/DATA.md에 표로).

- 기본: data/raw/와 data/sample/의 모든 파일을 읽어 docs/DATA.md의 표시 사이 블록을 다시 쓴다.
- --check: 지금 폴더의 파일을 표와 대조한다(재현할 때). 다르거나 없는 파일을 출력하고 종료 코드 1.
출처 URL은 저장소에 기록된 것만 쓴다(크롤링·외부 조회 없음). 기록이 없으면 "확인 필요".
받은 날짜: 문서에 기록된 날짜가 있으면 그것, 없으면 파일 수정 시각(받은 날로 확인 필요)을 적는다.
사용: python -I scripts/data_manifest.py [--check] [--root <재현 폴더>]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ST, EN = "<!-- 원본 파일 목록 시작 (scripts/data_manifest.py 생성) -->", "<!-- 원본 파일 목록 끝 -->"
DIRS = ("data/raw", "data/sample")
# 폴더별 출처 (저장소 기록 기준). URL이 기록되지 않은 것은 '확인 필요'
SOURCE = {
    "data/raw/usage": ("국민건강보험공단 의약품 사용통계 (공공데이터포털)", "https://www.data.go.kr/biz/dcat/metadata/15128709.do",
                       "2026-11-01 (docs/DATA.md §2, 2026-10-28 공개 판 등)"),
    "data/raw/benefit": ("건강보험심사평가원 약제급여목록 월별 파일 (사용자가 병합한 parquet + 대조용 원본)", "확인 필요", None),
    "data/raw/substitute": ("건강보험심사평가원 저가약 대체조제 장려금 대상 약제목록 월별 파일 (사용자가 병합한 parquet)", "확인 필요", None),
    "data/raw/master": ("2026 약가정보 2종 (심평원 코드 대응표, 의약품안전나라 품목 정보)", "확인 필요", None),
    "data/raw/orangebook": ("식품의약품안전처 의약품 동등성 인정 품목(오렌지북) 2026", "확인 필요", None),
    "data/raw/price_2026": ("(비어 있음)", "", None),
    "data/sample": ("설계에 쓴 표본 parquet (사용자 제공, 회귀 시험용)", "사용자 제공", None),
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def files(root: Path) -> list[Path]:
    out = []
    for d in DIRS:
        out += sorted(p for p in (root / d).rglob("*") if p.is_file() and p.name != ".DS_Store")
    return out


def folder_of(rel: str) -> str:
    parts = rel.split("/")
    return "/".join(parts[:3]) if parts[1] == "raw" else "data/sample"


def build(root: Path) -> list[str]:
    rows = []
    for p in files(root):
        rel = p.relative_to(root).as_posix()
        rows.append((rel, p.stat().st_size, sha256(p), dt.date.fromtimestamp(p.stat().st_mtime).isoformat()))
    summ = {}
    for rel, size, _, mt in rows:
        f = folder_of(rel)
        n, s, lo, hi = summ.get(f, (0, 0, mt, mt))
        summ[f] = (n + 1, s + size, min(lo, mt), max(hi, mt))
    L = [ST, "", "## 원본 파일 목록 (재현 대조용)", "",
         "데이터는 저장소에 없다. 아래 표의 이름·크기·SHA-256으로 받은 파일을 대조한다(`python -I scripts/data_manifest.py --check`). "
         "출처 URL은 저장소에 기록된 것만 적었고, 기록이 없는 것은 '확인 필요'다. 받은 날짜는 기록된 날짜, 없으면 파일 수정 시각이다(받은 날로 확인 필요).", "",
         "| 폴더 | 출처 | 공식 URL | 받은 날짜 | 파일 수 | 크기 합 (바이트) |", "|---|---|---|---|---|---|"]
    for f, (n, s, lo, hi) in sorted(summ.items()):
        name, url, date = SOURCE.get(f, ("확인 필요", "확인 필요", None))
        L.append(f"| `{f}` | {name} | {url} | {date or f'파일 수정 시각 {lo} ~ {hi} (확인 필요)'} | {n} | {s:,} |")
    L += ["", "| 파일 | 크기 (바이트) | SHA-256 |", "|---|---|---|"]
    L += [f"| `{rel}` | {size:,} | `{h}` |" for rel, size, h, _ in rows]
    L += ["", EN]
    return L


def check(root: Path) -> int:
    text = (ROOT / "docs/DATA.md").read_text(encoding="utf-8")
    want = {m.group(1): (int(m.group(2).replace(",", "")), m.group(3)) for m in
            re.finditer(r"^\| `([^`]+)` \| ([\d,]+) \| `([0-9a-f]{64})` \|$", text, flags=re.M)}
    have = {p.relative_to(root).as_posix(): p for p in files(root)}
    bad = []
    for rel, (size, h) in want.items():
        p = have.get(rel)
        if p is None:
            bad.append(f"없음: {rel}")
        elif p.stat().st_size != size or sha256(p) != h:
            bad.append(f"다름: {rel}")
    extra = sorted(set(have) - set(want))
    print(f"표 {len(want)}개, 폴더 {len(have)}개, 다르거나 없는 파일 {len(bad)}개, 표에 없는 파일 {len(extra)}개")
    for b in bad[:20]:
        print(" ", b)
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    root = Path(a.root)
    if a.check:
        sys.exit(check(root))
    blk = "\n".join(build(root))
    p = ROOT / "docs/DATA.md"
    t = p.read_text(encoding="utf-8")
    if ST in t:
        t = t[: t.index(ST)] + blk + t[t.index(EN) + len(EN):]
    else:
        t = t.rstrip("\n") + "\n\n" + blk + "\n"
    p.write_text(t, encoding="utf-8")
    print(f"DATA.md 원본 파일 목록 갱신 ({len(files(root))}개)")


if __name__ == "__main__":
    main()
