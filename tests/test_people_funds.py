import pytest

import db
import exporter
from data import activity
from data.connection import ReadOnlyError
from services import backup, funds, people, startup
from tests import factories


def add_people(conn, n, **extra):
    ids = []
    for i in range(n):
        p = factories.fake_person(i)
        p.update(extra)
        ids.append(db.upsert_person(conn, p))
    return ids


def log_rows(conn, action):
    return conn.execute("SELECT * FROM activity_log WHERE action = ?", (action,)).fetchall()


# ---------- funds ----------

def test_funds_are_seeded_and_can_be_managed(conn):
    assert [f["name"] for f in funds.list_funds(conn)] == ["AMCA", "MAAP"]
    fid = funds.add_fund(conn, "  Fake   Fund ")
    assert conn.execute("SELECT name FROM funds WHERE id = ?", (fid,)).fetchone()[0] == "Fake Fund"
    with pytest.raises(funds.FundError):
        funds.add_fund(conn, "fake fund")
    with pytest.raises(funds.FundError):
        funds.add_fund(conn, "   ")
    funds.rename_fund(conn, fid, "Renamed")
    funds.set_fund_active(conn, fid, False)
    assert fid not in [f["id"] for f in funds.active_funds(conn)]
    assert len(log_rows(conn, "fund.add")) == 1 and len(log_rows(conn, "fund.rename")) == 1


def test_inactive_fund_cannot_be_assigned(conn):
    ids = add_people(conn, 3)
    fid = funds.add_fund(conn, "Old")
    funds.set_fund_active(conn, fid, False)
    with pytest.raises(ValueError):
        people.set_fund(conn, ids, fid)


# ---------- bulk Set Fund ----------

def maap_id(conn):
    return conn.execute("SELECT id FROM funds WHERE name = 'MAAP'").fetchone()[0]


def test_set_fund_on_everyone_is_one_logged_batch(conn):
    ids = add_people(conn, 40)
    fid = maap_id(conn)
    result = people.set_fund(conn, ids, fid)
    assert result == {"changed": 40, "unchanged": 0}
    assert conn.execute("SELECT COUNT(*) FROM people WHERE fund_id IS NULL").fetchone()[0] == 0
    batches = log_rows(conn, "fund.set_bulk")
    assert len(batches) == 1
    assert "40 changed" in batches[0]["summary"]
    detail = conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'fund.set' AND parent_id = ?",
                          (batches[0]["id"],)).fetchone()[0]
    assert detail == 40
    before_bulk = [b for b in backup.list_backups(conn.db_path) if b["reason"] == "before-set-fund"]
    assert len(before_bulk) == 2  # beside the file and on this PC
    assert people.set_fund(conn, ids, fid) == {"changed": 0, "unchanged": 40}


def test_set_fund_can_clear_and_is_blocked_when_read_only(conn, db_path):
    ids = add_people(conn, 3)
    people.set_fund(conn, ids, maap_id(conn))
    people.set_fund(conn, ids[:1], None)
    assert conn.execute("SELECT COUNT(*) FROM people WHERE fund_id IS NULL").fetchone()[0] == 1
    second = startup.open_session(db_path)
    try:
        assert second.read_only
        before = len(backup.list_backups(db_path))
        with pytest.raises(ReadOnlyError):
            people.set_fund(second.conn, ids, None)
        assert len(backup.list_backups(db_path)) == before
    finally:
        second.close()


# ---------- list and filters ----------

def test_list_filters_including_needs_fund(conn):
    ids = add_people(conn, 12)
    people.set_fund(conn, ids[:5], maap_id(conn))
    assert len(people.list_people(conn)) == 12
    assert len(people.list_people(conn, fund=people.NO_FUND)) == 7
    assert len(people.list_people(conn, fund=maap_id(conn))) == 5
    assert len(people.list_people(conn, needs_attention=True)) == 7
    db.upsert_person(conn, {"address1": ""}, ids[0])  # funded but no address
    assert len(people.list_people(conn, needs_attention=True)) == 8
    one = people.list_people(conn, search="Fake Church 3")
    assert one and all(r["id"] in ids for r in one)
    state = people.list_people(conn)[0]["state"]
    assert all(r["state"] == state for r in people.list_people(conn, state=state))
    assert people.list_people(conn, type_="Church") == []


def test_gets_column_and_both_marker(conn):
    ids = add_people(conn, 2)
    nl = conn.execute("SELECT id FROM newsletters ORDER BY id").fetchall()
    with db.transaction(conn):
        conn.execute("INSERT INTO subscriptions_v2 (person_id, newsletter_id, quantity, delivery, active) "
                     "VALUES (?, ?, 3, 'Both', 1)", (ids[0], nl[0]["id"]))
        conn.execute("INSERT INTO subscriptions_v2 (person_id, newsletter_id, quantity, delivery, active) "
                     "VALUES (?, ?, 1, 'Print', 0)", (ids[1], nl[0]["id"]))
    rows = {r["id"]: r for r in people.list_people(conn)}
    assert "(3 copies)" in rows[ids[0]]["gets"] and rows[ids[0]]["is_both"] == 1
    assert rows[ids[1]]["gets"] == "" and rows[ids[1]]["is_both"] == 0
    d = people.person_detail(conn, ids[0])
    assert d["gets"][0]["delivery"] == "Both"
    assert d["history_starts"] is None


# ---------- soft remove ----------

def test_remove_and_restore_a_person(conn):
    ids = add_people(conn, 3)
    assert people.remove_people(conn, ids[:1]) == 1
    assert len(people.list_people(conn)) == 2
    assert [r["id"] for r in people.list_people(conn, removed=True)] == ids[:1]
    assert db.get_person(conn, ids[0]) is not None  # still on file
    assert people.restore_people(conn, ids[:1]) == 1
    assert len(people.list_people(conn)) == 3
    assert len(log_rows(conn, "person.remove")) == 1 and len(log_rows(conn, "person.restore")) == 1


def test_removing_more_than_ten_needs_the_count_typed(conn):
    ids = add_people(conn, 15)
    with pytest.raises(people.ConfirmationRequired):
        people.remove_people(conn, ids)
    with pytest.raises(people.ConfirmationRequired):
        people.remove_people(conn, ids, confirm_count=14)
    assert len(people.list_people(conn)) == 15
    assert people.remove_people(conn, ids, confirm_count=15) == 15
    assert len(people.list_people(conn)) == 0
    assert len(log_rows(conn, "person.remove_bulk")) == 1
    assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'person.remove'").fetchone()[0] == 15
    assert people.remove_people(conn, ids[:10]) == 0  # exactly ten needs no typing; already removed
    assert people.restore_people(conn, ids) == 15


def test_removed_people_are_left_out_of_subscriber_lists_and_exports(conn, tmp_path):
    ids = add_people(conn, 4)
    nl = conn.execute("SELECT id FROM newsletters ORDER BY id").fetchone()[0]
    for pid in ids:
        db.add_subscription(conn, pid, nl, 2)
    people.remove_people(conn, ids[:1])
    assert len(db.list_subscribers(conn, nl)) == 3
    assert db.subscription_totals(conn, nl) == (3, 6)
    assert ids[0] not in [r["id"] for r in db.people_not_subscribed(conn, nl)]
    people.remove_people(conn, ids[1:2])
    assert exporter.export_newsletter_csv(conn, nl, str(tmp_path / "out.csv")) == 2
    removed_unsubscribed = add_people(conn, 1, last_name="Gone")[0]
    people.remove_people(conn, [removed_unsubscribed])
    assert removed_unsubscribed not in [r["id"] for r in db.people_not_subscribed(conn, nl)]


def test_editing_a_removed_person_does_not_bring_them_back(conn):
    pid = add_people(conn, 1)[0]
    people.remove_people(conn, [pid])
    db.upsert_person(conn, {"city": "Elsewhere"}, pid)
    assert db.get_person(conn, pid)["active"] == 0


# ---------- starter newsletters ----------

def test_starter_newsletters_are_not_recreated_after_first_run(db_path):
    s = startup.open_session(db_path)
    names = [n["name"] for n in db.list_newsletters(s.conn)]
    assert sorted(names) == sorted(n for n, _ in db.DEFAULT_NEWSLETTERS)
    for n in db.list_newsletters(s.conn):
        db.delete_newsletter(s.conn, n["id"])
    s.close()
    s = startup.open_session(db_path)
    try:
        assert db.list_newsletters(s.conn) == []
    finally:
        s.close()


def test_activity_recent_lists_newest_first(conn):
    people.set_fund(conn, add_people(conn, 2), maap_id(conn))
    rows = activity.recent(conn, 5)
    assert rows[0]["id"] > rows[-1]["id"]
