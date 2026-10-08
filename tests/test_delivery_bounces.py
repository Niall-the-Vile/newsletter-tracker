import csv

import pytest

import db
import exporter
from data.connection import ConflictError
from services import bounces, mailings, subscriptions
from tests import factories
from tests.test_mailings import Scene, read_csv


@pytest.fixture
def scene(conn):
    return Scene(conn)


def set_delivery(scene, kind, eid, delivery, copies=1):
    subscriptions.set_subscription(scene.conn, kind, eid, scene.news, copies, delivery)


def email_rows(scene):
    return mailings.build_mailing(scene.conn, scene.news, "Email")


def person_email(conn, pid, email):
    db.upsert_person(conn, {"email": email}, pid)


def ids(rows):
    return {(r["church_id"], r["person_id"]) for r in rows}


# ---------- delivery and channels ----------

def test_both_is_in_the_mailed_run_and_the_email_run_once_each(scene, tmp_path):
    set_delivery(scene, "person", scene.p_maap, "Both")
    set_delivery(scene, "person", scene.p_none, "Email")  # no fund: left out either way
    printed = mailings.build_mailing(scene.conn, scene.news, "Print")["rows"]
    emailed = email_rows(scene)["rows"]
    assert [r["person_id"] for r in printed].count(scene.p_maap) == 1
    assert [r["person_id"] for r in emailed].count(scene.p_maap) == 1
    run_print = mailings.create_mailing(scene.conn, scene.news, "Fall 2026", str(tmp_path / "a.csv"))
    run_email = mailings.create_mailing(scene.conn, scene.news, "Fall 2026", str(tmp_path / "b.csv"), channel="Email")
    for run_id, channel in ((run_print, "Print"), (run_email, "Email")):
        assert mailings.get_run(scene.conn, run_id)["channel"] == channel
        rows = [r for r in mailings.run_rows(scene.conn, run_id) if r["person_id"] == scene.p_maap]
        assert len(rows) == 1 and rows[0]["delivery"] == "Both"


def test_email_only_is_not_mailed_and_mail_only_is_not_emailed(scene):
    set_delivery(scene, "person", scene.p_maap, "Email")
    printed = mailings.build_mailing(scene.conn, scene.news, "Print")
    assert scene.p_maap not in {r["person_id"] for r in printed["rows"]}
    assert any(s["id"] == scene.p_maap and s["reason"] == mailings.EMAIL_ONLY for s in printed["skipped"])
    emailed = email_rows(scene)
    assert {r["person_id"] for r in emailed["rows"]} == {scene.p_maap}
    assert any(s["reason"] == mailings.MAIL_ONLY for s in emailed["skipped"])


def test_a_church_set_to_email_goes_to_its_default_rep_with_no_second_row(scene):
    set_delivery(scene, "church", scene.church_a, "Email", 40)
    set_delivery(scene, "person", scene.rep_a, "Email")
    rows = email_rows(scene)["rows"]
    mine = [r for r in rows if r["person_id"] == scene.rep_a]
    assert len(mine) == 1 and mine[0]["church_id"] == scene.church_a
    assert any(s["id"] == scene.rep_a and s["reason"] == mailings.VIA_CHURCH for s in email_rows(scene)["skipped"])


def test_fund_rules_still_apply_to_the_email_list(scene):
    for kind, eid in (("church", scene.church_a), ("church", scene.church_b), ("person", scene.p_maap),
                      ("person", scene.p_none), ("person", scene.p_amca)):
        set_delivery(scene, kind, eid, "Email")
    rows = email_rows(scene)["rows"]
    assert {r["church_id"] for r in rows if r["church_id"]} == {scene.church_a}
    assert {r["person_id"] for r in rows if not r["church_id"]} == {scene.p_maap}


# ---------- pre-flight ----------

def test_email_delivery_with_no_valid_email_is_flagged_in_preflight(scene):
    pid = scene.p_maap
    set_delivery(scene, "person", pid, "Email")
    person_email(scene.conn, pid, "")
    check = mailings.preflight(scene.conn, scene.news, "Email")
    assert check["blocked"] and any(r["text"] == mailings.NO_EMAIL for r in check["red"])
    person_email(scene.conn, pid, "not an email")
    assert mailings.preflight(scene.conn, scene.news, "Email")["blocked"]
    mailed = mailings.preflight(scene.conn, scene.news, "Print")
    assert not mailed["blocked"]
    assert any("no valid email" in y["text"] for y in mailed["yellow"])
    person_email(scene.conn, pid, "ok@example.org")
    assert not mailings.preflight(scene.conn, scene.news, "Email")["blocked"]


def test_both_with_no_email_warns_in_the_mailed_run_and_blocks_the_email_list(scene, tmp_path):
    set_delivery(scene, "person", scene.p_maap, "Both")
    person_email(scene.conn, scene.p_maap, None)
    assert any("no valid email" in y["text"] for y in mailings.preflight(scene.conn, scene.news)["yellow"])
    with pytest.raises(mailings.PreflightBlocked):
        mailings.create_mailing(scene.conn, scene.news, "X", str(tmp_path / "e.csv"), channel="Email")
    assert scene.conn.execute("SELECT COUNT(*) FROM mailing_runs").fetchone()[0] == 0


def test_valid_email_check():
    assert bounces.is_valid_email("a.b@example.org") and bounces.is_valid_email(" A@B.co.uk ")
    assert not any(bounces.is_valid_email(x) for x in ("", None, "a@b", "a b@c.org", "@c.org", "a@@c.org"))


# ---------- bounces ----------

def test_a_hard_or_manual_bounce_skips_the_address_and_it_is_counted(scene):
    set_delivery(scene, "person", scene.p_maap, "Email")
    email = scene.conn.execute("SELECT email FROM people WHERE id = ?", (scene.p_maap,)).fetchone()[0]
    bounces.mark_bounced(scene.conn, email, "Manual")
    m = email_rows(scene)
    assert scene.p_maap not in {r["person_id"] for r in m["rows"]}
    assert any(s["id"] == scene.p_maap and s["reason"] == mailings.BOUNCED for s in m["skipped"])
    check = mailings.preflight(scene.conn, scene.news, "Email")
    assert check["skipped_by_reason"][mailings.BOUNCED] == 1
    assert "1 (email address bounced)" in mailings.summary_text(check)


def test_bounce_is_matched_ignoring_case_and_spaces(scene):
    set_delivery(scene, "person", scene.p_maap, "Email")
    person_email(scene.conn, scene.p_maap, "  Mixed.Case@Example.org ")
    bounces.mark_bounced(scene.conn, "mixed.case@example.ORG", "Hard")
    assert scene.p_maap not in {r["person_id"] for r in email_rows(scene)["rows"]}


def test_a_soft_bounce_is_skipped_only_after_three_in_a_row(scene):
    set_delivery(scene, "person", scene.p_maap, "Email")
    email = scene.conn.execute("SELECT email FROM people WHERE id = ?", (scene.p_maap,)).fetchone()[0]
    for n in (1, 2):
        bounces.mark_bounced(scene.conn, email, "Soft")
        assert scene.p_maap in {r["person_id"] for r in email_rows(scene)["rows"]}, n
    bounces.mark_bounced(scene.conn, email, "Soft")
    assert scene.p_maap not in {r["person_id"] for r in email_rows(scene)["rows"]}


def test_editing_the_email_clears_the_skip(scene):
    set_delivery(scene, "person", scene.p_maap, "Email")
    old = scene.conn.execute("SELECT email FROM people WHERE id = ?", (scene.p_maap,)).fetchone()[0]
    bounces.mark_bounced(scene.conn, old, "Hard")
    assert scene.p_maap not in {r["person_id"] for r in email_rows(scene)["rows"]}
    person_email(scene.conn, scene.p_maap, "corrected@example.org")
    assert scene.p_maap in {r["person_id"] for r in email_rows(scene)["rows"]}
    assert not bounces.is_skipped(scene.conn, "corrected@example.org")


def test_a_bounce_through_a_church_rep_skips_the_church_row(scene):
    set_delivery(scene, "church", scene.church_a, "Email", 40)
    rep_email = scene.conn.execute("SELECT email FROM people WHERE id = ?", (scene.rep_a,)).fetchone()[0]
    bounces.mark_bounced(scene.conn, rep_email, "Hard")
    m = email_rows(scene)
    assert scene.church_a not in {r["church_id"] for r in m["rows"]}
    assert any(s["kind"] == "church" and s["id"] == scene.church_a and s["reason"] == mailings.BOUNCED
               for s in m["skipped"])


def test_clear_puts_the_address_back_but_keeps_the_record(scene):
    set_delivery(scene, "person", scene.p_maap, "Email")
    email = scene.conn.execute("SELECT email FROM people WHERE id = ?", (scene.p_maap,)).fetchone()[0]
    bounces.mark_bounced(scene.conn, email)
    bounces.clear(scene.conn, email)
    assert scene.p_maap in {r["person_id"] for r in email_rows(scene)["rows"]}
    assert scene.conn.execute("SELECT COUNT(*) FROM email_bounces").fetchone()[0] == 1
    assert bounces.list_bounces(scene.conn) == []


def test_paste_in_finds_addresses_and_counts_the_rest(conn):
    text = "Jo <jo@example.org>, bad@x, sam@example.net;\nlee@example.com.\nnothing here @ all"
    result = bounces.paste_in(conn, text, "Hard")
    assert result["recorded"] == 3
    keys = {b["key"] for b in bounces.list_bounces(conn)}
    assert keys == {"jo@example.org", "sam@example.net", "lee@example.com"}
    assert result["ignored"] == 1  # bad@x has no dot in its domain


def test_mark_bounced_rejects_bad_input_and_is_logged(conn):
    with pytest.raises(bounces.BounceError):
        bounces.mark_bounced(conn, "nope")
    with pytest.raises(bounces.BounceError):
        bounces.mark_bounced(conn, "a@b.org", "Weird")
    bounces.mark_bounced(conn, "a@b.org")
    assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'bounce.mark'").fetchone()[0] == 1


def test_bounces_list_shows_who_holds_the_address_now(scene):
    email = scene.conn.execute("SELECT email FROM people WHERE id = ?", (scene.p_maap,)).fetchone()[0]
    bounces.mark_bounced(scene.conn, email)
    assert bounces.list_bounces(scene.conn)[0]["holders"] == 1
    person_email(scene.conn, scene.p_maap, "new@example.org")
    assert bounces.list_bounces(scene.conn)[0]["holders"] == 0


# ---------- email list file ----------

def test_email_list_csv_is_clean_and_reexport_is_identical(scene, tmp_path):
    set_delivery(scene, "person", scene.p_maap, "Both")
    bad = scene.person(40, scene.maap)
    set_delivery(scene, "person", bad, "Email")
    bounces.mark_bounced(scene.conn, scene.conn.execute("SELECT email FROM people WHERE id = ?", (bad,)).fetchone()[0])
    path = str(tmp_path / "email.csv")
    run_id = mailings.create_mailing(scene.conn, scene.news, "Fall 2026", path, channel="Email")
    rows = read_csv(path)
    assert rows[0] == exporter.EMAIL_HEADERS
    assert len(rows) - 1 == mailings.get_run(scene.conn, run_id)["row_count"] == 1
    assert rows[1][rows[0].index("Email")].endswith("@example.invalid") and rows[1][-1] == "Both"
    assert "Address 1" not in rows[0] and "Quantity" not in rows[0]
    again = str(tmp_path / "again.csv")
    mailings.export_run(scene.conn, run_id, again)
    assert open(path, "rb").read() == open(again, "rb").read()


def test_skipped_reasons_file(scene, tmp_path):
    set_delivery(scene, "person", scene.p_maap, "Email")
    bounces.mark_bounced(scene.conn, scene.conn.execute(
        "SELECT email FROM people WHERE id = ?", (scene.p_maap,)).fetchone()[0])
    skipped = mailings.preflight(scene.conn, scene.news, "Email")["skipped"]
    path = str(tmp_path / "left out.csv")
    mailings.write_skipped_csv(path, skipped)
    rows = read_csv(path)
    assert rows[0] == exporter.SKIPPED_HEADERS and len(rows) == len(skipped) + 1
    assert [mailings.BOUNCED] == [r[2] for r in rows[1:] if r[2] == mailings.BOUNCED]


def test_email_csv_guards_formula_text(scene, tmp_path):
    pid = scene.person(41, scene.maap, first_name="=cmd")
    set_delivery(scene, "person", pid, "Email")
    path = str(tmp_path / "e.csv")
    mailings.create_mailing(scene.conn, scene.news, "Guard", path, channel="Email")
    assert any(r[0] == "'=cmd" for r in read_csv(path))


def test_email_run_does_not_clash_with_the_mailed_run_label_or_history(scene, tmp_path):
    set_delivery(scene, "person", scene.p_maap, "Both")
    mailings.create_mailing(scene.conn, scene.news, "Fall 2026", str(tmp_path / "a.csv"))
    assert mailings.same_label_runs(scene.conn, scene.news, "Fall 2026", "Email") == []
    mailings.create_mailing(scene.conn, scene.news, "Fall 2026", str(tmp_path / "b.csv"), channel="Email")
    with pytest.raises(mailings.DuplicateIssue):
        mailings.create_mailing(scene.conn, scene.news, "fall 2026", str(tmp_path / "c.csv"), channel="Email")
    texts = [h["summary"] for h in mailings.history(scene.conn, "person", scene.p_maap)]
    assert any(t.startswith("Emailed Fall 2026") for t in texts) and any(t.startswith("Mailed Fall 2026") for t in texts)
    run = scene.conn.execute("SELECT id FROM mailing_runs WHERE channel = 'Email'").fetchone()[0]
    with pytest.raises(mailings.MailingError):
        mailings.packing_sheet(scene.conn, run)


def test_nothing_is_sent_or_imported_for_email():
    import sys
    assert "msal" not in sys.modules


# ---------- S1: optimistic concurrency ----------

def test_stale_subscription_change_is_refused(scene):
    sub = subscriptions.gets(scene.conn, "person", scene.p_maap)[0]
    seen = sub["updated_at"]
    scene.conn.execute("UPDATE subscriptions_v2 SET quantity = 7, updated_at = '2000-01-01T00:00:00+00:00' "
                       "WHERE id = ?", (sub["sub_id"],))
    with pytest.raises(ConflictError):
        subscriptions.set_subscription(scene.conn, "person", scene.p_maap, scene.news, 2, "Print", seen)
    with pytest.raises(ConflictError):
        subscriptions.stop_subscription(scene.conn, sub["sub_id"], seen)
    row = scene.conn.execute("SELECT quantity, active FROM subscriptions_v2 WHERE id = ?", (sub["sub_id"],)).fetchone()
    assert (row["quantity"], row["active"]) == (7, 1)
    fresh = subscriptions.gets(scene.conn, "person", scene.p_maap)[0]["updated_at"]
    subscriptions.set_subscription(scene.conn, "person", scene.p_maap, scene.news, 2, "Print", fresh)
    assert scene.conn.execute("SELECT quantity FROM subscriptions_v2 WHERE id = ?", (sub["sub_id"],)).fetchone()[0] == 2


def test_stale_person_edit_is_refused(conn):
    pid = db.upsert_person(conn, factories.fake_person(1))
    seen = db.get_person(conn, pid)["updated_at"]
    with db.transaction(conn):
        conn.execute("UPDATE people SET last_name = 'Else', updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                     (pid,))
    with pytest.raises(ConflictError):
        db.upsert_person(conn, {"first_name": "Mine"}, pid, seen)
    row = db.get_person(conn, pid)
    assert row["last_name"] == "Else" and row["first_name"] != "Mine"
    db.upsert_person(conn, {"first_name": "Mine"}, pid, row["updated_at"])
    assert db.get_person(conn, pid)["first_name"] == "Mine"


def test_old_style_add_subscription_keeps_existing_copies(conn):
    pid = db.upsert_person(conn, factories.fake_person(2))
    nid = db.list_newsletters(conn)[0]["id"]
    db.add_subscription(conn, pid, nid, 12)
    db.add_subscription(conn, pid, nid, 1)
    q = lambda: conn.execute("SELECT quantity, active FROM subscriptions WHERE person_id = ?", (pid,)).fetchone()
    assert tuple(q()) == (12, 1)
    db.remove_subscription(conn, conn.execute("SELECT id FROM subscriptions WHERE person_id = ?", (pid,)).fetchone()[0])
    db.add_subscription(conn, pid, nid, 3)
    assert tuple(q()) == (3, 1)


def test_inactive_old_subscription_comes_back_with_the_new_copies(conn):
    pid = db.upsert_person(conn, factories.fake_person(3))
    nid = db.list_newsletters(conn)[0]["id"]
    db.add_subscription(conn, pid, nid, 12)
    with db.transaction(conn):
        conn.execute("UPDATE subscriptions SET active = 0 WHERE person_id = ?", (pid,))
    db.add_subscription(conn, pid, nid, 4)
    assert tuple(conn.execute("SELECT quantity, active FROM subscriptions WHERE person_id = ?", (pid,)).fetchone()) == (4, 1)
