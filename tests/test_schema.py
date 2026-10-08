import sqlite3

import pytest

import db
from data import activity
from data.connection import ConflictError, ReadOnlyError, connect, transaction


def add_church(conn, name="Fake Church"):
    return conn.execute("INSERT INTO churches (name, name_key) VALUES (?, ?)", (name, name.lower())).lastrowid


def add_person(conn, n=1):
    return db.upsert_person(conn, {"first_name": f"P{n}", "last_name": "Fake"})


def test_funds_are_seeded(conn):
    assert [r["name"] for r in conn.execute("SELECT name FROM funds ORDER BY name")] == ["AMCA", "MAAP"]


def test_new_tables_start_empty(conn):
    for t in ("churches", "church_reps", "subscriptions_v2", "mailing_runs", "mailing_run_rows",
              "church_aliases", "subscription_changes"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


def test_one_active_default_rep_per_church(conn):
    ch = add_church(conn)
    a, b = add_person(conn, 1), add_person(conn, 2)
    ins = "INSERT INTO church_reps (church_id, person_id, is_default, active) VALUES (?, ?, ?, ?)"
    conn.execute(ins, (ch, a, 1, 1))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (ch, b, 1, 1))
    conn.execute(ins, (ch, b, 0, 1))
    conn.execute(ins, (ch, b, 1, 0))  # an inactive default does not count


def test_subscription_v2_rules(conn):
    ch, p = add_church(conn), add_person(conn)
    ins = "INSERT INTO subscriptions_v2 (person_id, church_id, newsletter_id, quantity, delivery) VALUES (?,?,?,?,?)"
    conn.execute(ins, (p, None, 1, 1, "Print"))
    conn.execute(ins, (None, ch, 1, 40, "Both"))
    bad = [(p, ch, 2, 1, "Print"), (None, None, 2, 1, "Print"), (p, None, 2, 0, "Print"),
           (p, None, 2, 1, "Fax"), (p, None, 1, 1, "Print"), (None, ch, 1, 5, "Print")]
    for args in bad:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ins, args)
    assert conn.execute("SELECT delivery FROM subscriptions_v2 WHERE person_id = ?", (p,)).fetchone()[0] == "Print"


def test_entity_view_unions_churches_and_people(conn):
    add_church(conn)
    add_person(conn)
    kinds = [r["entity_type"] for r in conn.execute("SELECT entity_type FROM v_entities ORDER BY entity_type")]
    assert kinds == ["Church", "Member"]


def test_fund_cannot_be_deleted_while_a_church_uses_it(conn):
    fund = conn.execute("SELECT id FROM funds WHERE name='MAAP'").fetchone()[0]
    with transaction(conn):
        conn.execute("INSERT INTO churches (name, name_key, fund_id) VALUES ('C', 'c', ?)", (fund,))
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            conn.execute("DELETE FROM funds WHERE id = ?", (fund,))


def test_newsletter_with_runs_cannot_be_deleted(conn):
    with transaction(conn):
        conn.execute(
            "INSERT INTO mailing_runs (newsletter_id, newsletter_name, issue_label, channel, sent_on) "
            "VALUES (1, 'N', 'Fall', 'Print', '2026-10-03')")
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            conn.execute("DELETE FROM newsletters WHERE id = 1")


def test_activity_is_written_in_the_same_transaction(conn):
    before = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
    pid = add_person(conn)
    row = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["action"], row["entity_id"]) == ("person.add", pid)
    assert row["user"] and row["app_version"]
    assert conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0] == before + 1

    with pytest.raises(RuntimeError):
        with transaction(conn):
            db.upsert_person(conn, {"first_name": "Gone"})
            raise RuntimeError("fail after the change and its log row")
    assert conn.execute("SELECT COUNT(*) FROM people WHERE first_name='Gone'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0] == before + 1


def test_bulk_activity_has_summary_and_per_entity_rows(conn):
    with transaction(conn):
        parent = activity.log(conn, "bulk.test", "Did 2 things")
        for i in (1, 2):
            activity.log(conn, "thing", "one", "person", i, parent_id=parent)
    assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE parent_id = ?", (parent,)).fetchone()[0] == 2


def test_each_commit_bumps_data_version(conn):
    v = int(conn.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()[0])
    add_person(conn)
    assert int(conn.execute("SELECT value FROM meta WHERE key='data_version'").fetchone()[0]) == v + 1
    assert conn.data_version == v + 1


def test_write_is_refused_after_someone_else_changed_the_file(session, db_path):
    other = connect(db_path)
    add_person(other)
    other.close()
    with pytest.raises(ConflictError):
        add_person(session.conn)
    assert not session.conn.in_transaction


def test_read_only_connection_cannot_write(db_path, session):
    c = connect(db_path, read_only=True)
    with pytest.raises(ReadOnlyError):
        add_person(c)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("INSERT INTO funds (name) VALUES ('Z')")
    c.close()
