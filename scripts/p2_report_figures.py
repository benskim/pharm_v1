"""외부 보고서 그림 4개(한·영 SVG)와 거리별 그림의 글자 수정. 값은 outputs/에서 읽는다(가격 예고는 ev_v5에서 셈).
실행: python scripts/p2_report_figures.py <ko images 폴더> <en images 폴더>"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INK, SUB, GRID, PINE = "#14231E", "#5A6B64", "#E1EAE5", "#0E5A43"
BLUE, ORANGE, AQUA, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#A9B9B1"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;")


def T(x, y, s, size=13, fill=SUB, anchor="start", w=400):
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{w}">{esc(s)}</text>'


def svg(h, title, sub, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 {h}" role="img" aria-label="{esc(title)}" '
            f'font-family="Pretendard, \'Noto Sans CJK KR\', \'Apple SD Gothic Neo\', sans-serif">\n<rect width="760" height="{h}" fill="#ffffff"/>\n'
            + T(20, 28, title, 17, INK, w=700) + T(20, 50, sub) + "\n" + "\n".join(body) + "\n</svg>\n")


def bar(x, y, w, h, c):
    r = min(4, w / 2)
    return f'<path d="M{x:.1f} {y:.1f}h{w - r:.1f}q{r} 0 {r} {r}v{h - 2 * r}q0 {r} -{r} {r}h-{w - r:.1f}z" fill="{c}"/>'


def classes(L, d):
    n = dict(C10=("지질조절제", "lipid modifiers"), B01=("항혈전제", "antithrombotics"), C08=("칼슘채널차단제", "calcium blockers"), C09=("레닌-안지오텐신계", "renin-angiotensin"),
             S01=("안과용제", "ophthalmologicals"), R03=("기도질환용제", "airway drugs"), J01=("전신용 항균제", "antibacterials"), R05=("기침·감기 제제", "cough & cold"))
    b, X, Y = [], lambda v: 190 + v * 100 * 17, 92
    for v in range(0, 25, 5):
        b += [f'<line x1="{X(v / 100):.1f}" x2="{X(v / 100):.1f}" y1="78" y2="376" stroke="{GRID}"/>', T(X(v / 100), 394, f"{v}%", 12, SUB, "middle")]
    b += [f'<circle cx="196" cy="66" r="5" fill="{PINE}"/>', T(206, 70, L("기계학습", "Machine learning"), 12), f'<circle cx="336" cy="66" r="4.5" fill="#fff" stroke="{ORANGE}" stroke-width="2.5"/>', T(346, 70, "ETS", 12)]
    for i, (k, nm) in enumerate(n.items()):
        y = Y + i * 34 + (18 if i >= 4 else 0)
        g, e = d.loc[k, "wape_G"], d.loc[k, "wape_B3"]
        b += [T(180, y + 4, f"{k} {L(*nm)}", 13, INK, "end"), f'<line x1="{X(min(g, e)):.1f}" x2="{X(max(g, e)):.1f}" y1="{y}" y2="{y}" stroke="{GRAY}" stroke-width="2"/>',
              f'<circle cx="{X(g):.1f}" cy="{y}" r="5" fill="{PINE}"/>', f'<circle cx="{X(e):.1f}" cy="{y}" r="4.5" fill="#fff" stroke="{ORANGE}" stroke-width="2.5"/>']
        b.append(T(X(max(g, e)) + 12, y + 4, f"{L('기계학습', 'ML')} {g:.1%} · ETS {e:.1%}", 12))
    return svg(410, L("오차는 분류군마다 크게 다르다: 2%대에서 20%대까지", "Error differs widely by drug class: from about 2% to over 20%"),
               L("평균 오차(WAPE), 성분 10개 이상 군 중 기계학습 오차가 가장 작은 4개와 큰 4개", "WAPE; 4 lowest and 4 highest by machine learning error, classes with 10+ ingredients"), b)


def winter(L, d):
    cs = [c for c in d if c.startswith("h2")]
    X, Y = lambda i: 70 + i * 87, lambda v: 330 - (v - .75) * 140
    b = [f'<rect x="{X(1.5):.1f}" y="76" width="{X(3.5) - X(1.5):.1f}" height="{330 - 76}" fill="#F3F6F4"/>', T(X(2.5), 322, L("코로나 기간에 걸친 겨울", "Winters in the COVID-19 period"), 12, SUB, "middle")]
    for v in (1, 1.5, 2, 2.5):
        b += [f'<line x1="60" x2="610" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{SUB if v == 1 else GRID}"/>', T(52, Y(v) + 4, L(f"{v:g}배", f"{v:g}×"), 12, SUB, "end")]
    b.append(T(66, Y(1) + 16, L("1배 = 겨울과 여름이 같음", "1× = winter equals summer"), 11))
    b += [T(X(i), 352, c[3:5] + "/" + str(int(c[3:5]) + 1), 12, SUB, "middle") for i, c in enumerate(cs)]
    for k, c, dy in (("R05", BLUE, -4), ("R03", AQUA, 12), ("J01", ORANGE, 12)):
        v = np.exp(d.loc[k, cs].to_numpy(float))
        p = " ".join(f"{X(i):.1f},{Y(x):.1f}" for i, x in enumerate(v))
        b.append(f'<polyline fill="none" stroke="{c}" stroke-width="2" stroke-linejoin="round" points="{p}"/>')
        b += [f'<circle cx="{X(i):.1f}" cy="{Y(x):.1f}" r="4.5" fill="{c}" stroke="#fff" stroke-width="2"/>' for i, x in enumerate(v)]
        b += [f'<circle cx="612" cy="{Y(v[-1]) + dy - 4:.1f}" r="4" fill="{c}"/>', T(622, Y(v[-1]) + dy, L(f"{k} {v[-1]:.2f}배", f"{k} {v[-1]:.2f}×"), 13, INK, w=600)]
        if k == "R05":
            b += [T(X(3), Y(v[3]) - 10, f"{v[3]:.2f}", 12, INK, "middle", 600), T(X(0) + 8, Y(v[0]) - 8, f"{v[0]:.2f}", 12, INK, w=600)]
    return svg(368, L("겨울 처방이 여름의 몇 배인지는 해마다 달랐다", "How far winter exceeded summer differed from year to year"),
               L("11~3월 평균 ÷ 직전 6~8월 평균(기하평균, 금액 가중). 관측된 현상이며 원인은 확인하지 않았다", "Nov-Mar mean ÷ preceding Jun-Aug mean (geometric, amount-weighted). Observed only; cause not examined"), b)


def s01(L, t):
    r = [(L("히알루론산", "Sodium hyaluronate"), t.loc["5300"], PINE), (L("빌베리", "Bilberry extract"), t.loc["2466"], PINE), (L("올로파타딘", "Olopatadine"), t.loc["3917"], GRAY),
         (L("브리모니딘", "Brimonidine"), t.loc["1184"], GRAY), (L("토브라마이신", "Tobramycin"), t.loc["2408"], GRAY)]
    r.append((L("나머지 15개 성분", "15 other ingredients"), 1 - sum(x[1] for x in r), GRAY))
    b = []
    for i, (n, v, c) in enumerate(r):
        y = 76 + i * 34
        b += [T(180, y + 14, n, 13, INK, "end"), bar(190, y, v * 780, 20, c), T(198 + v * 780, y + 15, f"{v:.0%}", 13, INK, w=600)]
    return svg(290, L("S01 오차의 71%는 두 성분에서 나온다", "Two ingredients make up 71% of the S01 error"),
               L("S01 안과용제 기계학습 오차의 성분별 몫(성분 20개)", "Share of machine learning error by ingredient, S01 ophthalmologicals (20 ingredients)"), b)


def price(L, q):
    seg = [(L("비고에 예고됨", "Noted in remarks"), q["noted"], PINE), (L("일괄 인하 세 달, 비고 없음", "Bulk-cut months, no note"), q["bulk"], ORANGE), (L("그 밖, 비고 없음", "Other, no note"), q["other"], GRAY)]
    n, b, x = sum(s[1] for s in seg), [], 20.0
    for nm, v, c in seg:
        w = v / n * 720
        b += [f'<rect x="{x:.1f}" y="74" width="{w - 2:.1f}" height="24" fill="{c}"/>']
        b += [f'<rect x="{x:.1f}" y="110" width="10" height="10" fill="{c}"/>', T(x + 15, 120, nm, 12, INK), T(x, 140, f"{v:,}{L('건', '')} ({v / n:.1%})", 13, INK, w=600)]
        x += w
    return svg(158, L("가격 변경 세 건 중 두 건은 비고에 예고가 없었다", "Two in three price changes had no note in the remarks"),
               L(f"가격이 바뀐 제품별 변경 {n:,}건, 2022-01 ~ 2025-12", f"{n:,} product-level price changes, 2022-01 to 2025-12"), b)


def main(ko, en):
    d = pd.read_csv(ROOT / "outputs/p2v3/group_atc2.csv", encoding="utf-8-sig").set_index("group")
    a = pd.read_csv(ROOT / "outputs/p2_diag/d8a.csv").set_index("group")
    t = pd.read_csv(ROOT / "outputs/p2_diag/top_ingredients.csv", dtype={"c4": str}).query("group == 'S01' and model == 'G'").set_index("c4").share
    c = duckdb.connect(str(ROOT / "data/interim/pbd.duckdb"), read_only=True)
    x = c.execute("""select count(*) n, count(*) filter (signal_id is null) u,
        count(*) filter (signal_id is null and batch) bk from ev_v5 where event_type = 'PRICE_CHANGE' and retracted_month is null""").fetchone()
    q = dict(noted=x[0] - x[1], bulk=x[2], other=x[1] - x[2])
    for lang, out in (("ko", ko), ("en", en)):
        L = (lambda k, e: k) if lang == "ko" else (lambda k, e: e)
        for f, s in (("class-error", classes(L, d)), ("winter-height", winter(L, a)), ("s01-share", s01(L, t)), ("price-notes", price(L, q))):
            (Path(out) / f"{f}.svg").write_text(s, encoding="utf-8")
    h = (Path(ko) / "horizon-curve.svg").read_text(encoding="utf-8")
    h = h.replace("멀리 볼수록 오차가 커지고, 기계학습의 이점은 2~7개월 앞뿐이다", "ETS와 기계학습은 멀리 볼수록 오차가 커진다").replace("몇 개월 앞을 예측했는지에 따른 평균 오차. 기계학습의 이점은 2~7개월 앞뿐이다", "몇 개월 앞을 예측했는지에 따른 평균 오차")
    h = h.replace("몇 개월 앞을 예측했는지에 따른 평균 오차(WAPE), 성분 501개", "평균 오차(WAPE), 성분 501개. 6개월 앞의 ETS·기계학습 차이는 주 분석에서 우연과 구분되지 않았다")
    (Path(ko) / "horizon-curve.svg").write_text(h, encoding="utf-8")
    for k, e in (("평균 오차(WAPE), 성분 501개. 6개월 앞의 ETS·기계학습 차이는 주 분석에서 우연과 구분되지 않았다", "WAPE, 501 ingredients. In the main analysis, the 6-month gap could not be told apart from chance"),
                 ("몇 개월 앞을 예측했는지에 따른 평균 오차", "Average error by forecast horizon"), ("ETS와 기계학습은 멀리 볼수록 오차가 커진다", "ETS and machine learning: error grows with distance"), ("몇 개월 앞을 예측했나 (개월)", "Months ahead"),
                 ("실무 기준: 6개월 앞", "Practical target: 6 months"), ("작년 같은 달 값", "Same month last year"), ("기계학습", "Machine learning"), ("개월 앞:", " months ahead:")):
        h = h.replace(k, e)
    h = h.replace("Same month last year 9.1%", "Last year 9.1%")
    (Path(en) / "horizon-curve.svg").write_text(h, encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:3])
