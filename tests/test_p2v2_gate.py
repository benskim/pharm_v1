"""사전등록 잠금·실행 진입점·Stage 4 시험. 임시 git 저장소와 손 계산 (실제 평가 기간 자료를 읽지 않는다)."""
from __future__ import annotations

import subprocess

import numpy as np
import pandas as pd
import pytest

from pbd.forecast_v2 import gate as G
from pbd.forecast_v2 import stage4 as S4


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    for p, txt in {"config/p2v2.yaml": "a: 1\n", "src/pbd/forecast_v2/m.py": "x = 1\n", "scripts/p2v2_v.py": "y\n",
                   "docs/free.md": "free\n", "src/pbd/forecast_v2/sub/n.py": "z\n"}.items():
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(txt)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "c")
    return tmp_path


def test_no_tag_refuses(repo):
    g = G.check(G.TAG, repo)
    assert not g.ok and not g.deviation_only
    with pytest.raises(PermissionError):
        G.unlock(False, G.TAG, repo)
    with pytest.raises(PermissionError):
        G.unlock(True, G.TAG, repo)                    # 태그가 없으면 이탈 판도 거부


def test_tag_match_ok_and_change_detected(repo):
    _git(repo, "tag", G.TAG)
    assert G.check(G.TAG, repo).ok
    (repo / "docs/free.md").write_text("changed\n")    # 잠그지 않는 파일
    (repo / "src/pbd/forecast_v2/sub/n.py").write_text("changed\n")   # 하위 폴더는 잠금 대상 아님
    assert G.check(G.TAG, repo).ok
    (repo / "src/pbd/forecast_v2/m.py").write_text("x = 2\n")
    g = G.check(G.TAG, repo)
    assert not g.ok and g.deviation_only
    with pytest.raises(PermissionError):
        G.unlock(False, G.TAG, repo)
    assert G.unlock(True, G.TAG, repo).deviation


def test_tag_read_even_if_branch_has_same_name(repo):
    """브랜치 이름이 태그와 같고(p2v2-prereg) 브랜치가 앞서 나가도 잠금은 태그 시점 파일과 대조한다"""
    _git(repo, "tag", G.TAG)
    _git(repo, "checkout", "-qb", G.TAG)
    (repo / "src/pbd/forecast_v2/m.py").write_text("x = 3\n")
    _git(repo, "commit", "-qam", "branch moves")
    g = G.check(G.TAG, repo)
    assert g.deviation_only and g.tag_hash != g.work_hash


def test_new_locked_file_detected(repo):
    _git(repo, "tag", G.TAG)
    (repo / "scripts/p2v2_new.py").write_text("n\n")
    assert G.check(G.TAG, repo).deviation_only


def test_verification_scripts_not_locked(repo):
    """검증 스크립트(dryrun.py, p2v2_ewc_check.py)는 바뀌어도 잠금에 걸리지 않는다(사용자 결정 2026-11-20)"""
    for p in G.VERIFY_ONLY:
        (repo / p).write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v")
    _git(repo, "tag", G.TAG)
    for p in G.VERIFY_ONLY:
        (repo / p).write_text("v2\n")
    assert G.check(G.TAG, repo).ok
    assert all(h is not None for h in G.verification_hashes(repo).values())
    assert not set(G.VERIFY_ONLY) & set(G.working_files(repo))


def test_content_hash_order_independent():
    assert G.content_hash({"b": b"2", "a": b"1"}) == G.content_hash({"a": b"1", "b": b"2"})
    assert G.content_hash({"a": b"1"}) != G.content_hash({"a": b"2"})


def test_real_repo_refuses_backtest_without_tag(monkeypatch):
    """이 저장소에는 아직 태그가 없다 → 진입점이 거부하고, 평가 기간 패널을 읽는 함수가 불리지 않는다"""
    from pbd.forecast_v2 import real, timeaxis
    if G.tag_hash(G.TAG) is not None:
        pytest.skip("태그가 이미 있음")
    called = []
    monkeypatch.setattr(timeaxis, "panel_full", lambda *a, **k: called.append(1))
    monkeypatch.setattr(real, "panel_full", lambda *a, **k: called.append(1))
    assert real.main([]) == 2 and real.main(["--deviation"]) == 2
    assert not called


def test_panel_full_requires_unlock():
    from pbd.forecast_v2.timeaxis import panel_full
    with pytest.raises(AssertionError):
        panel_full(None, object())


def test_stage4_guard_refuses_without_tag():
    if G.tag_hash(G.TAG) is not None:
        pytest.skip("태그가 이미 있음")
    for step in ("manifest", "forecast", "actuals", "score"):
        with pytest.raises(PermissionError):
            S4.guard(step)


def test_stage4_score_hand():
    """성분 2(w = 0.5씩), 6개월 정답 10. G = 10(정확). B1 = 12 → d_t^{B1} = Σ_i 0.5·2/60 = 1/30, D_t = −1/30, 합 −0.2.
    B2: 1 ~ 3월 G가 나쁘게(B2 정확, G 정확 아님이 되도록 B2 = 10, G는 B2 비교용으로 1 ~ 3월에 차이) — 아래 G2로 따로.
    B3: 백테스트 '판단 보류', diff +0.01 → 점추정 일치만"""
    tg = pd.DataFrame({"c4": ["X", "Z"], "w": [0.5, 0.5]})
    months = S4.TARGET_MONTHS
    act = pd.DataFrame([(c, m, 10.0) for c in ("X", "Z") for m in months], columns=["c4", "month", "qty"])

    def fc(gvals, bvals):
        rows = []
        for c in ("X", "Z"):
            for k, m in enumerate(months):
                for mod, v in (("G", gvals[k]), ("B1", 12.0), ("B2", bvals[k]), ("B3", 11.0)):
                    rows.append((c, m, mod, v))
        return pd.DataFrame(rows, columns=["c4", "t", "model", "yhat"])

    f = fc([10.0] * 6, [10.0] * 6)
    s = S4.score(f, act, tg, {"B1": dict(verdict="나음", diff=-0.01)}).set_index("comparison")
    assert s.loc["G 대 B1", "sum_dwape"] == pytest.approx(-0.2)
    assert s.loc["G 대 B1", "months_same_sign"] == 6 and s.loc["G 대 B1", "verdict"] == S4.NOT_OPPOSITE
    # G 대 B2: G가 1 ~ 3월 13(오차 3), 4 ~ 6월 10. B2 = 1 ~ 3월 10, 4 ~ 6월 11(오차 1)
    # D_t = (3 − 0)/60 ·(0.5·2) = 0.05 (1 ~ 3월), (0 − 1)/60 = −1/60 (4 ~ 6월). 합 = 0.15 − 0.05 = 0.1 > 0
    f2 = fc([13.0] * 3 + [10.0] * 3, [10.0] * 3 + [11.0] * 3)
    s2 = S4.score(f2, act, tg, {"B2": dict(verdict="나음", diff=-0.01), "B3": dict(verdict="판단 보류", diff=0.01)}).set_index("comparison")
    assert s2.loc["G 대 B2", "sum_dwape"] == pytest.approx(0.1)
    assert s2.loc["G 대 B2", "months_same_sign"] == 3 and s2.loc["G 대 B2", "verdict"] == S4.OPPOSITE
    assert "점추정" in s2.loc["G 대 B3", "verdict"]
    assert "p" not in [c for c in s2.columns]                    # p값을 내지 않는다


def test_stage4_schema_check_reads_codes_only(tmp_path):
    """합성 CSV 2개: 헤더·코드 칸만 본다. 새 칸과 새 원내외 코드를 표시한다(청구 값은 결과에 없음)"""
    from pbd.common import connect
    head = "STD_YYYYMM,WK_COMPN_4,OUT_IN_HOS,PRSCRPTN_TOT_USE\n"
    (tmp_path / "a.csv").write_text(head + "202601,0001,OUT,5\n202601,0001,IN,7\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("STD_YYYYMM,WK_COMPN_4,OUT_IN_HOS,PRSCRPTN_TOT_USE,NEW_COL\n202602,0001,ETC,5,x\n", encoding="utf-8")
    u = S4.Stage4Unlock("manifest", None)
    r = S4.schema_check(u, connect(None), [str(tmp_path / "a.csv"), str(tmp_path / "b.csv")],
                        ["STD_YYYYMM", "WK_COMPN_4", "OUT_IN_HOS", "PRSCRPTN_TOT_USE"]).set_index("file")
    assert r.loc["a.csv", "new_columns"] == "" and r.loc["a.csv", "new_out_in_hos"] == ""
    assert r.loc["b.csv", "new_columns"] == "NEW_COL" and r.loc["b.csv", "new_out_in_hos"] == "ETC"
    assert not any("5" == str(v) for v in r.loc["a.csv"].values)
