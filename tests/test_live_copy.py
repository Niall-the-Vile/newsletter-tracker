"""Migrates a scratch COPY of the real database. Prints nothing from it: counts and ids only."""
import os
import shutil
import sqlite3
import tempfile

import pytest

from data import migrations
from services import backup, review, startup
from services import people as people_service

LIVE_COPY = os.environ.get(
    "NT_LIVE_COPY", os.path.join(tempfile.gettempdir(), "nt-build", "live-copy.db"))

pytestmark = pytest.mark.skipif(not os.path.exists(LIVE_COPY), reason="live-copy.db not available")


def snapshot(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for t in migrations.LEGACY_TABLES:
            out[t] = [r[0] for r in c.execute(f"SELECT id FROM {t} ORDER BY id")]
        return out
    finally:
        c.close()


@pytest.fixture
def copy(tmp_path):
    folder = tmp_path / "shared"
    folder.mkdir()
    dest = str(folder / "newsletter_tracker.db")
    shutil.copy(LIVE_COPY, dest)
    return dest


def test_live_copy_migrates_with_counts_and_ids_kept(copy):
    before = snapshot(copy)
    s = startup.open_session(copy)
    try:
        assert migrations.get_version(s.conn) == migrations.SCHEMA_VERSION
        assert s.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert s.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        s.close()
    assert snapshot(copy) == before
    found = backup.list_backups(copy)
    assert len(found) == 2
    assert all(b["counts"] == tuple(len(before[t]) for t in migrations.LEGACY_TABLES) for b in found)


def test_live_copy_rerun_is_a_noop_and_old_code_is_blocked(copy):
    startup.open_session(copy).close()
    n = len(backup.list_backups(copy))
    s = startup.open_session(copy)
    assert migrations.migrate(s.conn) == []
    s.close()
    assert len(backup.list_backups(copy)) == n
    c = sqlite3.connect(copy)
    with pytest.raises(sqlite3.IntegrityError, match="out of date"):
        c.execute("UPDATE people SET first_name = first_name WHERE id = (SELECT MIN(id) FROM people)")
    c.close()


def test_live_copy_everyone_gets_a_fund_in_one_batch_and_review_list_matches(copy):
    c = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    people_n = c.execute("SELECT COUNT(*) FROM people WHERE COALESCE(active, 1) = 1").fetchone()[0]
    maap_rows = c.execute(
        "SELECT COUNT(*) FROM subscriptions s JOIN newsletters n ON n.id = s.newsletter_id "
        "JOIN people p ON p.id = s.person_id WHERE lower(n.name) LIKE 'maap%' AND s.quantity = 1 "
        "AND s.active = 1 AND COALESCE(p.active, 1) = 1").fetchone()[0]
    legacy_before = c.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
    c.close()

    s = startup.open_session(copy)
    try:
        conn = s.conn
        fund = conn.execute("SELECT id FROM funds WHERE name = 'MAAP'").fetchone()[0]
        ids = [r["id"] for r in people_service.list_people(conn)]
        assert len(ids) == people_n
        result = people_service.set_fund(conn, ids, fund)
        assert result["changed"] == people_n
        assert conn.execute("SELECT COUNT(*) FROM people WHERE fund_id IS NULL "
                            "AND COALESCE(active, 1) = 1").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'fund.set_bulk'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM activity_log WHERE action = 'fund.set'").fetchone()[0] == people_n

        assert review.copy_legacy(conn) == maap_rows
        assert review.pending_count(conn) == maap_rows
        assert len(review.pending_rows(conn)) == maap_rows
        assert review.move_pending(conn, None)["moved"] == maap_rows
        assert review.pending_count(conn) == 0
        assert review.undo_move(conn, review.last_move(conn)["id"])["restored"] == maap_rows
        assert review.pending_count(conn) == maap_rows
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        s.close()
    c = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    assert c.execute("SELECT * FROM subscriptions ORDER BY id").fetchall() == legacy_before
    c.close()
