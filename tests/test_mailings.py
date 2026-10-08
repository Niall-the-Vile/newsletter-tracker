import csv
import os
import sqlite3

import pytest

import db
import exporter
from data import activity
from services import churches, mailings, newsletters, people, subscriptions
from tests import factories


class Scene:
    """MAAP is limited to the MAAP fund. Fake people and churches in several funds, all subscribed to it."""

    def __init__(self, conn):
        self.conn = conn
        self.maap = conn.execute("SELECT id FROM funds WHERE name='MAAP'").fetchone()[0]
        self.amca = conn.execute("SELECT id FROM funds WHERE name='AMCA'").fetchone()[0]
        self.news = conn.execute("SELECT id FROM newsletters WHERE name='MAAP Newsletter'").fetchone()[0]
        self.health = conn.execute("SELECT id FROM newsletters WHERE name='Our Health'").fetchone()[0]
        newsletters.set_fund(conn, self.news, self.maap)
        self.church_a, self.rep_a = self.church("Alpha Church", self.maap, 1)
        subscriptions.set_subscription(conn, "church", self.church_a, self.news, 40)
        subscriptions.set_subscription(conn, "person", self.rep_a, self.news, 1)  # personal copy too
        self.church_b, self.rep_b = self.church("Beta Church", self.amca, 2)
        subscriptions.set_subscription(conn, "church", self.church_b, self.news, 5)
        self.church_c, _ = self.church("Gamma Church", None, 3)
        subscriptions.set_subscription(conn, "church", self.church_c, self.news, 5)
        self.church_d = conn.execute("INSERT INTO churches (name, name_key, fund_id, no_contact) VALUES "
                                     "('Delta Church', 'delta church', ?, 1)", (self.maap,)).lastrowid
        subscriptions.set_subscription(conn, "church", self.church_d, self.news, 5)
        self.p_maap = self.person(10, self.maap)
        self.p_none = self.person(11, None)
        self.p_amca = self.person(12, self.amca)

    def person(self, i, fund, **extra):
        pid = db.upsert_person(self.conn, {**factories.fake_person(i), **extra})
        if fund:
            people.set_fund(self.conn, [pid], fund)
        subscriptions.set_subscription(self.conn, "person", pid, self.news, 1)
        return pid

    def church(self, name, fund, i):
        cid = self.conn.execute("INSERT INTO churches (name, name_key, fund_id) VALUES (?, ?, ?)",
                                (name, name.lower(), fund)).lastrowid
        rep = db.upsert_person(self.conn, {**factories.fake_person(i), "recipient_type": "Rep"})
        if fund:
            people.set_fund(self.conn, [rep], fund)
        churches.add_rep(self.conn, cid, rep)
        return cid, rep


@pytest.fixture
def scene(conn):
    return Scene(conn)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def run_count(conn):
    return conn.execute("SELECT COUNT(*) FROM mailing_runs").fetchone()[0]


def make_run(scene, tmp_path, label="Fall 2026", name="out.csv"):
    path = str(tmp_path / name)
    return mailings.create_mailing(scene.conn, scene.news, label, path), path


# ---------- M11: funds by query ----------

def test_a_fund_run_has_no_other_fund_or_no_fund_entities(scene):
    m = mailings.build_mailing(scene.conn, scene.news)
    church_ids = {r["church_id"] for r in m["rows"] if r["church_id"]}
    person_ids = {r["person_id"] for r in m["rows"] if not r["church_id"]}
    assert church_ids == {scene.church_a}
    assert person_ids == {scene.p_maap}
    reasons = {(s["kind"], s["id"]): s["reason"] for s in m["skipped"]}
    assert reasons[("church", scene.church_b)] == mailings.WRONG_FUND
    assert reasons[("church", scene.church_c)] == mailings.NO_FUND
    assert reasons[("church", scene.church_d)] == mailings.NO_CONTACT
    assert reasons[("person", scene.p_none)] == mailings.NO_FUND
    assert reasons[("person", scene.p_amca)] == mailings.WRONG_FUND
    check = mailings.preflight(scene.conn, scene.news)
    assert check["skipped_by_reason"] == {mailings.WRONG_FUND: 2, mailings.NO_FUND: 2, mailings.NO_CONTACT: 1,
                                          mailings.VIA_CHURCH: 1}
    assert not check["blocked"]


def test_unrestricted_newsletter_ignores_funds(scene):
    subscriptions.set_subscription(scene.conn, "person", scene.p_none, scene.health, 1)
    m = mailings.build_mailing(scene.conn, scene.health)
    assert [r["person_id"] for r in m["rows"]] == [scene.p_none] and m["skipped"] == []


def test_church_is_one_row_to_its_default_rep_with_no_second_row(scene):
    rows = mailings.build_mailing(scene.conn, scene.news)["rows"]
    mine = [r for r in rows if r["person_id"] == scene.rep_a]
    assert len(mine) == 1
    assert mine[0]["church_id"] == scene.church_a and mine[0]["quantity"] == 40
    assert mine[0]["church_name"] == "Alpha Church" and mine[0]["recipient_type"] == "Church"
    skipped = [s for s in mailings.build_mailing(scene.conn, scene.news)["skipped"] if s["kind"] == "person" and s["id"] == scene.rep_a]
    assert skipped[0]["reason"] == mailings.VIA_CHURCH


def test_a_rep_who_is_not_the_default_still_gets_their_own_copy(scene):
    other = db.upsert_person(scene.conn, factories.fake_person(30))
    people.set_fund(scene.conn, [other], scene.maap)
    churches.add_rep(scene.conn, scene.church_a, other)
    subscriptions.set_subscription(scene.conn, "person", other, scene.news, 1)
    assert other in [r["person_id"] for r in mailings.build_mailing(scene.conn, scene.news)["rows"]]


def test_email_only_is_left_out_of_a_mailed_run_and_both_is_kept(scene, tmp_path):
    subscriptions.set_subscription(scene.conn, "person", scene.p_maap, scene.news, 1, "Email")
    m = mailings.build_mailing(scene.conn, scene.news)
    assert scene.p_maap not in [r["person_id"] for r in m["rows"]]
    assert [s["reason"] for s in m["skipped"] if s["kind"] == "person" and s["id"] == scene.p_maap] == [mailings.EMAIL_ONLY]
    subscriptions.set_subscription(scene.conn, "person", scene.p_maap, scene.news, 1, "Both")
    _, path = make_run(scene, tmp_path)
    rows = read_csv(path)
    assert "Both" in [r[13] for r in rows[1:]]


# ---------- M9: immutable runs ----------

def test_run_matches_the_file_and_later_edits_leave_it_unchanged(scene, tmp_path):
    conn = scene.conn
    run_id, path = make_run(scene, tmp_path)
    run = mailings.get_run(conn, run_id)
    snap = [dict(r) for r in mailings.run_rows(conn, run_id)]
    lines = read_csv(path)
    assert len(lines) - 1 == run["row_count"] == len(snap) == 2
    assert sum(int(r[10]) for r in lines[1:]) == run["copy_count"] == sum(r["quantity"] for r in snap) == 41
    assert run["skipped_count"] == 6 and run["channel"] == "Print" and run["status"] == "saved"
    assert not mailings.packing_sheet(conn, run_id)["groups"] == []
    before = open(path, "rb").read()

    db.upsert_person(conn, {"first_name": "Changed", "address1": "9 New Street", "city": "Elsewhere"}, scene.rep_a)
    people.remove_people(conn, [scene.p_maap])
    other = db.upsert_person(conn, factories.fake_person(40))
    churches.add_rep(conn, scene.church_a, other, make_default=True)
    subscriptions.set_subscription(conn, "church", scene.church_a, scene.news, 2)
    assert [dict(r) for r in mailings.run_rows(conn, run_id)] == snap

    again = str(tmp_path / "again.csv")
    mailings.export_run(conn, run_id, again)
    assert open(again, "rb").read() == before


def test_sent_on_is_the_local_date_and_rows_have_no_foreign_keys(scene, tmp_path):
    from datetime import date
    run_id, _ = make_run(scene, tmp_path)
    assert mailings.get_run(scene.conn, run_id)["sent_on"] == date.today().isoformat()
    fks = scene.conn.execute("PRAGMA foreign_key_list(mailing_run_rows)").fetchall()
    assert [f["table"] for f in fks] == ["mailing_runs"]


def test_a_duplicate_issue_label_prompts(scene, tmp_path):
    make_run(scene, tmp_path)
    with pytest.raises(mailings.DuplicateIssue) as e:
        mailings.create_mailing(scene.conn, scene.news, "  fall 2026 ", str(tmp_path / "b.csv"))
    assert len(e.value.sent_on) == 1
    assert not os.path.exists(tmp_path / "b.csv") and run_count(scene.conn) == 1
    mailings.create_mailing(scene.conn, scene.news, "Fall 2026", str(tmp_path / "b.csv"), allow_duplicate=True)
    assert run_count(scene.conn) == 2


def test_a_failed_file_save_leaves_no_run(scene, tmp_path, monkeypatch):
    conn = scene.conn
    with pytest.raises(OSError):
        mailings.create_mailing(conn, scene.news, "Fall 2026", str(tmp_path / "missing folder" / "out.csv"))
    assert run_count(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM mailing_run_rows").fetchone()[0] == 0

    def boom(*a, **k):
        raise PermissionError("file is open in Excel")
    monkeypatch.setattr(exporter, "write_run_csv", boom)
    with pytest.raises(PermissionError):
        mailings.create_mailing(conn, scene.news, "Fall 2026", str(tmp_path / "out.csv"))
    assert run_count(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'mailing.create'").fetchone()[0] == 0


def test_run_and_rows_are_one_transaction(scene, tmp_path, monkeypatch):
    real = activity.log

    def fail_on_create(conn, action, *a, **k):
        if action == "mailing.create":
            raise RuntimeError("fail after the rows were written")
        return real(conn, action, *a, **k)
    monkeypatch.setattr(activity, "log", fail_on_create)
    path = tmp_path / "out.csv"
    with pytest.raises(RuntimeError):
        mailings.create_mailing(scene.conn, scene.news, "Fall 2026", str(path))
    assert run_count(scene.conn) == 0
    assert scene.conn.execute("SELECT COUNT(*) FROM mailing_run_rows").fetchone()[0] == 0
    assert not path.exists()


def test_newsletters_with_runs_are_archived_not_deleted(scene, tmp_path):
    conn = scene.conn
    make_run(scene, tmp_path)
    assert db.delete_newsletter(conn, scene.news) == "archived"
    assert conn.execute("SELECT archived FROM newsletters WHERE id = ?", (scene.news,)).fetchone()[0] == 1
    assert scene.news not in [n["id"] for n in db.list_newsletters(conn)]
    with pytest.raises(mailings.MailingError):
        mailings.create_mailing(conn, scene.news, "Winter", str(tmp_path / "w.csv"))
    assert mailings.list_runs(conn)[0]["newsletter_name"] == "MAAP Newsletter"
    db.add_newsletter(conn, "Spare")
    spare = conn.execute("SELECT id FROM newsletters WHERE name='Spare'").fetchone()[0]
    assert db.delete_newsletter(conn, spare) == "deleted"


def test_snapshots_are_kept_when_people_are_removed(scene, tmp_path):
    run_id, _ = make_run(scene, tmp_path)
    people.remove_people(scene.conn, [scene.rep_a, scene.p_maap])
    assert len(mailings.run_rows(scene.conn, run_id)) == 2


# ---------- M12: pre-flight ----------

def test_red_items_block_and_yellow_items_warn(scene, tmp_path):
    conn = scene.conn
    run_id, _ = make_run(scene, tmp_path, "Spring")
    db.upsert_person(conn, {"address1": "77 Moved Road"}, scene.p_maap)
    subscriptions.set_subscription(conn, "church", scene.church_a, scene.news, 45)
    late = scene.person(50, scene.maap, zip="ABC12")
    gone = scene.p_maap
    subscriptions.stop_subscription(conn, conn.execute(
        "SELECT id FROM subscriptions_v2 WHERE person_id = ? AND newsletter_id = ?", (gone, scene.news)).fetchone()[0])
    check = mailings.preflight(conn, scene.news)
    texts = [y["text"] for y in check["yellow"]]
    assert any("Copies changed" in t and "40 to 45" in t for t in texts)
    assert any("ZIP code looks wrong" in t for t in texts)
    assert check["diff"]["added"] == [people.display_name(*conn.execute(
        "SELECT first_name, last_name FROM people WHERE id = ?", (late,)).fetchone())]
    assert len(check["diff"]["removed"]) == 1 and check["diff"]["changed"] == [("Alpha Church", 40, 45)]
    assert check["diff"]["previous"]["id"] == run_id
    assert not check["blocked"]

    db.upsert_person(conn, {"address1": "", "city": ""}, late)
    blocked = mailings.preflight(conn, scene.news)
    assert blocked["blocked"] and blocked["red"][0]["text"] == "No address"
    with pytest.raises(mailings.PreflightBlocked):
        mailings.create_mailing(conn, scene.news, "Summer", str(tmp_path / "s.csv"))
    assert run_count(conn) == 1


def test_address_change_and_duplicate_address_are_yellow(scene, tmp_path):
    conn = scene.conn
    make_run(scene, tmp_path, "Spring")
    db.upsert_person(conn, {"address1": "77 Moved Road"}, scene.p_maap)
    twin = scene.person(60, scene.maap)
    db.upsert_person(conn, {"address1": "77 Moved Road", "zip": db.get_person(conn, scene.p_maap)["zip"]}, twin)
    texts = [y["text"] for y in mailings.preflight(conn, scene.news)["yellow"]]
    assert "Address changed since the last issue" in texts and "Same address as another row" in texts


def test_summary_counts_by_delivery_fund_and_skipped(scene):
    check = mailings.preflight(scene.conn, scene.news)
    assert check["by_delivery"] == {"Mail": 2} and check["by_fund"] == {"MAAP": 2}
    text = mailings.summary_text(check)
    assert "2 addresses, 41 copies" in text and "6 left out" in text and "first mailing" in text


# ---------- M20: CSV ----------

def test_csv_is_guarded_and_has_fund_issue_and_delivery_columns(scene, tmp_path):
    conn = scene.conn
    db.upsert_person(conn, {"first_name": "=HYPERLINK(1)", "address1": "+1 Plus Street", "zip": "1234"}, scene.p_maap)
    _, path = make_run(scene, tmp_path)
    rows = read_csv(path)
    assert rows[0] == exporter.EXPORT_HEADERS and rows[0][-3:] == ["Fund", "Issue label", "Delivery"]
    mine = next(r for r in rows[1:] if r[0].startswith("'=HYPERLINK"))
    assert mine[3] == "'+1 Plus Street" and mine[7] == "01234"  # ZIP stays text with its leading zero
    assert mine[11] == "MAAP" and mine[12] == "Fall 2026" and mine[13] == "Mail"
    assert open(path, "rb").read().startswith(b"\xef\xbb\xbf")
    stored = conn.execute("SELECT first_name, address1 FROM mailing_run_rows WHERE first_name LIKE '%HYPER%'").fetchone()
    assert stored["first_name"] == "=HYPERLINK(1)"  # the guard is applied only when the file is written


def test_csv_safe_guards_every_leading_character():
    for lead in "=+-@\t\r":
        assert exporter.csv_safe(lead + "x") == "'" + lead + "x"
    assert exporter.csv_safe("Plain") == "Plain" and exporter.csv_safe(None) == ""


# ---------- subscriptions ----------

def test_subscriptions_are_added_changed_and_stopped_never_deleted(conn):
    pid = db.upsert_person(conn, factories.fake_person(1))
    nid = conn.execute("SELECT id FROM newsletters WHERE name='Our Health'").fetchone()[0]
    sid = subscriptions.set_subscription(conn, "person", pid, nid, 2)
    assert subscriptions.set_subscription(conn, "person", pid, nid, 5, "Both") == sid
    row = conn.execute("SELECT * FROM subscriptions_v2 WHERE id = ?", (sid,)).fetchone()
    assert (row["quantity"], row["delivery"], row["active"]) == (5, "Both", 1)
    subscriptions.stop_subscription(conn, sid)
    assert conn.execute("SELECT active FROM subscriptions_v2 WHERE id = ?", (sid,)).fetchone()[0] == 0
    assert subscriptions.gets(conn, "person", pid) == []
    subscriptions.set_subscription(conn, "person", pid, nid, 1)  # adding again brings the same row back
    assert conn.execute("SELECT COUNT(*) FROM subscriptions_v2 WHERE person_id = ?", (pid,)).fetchone()[0] == 1
    fields = [r["field"] for r in conn.execute("SELECT field FROM subscription_changes WHERE subscription_id = ?", (sid,))]
    assert "quantity" in fields and "delivery" in fields and "active" in fields
    for bad in (0, -3, "x"):
        with pytest.raises(subscriptions.SubscriptionError):
            subscriptions.set_subscription(conn, "person", pid, nid, bad)
    with pytest.raises(subscriptions.SubscriptionError):
        subscriptions.set_subscription(conn, "person", pid, nid, 1, "Carrier pigeon")


def test_church_subscription_and_activity_log(conn):
    cid = conn.execute("INSERT INTO churches (name, name_key) VALUES ('C', 'c')").lastrowid
    nid = conn.execute("SELECT id FROM newsletters ORDER BY id").fetchone()[0]
    subscriptions.set_subscription(conn, "church", cid, nid, 40)
    g = subscriptions.gets(conn, "church", cid)
    assert g[0]["copies"] == 40 and g[0]["delivery"] == "Mail" and g[0]["last_sent"] == ""
    assert churches.church_detail(conn, cid)["gets"][0]["copies"] == 40
    assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'subscription.set' AND entity_type = 'church'"
                        ).fetchone()[0] == 1


def test_newsletter_fund_change_is_logged_and_counts_mismatches(scene):
    n = newsletters.set_fund(scene.conn, scene.news, scene.amca)
    assert n == scene.conn.execute(
        "SELECT COUNT(*) FROM subscriptions_v2 s LEFT JOIN people p ON p.id = s.person_id "
        "LEFT JOIN churches c ON c.id = s.church_id WHERE s.newsletter_id = ? AND s.active = 1 "
        "AND COALESCE(p.fund_id, c.fund_id, 0) <> ?", (scene.news, scene.amca)).fetchone()[0]
    assert n > 0
    assert scene.conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'newsletter.fund'").fetchone()[0] >= 2
    assert newsletters.set_fund(scene.conn, scene.news, None) == 0


# ---------- history, last sent, quick answer ----------

def test_history_last_sent_and_history_starts(scene, tmp_path):
    conn = scene.conn
    assert mailings.history_starts(conn) is None
    assert people.person_detail(conn, scene.p_maap)["history_starts"] is None
    run_id, _ = make_run(scene, tmp_path)
    run = mailings.get_run(conn, run_id)
    d = people.person_detail(conn, scene.p_maap)
    assert d["history_starts"] == run["sent_on"]
    assert d["gets"][0]["last_sent"] == f"Fall 2026 ({run['sent_on']})"
    assert any("Mailed Fall 2026 (MAAP Newsletter): 1 copies" in h["summary"] for h in d["history"])
    c = churches.church_detail(conn, scene.church_a)
    assert c["gets"][0]["last_sent"].startswith("Fall 2026")
    assert any("40 copies" in h["summary"] for h in c["history"])
    # the rep's own history does not include the church bundle, which belongs to the church
    rep = people.person_detail(conn, scene.rep_a)
    assert not any("40 copies" in h["summary"] for h in rep["history"])


def test_quick_answer_card(scene, tmp_path):
    conn = scene.conn
    assert mailings.quick_answer(conn, "Alpha") == "Alpha Church: 40 copies, not mailed yet"
    make_run(scene, tmp_path)
    rep_name = people.display_name(*conn.execute("SELECT first_name, last_name FROM people WHERE id = ?",
                                                 (scene.rep_a,)).fetchone())
    assert mailings.quick_answer(conn, "alpha church") == f"Alpha Church: 40 copies, last mailed Fall 2026 to {rep_name}"
    assert mailings.quick_answer(conn, "Church") is None  # several matches
    assert mailings.quick_answer(conn, "zzz-nothing") is None and mailings.quick_answer(conn, "a") is None


# ---------- returned mail ----------

def test_returned_mail_warns_and_clears_when_the_address_is_edited(scene):
    conn = scene.conn
    people.set_returned_mail(conn, "person", scene.p_maap, True)
    assert any("returned" in y["text"] for y in mailings.preflight(conn, scene.news)["yellow"])
    db.upsert_person(conn, {"notes": "just a note"}, scene.p_maap)
    assert db.get_person(conn, scene.p_maap)["returned_mail"] == 1
    db.upsert_person(conn, {"address1": "5 Fresh Address"}, scene.p_maap)
    assert db.get_person(conn, scene.p_maap)["returned_mail"] == 0
    people.set_returned_mail(conn, "church", scene.church_a, True)
    assert any(y["who"] == "Alpha Church" and "returned" in y["text"]
               for y in mailings.preflight(conn, scene.news)["yellow"])
    db.upsert_person(conn, {"zip": "99999"}, scene.rep_a)  # the church's mail goes to its default rep
    assert conn.execute("SELECT returned_mail FROM churches WHERE id = ?", (scene.church_a,)).fetchone()[0] == 0


# ---------- packing sheet ----------

def test_packing_sheet_by_state_and_church_with_totals(scene, tmp_path):
    conn = scene.conn
    db.upsert_person(conn, {"state": "OH"}, scene.rep_a)
    db.upsert_person(conn, {"state": "PA"}, scene.p_maap)
    run_id, _ = make_run(scene, tmp_path)
    sheet = mailings.packing_sheet(conn, run_id)
    assert [(g["state"], g["copies"]) for g in sheet["groups"]] == [("OH", 40), ("PA", 1)]
    assert sheet["groups"][0]["bundles"][0]["church"] == "Alpha Church"
    assert (sheet["bundles"], sheet["copies"]) == (2, 41)
    out = str(tmp_path / "pack.csv")
    mailings.write_packing_csv(out, sheet)
    rows = read_csv(out)
    assert rows[-1][0] == "Issue total" and rows[-1][-1] == "41" and ["OH total", "", "", "", "40"] in rows


def test_the_database_blocks_a_second_row_per_entity_and_newsletter(conn):
    cid = conn.execute("INSERT INTO churches (name, name_key) VALUES ('C', 'c')").lastrowid
    subscriptions.set_subscription(conn, "church", cid, 1, 3)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO subscriptions_v2 (church_id, newsletter_id, quantity) VALUES (?, 1, 2)", (cid,))
