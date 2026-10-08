import sqlite3

import pytest

import db
from services import people, review, startup

PENDING = "legacy import default, pending review"


@pytest.fixture
def old_db(db_path):
    """Fake old-style file: 25 people, MAAP + Our Health, copies 1,2,3 in turn on MAAP."""
    from tests import factories
    factories.make_legacy_db(db_path)
    c = sqlite3.connect(db_path)
    c.execute("UPDATE newsletters SET name = 'MAAP Newsletter' WHERE id = 1")
    c.execute("UPDATE newsletters SET name = 'Our Health' WHERE id = 2")
    c.execute("UPDATE subscriptions SET active = 0 WHERE person_id = 4")   # stopped
    c.execute("UPDATE people SET active = 0 WHERE id = 7")                 # removed
    c.commit()
    expected = c.execute("SELECT COUNT(*) FROM subscriptions s JOIN people p ON p.id = s.person_id "
                         "WHERE s.active = 1 AND p.active = 1").fetchone()[0]
    c.close()
    assert expected == 23
    return db_path, expected


@pytest.fixture
def oconn(old_db):
    s = startup.open_session(old_db[0])
    yield s.conn
    s.close()


def legacy_rows(conn):
    return conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()


def as_tuples(rows):
    return [tuple(r) for r in rows]


def test_copy_creates_inactive_pending_rows_and_freezes_legacy(old_db, oconn):
    before = as_tuples(legacy_rows(oconn))
    assert review.count_to_copy(oconn) == old_db[1]
    assert review.copy_legacy(oconn) == old_db[1]
    assert review.pending_count(oconn) == old_db[1]
    rows = oconn.execute("SELECT * FROM subscriptions_v2").fetchall()
    assert len(rows) == old_db[1]
    assert all(r["active"] == 0 and r["notes"] == PENDING and r["delivery"] == "Print"
               for r in rows)
    assert as_tuples(legacy_rows(oconn)) == before
    assert review.copy_legacy(oconn) == 0  # nothing new to copy
    assert oconn.execute("SELECT COUNT(*) FROM subscriptions_v2").fetchone()[0] == old_db[1]


def test_pending_people_show_as_pending_review_in_the_list(old_db, oconn):
    first = people.list_people(oconn)
    assert any("MAAP Newsletter" in r["gets"] for r in first)  # old list still shown before review
    review.copy_legacy(oconn)
    pending = [r for r in people.list_people(oconn) if r["gets"] == "Pending review"]
    assert len(pending) == old_db[1]
    d = people.person_detail(oconn, pending[0]["id"])
    assert d["gets"][0]["pending"] is True


def test_preview_then_move_to_a_newsletter_then_undo(old_db, oconn):
    review.copy_legacy(oconn)
    n = old_db[1]
    health = oconn.execute("SELECT id FROM newsletters WHERE name = 'Our Health'").fetchone()[0]
    legacy = as_tuples(legacy_rows(oconn))
    assert review.preview_move(oconn, health) == {"waiting": n, "will_move": n, "already_there": 0}
    assert review.pending_count(oconn) == n  # preview changes nothing

    result = review.move_pending(oconn, health)
    assert result["moved"] == n and result["skipped"] == 0
    assert review.pending_count(oconn) == 0
    assert oconn.execute("SELECT COUNT(*) FROM subscriptions_v2 WHERE newsletter_id = ? AND active = 1",
                         (health,)).fetchone()[0] == n
    assert as_tuples(legacy_rows(oconn)) == legacy
    batch = oconn.execute("SELECT * FROM activity_log WHERE action = 'review.move'").fetchall()
    assert len(batch) == 1 and str(n) in batch[0]["summary"]
    assert oconn.execute("SELECT COUNT(*) FROM activity_log WHERE parent_id = ?", (batch[0]["id"],)).fetchone()[0] == n
    assert review.last_move(oconn)["id"] == batch[0]["id"]

    assert review.undo_move(oconn, result["batch_id"]) == {"restored": n, "skipped": 0}
    assert review.pending_count(oconn) == n
    assert oconn.execute("SELECT COUNT(*) FROM subscriptions_v2 WHERE active = 1").fetchone()[0] == 0
    assert oconn.execute("SELECT COUNT(*) FROM subscriptions_v2 WHERE newsletter_id = 1").fetchone()[0] == n
    assert review.last_move(oconn) is None
    with pytest.raises(review.ReviewError):
        review.undo_move(oconn, result["batch_id"])
    assert as_tuples(legacy_rows(oconn)) == legacy


def test_move_to_none_keeps_them_inactive_and_out_of_the_pending_list(old_db, oconn):
    review.copy_legacy(oconn)
    result = review.move_pending(oconn, None)
    assert result["moved"] == old_db[1]
    assert review.pending_count(oconn) == 0
    assert oconn.execute("SELECT COUNT(*) FROM subscriptions_v2 WHERE active = 1").fetchone()[0] == 0
    review.undo_move(oconn, result["batch_id"])
    assert review.pending_count(oconn) == old_db[1]


def test_move_only_the_selected_people(old_db, oconn):
    review.copy_legacy(oconn)
    some = [r["person_id"] for r in review.pending_rows(oconn)][:3]
    assert review.preview_move(oconn, None, some)["will_move"] == 3
    assert review.move_pending(oconn, 2, some)["moved"] == 3
    assert review.pending_count(oconn) == old_db[1] - 3


def test_people_who_already_have_the_target_are_skipped(old_db, oconn):
    review.copy_legacy(oconn)
    waiting = [r["person_id"] for r in review.pending_rows(oconn)]
    with db.transaction(oconn):
        oconn.execute("INSERT INTO subscriptions_v2 (person_id, newsletter_id, quantity, active) VALUES (?, 2, 5, 1)",
                      (waiting[0],))
    p = review.preview_move(oconn, 2)
    assert p["already_there"] == 1 and p["will_move"] == old_db[1] - 1
    result = review.move_pending(oconn, 2)
    assert result["skipped"] == 1
    assert oconn.execute("SELECT quantity FROM subscriptions_v2 WHERE person_id = ? AND newsletter_id = 2",
                         (waiting[0],)).fetchone()[0] == 5  # untouched
    assert review.pending_count(oconn) == 1


def test_undo_leaves_rows_that_changed_since(old_db, oconn):
    review.copy_legacy(oconn)
    result = review.move_pending(oconn, 2)
    pid = [r for r in oconn.execute("SELECT person_id FROM subscriptions_v2 WHERE active = 1")][0][0]
    with db.transaction(oconn):
        oconn.execute("UPDATE subscriptions_v2 SET quantity = 4, active = 0 WHERE person_id = ?", (pid,))
    undone = review.undo_move(oconn, result["batch_id"])
    assert undone["skipped"] == 1 and undone["restored"] == old_db[1] - 1


def test_removed_after_copy_drops_out_of_the_pending_list(old_db, oconn):
    review.copy_legacy(oconn)
    pid = review.pending_rows(oconn)[0]["person_id"]
    people.remove_people(oconn, [pid])
    assert review.pending_count(oconn) == old_db[1] - 1
    assert review.preview_move(oconn, None)["waiting"] == old_db[1] - 1


def test_no_maap_newsletter_means_nothing_to_copy(conn):
    conn.execute("DELETE FROM newsletters")
    assert review.count_to_copy(conn) == 0
    with pytest.raises(review.ReviewError):
        review.copy_legacy(conn)


def test_rows_with_many_copies_are_copied_with_their_quantity(old_db, oconn):
    pid = oconn.execute("SELECT person_id FROM subscriptions WHERE newsletter_id = 1 AND active = 1 "
                        "AND person_id NOT IN (4, 7) LIMIT 1").fetchone()[0]
    with db.transaction(oconn):
        oconn.execute("UPDATE subscriptions SET quantity = 40 WHERE person_id = ? AND newsletter_id = 1", (pid,))
    review.copy_legacy(oconn)
    row = oconn.execute("SELECT quantity, active, notes FROM subscriptions_v2 WHERE person_id = ?", (pid,)).fetchone()
    assert (row["quantity"], row["active"], row["notes"]) == (40, 0, PENDING)
    assert oconn.execute("SELECT COUNT(*) FROM subscriptions_v2").fetchone()[0] == old_db[1]
