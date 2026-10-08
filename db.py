"""SQLite data layer for the Newsletter Tracker.

The schema itself lives in data/migrations.py. Writes go through data.connection.transaction()
and are logged in the same transaction.

Tables:
  people        - master directory of reps/members (one row per person)
  newsletters   - the publications being tracked (e.g. "MAAP Newsletter", "Our Health")
  subscriptions - join table: which people receive which newsletter, and how many copies
"""
from data import activity
from data.connection import ConflictError, connect, now, transaction  # noqa: F401  (re-exported for callers)

DEFAULT_NEWSLETTERS = [
    ("MAAP Newsletter", "Sent to church reps in bulk; reps distribute copies into church mailboxes."),
    ("Our Health", "Sent directly to each member's home address."),
]

PEOPLE_COLUMNS = [
    "first_name", "middle_name", "last_name", "address1", "address2",
    "city", "state", "zip", "home_phone", "cell_phone", "email",
    "church_name", "conference", "title", "recipient_type", "verified",
    "notes", "source", "active",
]

ADDRESS_FIELDS = ["address1", "address2", "city", "state", "zip"]

# ---------- People ----------

def get_person(conn, person_id):
    return conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()


def upsert_person(conn, data, person_id=None, expected_updated_at=None):
    """Adds or edits a person. expected_updated_at is the time the screen last showed; if the row changed
    since, nothing is saved."""
    ts = now()
    with transaction(conn):
        if person_id:
            existing = get_person(conn, person_id)
            merged = {
                k: (data[k] if k in data else (existing[k] if existing else None))
                for k in PEOPLE_COLUMNS
            }
            merged["updated_at"] = ts
            address_changed = existing is not None and any(
                (existing[k] or "") != (merged[k] or "") for k in ADDRESS_FIELDS)
            set_clause = ", ".join(f"{col} = :{col}" for col in merged)
            merged["id"] = person_id
            merged["expected"] = expected_updated_at
            cur = conn.execute(f"UPDATE people SET {set_clause} WHERE id = :id "
                               "AND (:expected IS NULL OR updated_at IS :expected)", merged)
            if cur.rowcount == 0 and expected_updated_at is not None:
                raise ConflictError("Someone else changed this person after you opened them. "
                                    "Close this window and look again.")
            if address_changed:  # a new address clears the returned-mail flag
                conn.execute("UPDATE people SET returned_mail = 0 WHERE id = ?", (person_id,))
                conn.execute("UPDATE churches SET returned_mail = 0 WHERE id IN (SELECT church_id FROM church_reps "
                             "WHERE person_id = ? AND is_default = 1 AND active = 1)", (person_id,))
            activity.log(conn, "person.update", "Edited a person", "person", person_id)
            return person_id
        data = {k: data.get(k) for k in PEOPLE_COLUMNS}
        data["created_at"] = ts
        data["updated_at"] = ts
        cols = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())
        cur = conn.execute(f"INSERT INTO people ({cols}) VALUES ({placeholders})", data)
        activity.log(conn, "person.add", "Added a person", "person", cur.lastrowid)
        return cur.lastrowid


def _normalize_match(value):
    return " ".join((value or "").split()).lower()


def find_duplicate_person(conn, first_name, last_name, address1, zip_code):
    zip5 = (zip_code or "")[:5]
    addr_norm = _normalize_match(address1)
    candidates = conn.execute(
        "SELECT * FROM people WHERE lower(first_name)=lower(?) AND lower(last_name)=lower(?)",
        (first_name or "", last_name or ""),
    ).fetchall()
    for row in candidates:
        if _normalize_match(row["address1"]) == addr_norm and (row["zip"] or "")[:5] == zip5:
            return row
    return None


def person_subscriptions_summary(conn, person_id):
    row = conn.execute(
        "SELECT COUNT(*) AS newsletters, COALESCE(SUM(quantity),0) AS copies "
        "FROM subscriptions WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    return row["newsletters"], row["copies"]


# ---------- Newsletters ----------

def list_newsletters(conn, include_archived=False):
    where = "" if include_archived else " WHERE archived = 0"
    return conn.execute(f"SELECT * FROM newsletters{where} ORDER BY name").fetchall()


def add_newsletter(conn, name, description=""):
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO newsletters (name, description, created_at) VALUES (?, ?, ?)",
            (name, description, now()),
        )
        activity.log(conn, "newsletter.add", "Added a newsletter", "newsletter", cur.lastrowid)


def rename_newsletter(conn, newsletter_id, new_name, description=""):
    with transaction(conn):
        conn.execute(
            "UPDATE newsletters SET name = ?, description = ? WHERE id = ?",
            (new_name, description, newsletter_id),
        )
        activity.log(conn, "newsletter.rename", "Renamed a newsletter", "newsletter", newsletter_id)


def delete_newsletter(conn, newsletter_id):
    """Deletes a newsletter with no history at all. Otherwise archives it, so nothing on record is lost."""
    with transaction(conn):
        used = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM mailing_runs WHERE newsletter_id = ?) "
            "OR EXISTS (SELECT 1 FROM subscriptions_v2 WHERE newsletter_id = ?) "
            "OR EXISTS (SELECT 1 FROM subscriptions WHERE newsletter_id = ?)",
            (newsletter_id, newsletter_id, newsletter_id)
        ).fetchone()[0]
        if used:
            conn.execute("UPDATE newsletters SET archived = 1 WHERE id = ?", (newsletter_id,))
            activity.log(conn, "newsletter.archive", "Archived a newsletter", "newsletter", newsletter_id)
            return "archived"
        conn.execute("DELETE FROM newsletters WHERE id = ?", (newsletter_id,))
        activity.log(conn, "newsletter.delete", "Deleted a newsletter", "newsletter", newsletter_id)
        return "deleted"


# ---------- Subscriptions ----------

def list_subscribers(conn, newsletter_id, search=""):
    query = (
        "SELECT s.id AS sub_id, s.quantity, s.active AS sub_active, s.notes AS sub_notes, p.* "
        "FROM subscriptions s JOIN people p ON p.id = s.person_id "
        "WHERE s.newsletter_id = ? AND COALESCE(p.active, 1) = 1"
    )
    params = [newsletter_id]
    if search:
        query += (
            " AND (COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'') || ' ' "
            "|| COALESCE(p.church_name,'') || ' ' || COALESCE(p.conference,'') LIKE ?)"
        )
        params.append(f"%{search}%")
    query += " ORDER BY p.last_name, p.first_name"
    return conn.execute(query, params).fetchall()


def subscription_totals(conn, newsletter_id):
    row = conn.execute(
        "SELECT COUNT(*) AS recipients, COALESCE(SUM(s.quantity),0) AS copies "
        "FROM subscriptions s JOIN people p ON p.id = s.person_id "
        "WHERE s.newsletter_id = ? AND s.active = 1 AND COALESCE(p.active, 1) = 1",
        (newsletter_id,),
    ).fetchone()
    return row["recipients"], row["copies"]


def people_not_subscribed(conn, newsletter_id, search=""):
    query = (
        "SELECT * FROM people WHERE COALESCE(active, 1) = 1 AND NOT EXISTS "
        "(SELECT 1 FROM subscriptions s WHERE s.person_id = people.id AND s.newsletter_id = ?)"
    )
    params = [newsletter_id]
    if search:
        query += (
            " AND (COALESCE(first_name,'') || ' ' || COALESCE(last_name,'') || ' ' || COALESCE(church_name,'') || ' ' "
            "|| COALESCE(conference,'') LIKE ?)"
        )
        params.append(f"%{search}%")
    query += " ORDER BY last_name, first_name"
    return conn.execute(query, params).fetchall()


def is_subscribed(conn, person_id, newsletter_id):
    row = conn.execute(
        "SELECT 1 FROM subscriptions WHERE person_id = ? AND newsletter_id = ?",
        (person_id, newsletter_id),
    ).fetchone()
    return row is not None


def add_subscription(conn, person_id, newsletter_id, quantity=1):
    with transaction(conn):
        conn.execute(
            "INSERT INTO subscriptions (person_id, newsletter_id, quantity, active) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(person_id, newsletter_id) DO UPDATE SET active=1, "
            "quantity=CASE WHEN active=1 THEN quantity ELSE excluded.quantity END",  # keep copies already set
            (person_id, newsletter_id, quantity),
        )
        activity.log(conn, "subscription.add", f"Added to newsletter {newsletter_id}, {quantity} copies",
                     "person", person_id)


def update_subscription_quantity(conn, sub_id, quantity):
    with transaction(conn):
        conn.execute("UPDATE subscriptions SET quantity = ? WHERE id = ?", (quantity, sub_id))
        activity.log(conn, "subscription.quantity", f"Copies set to {quantity}", "subscription", sub_id)


def remove_subscription(conn, sub_id):
    with transaction(conn):
        conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        activity.log(conn, "subscription.remove", "Removed from a newsletter", "subscription", sub_id)
