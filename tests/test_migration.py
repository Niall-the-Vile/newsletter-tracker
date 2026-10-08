import os
import sqlite3

import pytest

import db
from data import migrations
from data.connection import connect
from services import backup, startup
from tests import factories


def counts(path):
    c = sqlite3.connect(path)
    try:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in migrations.LEGACY_TABLES)
    finally:
        c.close()


def ids(path, table):
    c = sqlite3.connect(path)
    try:
        return [r[0] for r in c.execute(f"SELECT id FROM {table} ORDER BY id")]
    finally:
        c.close()


def test_fresh_file_is_created_at_current_version(db_path):
    s = startup.open_session(db_path)
    try:
        assert migrations.get_version(s.conn) == migrations.SCHEMA_VERSION
        names = [r["name"] for r in db.list_newsletters(s.conn)]
        assert names == sorted(n for n, _ in db.DEFAULT_NEWSLETTERS)
        assert {b["reason"] for b in backup.list_backups(db_path)} == {"daily"}  # no update backup: nothing to protect
    finally:
        s.close()


def test_legacy_file_migrates_with_rows_and_ids_kept(legacy_db):
    path, before = legacy_db
    people_ids, sub_ids = ids(path, "people"), ids(path, "subscriptions")
    s = startup.open_session(path)
    s.close()
    assert counts(path) == before
    assert ids(path, "people") == people_ids and ids(path, "subscriptions") == sub_ids
    assert len(backup.list_backups(path)) == 2  # one beside the file, one on this PC


def test_backup_exists_in_both_places_with_matching_counts(legacy_db):
    path, before = legacy_db
    startup.open_session(path).close()
    found = backup.list_backups(path)
    assert {b["where"] for b in found} == {"Next to the data file", "On this PC"}
    assert all(b["counts"] == before for b in found)


def test_migrated_file_uses_safe_journal_settings(legacy_db):
    path, _ = legacy_db
    s = startup.open_session(path)
    try:
        assert s.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert s.conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    finally:
        s.close()


def test_rerun_is_a_noop(legacy_db):
    path, _ = legacy_db
    startup.open_session(path).close()
    c = connect(path)
    log_before = c.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
    assert migrations.migrate(c) == []
    assert c.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0] == log_before
    c.close()
    n_backups = len(backup.list_backups(path))
    startup.open_session(path).close()  # no second "before update" backup
    assert len(backup.list_backups(path)) == n_backups


def test_forced_mid_step_failure_keeps_previous_version(legacy_db):
    path, before = legacy_db
    broken = [(1, migrations._v1()), (2, migrations._v2() + ["THIS IS NOT SQL"]), (3, migrations._v3())]
    c = connect(path)
    with pytest.raises(sqlite3.Error):
        migrations.migrate(c, broken)
    assert migrations.get_version(c) == 1  # step 1 was complete, step 2 left no trace
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "funds" not in tables and "activity_log" not in tables
    assert "fund_id" not in [r[1] for r in c.execute("PRAGMA table_info(people)")]
    c.close()
    assert counts(path) == before


def test_old_code_write_hits_abort_trigger(legacy_db, raw):
    path, _ = legacy_db
    startup.open_session(path).close()
    for sql in ("INSERT INTO people (first_name) VALUES ('X')",
                "UPDATE people SET first_name = 'X'",
                "DELETE FROM subscriptions",
                "INSERT INTO subscriptions (person_id, newsletter_id) VALUES (1, 1)"):
        with pytest.raises(sqlite3.IntegrityError, match="out of date"):
            raw.execute(sql)
    raw.rollback()
    assert counts(path)[0] == 25


def test_new_code_still_writes_legacy_tables_and_closes_the_gate(legacy_db, raw):
    path, _ = legacy_db
    s = startup.open_session(path)
    try:
        pid = db.upsert_person(s.conn, {"first_name": "New", "last_name": "Person"})
        db.add_subscription(s.conn, pid, 1, 4)
        assert db.person_subscriptions_summary(s.conn, pid) == (1, 4)
    finally:
        s.close()
    assert raw.execute("SELECT value FROM meta WHERE key='legacy_writes'").fetchone()[0] == "0"
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO people (first_name) VALUES ('X')")


def test_newer_file_is_refused(legacy_db, raw):
    path, _ = legacy_db
    raw.execute(f"PRAGMA user_version = {migrations.SCHEMA_VERSION + 1}")
    raw.commit()
    with pytest.raises(migrations.NewerVersionError, match="update your copy"):
        startup.open_session(path)
    assert not os.path.exists(path + ".lock")


def test_declined_update_changes_nothing(legacy_db):
    path, _ = legacy_db
    with pytest.raises(startup.UpdateDeclined):
        startup.open_session(path, confirm_update=lambda: False)
    c = sqlite3.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    c.close()
    assert not os.path.exists(path + ".lock")


def test_backup_mismatch_aborts_migration(legacy_db, monkeypatch):
    path, _ = legacy_db
    real = backup.table_counts
    calls = []

    def lying(conn):
        calls.append(1)
        out = real(conn)
        if len(calls) > 1:  # second call is the copy
            out["people"] += 1
        return out

    monkeypatch.setattr(backup, "table_counts", lying)
    with pytest.raises(backup.BackupError):
        startup.open_session(path)
    c = sqlite3.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    c.close()
    assert not any(f.endswith(".db") for f in os.listdir(backup.beside_dir(path)))


def test_restore_from_backup(legacy_db):
    path, before = legacy_db
    s = startup.open_session(path)
    pid = db.upsert_person(s.conn, {"first_name": "Later", "last_name": "Addition"})
    assert pid
    s.close()
    oldest = backup.list_backups(path)[-1]
    assert oldest["counts"] == before
    n = len(backup.list_backups(path))
    got = backup.restore_backup(path, oldest["path"])
    assert got == before and counts(path) == before
    assert len(backup.list_backups(path)) > n  # the safety backup
    startup.open_session(path).close()  # an older file migrates again
    assert counts(path) == before


def test_restore_refuses_a_newer_backup(legacy_db, tmp_path):
    path, _ = legacy_db
    other = str(tmp_path / "newer.db")
    factories.make_legacy_db(other)
    c = sqlite3.connect(other)
    c.execute(f"PRAGMA user_version = {migrations.SCHEMA_VERSION + 1}")
    c.close()
    with pytest.raises(migrations.NewerVersionError):
        backup.restore_backup(path, other)


def test_newsletter_with_old_subscriptions_cannot_be_hard_deleted(legacy_db):
    path, _ = legacy_db
    s = startup.open_session(path)
    try:
        assert db.delete_newsletter(s.conn, 1) == "archived"
        assert s.conn.execute("SELECT archived FROM newsletters WHERE id = 1").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="Archive it instead"):
            s.conn.execute("DELETE FROM newsletters WHERE id = 1")
        legacy_before = s.conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        db.add_newsletter(s.conn, "Spare")
        spare = s.conn.execute("SELECT id FROM newsletters WHERE name = 'Spare'").fetchone()[0]
        assert db.delete_newsletter(s.conn, spare) == "deleted"
        assert s.conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == legacy_before
    finally:
        s.close()


def test_saved_mailings_cannot_be_edited_or_deleted(session):
    conn = session.conn
    nid = conn.execute("SELECT id FROM newsletters LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO mailing_runs (newsletter_id, newsletter_name, issue_label, channel, sent_on) "
                 "VALUES (?, 'N', 'Fall', 'Print', '2026-01-01')", (nid,))
    run = conn.execute("SELECT id FROM mailing_runs").fetchone()[0]
    conn.execute("INSERT INTO mailing_run_rows (run_id, recipient_name, quantity) VALUES (?, 'Fake', 2)", (run,))
    for sql in ("UPDATE mailing_runs SET issue_label = 'Other'", "UPDATE mailing_runs SET row_count = 9",
                "DELETE FROM mailing_runs", "UPDATE mailing_run_rows SET quantity = 3",
                "UPDATE mailing_run_rows SET address1 = 'X'", "DELETE FROM mailing_run_rows"):
        with pytest.raises(sqlite3.IntegrityError, match="cannot be"):
            conn.execute(sql)
    conn.execute("UPDATE mailing_runs SET status = 'sent'")
    conn.execute("UPDATE mailing_run_rows SET send_status = 'sent'")
    assert conn.execute("SELECT status FROM mailing_runs").fetchone()[0] == "sent"
    with pytest.raises(sqlite3.IntegrityError, match="Archive it instead"):
        conn.execute("DELETE FROM newsletters WHERE id = ?", (nid,))


def test_upgrade_from_version_six_adds_the_guards(legacy_db):
    path, _ = legacy_db
    c = connect(path)
    migrations.migrate(c, migrations.STEPS[:6])
    assert migrations.get_version(c) == 6
    assert migrations.migrate(c) == [7]
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert {"newsletters_keep_when_used", "mailing_runs_keep_history", "mailing_run_rows_keep_history",
            "mailing_runs_no_delete", "mailing_run_rows_no_delete"} <= names
    c.close()
