"""M16: the owner's review of the old MAAP subscriptions.

Old rows are copied into subscriptions_v2 as inactive "pending review" rows. The owner then picks where
they go (or none). Every move is logged with per-row old values, so it can be undone. The old
subscriptions table is only ever read here.
"""
import getpass

from data import activity
from data.connection import now, require_writable, transaction
from services import backup
from services.people import PENDING_NOTE

REVIEWED_NOTE = "legacy import default, reviewed"
NO_SUB_NOTE = "legacy import default, reviewed: not subscribed"

_PENDING_WHERE = ("s.active = 0 AND s.notes = ? AND p.id = s.person_id AND COALESCE(p.active, 1) = 1")


class ReviewError(Exception):
    pass


def maap_newsletter(conn):
    return conn.execute(
        "SELECT * FROM newsletters WHERE lower(name) LIKE 'maap%' ORDER BY id LIMIT 1").fetchone()


def _legacy_to_copy_sql():
    return ("FROM subscriptions s JOIN people p ON p.id = s.person_id "
            "WHERE s.newsletter_id = ? AND COALESCE(s.active, 1) = 1 "
            "AND COALESCE(p.active, 1) = 1 "
            "AND NOT EXISTS (SELECT 1 FROM subscriptions_v2 v WHERE v.person_id = s.person_id "
            "AND v.newsletter_id = s.newsletter_id)")


def count_to_copy(conn):
    nl = maap_newsletter(conn)
    if nl is None:
        return 0
    return conn.execute("SELECT COUNT(*) " + _legacy_to_copy_sql(), (nl["id"],)).fetchone()[0]


def copy_legacy(conn):
    """Copies every active old MAAP row, keeping its copies, into the review list. Safe to run again: it only adds what is missing."""
    require_writable(conn)
    nl = maap_newsletter(conn)
    if nl is None:
        raise ReviewError("There is no MAAP newsletter to copy from.")
    if count_to_copy(conn) == 0:
        return 0
    backup.backup_both(conn.db_path, "before-review-copy")
    ts = now()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO subscriptions_v2 (person_id, newsletter_id, quantity, delivery, active, notes, "
            "created_at, updated_at) SELECT s.person_id, s.newsletter_id, MAX(COALESCE(s.quantity, 1), 1), 'Print', 0, ?, ?, ? "
            + _legacy_to_copy_sql(), (PENDING_NOTE, ts, ts, nl["id"]))
        activity.log(conn, "review.copy",
                     f"Copied {cur.rowcount} old MAAP subscriptions into the review list")
    return cur.rowcount


def pending_count(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM subscriptions_v2 s, people p WHERE " + _PENDING_WHERE, (PENDING_NOTE,)).fetchone()[0]


def pending_rows(conn):
    return conn.execute(
        "SELECT p.id AS person_id, p.first_name, p.last_name, p.church_name, p.city, p.state, n.name AS newsletter "
        "FROM subscriptions_v2 s JOIN newsletters n ON n.id = s.newsletter_id, people p "
        "WHERE " + _PENDING_WHERE + " ORDER BY p.last_name, p.first_name", (PENDING_NOTE,)).fetchall()


def _pending_subs(conn, person_ids):
    rows = conn.execute(
        "SELECT s.id, s.person_id, s.newsletter_id FROM subscriptions_v2 s, people p WHERE "
        + _PENDING_WHERE + " ORDER BY s.id", (PENDING_NOTE,)).fetchall()
    if person_ids is not None:
        wanted = {int(i) for i in person_ids}
        rows = [r for r in rows if r["person_id"] in wanted]
    return rows


def _blocked(conn, sub, target_id):
    return target_id is not None and target_id != sub["newsletter_id"] and conn.execute(
        "SELECT 1 FROM subscriptions_v2 WHERE person_id = ? AND newsletter_id = ?",
        (sub["person_id"], target_id)).fetchone() is not None


def preview_move(conn, target_id, person_ids=None):
    """Counts only. target_id None means 'no newsletter'. person_ids None means everyone waiting."""
    subs = _pending_subs(conn, person_ids)
    blocked = sum(1 for s in subs if _blocked(conn, s, target_id))
    return {"waiting": len(subs), "will_move": len(subs) - blocked, "already_there": blocked}


def move_pending(conn, target_id, person_ids=None):
    require_writable(conn)
    target_name = "no newsletter"
    if target_id is not None:
        row = conn.execute("SELECT name FROM newsletters WHERE id = ?", (target_id,)).fetchone()
        if row is None:
            raise ReviewError("That newsletter no longer exists.")
        target_name = row["name"]
    subs = _pending_subs(conn, person_ids)
    if len(subs) > 1:
        backup.backup_both(conn.db_path, "before-review-move")
    ts, user, moved, skipped = now(), getpass.getuser(), 0, 0
    with transaction(conn):
        batch = activity.log(conn, "review.move", f"Moving {len(subs)} old subscriptions to {target_name}")
        note = f"batch {batch}"
        for s in subs:
            if _blocked(conn, s, target_id):
                skipped += 1
                continue
            new = {"newsletter_id": s["newsletter_id"] if target_id is None else target_id,
                   "active": 0 if target_id is None else 1,
                   "notes": NO_SUB_NOTE if target_id is None else REVIEWED_NOTE}
            old = {"newsletter_id": s["newsletter_id"], "active": 0, "notes": PENDING_NOTE}
            conn.execute("UPDATE subscriptions_v2 SET newsletter_id = ?, active = ?, notes = ?, updated_at = ? "
                         "WHERE id = ?", (new["newsletter_id"], new["active"], new["notes"], ts, s["id"]))
            for field in new:
                conn.execute(
                    "INSERT INTO subscription_changes (subscription_id, field, old_value, new_value, changed_at, "
                    "changed_by, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (s["id"], field, str(old[field]), str(new[field]), ts, user, note))
            activity.log(conn, "review.move_one", f"Old subscription moved to {target_name}", "person",
                         s["person_id"], batch)
            moved += 1
        activity.set_summary(conn, batch, f"Moved {moved} old subscriptions to {target_name} ({skipped} skipped)")
    return {"moved": moved, "skipped": skipped, "batch_id": batch}


def last_move(conn):
    """The latest move that has not been undone, or None."""
    return conn.execute(
        "SELECT id, ts, summary FROM activity_log a WHERE action = 'review.move' AND NOT EXISTS "
        "(SELECT 1 FROM activity_log u WHERE u.action = 'review.undo' AND u.parent_id = a.id) "
        "ORDER BY id DESC LIMIT 1").fetchone()


def undo_move(conn, batch_id):
    """Puts back every row of a move that is still as the move left it. Others are skipped and counted."""
    require_writable(conn)
    if conn.execute("SELECT 1 FROM activity_log WHERE action = 'review.undo' AND parent_id = ?",
                    (batch_id,)).fetchone():
        raise ReviewError("That move was already undone.")
    changes = conn.execute(
        "SELECT * FROM subscription_changes WHERE note = ? ORDER BY id", (f"batch {batch_id}",)).fetchall()
    by_sub = {}
    for c in changes:
        by_sub.setdefault(c["subscription_id"], []).append(c)
    backup.backup_both(conn.db_path, "before-review-undo")
    ts, restored, skipped = now(), 0, 0
    with transaction(conn):
        undo = activity.log(conn, "review.undo", "Undoing a move of old subscriptions", parent_id=batch_id)
        for sub_id, fields in by_sub.items():
            row = conn.execute("SELECT * FROM subscriptions_v2 WHERE id = ?", (sub_id,)).fetchone()
            if row is None or any(str(row[c["field"]]) != c["new_value"] for c in fields):
                skipped += 1
                continue
            old = {c["field"]: c["old_value"] for c in fields}
            clash = conn.execute(
                "SELECT 1 FROM subscriptions_v2 WHERE person_id = ? AND newsletter_id = ? AND id <> ?",
                (row["person_id"], int(old["newsletter_id"]), sub_id)).fetchone()
            if clash:
                skipped += 1
                continue
            conn.execute("UPDATE subscriptions_v2 SET newsletter_id = ?, active = ?, notes = ?, updated_at = ? "
                         "WHERE id = ?", (int(old["newsletter_id"]), int(old["active"]), old["notes"], ts, sub_id))
            activity.log(conn, "review.undo_one", "Old subscription put back for review", "person",
                         row["person_id"], undo)
            restored += 1
        activity.set_summary(conn, undo, f"Put {restored} old subscriptions back for review ({skipped} skipped)")
    return {"restored": restored, "skipped": skipped}
