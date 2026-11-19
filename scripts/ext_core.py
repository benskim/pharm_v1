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
# '실현'·'MASE'는 2026-11-19 검토 반영(사용자 문장 A1·A2, A9 열 이름)으로 금지어에서 뺐다
EXTERNAL_FORBIDDEN = ("오라클", "M0", "M2", "M3", "E1", "원장", "신호", "미결", "판정 제외", "holdout", "Holdout",
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
    dsp = pd.read_csv(ROOT / "outputs/v5/disposition_summary.csv").set_index("kind")
    ds, dp = dsp.loc["급여중지 예고"], dsp.loc["약가 예고 (급여목록 비고)"]
    assert int(ds.products_blocked) == sus["stay"][0] and int(ds.n_products_judged) == sus["stay"][1], "처분 단위 표와 제품 단위 집계가 다르다"
    assert abs(dp.product_on_time - pr2["on_time"][2]) < 1e-12 and abs(ds.product_on_time - sus["on_time"][2]) < 1e-12
    m2309 = int(dsp.iloc[0].unannounced_2023_09_n)
    cmp_ = pd.read_csv(ROOT / "outputs/validation/holdout_30.compared_c63778c.csv", dtype=str)
    fixed = cmp_[cmp_.truth_type_initial != cmp_.truth_type]
    assert len(fixed) == 2 and set(fixed.truth_type_initial) == {"신규 등재"} and set(fixed.truth_type) == {"양도양수"}, "'고친 2건은 같은 허가를 이은 경우'와 다르다"
    disp = dict(sus_units=int(ds.n_units), sus_firms=int(ds.n_firms), sus_units_blocked=int(ds.units_any_blocked), sus_products=int(ds.n_products_judged),
                price_units=int(dp.n_units), price_units_blocked=int(dp.units_any_blocked), price_on_time=pr2["on_time"][2], sus_on_time=sus["on_time"][2],
                price_A_all=prall["A"][2], price_stay_all=prall["stay"][2], price_stay_all_k=prall["stay"][0], price_stay_all_n=prall["stay"][1],
                price_stay_later=q("""select count(*) from signal_v5 where signal_type = 'PRICE_NOTICE' and stay_blocked
                                       and outcome in ('REALIZED', 'REALIZED_LATE', 'REALIZED_CHANGED', 'REALIZED_BY_DELISTING')""")[0][0],
                batch_share=ub / u_, unannounced_n=u_, m2309=m2309, n_price_events=s_ + u_)
    return dict(**disp, price_B_k=pr2["B"][0], price_B_n=pr2["B"][1], n_price=s_ + u_, price_B_all=prall["B"][2], unannounced_rest=(u_ - ub) / (s_ + u_ - ub), sus_stay_list=(sus["stay"][0] - ky) / sus["stay"][1],
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
    h1 = pd.read_csv(O / "extra_2023h1_months.csv")
    h1r = pd.read_csv(O / "extra_2023h1_ratio.csv")
    rec = pd.read_csv(O / "extra_recent.csv").set_index(["set", "lag", "part"])
    sg = pd.read_csv(O / "extra_signif.csv").set_index(["set", "lag", "a", "b"])
    return dict(h1=h1, h1r=h1r, rec=rec, sg=sg, cmp=cmp_, e=e, w32=w32, hz=hz, hv=hv, t2=t2, t2c=t2c, st=st, stc=stc, stv=stv, t2v=t2v, rv=rv, dg=dg, dga=dga, ock=ock)


def texts(P1: dict, P2: dict) -> dict:
    """외부용 결론 문장 (2026-11-19 독립 검토 A·B 반영, 사용자 지시 A1~A6). 한 곳에서 정의하고 README·SUMMARY·P2_SUMMARY·OVERVIEW가 쓴다.
    미리 정한 주 분석을 먼저 쓰고, 숫자는 모두 결과 파일에서 계산·대조한다."""
    e, hz, cmp_, t2, st, sg = P2["e"], P2["hz"], P2["cmp"], P2["t2"], P2["st"], P2["sg"]
    # ---- 대조: 결론 (3) 개선 없음 (세 대상 × 시차 0·5), 오라클은 성분 32개
    for name, c in (("32", cmp_), ("200", P2["t2c"]), ("층화", P2["stc"])):
        for lag in (5, 0):
            assert c.loc[(lag, "M3", "M2"), "verdict_holm"] != "M3가 나음", f"(3) 개선 없음과 다름: {name} {lag}"
    for c in (P2["dg"], P2["dga"]):
        for lag in (5, 0):
            assert c.loc[(lag, "M3o_oracle", "E1_null"), "verdict_holm"] == "차이 없음"
    inj = pd.read_csv(O / "exp_inject.csv").set_index(["lag", "model", "injected"])["mase"]
    assert inj.loc[(5, "E6_M3", True)] < inj.loc[(5, "E6_M2", True)] and inj.loc[(0, "E6_M3", True)] >= inj.loc[(0, "E6_M2", True)], "'일부만 찾을 만큼'과 다름"
    inj_k = round(100 * (1 - float(re.search(r"^INJECT = ([0-9.]+)", (ROOT / "src/pbd/forecast/t3_experiments.py").read_text(encoding="utf-8"), re.M).group(1))))
    assert inj_k == 20
    ceil = round(100 * P2["ock"].loc[5, "improvement_ceiling"])
    assert ceil == 2, ceil
    # ---- 대조: 결론 (2)
    s_top = sg.loc[("상위 200", 5, "M2", "M0")]
    assert e.loc[("M0", 5), "wape"] < e.loc[("M2", 5), "wape"], "주 대상 32개에서 모델이 나빴다와 다름"
    assert t2.loc[("M2", 5), "wape"] < t2.loc[("M0", 5), "wape"] and t2.loc[("M2", 5), "wape_amt"] < t2.loc[("M0", 5), "wape_amt"]
    assert s_top.dm_verdict == "유의한 차이 없음" and s_top.dmw_verdict == "M2가 나음", "'달별 총오차 검정 비유의(가중은 유의)'와 다름"
    h1 = P2["h1"]
    h = h1[h1["set"] == "상위 200"].assign(d=lambda x: x.wape_m0 - x.wape_m2)
    hm = h.groupby("half")[["wape_m0", "wape_m2"]].mean()
    share_h1 = h.loc[h.half == "2023 상반기", "d"].sum() / h["d"].sum()
    assert share_h1 >= 0.9, f"'거의 모두 2023년 상반기'와 다름: {share_h1:.2f}"
    assert abs(hm.loc["나머지", "wape_m0"] - hm.loc["나머지", "wape_m2"]) < 0.005, "'나머지 달에는 거의 같았다'와 다름"
    r = P2["h1r"]
    rt = r[(r["set"] == "상위 200") & (r["kind"] == "전년 대비")].set_index("year")["median"]
    r32 = r[(r["set"] == "성분 32") & (r["grp"] == "전체") & (r["kind"] == "2019 대비") & (r["year"] == 2022)]["median"].iloc[0]
    assert rt[2023] > 1.1 and abs(rt[2024] - 1) < 0.02 and abs(rt[2025] - 1) < 0.02 and r32 < 0.9
    rec = P2["rec"]
    for name in ("상위 200", "층화 200"):
        x = rec.loc[(name, 5, "마지막 12개월 뺌")]
        assert x.wape_m2 < x.wape_m0, f"최근 12개월을 빼도 같은 모양과 다름: {name}"
    x32 = rec.loc[("성분 32", 5, "마지막 12개월 뺌")]
    assert x32.wape_m0 < x32.wape_m2
    xt = rec.loc[("상위 200", 5, "마지막 12개월 뺌")]
    hz2 = pd.read_csv(O / "extra_horizon_top200.csv").pivot_table(index="h", columns="model", values="wape")
    g200, g32 = hz2.loc[2, "M2"] / hz2.loc[1, "M2"] - 1, hz.loc[2, "M2"] / hz.loc[1, "M2"] - 1
    assert all(hz2["M2"].diff().dropna().iloc[:3] > 0) and all(hz["M2"].diff().dropna().iloc[:3] > 0), "'멀어질수록 커졌다'와 다름"
    # ---- 대조: 결론 (1)
    assert P1["sus_units_blocked"] == 3 and P1["price_units_blocked"] == 2
    assert P1["top2"] + 1 == P1["sus_stay_k"]
    assert abs(P1["unannounced"] - 2 / 3) < 0.01
    one = (f"급여목록에 미리 적힌 약값 인하와 급여중지 처분은 대부분 실현됐고, 법원 집행정지로 막힌 것은 처분 기준으로 소수(급여중지 {P1['sus_units']}건 중 {P1['sus_units_blocked']}건, "
           f"약값 인하 {P1['price_units']}건 중 {P1['price_units_blocked']}건)였다. 공개 사용통계로 성분 처방량을 예측할 때 기계학습 모델은 작년 같은 달 값과 대체로 비슷했고, "
           "급여목록의 예고 정보는 예측을 개선하지 못했다.")
    c1 = [f"1. **급여목록에 미리 적힌 처분은 대부분 실현됐고, 법원 집행정지로 막힌 것은 처분 기준으로 소수였다.**",
          f"   - 약값 인하: 결과가 난 약값 인하 예고(제품 {P1['price_B_n']:,}개) 중 {pct(P1['price_B'])}가 실현됐다(다른 금액·늦은 시점 포함). 예정일에 예고대로 실현된 것은 {pct(P1['price_on_time'])}다. "
          f"대체조제 목록에만 남은 막힌 처분까지 넣으면 현재까지 실행된 비율은 {pct(P1['price_A_all'])}, 법원 집행정지가 걸린 비율은 {pct(P1['price_stay_all'])}({P1['price_stay_all_k']}/{P1['price_stay_all_n']:,}개 제품, 그중 {P1['price_stay_later']}개는 집행정지가 끝난 뒤 실행)다.",
          f"   - 급여중지: 급여중지 예고는 처분 {P1['sus_units']}건({P1['sus_firms']}개 회사, 제품 {P1['sus_products']}개)이었고, 그중 {P1['sus_units_blocked']}건(동아에스티 72개 제품, 경보제약 23개, 삼성제약 1개)이 법원 집행정지로 막혔다. "
          f"제품 수로 세면 {pct(P1['sus_stay'])}지만, 큰 처분 몇 건이 제품 수를 부풀린 값이다. 급여목록 표시만으로 세면 제품 기준 {pct(P1['sus_stay_list'])}이며, 경보제약은 외부 보도로만 확인했다. "
          f"결과가 난 급여중지 예고 중 예정일에 그대로 시행된 비율은 {pct(P1['sus_on_time'])}이고, 나머지 실현은 늦은 시행과 삭제다.",
          f"   - 예고 없는 변화: 가격이 바뀐 제품의 {pct(P1['unannounced'])}({P1['unannounced_n']:,}/{P1['n_price_events']:,})는 급여목록 비고에 미리 적히지 않았다. 이 가운데 {pct(P1['batch_share'])}는 일괄 인하가 있었던 3개 달이고, "
          f"2023-09 제네릭 재평가 한 번이 {P1['m2309']:,}건이다. 이 재평가는 목록 밖에서 미리 보도됐다. 일괄 인하 3개 달을 빼면 {pct(P1['unannounced_rest'])}다."]
    c2 = ["2. **기계학습 모델은 작년 같은 달 값과 대체로 비슷했고, 이례적인 기간에 덜 빗나갔다.**",
          f"   - 미리 정한 주 대상(호흡기 계열 32개)에서는, 사용통계가 5개월 늦게 공개된다고 두고 마지막 자료에서 6개월 앞을 예측하면 모델이 작년 같은 달 값보다 나빴다(WAPE {pct(e.loc[('M2', 5), 'wape'])} 대 {pct(e.loc[('M0', 5), 'wape'])}).",
          f"   - 결과를 본 뒤 원래 계획 범위로 넓힌 상위 200개에서는 모델이 조금 나았다({pct(t2.loc[('M2', 5), 'wape'])} 대 {pct(t2.loc[('M0', 5), 'wape'])}, 청구금액 가중 {pct(t2.loc[('M2', 5), 'wape_amt'])} 대 {pct(t2.loc[('M0', 5), 'wape_amt'])}). "
          f"그 차이는 거의 모두 2023년 상반기에서 나왔다(달별 오차 평균: 작년 값 {pct(hm.loc['2023 상반기', 'wape_m0'])} 대 모델 {pct(hm.loc['2023 상반기', 'wape_m2'])}). "
          f"이때 처방량이 전년 같은 달보다 크게 늘어(중앙값 {100 * (rt[2023] - 1):+.1f}%, 2024·2025년 상반기는 약 {100 * (rt[2024] - 1):+.0f}%) 작년 값(2022년)이 크게 빗나갔다. 코로나 뒤 회복기와 겹친다(호흡기 32개는 2022년 상반기가 2019년의 {pct(r32, 0)} 수준). "
          f"나머지 달에는 둘이 거의 같았다({pct(hm.loc['나머지', 'wape_m0'])} 대 {pct(hm.loc['나머지', 'wape_m2'])}). 달별 총오차 검정으로는 유의하지 않았다(청구금액 가중으로는 유의).",
          f"   - 덜 집계됐을 수 있는 최근 12개월을 빼도 같은 모양이었다(상위 200개 {pct(xt.wape_m2)} 대 {pct(xt.wape_m0)}, 32개는 작년 값이 더 나음).",
          f"   - 수정이 끝난 자료로 지난달까지 볼 수 있다고 가정하면 상위 200개의 1개월 앞 오차는 {pct(t2.loc[('M2', 0), 'wape'])}였다(32개는 {pct(e.loc[('M2', 0), 'wape'])}). 이는 실제로 얻을 수 없는 상한이며, 예측 거리가 짧아진 효과와 자료 신선도의 효과가 섞여 있다. "
          f"1개월 앞에서 멀어질수록 오차가 커졌다(2023-01 ~ 2024-12에 시작한 예측으로 잰 곡선에서 2개월 앞이 1개월 앞 대비 약 {pct(g200, 0)}(상위 200개)·{pct(g32, 0)}(32개)).",
          "   - 2023년 상반기 분석은 시기별 비교와 관련성만 보였고 원인을 증명하지 않는다."]
    c3 = ["3. **성분 합계 처방량 예측은 급여목록 예고 정보를 넣어도 나아지지 않았다(세 대상, 시차 0·5).** "
          "실제로 일어난 변화를 미리 넣은 시험은 성분 32개에서만 했고, 거기서도 나아지지 않았다. "
          f"일부러 넣은 {inj_k}% 효과도 일부만 찾을 만큼 검출력이 낮아 '효과가 없다'고는 말하지 않는다. "
          "처방량이 상한금액에 둔감해서인지, 성분 안에서 제품이 바뀌어서인지는 성분 단위 자료로 구분할 수 없다. "
          f"가격을 미리 알 때 줄일 수 있는 오차의 상한은 금액 변화 분해로 근사하면 약 {ceil}%(성분 32개 기준)다. 대상은 업체가 3곳 이상인 성분(공개 기준)이다."]
    mean = ["- 급여목록의 처분 예고는 처분 기준으로 대부분 실현됐다. 약국 알림에 쓸 수 있고, 급여중지는 법원에서 막힐 수 있다는 표시를 함께 두는 것이 맞다(시험하지 않은 제안).",
            "- 공개 통계 기반 예측은 평상시에는 작년 같은 달 값과 비슷했고, 코로나 회복기처럼 이례적인 기간에 덜 빗나갔다. 바로 쌓이는 약국 주문·판매 자료가 예측을 더 낫게 할지는 검증하지 않은 가정이다.",
            "- 급여목록 예고 정보는 성분 전체 수요 예측보다 가격·재고 위험 알림에 맞는 정보다(시험하지 않은 제안)."]
    prec = (f"규칙을 고치기 전 판정 엔진(v5.2)을 사람 정답 {P1['hold_n']}건(판정 유형별 3건씩, 작성자 1인)과 맞춰 보면 유형 일치는 {pct(P1['hold_init'])}였다. "
            f"불일치 건만 다시 보고 2건을 고치면 {pct(P1['hold_after'])}다(고친 2건은 같은 허가를 이은 경우로, 급여 사건으로는 신규 등재로 볼 여지도 있다). "
            "이 불일치를 보고 규칙을 고친 v5.3의 정밀도는 별도 검증 표본으로 잰다. 유형별 같은 수로 뽑아 모집단 정밀도가 아니며 놓친 사건(재현율)은 재지 않았다. "
            "날짜 일치는 대부분 '그 달 1일' 관례가 양쪽에 같이 적용된 결과라 날짜 판정의 검증으로 보지 않는다.")
    return dict(one=one, c1=c1, c2=c2, c3=c3, mean=mean, prec=prec)


def core(P1: dict, P2: dict, which: str = "all") -> list[str]:
    """외부용 결론 세 가지와 의미. which = 'all'(README) / 'p1'(결론 1 + 나머지 링크) / 'p2'(결론 2·3 + 결론 1 링크) — 검토 4차 r-42"""
    T = texts(P1, P2)
    head = [f"**한 줄 요약**: {T['one']}", ""]
    mean = ["**의미**:", *T["mean"]]
    prec = ["**판정 정밀도**: " + T["prec"]]
    if which == "p1":
        body = [*T["c1"], "", *prec, "", "처방 수요 예측 쪽 결론은 [P2 요약](P2_SUMMARY.md)에, 세 결론을 한곳에 모은 것은 [README](../README.md)에 있다."]
    elif which == "p2":
        body = [*T["c2"], *T["c3"], "", *mean, "", "급여목록 처분 예고 쪽 결론은 [P1 요약](SUMMARY.md)에 있다."]
    else:
        body = [*T["c1"], *T["c2"], *T["c3"], "", *mean, "", *prec]
    return ["## 핵심 결론", "", *head, *body, "", GLOSSARY, ""]


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
          f"32개 성분: 기계학습 모델 오차 1개월 앞 {pct(hz.loc[1, 'M2'])} → 2개월 앞 {pct(hz.loc[2, 'M2'])} → 12개월 앞 {pct(hz.loc[12, 'M2'])}, 가설 **{'채택' if hv.first_h_10pct >= 4 else '기각'}**. "
          f"상위 200개 성분: {pct(hz2.loc[1, 'M2'])} → {pct(hz2.loc[2, 'M2'])} → {pct(hz2.loc[12, 'M2'])}, 가설 **{'채택' if hv2.first_h_10pct >= 4 else '기각'}**"
          f"(10% 넘게 커지는 첫 개월 {int(hv2.first_h_10pct)}). 1개월 앞에서 멀어질수록 오차가 커졌다. "
          "(결론의 '공개 시차 5개월 가정' 값은 2023-01 ~ 2025-11의 35번 예측이라 평가 기간이 달라 값이 다르다)")
    lab = lambda v: "개선 없음" if v != "M3가 나음" else "개선"   # noqa: E731
    r4 = (f"이번: 처방량 상위 200개(2022년 원외 청구금액의 {pct(t2v.amt_share)})와, 약효분류·청구금액 규모·업체 수로 나눈 층에서 고르게 뽑은 200개(같은 비중 {pct(stv.amt_share)}, 상위 200개와 {int(stv.overlap_top200)}개 겹침). "
          f"5개월 시차 오차: 상위 200개 기계학습 {pct(t2.loc[('M2', 5), 'wape'])} 대 작년 같은 달 {pct(t2.loc[('M0', 5), 'wape'])}, 층화 200개 {pct(st.loc[('M2', 5), 'wape'])} 대 {pct(st.loc[('M0', 5), 'wape'])}. "
          f"목록 정보 추가: 상위 200개 {lab(t2c.loc[(5, 'M3', 'M2'), 'verdict_holm'])}, 층화 200개 {lab(stc.loc[(5, 'M3', 'M2'), 'verdict_holm'])}. "
          "**200개로도 남는 한계**: 96개월 연속 관측 조건의 생존 편향, 업체 2곳 이하 성분은 사용통계에서 비공개(제품 하나의 변화가 성분 합계를 움직일 곳이 빠짐), "
          "성분 합계라 성분 안의 처방 이동이 안 보임, 예측 개선 여부로 효과를 재는 시험의 낮은 검출력")
    rows = [
        (1, "성분 합계만 있어 제품별 처방 비중을 볼 수 없다", "제품별 처방 비중 분석(제품 단위 자료 확보 후)", "인하 예고 제품의 성분 안 비중이 예고 전후로 달라지는가", "이 자료로 불가. 사용통계에는 제품 단위 처방량이 없다(원본 96개 파일이 주성분 4자리까지만 있음). 제품 단위 처방 자료가 있어야 한다"),
        (2, "사용통계는 공개 뒤에 늘어날 수 있다(판본마다 심사완료분까지만 반영하는 공식 작성 기준, 이후 변동 가능)", "같은 달 자료를 매달 다시 내려받아 판본을 쌓고 수정 폭과 안정 기간을 측정, 완성 계수(공개 후 경과 개월별로 최종값의 몇 %가 집계되는지)로 최근 달 보정", "같은 달의 두 판본 차이가 공개 후 경과 개월에 따라 줄어드는가", r2),
        (3, "몇 개월 앞까지 쓸 만한지 몰랐다", "1~12개월 앞 오차 곡선 (32개, 상위 200개)", "1개월 앞 대비 오차가 10% 넘게 커지는 첫 개월, 작년 같은 달 값보다 오차가 작은 마지막 개월", r3),
        (4, "대상이 호흡기·알레르기 계열 32개 성분뿐이다", "상위 200개 + 층화 200개 성분", "5개월 시차에서 결론(작년 값보다 나쁜가, 목록 정보가 개선하는가)이 유지되는가", r4),
        (5, "전국 합계만 예측했다", "시도별 예측", "시도별 합이 전국 예측보다 나은가", "향후 과제"),
        (6, "약국 단위 자료가 없다", "약국 주문·판매 자료에 같은 방법 적용", "바로 쌓이는 자료로 작년 같은 달 값을 몇 개월 앞까지 이기는가", "향후 과제"),
        (7, "알려진 가격 변화를 예측에 직접 쓰지 않았다", "알려진 가격 변화를 공식으로 반영하는 모델(새 검증 기간에서)", "새 검증 기간에서 청구금액 오차가 줄어드는가", "향후 과제"),
        (8, "급여목록에 없는 집행정지는 놓친다. 외부 근거는 검증 표본에 걸린 1건만 넣었다", "외부 집행정지 체계 탐색(법원 공시·보도, 사용자가 준 자료로)", "놓친 집행정지 건수와 막힌 비율의 변화(양방향)", "향후 과제"),
        (9, "공급 중단 정보가 없다", "공급 중단 외부 자료 연결", "공급 중단 성분의 오차가 줄어드는가", "향후 과제"),
        (10, "자동 판정의 정답을 한 사람이 정했다", "두 번째 라벨러", "두 사람의 정답 일치율(우연히 맞을 확률을 뺀 일치도, 카파)", "향후 과제"),
        (11, "제품 기준 비율과 검정이 서로 독립이라고 가정한다(같은 처분의 제품, 같은 달의 성분)", "처분 단위 집계(이번에 함) + 예측 시점 묶음 부트스트랩", "처분 단위 비율, 묶음 부트스트랩 구간이 결론을 바꾸는가", "처분 단위는 이번에 보고. 부트스트랩은 향후 과제"),
        (12, "비교 기준이 작년 같은 달 값 하나뿐이다", "단순 시계열 기준(예: 지수평활) 추가", "기계학습 모델이 단순 시계열 기준보다 나은가", "향후 과제"),
        (13, "코로나 표시는 2020~2021만이라 회복기(2022)는 표시되지 않는다", "회복기 표시 또는 2022 제외 민감도", "2023년 상반기 오차 차이가 줄어드는가", "향후 과제"),
        (14, "판정 규칙을 고친 판(v5.3)의 정밀도를 따로 재지 않았다", "판정 결과와 규칙 용어를 가린 새 검증 표본 30건", "유형 일치율(층별)", "라벨 화면만 만듦, 라벨 대기"),
        (15, "집행정지 뒤 본안에서 처분이 취소되는 결말을 기록할 칸이 없다", "본안 결말 칸 추가(규칙 변경)", "'실행 안 됨' 가운데 본안 취소의 몫", "향후 과제"),
        (16, "급여정지(처분)와 급여중지(허가 관련)의 사유가 섞였다", "사유별 분해(비고 원문 또는 외부 자료 필요)", "사유별 집행정지 비율", "향후 과제"),
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
