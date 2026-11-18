"""P1 원장 v5 빌드 (docs/RULES_2026-11-17.md). 규칙은 pbd.ledger.v5_rules (순수 함수).

입력 (data/interim/pbd.duckdb, S1·S2 결과): stg_benefit, stg_substitute, note_token, bridge_product_item, bridge_code_recode,
product_attr_scd2 (ATTR_CHANGE). 기존 v4 원장 표(product_state_month, signal_ledger, signal_outcome, event_ledger)는 고치지 않는다
(개정 전 비교용).

출력 (같은 DB)
- product_state_month_v5 : 제품×월 (급여목록에 처음 실린 달부터 분석 끝까지). as_known / revised 상태, 약가 유형, 집행정지 표시, subst_lag
- state_revision_log_v5  : 정정 이력 (as_known ≠ revised)
- stay_v5                : 집행정지 (급여목록 R4), stay_target_v5: 집행정지 → 막은 신호
- succession_v5          : 코드 이어받기 쌍 (1:1)
- signal_v5              : 신호와 결과 (한 행)
- event_ledger_v5        : 이벤트 (known_month / retracted_month: as_known 보기는 known ≤ M < retracted)
표: outputs/v5/

사용: PYTHONPATH=src python -m pbd.ledger.v5_build   (make p1-v5)
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import asdict

import pandas as pd

from pbd.common import ROOT, connect, load_yaml
from pbd.ledger.v5_rules import (
    ONGOING_STAY_TEXT, Event, Life, Obs, Stay, month_of, stay_records, Tok, base, check_transitions, derive_signals, derive_stays, derive_status, exit_reentry_events, flag_batch,
    link_codes, price_events, price_type, state_of,
)

OUT = ROOT / "outputs/v5"
PARAMS = load_yaml("params.yaml")
EXCLUDE_LABEL = {"listed_during_suspension": "급여중지 중 예고", "suspended_before_price_date": "인하일 전 급여중지", "target_gone": "대상 소멸"}


def _d(x):
    if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NaT:
        return None
    if isinstance(x, pd.Timestamp):
        return x.date()
    return x


def load_inputs(con):
    months = [r[0] for r in con.execute("select distinct list_month from stg_benefit order by 1").fetchall()]
    b = con.execute("""select product_code, list_month, price, unit, firm, product_name, ingredient_code, c4
                       from stg_benefit where row_kind = 'product_candidate'""").fetchall()
    s = con.execute("""select product_code, list_month, min(price) price from stg_substitute
                       where row_kind = 'product_candidate' group by 1, 2""").fetchall()
    t = con.execute("""select distinct product_code, list_month, source_list, token_type, token_date, token_date_end, amount
                       from note_token where token_type in ('R1','R2','R3','R3b','R4','R5','R6','R7','R9')""").fetchall()
    items = dict(con.execute("""select product_code, min(item_code) from bridge_product_item group by 1
                                having count(distinct item_code) = 1""").fetchall())
    recode = defaultdict(set)
    for old, new in con.execute("select old_code, new_code from bridge_code_recode where old_in_benefit").fetchall():
        recode[new].add(old)
    return months, b, s, t, items, recode


def build_obs(months, b, s, t):
    bm = {}
    attrs = defaultdict(dict)
    for pc, m, price, unit, firm, name, ing, c4 in b:
        bm[(pc, m)] = (price, unit)
        attrs[pc][m] = (firm, name, ing, c4)
    sm = {(pc, m): price for pc, m, price in s}
    toks = defaultdict(list)
    for pc, m, src, typ, d, d2, amt in t:
        toks[(pc, m)].append(Tok(typ, _d(d), _d(d2) if typ == "R3b" else None, float(amt) if amt is not None else None,
                                 "B" if src == "benefit" else "S"))
    obs = defaultdict(dict)
    for key in set(bm) | set(sm):
        pc, m = key
        pb = bm.get(key)
        obs[pc][m] = Obs(in_b=pb is not None, in_s=key in sm, price_b=float(pb[0]) if pb and pb[0] is not None else None,
                         price_s=float(sm[key]) if sm.get(key) is not None else None, unit=pb[1] if pb else None,
                         toks=tuple(sorted(toks.get(key, ()), key=lambda z: (z.type, z.src, z.date or dt.date.min))))
    return obs, attrs


def external_config():
    cfg = load_yaml("external_evidence.yaml")
    lift = defaultdict(dict)
    for e in cfg.get("suspend_lift", []):
        lift[e["product_code"]][dt.date.fromisoformat(e["mark_date"])] = dt.date.fromisoformat(e["lift_date"])
    targets = []   # (조건, 신호 유형, 날짜들, id)
    for e in cfg.get("suspend_signal", []):
        if e.get("stay_target"):
            mt = e["match"]
            targets.append(("SUSPEND_NOTICE", mt["signal_month"], dt.date.fromisoformat(mt["token_date"]),
                            tuple(dt.date.fromisoformat(x) for x in e["stay_target"]), e["id"]))
    for e in cfg.get("price_signal", []):
        if e.get("stay_target"):
            mt = e["match"]
            targets.append(("PRICE_NOTICE", mt["issued_month"], dt.date.fromisoformat(mt["signal_date"]),
                            tuple(dt.date.fromisoformat(x) for x in e["stay_target"]), e["id"]))
    ext_stays = []
    for e in cfg.get("external_stay", []):
        ext_stays.append(dict(e, signal_date=dt.date.fromisoformat(e["signal_date"]), stay_date=dt.date.fromisoformat(e["stay_date"]),
                              stay_decision_date=dt.date.fromisoformat(e["stay_decision_date"]) if e.get("stay_decision_date") else None))
    return lift, targets, ext_stays


def build(con) -> dict:
    months, b, s, t, items, recode = load_inputs(con)
    obs, attrs = build_obs(months, b, s, t)
    lift_cfg, target_cfg, ext_stay_cfg = external_config()
    codes = sorted(obs)

    # 1. 상태 (급여목록만)
    status = {pc: derive_status(months, obs[pc], external_lift=lift_cfg.get(pc)) for pc in codes}

    # 2. 코드 이어받기
    lives = {}
    for pc in codes:
        ms = tuple(m for m in months if obs[pc].get(m) and obs[pc][m].in_b)
        if not ms:
            continue
        a0, a1 = attrs[pc][ms[0]], attrs[pc][ms[-1]]
        lives[pc] = Life(ms, a0[0], a1[0], a0[1], a1[1], a0[2], a1[2], items.get(pc), frozenset(recode.get(pc, ())))
    link = link_codes(months, lives)
    succ = defaultdict(dict)
    for p in link.pairs:
        succ[p.old][p.gone_month] = (p.new, obs[p.new], status[p.new])

    # 넘겨준 쪽의 빠진 달: revised = transferred_out, as_known은 연결이 알려진 달 전까지 delisted (§3-1)
    revisions_extra = []
    for x in link.exits.values():
        if x.kind != "succession":
            continue
        st = status[x.code]
        i = months.index(x.gone_month)
        for m in months[i:]:
            r = st.rows.get(m)
            if r is None or r.in_b:
                break
            r.revised, r.sub_revised = "off_benefit", "transferred_out"
            if m >= x.pair.known_month:
                r.as_known, r.sub_as_known = "off_benefit", "transferred_out"
            else:
                r.revision_reason = "succession_found_later"
                revisions_extra.append((x.code, m, "status", "delisted", "transferred_out", x.pair.known_month,
                                        "succession_found_later"))

    # 3. 집행정지·신호
    all_stays, all_sigs = {}, {}
    ext_hits: dict[str, list] = {}
    for pc in codes:
        stays = derive_stays(months, obs[pc])
        all_stays[pc] = stays
        sigs = derive_signals(pc, months, obs[pc], status[pc], stays, successors=succ.get(pc), external_targets=None)
        # 외부 대상은 최초 기재월까지 맞는 신호에만
        if any(s.signal_type in ("PRICE_NOTICE", "SUSPEND_NOTICE") and (s.signal_type, s.first_month, s.signal_date)
               in {(a, b_, c) for a, b_, c, _, _ in target_cfg} for s in sigs):
            ext = {(a, c): ds for a, b_, c, ds, _ in target_cfg
                   if any(s.signal_type == a and s.first_month == b_ and s.signal_date == c for s in sigs)}
            sigs = derive_signals(pc, months, obs[pc], status[pc], stays, successors=succ.get(pc), external_targets=ext)
        # 외부 집행정지 (v5.3): 업체(최초 기재 달 급여목록)·신호 유형·신호 날짜가 맞는 신호를 외부 집행정지로 막힘 처리
        ext_st = {}
        for e in ext_stay_cfg:
            for g in sigs:
                f = attrs.get(pc, {}).get(g.first_month, (None,))[0]
                if g.signal_type == e["signal_type"] and g.signal_date == e["signal_date"] and f == e["firm"]:
                    ext_st[(g.signal_type, g.signal_date)] = (Stay(e["stay_date"], month_of(e["stay_date"]), None, False),)
                    ext_hits.setdefault(e["id"], []).append((pc, g.signal_id))
        if ext_st:
            ext = {(a, c): ds for a, b_, c, ds, _ in target_cfg
                   if any(s.signal_type == a and s.first_month == b_ and s.signal_date == c for s in sigs)}
            sigs = derive_signals(pc, months, obs[pc], status[pc], stays, successors=succ.get(pc), external_targets=ext or None,
                                  external_stays=ext_st)
        all_sigs[pc] = sigs

    for e in ext_stay_cfg:
        n = len(ext_hits.get(e["id"], []))
        if e.get("expected_count") is not None and n != e["expected_count"] and len(months) == 48:
            raise RuntimeError(f"외부 집행정지 {e['id']}: 맞는 신호 {n}건, 기대 {e['expected_count']}건")
    # 4. 이벤트
    ev_rows = []

    def c4_at(pc, m):
        a = attrs.get(pc, {})
        if m in a:
            return a[m][3]
        prev = [mm for mm in a if mm <= m]
        return a[max(prev)][3] if prev else (a[min(a)][3] if a else None)

    def add(pc, e, extra_id=""):
        d = asdict(e)
        d["product_code"] = pc
        d["event_id"] = f"{e.event_type}:{pc}:{e.observed_month}" + (f":{extra_id}" if extra_id else "")
        d["market_c4"] = c4_at(pc, e.observed_month)
        ev_rows.append(d)

    price_rows = []
    for pc in codes:
        st = status[pc]
        for e in st.events:
            add(pc, e)
        xr = exit_reentry_events(pc, months, obs[pc], link)
        # 유효일 규칙 1: 실현된 삭제 예고(R9)와 짝지어진 급여삭제 → 신호 날짜 (구간은 함께 남긴다)
        dl = {g.realized_month: g for g in all_sigs[pc] if g.signal_type == "DELISTING_NOTICE" and g.outcome == "REALIZED"}
        for e in xr:
            if e.event_type == "DELISTING" and e.retracted_month is None and e.observed_month in dl:
                g = dl[e.observed_month]
                e.effective_date, e.date_source, e.signal_id, e.outcome = g.signal_date, "signal_confirmed", g.signal_id, g.outcome
        for e in xr:
            add(pc, e, e.kind if e.event_type == "DELISTING" and e.kind == "confirmed_after_stay" else
                ("delisting_target" if e.event_type == "STAY_START" else ""))
        for e in price_events(pc, months, obs[pc], st, all_sigs[pc]):
            add(pc, e)
            price_rows.append(ev_rows[-1])
        # 집행정지 시작·종료
        dl_stay = {(e.observed_month, e.effective_date) for e in xr if e.event_type == "STAY_START"}
        sig_by_month = defaultdict(list)
        for sg in all_sigs[pc]:
            sig_by_month[sg.first_month].append(sg)
        for sy in all_stays[pc]:
            if (sy.first_month, sy.date) in dl_stay:
                continue
            if not (sy.left_censored and sy.date < base(months[0])):
                first_b = lives[pc].months[0] if pc in lives else None
                kind = "window_first_month" if sy.left_censored else ("at_entry" if sy.first_month == first_b else "in_window")
                add(pc, _stay_event("STAY_START", sy.first_month, sy.date, "note_benefit", kind), sy.date.isoformat())
            if sy.end_month:
                rel = [g for g in all_sigs[pc] if g.signal_type == "STAY_RELEASE_PRICE" and g.realized_month == sy.end_month
                       and g.outcome in ("REALIZED", "REALIZED_CHANGED", "REALIZED_LATE")]
                if rel:                                   # 유효일 규칙 7: 집행정지 해제·인하 예고가 실현된 달 → 그 예고일
                    eff, src = rel[0].signal_date, "signal_confirmed"
                elif sy.released_by_r5:
                    eff, src = sy.released_by_r5, "note_benefit"
                else:
                    ps = [g for g in sig_by_month.get(sy.end_month, []) if g.signal_type == "PRICE_NOTICE" and g.outcome == "REALIZED"]
                    eff, src = (min(g.signal_date for g in ps), "signal_confirmed") if ps else (base(sy.end_month), "snapshot_first")
                add(pc, _stay_event("STAY_END", sy.end_month, eff, src, "released" if (sy.released_by_r5 or rel) else "note_gone"),
                    sy.date.isoformat())
    # 등재·이어받기
    for (pc, m), en in link.entries.items():
        if en.kind == "succession":
            p = en.pair
            add(pc, Event("SUCCESSION", p.kind, m, effective_date=base(m), date_source="snapshot_first", known_month=p.known_month,
                          link_basis=p.link_basis, counterpart=p.old, gap_months=p.gap_months))
            continue
        ls = [g for g in all_sigs[pc] if g.signal_type == "LISTING_NOTICE" and g.outcome in ("REALIZED", "REALIZED_LATE")]
        past = sorted(tk.date for tk in obs[pc][m].toks if tk.type == "R1" and tk.date and tk.date <= base(m))
        if ls:
            g = min(ls, key=lambda z: z.signal_date)
            e = Event("LISTING", en.kind, m, effective_date=g.effective_date, date_source="signal_confirmed", known_month=m,
                      link_basis=en.link_basis, signal_id=g.signal_id, outcome=g.outcome)
        elif past:
            src = "note_benefit" if any(tk.type == "R1" and tk.src == "B" and tk.date == past[0] for tk in obs[pc][m].toks) else "note_substitute"
            e = Event("LISTING", en.kind, m, effective_date=past[0], date_source=src, known_month=m, link_basis=en.link_basis)
        else:
            e = Event("LISTING", en.kind, m, effective_date=base(m), date_source="snapshot_first", known_month=m, link_basis=en.link_basis)
        add(pc, e)
    # batch (§3 ★·§11-2): 그달 예고 미관측 변경의 성분4 수. 판정에 쓰지 않는 설명용 표시, 달마다 n_products·n_ingredients 저장
    p1 = PARAMS["p1"]
    flagged, bstats = flag_batch([dict(key=r["event_id"], month=r["observed_month"], c4=r["market_c4"], kind=r["kind"]) for r in price_rows],
                                 p1.get("batch_min_c4", 50), p1.get("batch_min_products", 0))
    for r in ev_rows:
        r["batch"] = (r["event_id"] in flagged) if r["event_type"] == "PRICE_CHANGE" else None
    batch_rows = [dict(observed_month=m, **v, min_c4=p1.get("batch_min_c4", 50)) for m, v in sorted(bstats.items())]

    dl_codes = {e["product_code"] for e in ev_rows if e["event_type"] == "STAY_START" and e["kind"] == "delisting_target"}
    # 외부 집행정지 시작 이벤트 (유효일 = 외부 결정일, 급여목록 비고 없음)
    for e in ext_stay_cfg:
        for pc, sid in ext_hits.get(e["id"], []):
            add(pc, Event("STAY_START", "external", month_of(e["stay_date"]), effective_date=e["stay_date"], date_source="external",
                          known_month=month_of(e["stay_date"]), signal_id=sid, stay_target=e.get("target_type"), stay_target_source="external",
                          evidence=e["note"] + " | " + " ".join(e.get("evidence", []))), "external:" + e["stay_date"].isoformat())
    # 5. 표 쓰기
    state_rows, rev_rows = [], []
    for pc in codes:
        for m, r in status[pc].rows.items():
            o = obs[pc].get(m) or Obs()
            pt = price_type(o) if o.in_b else (None, None, None)
            a = attrs.get(pc, {}).get(m)
            state_rows.append(dict(product_code=pc, list_month=m, in_benefit=o.in_b, in_substitute=o.in_s,
                                   status_as_known=r.as_known, sub_as_known=r.sub_as_known, state_as_known=state_of(r, "as_known"),
                                   status_revised=r.revised, sub_revised=r.sub_revised, state_revised=state_of(r, "revised"),
                                   revised_differs=state_of(r, "as_known") != state_of(r, "revised"), revision_reason=r.revision_reason,
                                   suspend_date=r.suspend_date, listing_date=r.listing_date, subst_lag=r.subst_lag,
                                   stay_active=bool(r.stay_dates), stay_dates=",".join(d.isoformat() for d in r.stay_dates) or None,
                                   price_listed=o.price_b, price_substitute=o.price_s, unit=o.unit,
                                   price_type=pt[0], price_type_basis=pt[1], unit_ambiguous=pt[2],
                                   c4=a[3] if a else None, firm=a[0] if a else None, ingredient_code=a[2] if a else None))
        for v in status[pc].revisions:
            rev_rows.append(dict(product_code=pc, list_month=v.month, field=v.field, as_known=v.as_known, revised=v.revised,
                                 known_month=v.known_month, reason=v.reason))
    for pc, m, f, a, rv, km, rs in revisions_extra:
        rev_rows.append(dict(product_code=pc, list_month=m, field=f, as_known=a, revised=rv, known_month=km, reason=rs))
    ext_reason = {}
    for e in load_yaml("external_evidence.yaml").get("suspend_signal", []):
        oe = e.get("outcome_external")
        if oe:
            mt = e["match"]
            ext_reason[("SUSPEND_NOTICE", mt["signal_month"], dt.date.fromisoformat(mt["token_date"]), oe["reason"])] = \
                oe["note"] + " | " + " ".join(oe.get("evidence", []))
    # §11-9: 대상 소멸 4건의 외부 원인 (사용자 확인 2026-11-18)
    gone_ext = {}
    for e in load_yaml("external_evidence.yaml").get("target_gone", []):
        for sid in e["signal_ids"]:
            gone_ext[sid] = e["reason_external"]
    ext_stay_note = {sid: e["note"] + " | " + " ".join(e.get("evidence", [])) for e in ext_stay_cfg for _, sid in ext_hits.get(e["id"], [])}
    sig_rows = []
    tgt_rows = []
    for pc in codes:
        for g in all_sigs[pc]:
            d = asdict(g)
            d["blocking_stays"] = ",".join(x.isoformat() for x in g.blocking_stays) or None
            d["source_list"] = "+".join(g.source_list)
            d["raw_types"] = "+".join(g.raw_types)
            d["market_c4"] = c4_at(pc, g.first_month)
            d["reason_external"] = (ext_reason.get((g.signal_type, g.first_month, g.signal_date, g.reason)) or gone_ext.get(g.signal_id)
                                    or ext_stay_note.get(g.signal_id))
            d["exclude_reason"] = EXCLUDE_LABEL.get(g.reason) if g.outcome == "EXCLUDED" else None
            d["reason_text"] = ONGOING_STAY_TEXT if g.current_status == "ONGOING_STAY" else None
            sig_rows.append(d)
            for x in g.blocking_stays:
                tgt_rows.append(dict(product_code=pc, stay_date=x, signal_id=g.signal_id, stay_target_source=g.stay_target_source))
    # §5 집행정지 기록 칸. 삭제 집행정지는 대상 = 급여삭제. 제품이 사라져 끝나면 stay_end_reason = delisted
    stay_rows = []
    for pc in codes:
        last_b = lives[pc].months[-1] if pc in lives else None
        dl_targets = {e["effective_date"]: e for e in ev_rows if e["product_code"] == pc and e["event_type"] == "STAY_START"
                      and e["kind"] == "delisting_target"} if pc in dl_codes else {}
        for r in stay_records(pc, all_stays[pc], all_sigs[pc]):
            sy = next(x for x in all_stays[pc] if x.date == r["stay_date"])
            if r["end_month"] is None and last_b and last_b < months[-1] and sy.months and sy.months[-1] == last_b:
                r["stay_end_reason"] = "delisted"
            if r["stay_date"] in dl_targets:
                e = dl_targets[r["stay_date"]]
                r.update(target_type="delisting", target_signal_id=None, target_detail=f"급여삭제 {e['delisting_case_id']}",
                         target_source="timing")
            r["n_months"] = len(sy.months)
            stay_rows.append(r)
        for e in ext_stay_cfg:                 # 외부 집행정지 기록 칸 (§5)
            for hp, sid in ext_hits.get(e["id"], []):
                if hp != pc:
                    continue
                g = next(x for x in all_sigs[pc] if x.signal_id == sid)
                d = g.signal_date
                stay_rows.append(dict(stay_id=f"STAY:{pc}:{e['stay_date'].isoformat()}", product_code=pc, stay_date=e["stay_date"],
                                      stay_decision_date=e["stay_decision_date"], first_month=month_of(e["stay_date"]), end_month=None,
                                      stay_end_date=None, stay_end_reason="delisted" if g.reason == "stay_then_delisted" else None,
                                      left_censored=False, released_by_r5=None, target_type=e.get("target_type", "unknown"),
                                      target_signal_id=sid, target_detail=f"{d.year % 100}.{d.month}.{d.day}. 급여중지",
                                      target_source="external", external_ref=" ".join(e.get("evidence", [])), n_months=0))
    pair_rows = [asdict(p) for p in link.pairs]

    def put(name, rows, cols=None):
        df = pd.DataFrame(rows, columns=cols)
        con.register("_df", df)
        con.execute(f"create or replace table {name} as select * from _df")
        con.unregister("_df")
        return len(df)

    ev_cols = ["event_id", "event_type", "kind", "product_code", "counterpart", "market_c4", "observed_month", "known_month",
               "retracted_month", "effective_date", "effective_lo", "effective_hi", "date_source", "link_basis", "gap_months",
               "signal_id", "outcome", "batch", "price_before", "price_after", "delisting_case_id", "stay_target",
               "stay_target_source", "evidence"]
    ev_df = pd.DataFrame(ev_rows)[ev_cols].sort_values(["observed_month", "event_type", "product_code", "event_id"])
    assert ev_df.event_id.is_unique, ev_df[ev_df.event_id.duplicated(keep=False)].head()
    counts = dict(
        product_state_month_v5=put("product_state_month_v5", state_rows),
        state_revision_log_v5=put("state_revision_log_v5", rev_rows,
                                  ["product_code", "list_month", "field", "as_known", "revised", "known_month", "reason"]),
        stay_v5=put("stay_v5", stay_rows),
        batch_month_v5=put("batch_month_v5", batch_rows, ["observed_month", "n_products", "n_ingredients", "batch", "min_c4"]),
        stay_target_v5=put("stay_target_v5", tgt_rows, ["product_code", "stay_date", "signal_id", "stay_target_source"]),
        succession_v5=put("succession_v5", pair_rows),
        signal_v5=put("signal_v5", sig_rows),
        event_ledger_v5=put("event_ledger_v5", ev_df.to_dict("records"), ev_cols),
    )
    for tb, cols in (("event_ledger_v5", ["effective_date", "effective_lo", "effective_hi"]),
                     ("signal_v5", ["signal_date", "period_end", "effective_date"]),
                     ("product_state_month_v5", ["suspend_date", "listing_date"]),
                     ("stay_v5", ["stay_date", "released_by_r5", "stay_end_date", "stay_decision_date"]),
                     ("stay_target_v5", ["stay_date"])):
        for col in cols:
            con.execute(f"alter table {tb} alter {col} type date")
    # ATTR_CHANGE: v4 product_attr_scd2 그대로 (속성 변화 규칙은 바뀌지 않음)
    con.execute("""insert into event_ledger_v5 (event_id, event_type, kind, product_code, market_c4, observed_month, known_month,
                       effective_date, date_source)
                   select 'ATTR_CHANGE:' || a.product_code || ':' || a.valid_from_month, 'ATTR_CHANGE',
                          concat_ws('+', case when a.changed_firm then 'firm' end, case when a.changed_product_name then 'product_name' end,
                                    case when a.changed_ingredient_code then 'ingredient_code' end),
                          a.product_code, (select max(c4) from stg_benefit s where s.product_code = a.product_code and s.list_month = a.valid_from_month),
                          a.valid_from_month, a.valid_from_month, cast(a.valid_from_month || '-01' as date), 'snapshot_first'
                   from product_attr_scd2 a where a.version > 1""")
    build_views(con)
    # 전이표 점검 (§8): 불가능한 칸이 나오면 실패
    bad = []
    for pc in codes:
        seq = [state_of(r) for r in status[pc].rows.values()]
        try:
            check_transitions(seq)
        except ValueError as e:
            bad.append((pc, str(e)))
    counts["impossible_transitions"] = len(bad)
    if bad:
        OUT.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(bad, columns=["product_code", "error"]).to_csv(OUT / "impossible_transitions.csv", index=False)
    return counts


def build_views(con) -> None:
    """S4·S5가 읽는 v5 보기 (사용자 지시 2026-11-18: S4·S5를 v5로 이전).
    cal_v5: 달력. state_month_v5: 급여목록에 있는 제품×월의 v5 상태(state = 그 달 1일 as_known) + S2 속성(product_attr_month).
    ev_v5: 철회되지 않은 v5 이벤트 (revised 보기)"""
    con.execute("""create or replace view cal_v5 as
        select list_month, cast(list_month || '-01' as date) base_date, row_number() over (order by list_month) mi,
               lag(list_month) over (order by list_month) prev_month, lead(list_month) over (order by list_month) next_month,
               cast(list_month || '-01' as date) first_day
        from (select distinct list_month from stg_benefit)""")
    has_attr = con.execute("""select count(*) from duckdb_tables() where table_name = 'product_attr_month'
                              and database_name = current_database()""").fetchone()[0]
    attr = """left join product_attr_month a on a.product_code = s.product_code and a.list_month = s.list_month""" if has_attr else ""
    acol = ("a.class_code, a.product_name, a.in_substitute_list, a.product_role, a.product_role_carried, a.is_bioequiv, a.role_conflict, "
            "a.sub_ref_drug, a.sub_bioeq, a.sub_bioeq_raw, a.sub_equiv, a.sub_group_code, a.sub_item_code, a.sub_price") if has_attr else \
           ("null class_code, null product_name, s.in_substitute in_substitute_list, null product_role, null product_role_carried, "
            "null is_bioequiv, null role_conflict, null sub_ref_drug, null sub_bioeq, null sub_bioeq_raw, null sub_equiv, null sub_group_code, "
            "null sub_item_code, s.price_substitute sub_price")
    con.execute(f"""create or replace view state_month_v5 as
        select s.product_code, s.list_month, s.c4, s.ingredient_code, s.firm, s.price_listed, s.state_as_known state,
               s.state_revised, s.price_type, s.stay_active, s.subst_lag, {acol}
        from product_state_month_v5 s {attr} where s.in_benefit""")
    con.execute("create or replace view ev_v5 as select * from event_ledger_v5 where retracted_month is null")


def _stay_event(kind_type, m, eff, src, kind):
    return Event(kind_type, kind, m, effective_date=eff, date_source=src, known_month=m)


def write_tables(con) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    def dump(name, sql):
        con.execute(sql).df().to_csv(OUT / f"{name}.csv", index=False)

    dump("event_counts", """select event_type, coalesce(kind, '-') kind, count(*) n,
               count(*) filter (where retracted_month is null) n_revised, count(*) filter (where retracted_month is not null) n_retracted
        from event_ledger_v5 group by all order by 1, 2""")
    dump("signal_outcomes", """select signal_type, source_rule, outcome, coalesce(reason, '-') reason, stay_blocked, count(*) n
        from signal_v5 group by all order by 1, 2, 3, 4, 5""")
    dump("state_counts", """select state_as_known, state_revised, count(*) n from product_state_month_v5 group by all order by 3 desc""")
    dump("revision_reasons", "select reason, count(*) n from state_revision_log_v5 group by 1 order by 2 desc")
    dump("price_type_units", """select price_type, price_type_basis, unit_ambiguous, unit, count(distinct product_code) n_products,
               count(distinct product_code) filter (where list_month = (select max(list_month) from product_state_month_v5)) n_products_last
        from product_state_month_v5 where in_benefit and (price_type <> 'production_unit' or unit_ambiguous) group by all order by 1, 4""")
    dump("batch_groups", """select observed_month, effective_date, count(*) n, count(distinct market_c4) n_c4, bool_or(batch) batch
        from event_ledger_v5 where event_type = 'PRICE_CHANGE' group by 1, 2 having count(*) >= 20 order by n desc""")


def main() -> None:
    con = connect()
    counts = build(con)
    write_tables(con)
    for k, v in counts.items():
        print(f"{k}: {v:,}")
    con.close()


if __name__ == "__main__":
    main()
