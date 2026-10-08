"""Subscriptions for one person or church (subscriptions_v2). Nothing is hard-deleted: stopping sets active = 0."""
import getpass

from data import activity
from data.connection import now, require_writable, transaction
from services import mailings

PENDING_NOTE = "legacy import default, pending review"
PLAIN_DELIVERY = {"Print": "Mail", "Email": "Email", "Both": "Both"}
DELIVERY_CODES = {v: k for k, v in PLAIN_DELIVERY.items()}


class SubscriptionError(Exception):
    pass


def _column(kind):
    if kind not in ("person", "church"):
        raise ValueError(kind)
    return f"{kind}_id"


def gets(conn, kind, entity_id):
    """What this person or church gets: active subscriptions and old ones waiting for review."""
    last = mailings.last_sent(conn, kind, entity_id)
    rows = conn.execute(
        f"SELECT s.id, s.newsletter_id, n.name, s.quantity, s.delivery, s.active, (s.active = 0 AND s.notes = ?) AS pending "
        f"FROM subscriptions_v2 s JOIN newsletters n ON n.id = s.newsletter_id "
        f"WHERE s.{_column(kind)} = ? ORDER BY s.active DESC, n.name", (PENDING_NOTE, entity_id)).fetchall()
    return [{"sub_id": r["id"], "newsletter_id": r["newsletter_id"], "newsletter": r["name"],
             "copies": r["quantity"], "delivery": PLAIN_DELIVERY[r["delivery"]], "pending": bool(r["pending"]),
             "last_sent": last.get(r["newsletter_id"], "")}
            for r in rows if r["active"] or r["pending"]]


def has_any(conn, kind, entity_id):
    return conn.execute(f"SELECT 1 FROM subscriptions_v2 WHERE {_column(kind)} = ?", (entity_id,)).fetchone() is not None


def _change(conn, sub_id, field, old, new, ts, user, note):
    if str(old) != str(new):
        conn.execute("INSERT INTO subscription_changes (subscription_id, field, old_value, new_value, changed_at, "
                     "changed_by, note) VALUES (?, ?, ?, ?, ?, ?, ?)", (sub_id, field, str(old), str(new), ts, user, note))


def set_subscription(conn, kind, entity_id, newsletter_id, quantity, delivery="Print"):
    """Adds the newsletter for this person or church, or changes copies and delivery on the existing one."""
    require_writable(conn)
    col = _column(kind)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise SubscriptionError("Copies must be a whole number.")
    if quantity < 1:
        raise SubscriptionError("Copies must be at least 1.")
    if delivery not in PLAIN_DELIVERY:
        raise SubscriptionError("Choose Mail, Email or Both.")
    ts, user = now(), getpass.getuser()
    with transaction(conn):
        nl = conn.execute("SELECT name, archived FROM newsletters WHERE id = ?", (newsletter_id,)).fetchone()
        if nl is None or nl["archived"]:
            raise SubscriptionError("That newsletter is not available.")
        table = "people" if kind == "person" else "churches"
        if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,)).fetchone() is None:
            raise SubscriptionError("That person or church was not found.")
        old = conn.execute(f"SELECT * FROM subscriptions_v2 WHERE {col} = ? AND newsletter_id = ?",
                           (entity_id, newsletter_id)).fetchone()
        if old is None:
            cur = conn.execute(
                f"INSERT INTO subscriptions_v2 ({col}, newsletter_id, quantity, delivery, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)", (entity_id, newsletter_id, quantity, delivery, ts, ts))
            summary = f"Now gets {nl['name']}: {quantity} copies"
        else:
            notes = None if old["notes"] == PENDING_NOTE else old["notes"]
            conn.execute("UPDATE subscriptions_v2 SET quantity = ?, delivery = ?, active = 1, notes = ?, updated_at = ? "
                         "WHERE id = ?", (quantity, delivery, notes, ts, old["id"]))
            for field, a, b in (("quantity", old["quantity"], quantity), ("delivery", old["delivery"], delivery),
                                ("active", old["active"], 1)):
                _change(conn, old["id"], field, a, b, ts, user, "edited by hand")
            summary = f"{nl['name']} changed to {quantity} copies"
        activity.log(conn, "subscription.set", summary, kind, entity_id)
    return cur.lastrowid if old is None else old["id"]


def stop_subscription(conn, sub_id):
    require_writable(conn)
    ts, user = now(), getpass.getuser()
    with transaction(conn):
        row = conn.execute("SELECT s.*, n.name FROM subscriptions_v2 s JOIN newsletters n ON n.id = s.newsletter_id "
                           "WHERE s.id = ?", (sub_id,)).fetchone()
        if row is None:
            raise SubscriptionError("That subscription was not found.")
        conn.execute("UPDATE subscriptions_v2 SET active = 0, updated_at = ? WHERE id = ?", (ts, sub_id))
        _change(conn, sub_id, "active", row["active"], 0, ts, user, "stopped by hand")
        kind, eid = ("person", row["person_id"]) if row["person_id"] else ("church", row["church_id"])
        activity.log(conn, "subscription.stop", f"Stopped getting {row['name']}", kind, eid)
