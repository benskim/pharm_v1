"""사전등록 잠금 (PLAN_P2_v2 §7-3 ~ §7-5).

- 잠그는 파일 = config/p2v2.yaml, src/pbd/forecast_v2/*.py, scripts/p2v2_*.py. 단 검증 스크립트 dryrun.py·p2v2_ewc_check.py는 뺀다.
- 코드 해시 = 잠그는 파일을 경로 순으로 정렬해 "경로\\0내용"을 이어 붙인 SHA-256(작업 트리 기준, 커밋 여부와 무관).
- check(tag): 태그가 없으면 거부. 태그가 있으면 태그 시점 잠금 파일의 해시와 작업 트리 해시가 같아야 '주 결과' 실행 가능,
  다르면 '사전등록 이탈' 판으로만 실행 가능(deviation). 태그가 없으면 어떤 판도 실행하지 않는다
  (평가 기간 2023-01 ~ 사용통계는 태그 전에 읽지 않는다).
- Unlock: check가 통과했을 때만 만들어지는 표. timeaxis.panel_full이 이 표를 요구한다.
- run_log: outputs/p2v2/run_log.csv에 시각·커밋·해시·명령·입력 SHA-256을 한 줄씩 더한다.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pbd.common import ROOT

TAG = "p2v2-prereg-r1"     # 사용자 지정 태그 이름 (2026-11-20). 브랜치 이름은 p2v2-prereg
LOCKED = ("config/p2v2.yaml", "src/pbd/forecast_v2/*.py", "scripts/p2v2_*.py")
# 검증 스크립트: 실제 분석 결과에 영향이 없어 잠금에서 뺀다(사용자 결정 2026-11-20). 해시는 verification_hashes()로 따로 기록
VERIFY_ONLY = ("src/pbd/forecast_v2/dryrun.py", "scripts/p2v2_ewc_check.py")


def _match(p: str) -> bool:
    if p in VERIFY_ONLY:
        return False
    return any(fnmatch.fnmatch(p, g) and "/" not in p[len(g.split("*")[0]):] if "*" in g else p == g for g in LOCKED)


def working_files(root: Path = ROOT) -> list[str]:
    out = []
    for g in LOCKED:
        out += [p.relative_to(root).as_posix() for p in root.glob(g) if p.is_file()]
    return sorted(set(out) - set(VERIFY_ONLY))


def verification_hashes(root: Path = ROOT) -> dict[str, str | None]:
    """잠금에서 뺀 검증 스크립트의 파일별 SHA-256(없으면 None)"""
    return {p: (hashlib.sha256((root / p).read_bytes()).hexdigest() if (root / p).exists() else None) for p in VERIFY_ONLY}


def content_hash(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for p in sorted(files):
        h.update(p.encode() + b"\0" + files[p] + b"\0")
    return h.hexdigest()


def working_hash(root: Path = ROOT) -> str:
    return content_hash({p: (root / p).read_bytes() for p in working_files(root)})


def _git(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def tag_hash(tag: str, root: Path = ROOT) -> str | None:
    r = _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}")
    if r.returncode != 0:
        return None
    ref = f"refs/tags/{tag}"           # 같은 이름의 브랜치가 있어도 태그만 읽는다
    names = _git(root, "ls-tree", "-r", "--name-only", ref).stdout.decode().split("\n")
    files = {n: _git(root, "show", f"{ref}:{n}").stdout for n in names if n and _match(n)}
    return content_hash(files)


@dataclass(frozen=True)
class Gate:
    ok: bool              # 주 결과 실행 가능
    deviation_only: bool  # 태그는 있으나 잠금 파일이 다름 → 이탈 판만
    reason: str
    work_hash: str
    tag_hash: str | None
    head: str


class Unlock:
    """check가 통과했을 때만 만든다"""
    def __init__(self, gate: Gate, deviation: bool):
        assert gate.ok or (deviation and gate.deviation_only), gate.reason
        self.gate, self.deviation = gate, deviation


def check(tag: str = TAG, root: Path = ROOT) -> Gate:
    head = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    wh = working_hash(root)
    th = tag_hash(tag, root)
    if th is None:
        return Gate(False, False, f"태그 {tag} 없음 — 사전등록 전에는 실제 백테스트를 실행하지 않는다", wh, None, head)
    if th != wh:
        return Gate(False, True, f"잠금 파일이 태그 {tag}와 다름 — '사전등록 이탈' 판으로만 실행 가능", wh, th, head)
    return Gate(True, False, "태그와 잠금 파일 일치", wh, th, head)


def unlock(deviation: bool = False, tag: str = TAG, root: Path = ROOT) -> Unlock:
    g = check(tag, root)
    if not (g.ok or (deviation and g.deviation_only)):
        raise PermissionError(g.reason)
    return Unlock(g, deviation)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run_log(command: str, u: Unlock, inputs: dict[str, str], out: Path = ROOT / "outputs/p2v2/run_log.csv") -> None:
    import csv
    new = not out.exists()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "head", "code_hash", "tag_hash", "kind", "command", "inputs"])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"), u.gate.head, u.gate.work_hash, u.gate.tag_hash,
                    "사전등록 이탈" if u.deviation else "주 결과", command,
                    ";".join(f"{k}={v}" for k, v in sorted(inputs.items()))])
