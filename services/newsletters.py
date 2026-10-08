"""Newsletters in Setup: the list, the fund a newsletter draws from, and archiving."""
from data import activity
from data.connection import require_writable, transaction


def list_all(conn):
    return conn.execute(
        "SELECT n.id, n.name, n.archived, n.fund_id, f.name AS fund_name, "
        "(SELECT COUNT(*) FROM mailing_runs m WHERE m.newsletter_id = n.id) AS runs, "
        "(SELECT COUNT(*) FROM subscriptions_v2 s WHERE s.newsletter_id = n.id AND s.active = 1) AS subscribers "
        "FROM newsletters n LEFT JOIN funds f ON f.id = n.fund_id ORDER BY n.archived, n.name").fetchall()


def mismatched_count(conn, newsletter_id, fund_id):
    """Active subscriptions whose person or church would no longer qualify if the newsletter used this fund."""
    if fund_id is None:
        return 0
    return conn.execute(
        "SELECT COUNT(*) FROM subscriptions_v2 s "
        "LEFT JOIN people p ON p.id = s.person_id LEFT JOIN churches c ON c.id = s.church_id "
        "WHERE s.newsletter_id = ? AND s.active = 1 AND COALESCE(p.fund_id, c.fund_id, 0) <> ?",
        (newsletter_id, fund_id)).fetchone()[0]


def set_fund(conn, newsletter_id, fund_id):
    """None means any fund. Returns how many current subscribers no longer qualify (they stay, but are left out)."""
    require_writable(conn)
    with transaction(conn):
        nl = conn.execute("SELECT name FROM newsletters WHERE id = ?", (newsletter_id,)).fetchone()
        fund = conn.execute("SELECT name FROM funds WHERE id = ?", (fund_id,)).fetchone() if fund_id else None
        if fund_id and fund is None:
            raise ValueError("That fund is not available.")
        conn.execute("UPDATE newsletters SET fund_id = ? WHERE id = ?", (fund_id, newsletter_id))
        mismatched = mismatched_count(conn, newsletter_id, fund_id)
        activity.log(conn, "newsletter.fund",
                     f"{nl['name']} now draws from {fund['name'] if fund else 'any fund'}"
                     f" ({mismatched} subscribers do not match)", "newsletter", newsletter_id)
    return mismatched


def set_archived(conn, newsletter_id, archived):
    require_writable(conn)
    with transaction(conn):
        nl = conn.execute("SELECT name FROM newsletters WHERE id = ?", (newsletter_id,)).fetchone()
        conn.execute("UPDATE newsletters SET archived = ? WHERE id = ?", (1 if archived else 0, newsletter_id))
        activity.log(conn, "newsletter.archive", f"{'Archived' if archived else 'Brought back'} {nl['name']}",
                     "newsletter", newsletter_id)
