"""외부용 문서(README, reports/SUMMARY.md, reports/P2_SUMMARY.md) 공통 부품 (2026-11-19 사용자 지시 6).

- core(): 외부용 결론 세 가지와 그 의미 (숫자는 DB와 outputs CSV에서 계산)
- next_table(): "한계와 다음 실험" 표 (1~10, 2·3·4는 이번 결과로 채움)
- check_external(): 외부용 문서 금지어(내부 용어) 검사. 링크 주소와 `코드`는 검사에서 뺀다
내부 용어를 쓰지 않는다. 채용담당자가 처음 읽어도 이해되게 쓴다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
O = ROOT / "outputs/p2"
EXTERNAL_FORBIDDEN = ("오라클", "M0", "M2", "M3", "E1", "MASE", "원장", "신호", "실현", "미결", "판정 제외", "holdout", "Holdout",
                      "batch", "지표 A", "지표 B", "P1 사건", "정확히", "완벽", "인과", "영향을 미", "확실히", "약국 재고 최적화",
                      "신호에 정보가 없")
GLOSSARY = ("**용어** — **급여목록**: 건강보험이 적용되는 약과 상한가격의 공식 목록(매달 새 파일). **비고**: 목록의 메모 칸. 앞으로의 처분(예: \"'25.7.1. 인하\")이 미리 적힌다. "
            "**변화 기록표**: 월별 목록 파일을 비교해 언제 무엇이 바뀌었는지 날짜별로 정리한 표. **WAPE**: 예측이 빗나간 양의 합 ÷ 실제 처방량의 합(작을수록 좋다). **원외 처방**: 병원 밖 약국에서 조제되는 처방. **급여중지**: 건강보험 적용을 일정 기간 멈추는 처분. **집행정지**: 처분에 소송이 걸렸을 때 법원이 판결 전까지 처분의 효력을 멈추는 결정. "
            "**양도양수**: 제품의 허가를 다른 회사가 넘겨받아 같은 약이 새 코드로 다시 실리는 것.")


def pct(x: float, nd: int = 1) -> str:
    return f"{100 * x:.{nd}f}%"


def p1_numbers() -> dict:
    from pbd.ledger.v5_rules import rates
    c = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    q = lambda s, a=None: c.execute(s, a or []).fetchall()   # noqa: E731
    def r(cond, args):
        rs = [dict(outcome=o, reason=x, stay_blocked=b, current_status=s) for o, x, b, s in
              q(f"select outcome, reason, stay_blocked, current_status from signal_v5 where {cond}", args)]
        return rates(rs)
    pr2, sus = r("signal_type = ? and source_rule = ?", ["PRICE_NOTICE", "R2"]), r("signal_type = ?", ["SUSPEND_NOTICE"])
    s_, u_ = q("""select count(*) filter (where kind = 'signaled'), count(*) filter (where kind = 'unobserved_notice')
                  from ev_v5 where event_type = 'PRICE_CHANGE'""")[0]
    firms = q("""select p.firm, count(*) from signal_v5 s join product_state_month_v5 p on p.product_code = s.product_code and p.list_month = s.first_month
                 where s.signal_type = 'SUSPEND_NOTICE' and s.stay_blocked group by 1 order by 2 desc""")
    hp = pd.read_csv(ROOT / "outputs/validation/holdout_precision_c63778c.csv")
    g = lambda lab: hp[(hp.group == "전체") & (hp.label == lab)].iloc[0]   # noqa: E731
    prall = r("signal_type = ?", ["PRICE_NOTICE"])
    ub = q("select count(*) from ev_v5 where event_type = 'PRICE_CHANGE' and kind = 'unobserved_notice' and batch")[0][0]
    ky = q("""select count(*) from signal_v5 where signal_type = 'SUSPEND_NOTICE' and stay_blocked and stay_target_source = 'external'
              and signal_date = '2025-06-24'""")[0][0]
    return dict(price_B_k=pr2["B"][0], price_B_n=pr2["B"][1], n_price=s_ + u_, price_B_all=prall["B"][2], unannounced_rest=(u_ - ub) / (s_ + u_ - ub), sus_stay_list=(sus["stay"][0] - ky) / sus["stay"][1],
                price_B=pr2["B"][2], sus_stay=sus["stay"][2], sus_stay_k=sus["stay"][0], sus_stay_n=sus["stay"][1],
                top2=firms[0][1] + firms[1][1], top2_names=(firms[0][0], firms[1][0]), unannounced=u_ / (s_ + u_),
                hold_after=g("유형 (라벨 판정 후)").precision, hold_init=g("유형 (처음 라벨)").precision, hold_date=g("날짜").precision,
                hold_n=int(g("유형 (처음 라벨)").n))


def p2_numbers() -> dict:
    e = pd.read_csv(O / "t3_errors.csv")
    e = e[e["part"] == "전체"].set_index(["model", "lag"])
    w32 = pd.read_csv(O / "extra_weighted_32.csv").set_index(["model", "lag"])
    hz = pd.read_csv(O / "extra_horizon.csv").pivot_table(index="h", columns="model", values="wape")
    hv = pd.read_csv(O / "extra_horizon_verdict.csv").iloc[0]
    t2 = pd.read_csv(O / "extra_top200_errors.csv").set_index(["model", "lag"])
    t2c = pd.read_csv(O / "extra_top200_compare.csv").set_index(["lag", "a", "b"])
    st = pd.read_csv(O / "extra_strat200_errors.csv").set_index(["model", "lag"])
    stc = pd.read_csv(O / "extra_strat200_compare.csv").set_index(["lag", "a", "b"])
    stv = pd.read_csv(O / "extra_strat200_coverage.csv").iloc[0]
    t2v = pd.read_csv(O / "extra_top200_coverage.csv").iloc[0]
    rv = pd.read_csv(O / "extra_revision_verdict.csv").set_index("unit")
    dg = pd.read_csv(O / "diag_compare.csv").set_index(["lag", "a", "b"])
    dga = pd.read_csv(O / "diag_compare_claim_amt.csv").set_index(["lag", "a", "b"])
    ock = pd.read_csv(O / "oracle_check.csv").set_index("lag")
    cmp_ = pd.read_csv(O / "t3_compare.csv").set_index(["lag", "a", "b"])
    return dict(cmp=cmp_, e=e, w32=w32, hz=hz, hv=hv, t2=t2, t2c=t2c, st=st, stc=stc, stv=stv, t2v=t2v, rv=rv, dg=dg, dga=dga, ock=ock)


def core(P1: dict, P2: dict, which: str = "all") -> list[str]:
    """외부용 결론 세 가지와 의미. which = 'all'(README) / 'p1'(결론 1 + 나머지 링크) / 'p2'(결론 2·3 + 결론 1 링크) — 검토 4차 r-42"""
    e, hz, hv, cmp_ = P2["e"], P2["hz"], P2["hv"], P2["cmp"]
    w0_m2, w0_m0, w5_m2 = e.loc[("M2", 0), "wape"], e.loc[("M0", 0), "wape"], e.loc[("M2", 5), "wape"]
    red = 1 - w0_m2 / w0_m0
    assert 0.35 <= red <= 0.47, f"'약 40%'와 다르다: {red:.3f}"
    assert w5_m2 > w0_m0, "'실제 공개 시차에서는 더 나쁘다'와 다르다"
    t2, st = P2["t2"], P2["st"]
    t2_better = t2.loc[("M2", 5), "wape"] < t2.loc[("M0", 5), "wape"]
    st_better = st.loc[("M2", 5), "wape"] < st.loc[("M0", 5), "wape"]
    assert t2.loc[("M2", 0), "wape"] < t2.loc[("M2", 5), "wape"] and st.loc[("M2", 0), "wape"] < st.loc[("M2", 5), "wape"], "신선도 문장(200개)과 다르다"
    grow = hz.loc[2, "M2"] / hz.loc[1, "M2"] - 1
    for name, c in (("32", cmp_), ("200", P2["t2c"]), ("층화", P2["stc"])):
        for lag in (5, 0):
            assert c.loc[(lag, "M3", "M2"), "verdict_holm"] != "M3가 나음", f"(3) 개선 없음과 다름: {name} {lag}"
    for c in (P2["dg"], P2["dga"]):
        for lag in (5, 0):
            assert c.loc[(lag, "M3o_oracle", "E1_null"), "verdict_holm"] == "차이 없음"
    assert P1["top2"] >= 0.9 * P1["sus_stay_k"], "'대부분 두 회사'와 다르다"
    size_note = []
    if t2_better:
        size_note.append(f"처방량 상위 200개 성분({pct(t2.loc[('M2', 5), 'wape'])} 대 {pct(t2.loc[('M0', 5), 'wape'])})")
    if st_better:
        size_note.append(f"분류·규모·업체 수로 고르게 뽑은 200개 성분({pct(st.loc[('M2', 5), 'wape'])} 대 {pct(st.loc[('M0', 5), 'wape'])})")
    c1 = (f"1. **약가 인하 예고는 대부분 예고대로 실행되고, 급여중지 예고는 47%가 법원 집행정지로 막혔다(대부분 두 회사).** "
          f"약가 인하 예고는 결과가 난 것 중 {pct(P1['price_B'])}가 예고대로 실행됐다(급여목록 비고에 적힌 예고 기준. 대체조제 목록으로만 보인 막힌 처분까지 넣으면 {pct(P1['price_B_all'])}). "
          f"급여중지 예고는 {pct(P1['sus_stay'])}({P1['sus_stay_k']}/{P1['sus_stay_n']}건)가 막혔고, 그중 {P1['top2']}건이 두 회사의 처분이다"
          f"(급여목록 표시만으로 세면 {pct(P1['sus_stay_list'])}). 약가 변화의 {pct(P1['unannounced'])}(약 3분의 2)는 목록에 미리 예고되지 않았다"
          f"(일괄 인하가 있었던 3개 달을 빼면 {pct(P1['unannounced_rest'])}). "
          f"자동 판정을 사람이 정답을 단 처음 보는 검증 표본 {P1['hold_n']}건과 맞춰 보면, 변화의 종류(약가 인하·등재·업체 변경 등)가 {pct(P1['hold_after'])} 일치했다"
          f"(판정 결과를 본 뒤 2건을 고친 정답 기준. 처음 정답 기준 {pct(P1['hold_init'])}), 날짜는 {pct(P1['hold_date'], 0)} 일치했다.")
    sg = pd.read_csv(O / "extra_signif.csv").set_index(["set", "lag", "a", "b"])
    s_top, s_st = sg.loc[("상위 200", 5, "M2", "M0")], sg.loc[("층화 200", 5, "M2", "M0")]
    assert s_top.dm_verdict == "유의한 차이 없음" and s_top.dmw_verdict == "M2가 나음", "DM 단서 문장(금액 가중 시에만 유의)과 다르다"
    hz2 = pd.read_csv(O / "extra_horizon_top200.csv").pivot_table(index="h", columns="model", values="wape")
    hv2 = pd.read_csv(O / "extra_horizon_top200_verdict.csv").iloc[0]
    g200, g32 = hz2.loc[2, "M2"] / hz2.loc[1, "M2"] - 1, hz.loc[2, "M2"] / hz.loc[1, "M2"] - 1
    wn = pd.read_csv(O / "extra_window.csv")
    wn = wn[wn["set"] == "상위 200"].set_index("part")
    win1, win2 = wn.loc["① 시차 5·다음 달 전체"], wn.loc["①과 ②에 공통인 대상 달"]
    assert wn["same_prediction_maxdiff"].max() == 0, "'같은 예측'과 다르다"
    assert win2.wape_m0 < win2.wape_m2 and win2.wape_amt_m2 < win2.wape_amt_m0, "(참고) 줄의 24개월 비교와 다르다"
    assert (win2["first"], win2["last"], int(win2.months)) == ("2023-07", "2025-06", 24), "(참고) 줄의 기간과 다르다"
    only = wn.loc["①에만 있는 대상 달"]
    assert only.wape_m0 > only.wape_m2, "'작년 같은 달 값이 크게 빗나간' 근거(11개월 M0 > M2)와 다르다"
    om = pd.read_csv(O / "extra_window_months.csv")
    om = set(om[om["set"] == "상위 200"]["target"])
    assert om == set(["2023-0%d" % k for k in range(2, 7)] + ["2025-%02d" % k for k in range(7, 13)]), f"'2023년 상반기와 2025년 하반기'와 다르다: {sorted(om)}"
    top_sig = s_top.verdict == "M2가 나음"
    st_sig = s_st.verdict == "M2가 나음"
    share76 = P2["t2v"].amt_share
    assert 0.755 <= share76 < 0.765, f"'76%'와 다르다: {share76}"
    for v, exp in ((t2.loc[("M2", 5), "wape"], "11.0%"), (t2.loc[("M0", 5), "wape"], "12.0%"), (t2.loc[("M2", 5), "wape_amt"], "6.7%"),
                   (t2.loc[("M0", 5), "wape_amt"], "8.5%"), (t2.loc[("M2", 0), "wape"], "7.2%")):
        assert pct(v) == exp, f"결론 (2)의 {exp}와 계산값 {pct(v)}가 다르다"
    assert e.loc[("M0", 5), "wape"] < w5_m2, "'32개 성분에서는 작년 같은 달 값이 더 나았다'와 다르다"
    verb = "작았다" if top_sig else "유의한 차이가 없었다"
    c2 = (f"2. **예측 정확도는 자료의 신선도가 크게 좌우한다.** 처방량 상위 200개 성분(공개 원외처방 청구금액의 {pct(share76, 0)})의 다음 달 처방량은, "
          f"실제 공개 시차(5개월)에서도 기계학습 모델의 오차가 작년 같은 달 값보다 {verb}"
          f"(WAPE {pct(t2.loc[('M2', 5), 'wape'])} vs {pct(t2.loc[('M0', 5), 'wape'])}, 금액 가중 {pct(t2.loc[('M2', 5), 'wape_amt'])} vs {pct(t2.loc[('M0', 5), 'wape_amt'])}) "
          "(성분별 비교 기준. 달마다 전체 오차를 비교하면 금액 가중 시에만 유의했다). "
          f"지난달 자료를 바로 쓸 수 있다면 오차는 {pct(t2.loc[('M2', 0), 'wape'])}로 더 줄었다. 계절 변동이 큰 호흡기 계열 32개 성분에서는 5개월 시차에서 작년 같은 달 값이 더 나았다. "
          f"(분류·규모·업체 수로 고르게 뽑은 층화 200개 성분: {'유의' if st_sig else '유의하지 않음'} — WAPE {pct(st.loc[('M2', 5), 'wape'])} vs {pct(st.loc[('M0', 5), 'wape'])}) "
          f"자료가 제때 있다면 상위 200개는 {int(hv2.last_h_m2_beats_m0)}개월 앞까지, 호흡기 32개는 {int(hv.last_h_m2_beats_m0)}개월 앞까지 작년 같은 달 값보다 나았다. "
          f"오차는 2개월 앞부터 1개월 앞 대비 약 {pct(g200, 0)}(상위 200개)·{pct(g32, 0)}(32개) 커졌다. "
          f"(참고: 평가하는 달을 바꾸면 결과가 달라진다. {win2['first']}~{win2['last']}의 {int(win2.months)}개월만 보면 전체 오차는 작년 같은 달 값이 약간 낮았고"
          f"({pct(win2.wape_m0)} vs {pct(win2.wape_m2)}), 금액 가중 오차는 모델이 낮았다({pct(win2.wape_amt_m0)} vs {pct(win2.wape_amt_m2)}). "
          "차이는 작년 같은 달 값이 크게 빗나간 2023년 상반기와 2025년 하반기에서 생겼다.)")
    c3 = ("3. **급여목록의 가격·등재 정보는 세 대상(32개, 상위 200개, 층화 200개) 모두에서 성분 전체 처방량 예측을 개선하지 못했다.** 실제로 일어난 변화를 미리 알려 준 시험에서도 개선이 없었다. "
          "이것은 목록 정보가 쓸모없다는 뜻이 아니다. 그 정보가 움직이는 곳은 성분 안의 제품별 처방 비중과 약국 재고의 가격 위험일 가능성이 크다고 본다(검증하지 않은 추정). 이 프로젝트는 그쪽을 측정하지 않았다.")
    wamt = t2.loc[("M2", 5), "wape_amt"]
    assert 0.06 <= wamt < 0.08, f"'약 7%'와 다르다: {wamt}"
    lvl = f"금액 가중 오차 약 {round(100 * wamt):.0f}%" if top_sig else "작년 같은 달 값과 비슷한 수준"
    mean = "\n".join([
        "**의미**:",
        f"- 넓은 성분 범위의 월 수요는 공개 통계로도 예측할 수 있다(상위 200개, {lvl}).",
        f"- 자료가 신선할수록 오차가 줄어든다({pct(t2.loc[('M2', 5), 'wape'])} → {pct(t2.loc[('M2', 0), 'wape'])}).",
        "- 계절 변동이 큰 약(호흡기 계열)은 예외다. 늦은 자료로는 유행 시작을 볼 수 없다.",
        "- 약국 주문·판매처럼 바로 쌓이는 자료의 가치는 이 프로젝트에서 **검증하지 않은 가정**이다.",
        "- 목록 정보는 수요 예측보다 약국 알림에 맞다(시험하지 않은 제안): 약가 인하 예고는 신뢰도가 높고, 급여중지 예고는 법원에서 막힐 수 있다는 점을 함께 표시해야 한다."])
    if which == "p1":
        body = [c1, "", "처방 수요 예측 쪽 결론(자료의 신선도, 목록 정보가 예측을 개선하지 못함)은 [P2 요약](P2_SUMMARY.md)에, 세 결론을 한곳에 모은 것은 [README](../README.md)에 있다."]
    elif which == "p2":
        body = [c2, c3, "", mean, "", "급여목록 처분 예고 쪽 결론(약가 인하 예고 실행 비율, 급여중지 예고의 법원 집행정지)은 [P1 요약](SUMMARY.md)에 있다."]
    else:
        body = [c1, c2, c3, "", mean]
    return ["## 핵심 결론", "", *body, "", GLOSSARY, ""]


def next_table(P2: dict) -> list[str]:
    hz, hv, rv = P2["hz"], P2["hv"], P2["rv"]
    hz2 = pd.read_csv(O / "extra_horizon_top200.csv").pivot_table(index="h", columns="model", values="wape")
    hv2 = pd.read_csv(O / "extra_horizon_top200_verdict.csv").iloc[0]
    t2, st, t2c, stc, stv, t2v = P2["t2"], P2["st"], P2["t2c"], P2["stc"], P2["stv"], P2["t2v"]
    cls = rv.loc["약효분류군 (주)"]
    r2 = ("공식 작성 기준에 따른 자료 성질이다: 사용통계는 건강보험 청구자료 기반이고 판본마다 특정 심사 완료 시점까지의 청구만 담는다"
          "(공공데이터포털 설명 '25.5월 심사완료분까지 반영, 25.6.27.'; 같은 기관 진료현황 자료 '청구권 소멸시효(3년) 이전 자료는 변동될 수 있음'). 그래서 최근 달은 덜 집계되고 이후 판본에서 늘 수 있다. "
          "공개 시차는 약 4개월(2025-02 자료가 2025-06-27 공개)로 이 프로젝트의 가정 5개월과 비슷하다. "
          f"이번: 같은 달을 다른 날 받은 판본이 하나뿐이라 간접 시험만 했다. 최근 3개월의 작년 같은 달 대비 비율이 2023~24년보다 약효분류군 {int(cls.units)}개의 중앙값으로 {100 * cls.median_drop:+.1f}%, "
          f"하락 방향인 분류군 {pct(cls.share_negative, 0)}. 이 방법으로는 수정 폭이 −5%를 넘는다는 증거를 찾지 못했다(실행 전에 정한 기준 −5%·70%). 실제 수요 감소와도 구분할 수 없다. "
          "수정 폭의 직접 측정은 이번 범위에서 하지 않았다(작성자 결정, 내려받은 지 1주일이라 비교할 다른 판본이 없음). 두 판본 비교 스크립트는 다음 실험용 도구로 두었다")
    r3 = (f"이번(가설 '1개월 앞 대비 오차가 10% 넘게 커지는 첫 개월이 4개월 이상'): 자료가 바로 공개된다고 두고 2023-01 ~ 2024-12에 시작한 24번의 예측으로 쟀다. "
          f"32개 성분: 기계학습 모델 오차 1개월 앞 {pct(hz.loc[1, 'M2'])} → 2개월 앞 {pct(hz.loc[2, 'M2'])} → 12개월 앞 {pct(hz.loc[12, 'M2'])}, 가설 **{'채택' if hv.first_h_10pct >= 4 else '기각'}**, "
          f"작년 같은 달 값을 이기는 것은 {int(hv.last_h_m2_beats_m0)}개월 앞까지. "
          f"상위 200개 성분: {pct(hz2.loc[1, 'M2'])} → {pct(hz2.loc[2, 'M2'])} → {pct(hz2.loc[12, 'M2'])}, 가설 **{'채택' if hv2.first_h_10pct >= 4 else '기각'}**"
          f"(10% 넘게 커지는 첫 개월 {int(hv2.first_h_10pct)}), 작년 같은 달 값을 이기는 것은 {int(hv2.last_h_m2_beats_m0)}개월 앞까지. "
          "(결론의 '다음 달' 값은 2023-01 ~ 2025-11의 35번 예측이라 평가 기간이 달라 값이 다르다)")
    lab = lambda v: "개선 없음" if v != "M3가 나음" else "개선"   # noqa: E731
    r4 = (f"이번: 처방량 상위 200개(2022년 원외 청구금액의 {pct(t2v.amt_share)})와, 약효분류·청구금액 규모·업체 수로 나눈 층에서 고르게 뽑은 200개(같은 비중 {pct(stv.amt_share)}, 상위 200개와 {int(stv.overlap_top200)}개 겹침). "
          f"5개월 시차 오차: 상위 200개 기계학습 {pct(t2.loc[('M2', 5), 'wape'])} 대 작년 같은 달 {pct(t2.loc[('M0', 5), 'wape'])}, 층화 200개 {pct(st.loc[('M2', 5), 'wape'])} 대 {pct(st.loc[('M0', 5), 'wape'])}. "
          f"목록 정보 추가: 상위 200개 {lab(t2c.loc[(5, 'M3', 'M2'), 'verdict_holm'])}, 층화 200개 {lab(stc.loc[(5, 'M3', 'M2'), 'verdict_holm'])}. "
          "**200개로도 남는 한계**: 96개월 연속 관측 조건의 생존 편향, 업체 2곳 이하 성분은 사용통계에서 비공개(제품 하나의 변화가 성분 합계를 움직일 곳이 빠짐), "
          "성분 합계라 성분 안의 처방 이동이 안 보임, 예측 개선 여부로 효과를 재는 시험의 낮은 검출력")
    rows = [
        (1, "성분 합계만 있어 제품별 처방 비중을 볼 수 없다", "제품별 처방 비중 분석(제품 단위 자료 확보 후)", "인하 예고 제품의 성분 안 비중이 예고 전후로 달라지는가", "향후 과제. 사용통계에는 제품 단위 처방량이 없다(원본 96개 파일이 주성분 4자리까지만 있음)"),
        (2, "사용통계는 공개 뒤에 늘어날 수 있다(판본마다 심사완료분까지만 반영하는 공식 작성 기준, 이후 변동 가능)", "같은 달 자료를 매달 다시 내려받아 판본을 쌓고 수정 폭과 안정 기간을 측정, 완성 계수(공개 후 경과 개월별로 최종값의 몇 %가 집계되는지)로 최근 달 보정", "같은 달의 두 판본 차이가 공개 후 경과 개월에 따라 줄어드는가", r2),
        (3, "몇 개월 앞까지 쓸 만한지 몰랐다", "1~12개월 앞 오차 곡선 (32개, 상위 200개)", "1개월 앞 대비 오차가 10% 넘게 커지는 첫 개월, 작년 같은 달 값보다 오차가 작은 마지막 개월", r3),
        (4, "대상이 호흡기·알레르기 계열 32개 성분뿐이다", "상위 200개 + 층화 200개 성분", "5개월 시차에서 결론(작년 값보다 나쁜가, 목록 정보가 개선하는가)이 유지되는가", r4),
        (5, "전국 합계만 예측했다", "시도별 예측", "시도별 합이 전국 예측보다 나은가", "향후 과제"),
        (6, "약국 단위 자료가 없다", "약국 주문·판매 자료에 같은 방법 적용", "바로 쌓이는 자료로 작년 같은 달 값을 몇 개월 앞까지 이기는가", "향후 과제"),
        (7, "알려진 가격 변화를 예측에 직접 쓰지 않았다", "알려진 가격 변화를 공식으로 반영하는 모델(새 검증 기간에서)", "새 검증 기간에서 청구금액 오차가 줄어드는가", "향후 과제"),
        (8, "급여목록에 없는 집행정지는 놓친다", "집행정지 자동 수집(뉴스·법원 공시)", "놓친 집행정지 건수와 판정 변화", "향후 과제"),
        (9, "공급 중단 정보가 없다", "공급 중단 외부 자료 연결", "공급 중단 성분의 오차가 줄어드는가", "향후 과제"),
        (10, "자동 판정의 정답을 한 사람이 정했다", "두 사람 판정 일치도", "두 사람의 정답 일치율(우연히 맞을 확률을 뺀 일치도, 카파)", "향후 과제"),
    ]
    L = ["## 한계와 다음 실험", "", "| # | 한계 | 다음 실험 | 판단 기준 | 상태 |", "|---|---|---|---|---|"]
    L += [f"| {n} | {a} | {b} | {c} | {d} |" for n, a, b, c, d in rows]
    return L + [""]


def strip_for_check(text: str) -> str:
    text = re.sub(r"\]\([^)]*\)", "]", text)      # 링크 주소
    text = re.sub(r"`[^`]*`", "", text)           # 코드
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return text


def check_external(path: Path, text: str) -> list[str]:
    t = strip_for_check(text)
    return [f"{path.name}: 외부용 금지어 '{w}'" for w in EXTERNAL_FORBIDDEN if w in t]
