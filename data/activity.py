"""Activity log. Rows are written inside the same transaction as the change they describe."""
import getpass

from data.connection import now, transaction
from version import APP_VERSION


def log(conn, action, summary="", entity_type=None, entity_id=None, parent_id=None):
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO activity_log (ts, user, app_version, action, entity_type, entity_id, summary, parent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), getpass.getuser(), APP_VERSION, action, entity_type, entity_id, summary, parent_id),
        )
    return cur.lastrowid


def recent(conn, limit=200):
    return conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def set_summary(conn, log_id, summary):
    conn.execute("UPDATE activity_log SET summary = ? WHERE id = ?", (summary, log_id))
