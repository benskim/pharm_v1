"""S2 통합 시험: make p1-s2로 만든 data/interim/pbd.duckdb를 검사한다.

기대값 출처
- 원본 행 수·non_product 행 수: data/raw 원본을 이 시험에서 직접 센다
- 2025-11 '25.11.23 등재' 27행 = 처음 관측 17 + 기존 10: docs/NOTE_RULES.md R1, PLAN_P1 F11
- F5 집행정지 구간, F6 속성 변화: reference/precheck_v3_output.md
- F5 급여정지 구간: reference와의 차이가 규칙(미확인 SUSPEND_SCHEDULED, 한 목록에만 남은 정지 토큰)으로만 생기는지
  원본 비고에서 직접 판정한다
- 1개월 공백 실사례 644914120·644914130·644914140 (2022-11): DECISIONS 2026-11-14 3차
"""
import duckdb
import pytest

from pbd.common import DB_PATH, RAW, ROOT

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="pbd.duckdb 없음 (make p1-s1, p1-s2)")


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(str(DB_PATH), read_only=True)
    c.execute("SET enable_progress_bar=false")
    if not c.execute("select count(*) from information_schema.tables where table_name = 'product_state_month'").fetchone()[0]:
        pytest.skip("product_state_month 없음 (make p1-s2)")
    yield c
    c.close()


def _raw_benefit():
    return f"read_parquet('{(RAW / 'benefit').as_posix()}/benefit_snapshot_table_*.parquet', union_by_name=true)"


def test_rows_equal_raw_minus_non_product(con):
    c = duckdb.connect()
    try:
        n_raw, n_bad = c.execute(f"""select count(*), count(*) filter (where not regexp_full_match(coalesce(trim(제품코드), ''), '[0-9]{{8,9}}'))
                                     from {_raw_benefit()}""").fetchone()
    finally:
        c.close()
    assert con.execute("select count(*) from product_state_month").fetchone()[0] == n_raw - n_bad
    assert con.execute("select count(*) from (select product_code, list_month from product_state_month group by all having count(*) > 1)").fetchone()[0] == 0


def test_value_domains(con):
    bad = con.execute("""select count(*) from product_state_month
        where state_asof not in ('reimbursed', 'scheduled_listing', 'suspended', 'unpriced')
           or state_eom not in ('reimbursed', 'scheduled_listing', 'suspended', 'unpriced')
           or exposure_frac < 0 or exposure_frac > 1
           or (product_role is not null and product_role not in ('reference', 'bioequiv'))
           or (role_source is not null and role_source not in ('observed', 'carried', 'orangebook'))
           or ((product_role_carried is null) <> (role_source is null))
           or (not in_substitute_list and (sub_ref_drug is not null or sub_bioeq is not null or sub_equiv is not null or is_bioequiv is not null))""").fetchone()[0]
    assert bad == 0


def test_listing_2025_11(con):
    got = dict(con.execute("""select p.state_asof, count(*) from product_state_month p join stg_benefit b using (src_file, src_row)
                              where b.list_month = '2025-11' and b.note like '%25.11.23%등재%' group by 1""").fetchall())
    assert got == {"scheduled_listing": 17, "reimbursed": 10}


def _reference_block(title):
    text = (ROOT / "reference/precheck_v3_output.md").read_text(encoding="utf-8")
    block = text.split(f"## {title}\n")[1].split("\n## ")[0].strip().splitlines()
    return [tuple(x.strip() for x in line.split("|")) for line in block[1:]]


F5_SQL = """
    with w as (select p.*, b.note from product_state_month p join stg_benefit b using (src_file, src_row)
               where p.list_month between '2024-01' and '2025-12'),
         mi as (select list_month, row_number() over (order by list_month) mi from (select distinct list_month from w)),
         ww as (select w.*, mi from w join mi using (list_month)),
         life as (select product_code, min(mi) f, max(mi) l from ww group by 1),
         s as (select product_code, min(mi) a, max(mi) z from ww where {cond} group by 1)
    select product_code, case when a = 1 then '창 시작 전부터' else '창 안에서 시작' end st,
           case when z = 24 then '창 끝까지' when l = z then '정지 중 목록 삭제' else '해제' end en
    from s join life using (product_code)"""


def _f5_counts(rows):
    out = {}
    for _, st, en in rows:
        out[(st, en)] = out.get((st, en), 0) + 1
    return out


def test_f5_stay_segments_equal_reference(con):
    want = {(st, en): int(n) for st, en, n in _reference_block("F5 집행정지 구간")}
    assert _f5_counts(con.execute(F5_SQL.format(cond="stay_flag")).fetchall()) == want


def test_f5_suspension_reference_reproduced_from_notes(con):
    """reference F5의 정의(급여목록 비고 문자열)를 이 DB의 급여목록 행에 적용하면 reference와 같다 (자료 대조)"""
    want = {(st, en): int(n) for st, en, n in _reference_block("F5 급여정지 구간")}
    cond = "note like '%급여정지%' and note not like '%해제%'"
    assert _f5_counts(con.execute(F5_SQL.format(cond=cond)).fetchall()) == want


def test_f5_suspension_state_differs_only_by_rules(con):
    """state_eom 기준 급여정지 구간과 reference 정의의 차이는 두 가지 규칙으로만 생긴다.
    (1) 미래 날짜 정지 토큰이 다음 스냅샷에서 확인되지 않음 (SUSPEND_SCHEDULED 미실현 → 상태 변화 없음)
    (2) 급여목록 비고에는 없고 대체조제 목록 비고에만 정지 토큰이 남음 (한 목록에만 남은 토큰 = 복귀 아님)
    """
    ref = {r[0]: r[1:] for r in con.execute(F5_SQL.format(cond="note like '%급여정지%' and note not like '%해제%'")).fetchall()}
    mine = {r[0]: r[1:] for r in con.execute(F5_SQL.format(cond="state_eom = 'suspended'")).fetchall()}
    diff = sorted(p for p in set(ref) | set(mine) if ref.get(p) != mine.get(p))
    assert diff, "차이가 없으면 이 시험의 전제가 바뀐 것이다"
    for pc in diff:
        # (1) 표본 창의 급여목록 정지 토큰이 모두 미래 날짜이고, 다음 달에 같은 토큰이 없다
        unconfirmed = con.execute("""
            with t as (select list_month, token_date, is_future from note_token
                       where source_list = 'benefit' and product_code = ? and token_type in ('R3', 'R3b')
                         and list_month between '2024-01' and '2025-12')
            select count(*) > 0 and bool_and(is_future) and bool_and(not exists (
                select 1 from note_token n where n.product_code = ? and n.token_type in ('R3', 'R3b')
                  and n.token_date = t.token_date and n.list_month = strftime(cast(t.list_month || '-01' as date) + interval 1 month, '%Y-%m')))
            from t""", [pc, pc]).fetchone()[0]
        # (2) state_eom이 suspended인데 급여목록 비고에 정지 토큰이 없는 달이 있고, 그 달 대체조제 목록에는 있다
        sub_only = con.execute("""
            select count(*) > 0 from product_state_month p
            where p.product_code = ? and p.state_eom = 'suspended' and p.list_month between '2024-01' and '2025-12'
              and not exists (select 1 from note_token t where t.source_list = 'benefit' and t.product_code = p.product_code
                              and t.list_month = p.list_month and t.token_type in ('R3', 'R3b'))
              and exists (select 1 from note_token t where t.source_list = 'substitute' and t.product_code = p.product_code
                          and t.list_month = p.list_month and t.token_type in ('R3', 'R3b'))""", [pc]).fetchone()[0]
        assert unconfirmed or sub_only, pc


def test_f6_attribute_changes_equal_reference(con):
    (want,) = [tuple(int(x) for x in r) for r in _reference_block("F6 시점에 따라 바뀌는 속성(제품 수)")]
    got = con.execute("""with v as (select * from product_attr_scd2 where valid_from_month <= '2025-12' and valid_to_month >= '2024-01')
        select count(*) filter (where nf > 1), count(*) filter (where nn > 1), count(*) filter (where ng > 1)
        from (select product_code, count(distinct firm) nf, count(distinct product_name) nn, count(distinct ingredient_code) ng
              from v group by 1)""").fetchone()
    assert got == want


@pytest.mark.parametrize("pc", ["644914120", "644914130", "644914140"])
def test_one_month_gap_real_cases(con, pc):
    r = con.execute("""select list_month, state_eom, state_revised from product_state_month
                       where product_code = ? and list_month between '2022-10' and '2022-12' order by 1""", [pc]).fetchall()
    assert r == [("2022-10", "suspended", False), ("2022-11", "suspended", False), ("2022-12", "suspended", False)]
    # 2022-11에는 두 목록 모두 정지 토큰이 없다 (시험 전제)
    assert con.execute("""select count(*) from note_token where product_code = ? and list_month = '2022-11'
                          and token_type in ('R3', 'R3b')""", [pc]).fetchone()[0] == 0


def test_role_matches_substitute_values(con):
    """대체조제 목록에 있는 달: 대조약 ○ → reference, 아니고 생동인정 ○ → bioequiv. is_bioequiv = 생동인정 ○ (stg와 직접 대조)"""
    bad = con.execute("""select count(*) from product_state_month p join stg_substitute s using (product_code, list_month)
        where s.row_kind = 'product_candidate' and (
              p.product_role is distinct from case when s.ref_drug then 'reference' when s.bioeq then 'bioequiv' end
           or p.is_bioequiv is distinct from s.bioeq or p.role_source <> 'observed' or p.product_role_carried is distinct from p.product_role
           or p.sub_equiv is distinct from s.equiv)""").fetchone()[0]
    assert bad == 0
    # 의약동등 = 6 → reference + is_bioequiv
    assert con.execute("""select count(*) from product_state_month where sub_equiv = '6'
                          and not (product_role = 'reference' and is_bioequiv)""").fetchone()[0] == 0


def test_role_orangebook_only(con):
    """대체조제 목록에 없는 달: 오렌지북 공고대조약 O → reference(orangebook), 제네릭의약품 표시만으로는 NULL"""
    bad = con.execute("""select count(*) from product_state_month where not in_substitute_list and (
              product_role is distinct from case when ob_ref then 'reference' end
           or (ob_generic and not coalesce(ob_ref, false) and product_role is not null))""").fetchone()[0]
    assert bad == 0


def test_late_release_656700050(con):
    """2025-12 비고의 해제일 2025-11-12 (사용자 지시 2026-11-14): 11월 기준일 시점은 정지, 월말은 복귀, exposure 19/30"""
    r = con.execute("""select state_asof, state_eom, exposure_frac, late_note_revised from product_state_month
                       where product_code = '656700050' and list_month = '2025-11'""").fetchone()
    assert r[:2] == ("suspended", "reimbursed") and r[2] == pytest.approx(19 / 30) and r[3]


def test_suspend_signal_check_complete(con):
    """미래 날짜 정지 토큰마다 확인 결과가 하나. 확인 = 확인 스냅샷에 같은 토큰 (note_token에서 직접 센다)"""
    n_tok = con.execute("""select count(*) from (select distinct product_code, list_month, token_type, token_date, token_date_end
                           from note_token where token_type in ('R3', 'R3b') and is_future)""").fetchone()[0]
    rows = dict(con.execute("select confirm_status, count(*) from s2_suspend_signal_check group by 1").fetchall())
    assert sum(rows.values()) == n_tok
    bad = con.execute("""select count(*) from s2_suspend_signal_check where (confirm_status = 'confirmed') <> coalesce(token_in_check, false)
                         or (confirm_status = 'not_confirmed') <> (reason is not null)
                         or (confirm_status = 'confirmed_other_date') <> (date_gap_days is not null)
                         or confirm_status not in ('confirmed', 'confirmed_other_date', 'unverifiable', 'not_confirmed', 'censored')""").fetchone()[0]
    assert bad == 0


@pytest.fixture(scope="module")
def truncated(con):
    """2023-06 이후 스냅샷을 지운 L1으로 S2를 다시 만든다 (메모리)"""
    from pbd.common import connect
    from pbd.ledger import s2_state as S2
    cut = "2023-06"
    c = connect(None)
    c.execute(f"attach '{DB_PATH.as_posix()}' as src (read_only)")
    for t in ("stg_benefit", "stg_substitute", "note_token"):
        c.execute(f"create table {t} as select * from src.{t} where list_month <= '{cut}'")
    for t in ("attr_2026_product", "attr_2026_item"):
        c.execute(f"create table {t} as select * from src.{t}")
    S2.build_all(c)
    yield c, cut
    c.close()


def test_state_asof_ignores_later_snapshots_real(con, truncated):
    c, cut = truncated
    q = "select product_code, list_month, state_asof, stay_flag, in_substitute_list, product_role, product_role_carried from product_state_month where list_month <= ? order by 1, 2"
    a = c.execute(q, [cut]).fetchall()
    b = con.execute(q, [cut]).fetchall()
    assert len(a) == len(b) and a == b


def test_external_evidence_kyongbo(con):
    """2025-06 경보제약 23건 (사용자 결정 2026-11-15): reason은 원본 사유 note_gone(7월 제품 있고 비고 없음),
    외부 원인 "집행정지"와 링크는 reason_external, reason_source = data+external. evidence는 데이터 근거만"""
    rows = con.execute("""select reason, reason_source, evidence, reason_external from s2_suspend_signal_check
                          where signal_month = '2025-06' and token_date = '2025-06-24'""").fetchall()
    assert len(rows) == 23
    for reason, src, ev, ext in rows:
        assert (reason, src) == ("note_gone", "data+external")
        assert ev == "2025-07 제품 있음, 정지·집행정지 비고 없음"
        assert ext.startswith("집행정지: ")
        for url in ("https://dailypharm.com/user/news/4876", "https://dailypharm.com/user/news/4216",
                    "https://www.medigatenews.com/news/1252800688"):
            assert url in ext
    # 원본이 아닌 곳에서 사유를 정한 신호는 없다
    assert con.execute("""select count(*) from s2_suspend_signal_check where outcome = 'not_realized'
                          and reason_source not in ('data', 'data+external')""").fetchone()[0] == 0


def test_shifted_date_2022_09(con):
    """2022-09 예고 9/8 → 확인 9/9: realized_partial, +1일, 이벤트 유효일 9/9 (사용자 결정 9차)"""
    rows = con.execute("""select outcome, date_gap_days, event_effective_date from s2_suspend_signal_check
                          where signal_month = '2022-09' and token_date = '2022-09-08'""").fetchall()
    assert len(rows) == 39
    assert set(rows) == {("realized_partial", 1, __import__("datetime").date(2022, 9, 9))}
    # 예고 날짜가 이벤트 유효일로 쓰이는 것은 같은 날짜로 확인된 경우뿐
    assert con.execute("""select count(*) from s2_suspend_signal_check
                          where event_effective_date = token_date and outcome <> 'realized'""").fetchone()[0] == 0


def test_nominal_base_date_comments(con):
    c = dict(con.execute("""select table_name || '.' || column_name, comment from duckdb_columns()
                            where table_name in ('snapshot_inventory', 'note_token', 'product_state_month')""").fetchall())
    # 사용자 결정 2026-11-15: 적용 기준일은 항상 그 달 1일
    assert c["snapshot_inventory.base_date"].startswith("적용 기준일: 항상 그 달 1일")
    assert "그 달 1일" in c["note_token.is_future"]
    assert "그 달 1일" in c["product_state_month.state_asof"]
    assert "그 달 1일" in c["product_state_month.stay_flag"]
    assert con.execute("select count(*) from snapshot_inventory where base_date <> date_trunc('month', base_date)").fetchone()[0] == 0


def test_donga_2022_05_evidence(con):
    """2022-05 동아에스티 72건: not_realized, 원본 사유 stay(확인 스냅샷의 집행정지 비고), reason_source = data+external.
    외부 원인·링크는 reason_external (사용자 결정 10차 → 2026-11-15에 칸을 나눔)"""
    rows = con.execute("""select outcome, reason, reason_source, evidence, reason_external from s2_suspend_signal_check
                          where signal_month = '2022-05' and token_type = 'R3b' and token_date = '2022-08-01'""").fetchall()
    assert len(rows) == 72
    for outcome, reason, src, ev, ext in rows:
        assert (outcome, reason, src) == ("not_realized", "stay", "data+external")
        assert ev.startswith("2022-08 집행정지 비고")
        assert "https://dailypharm.com/user/news/39450" in ext and "https://www.dailypharm.com/user/news/33781" in ext

