"""P2 v2 판정 문장 (PLAN_P2_v2 §7-6). verdict.json(run.py 출력)에서 사전등록 규칙대로 요약 문장과 표를 만든다.
판정 자체는 evaluate.py가 이미 냈다. 이 스크립트는 문장을 조립만 한다(새 계산 없음).
사용: python -I scripts/p2v2_verdict.py <결과 폴더>  → <결과 폴더>/verdict.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HEAD_LIMIT = ("먼저 읽을 한계: 2023–2025는 v1이 이미 본 기간이라 이 백테스트는 완전한 맹검이 아니다. 맹검 평가는 Stage 4뿐이다. "
              "모든 결론은 수정본 자료로 평가한 것이다. 결과는 사전등록한 G 설정(튜닝 없음)에 한정된다.")


def pct(x):
    return f"{x * 100:.2f}%"


def pp(x):
    return f"{x * 100:+.2f}%p"


def main(folder: str) -> None:
    d = Path(folder)
    r = json.loads((d / "verdict.json").read_text(encoding="utf-8"))
    q1, ov, wp = r["q1"], r["q1_overall"], r["q1_wape"]
    L = [f"# P2 v2 판정 ({d.name})", "", f"> {HEAD_LIMIT}", ""]
    ci = ", ".join(f"G − {b} {pp(q1[b]['diff'])} (90% 구간 {pp(q1[b]['lo90'])} ~ {pp(q1[b]['hi90'])})" for b in q1)
    L.append(f"**Q1 종합: {ov['text']}** — 금액 가중 WAPE B1 {pct(wp['B1'])}, B2 {pct(wp['B2'])}, B3 {pct(wp['B3'])}, "
             f"G {pct(wp['G'])}. {ci}. (6개월 앞, 공개 시차 5개월 가정, 대상월 2023-01 ~ 2025-12, 수정본 자료로 평가)")
    L += ["", "| 비교 | ΔWAPE | 주 검정 EWC p" + (" (Holm 3)" if ov["holm"] else "") + " | 판정 | 민감도 DM + HLN p (판정) | 민감도 Bartlett 12 p (판정) |",
          "|---|---|---|---|---|---|"]
    for b, c in q1.items():
        ind = ov["individual"][b]
        L.append(f"| G 대 {b} | {pp(c['diff'])} | {ind['p']:.3g} | {ind['verdict']} | {c['p_dm']:.3g} ({c['verdict_dm']}) | "
                 f"{c['p_b12']:.3g} ({c['verdict_b12']}) |" + (f" {c['note']}" if c.get("note") else ""))
    L.append("")
    L.append("주 검정과 민감도 검정의 결론이 다른 비교: " + (", ".join(
        f"G 대 {b}" for b, c in q1.items() if c["verdict_dm"] != c["verdict"].split("(")[0] or c["verdict_b12"] != c["verdict"].split("(")[0])
        or "없음") + ". 공식 판정은 주 검정(EWC)이다.")
    q = r["q2_main"]
    L += ["", f"**Q2 주(단가 예측, 목록 가격 경로 U1 대 사용통계 단가 추세 U0): {q['verdict']}** — 금액 가중 단가 절대 로그오차 "
          f"U1 {q['metric_a']:.4f}, U0 {q['metric_b']:.4f}, 차이 {q['diff']:+.4f} (90% 구간 {q['lo90']:+.4f} ~ {q['hi90']:+.4f}), EWC p {q['p']:.3g}. "
          "처방량이 가격에 반응했다는 뜻이 아니다."]
    for k, f in r["q2_fake"].items():
        L.append(f"- 시점 이동 가짜 {k}개월({f.get('months', '')}): U1 대 가짜 {f.get('verdict', f.get('note'))}")
    if r["q2_fake_statement"]:
        L.append(f"- {r['q2_fake_statement']}")
    L.append(f"- 성분 뒤섞기 순열 p = {r['q2_perm_p']:.3g}" + (f" → {r['q2_perm_statement']}" if r["q2_perm_statement"] else ""))
    q = r["q2d"]
    L += ["", f"**Q2-d(보조, 보정층 F1q 대 F0q′, 대상월 2024-01 ~ 2025-12): {q['verdict']}** — WAPE {pct(q['metric_a'])} 대 {pct(q['metric_b'])}, EWC p {q['p']:.3g}."]
    for k, v in r["q2d_injection"].items():
        rec = "—" if v["recovery"] is None else f"{v['recovery']:.2f}"
        L.append(f"- 주입 δ = {float(k) * 100:.0f}%: {v['verdict']}, 회수율 {rec}")
    if r["q2d_statement"]:
        L.append(f"- {r['q2d_statement']}")
    qb = r["q2b"]
    L += ["", f"Q2-b(보조, 금액): F1K 대 {qb['against']} {qb['verdict']} (EWC p {qb['p']:.3g})."]
    if r["a18_statement"]:
        L.append(f"A18: {r['a18_statement']} (주 판정은 바꾸지 않는다).")
    L += ["", f"평가 칸 {r['eval_cells']}, 대상 성분 {r['n_target']}, B3 대체 칸 {r['b3_fallback_cells']}(가중치 몫 {pct(r['b3_fallback_weight_share'])}).",
          "보조 분석 표: aux.csv. 이 목록 밖의 분석은 '사후 탐색'이다."]
    (d / "verdict.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1])
