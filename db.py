"""SQLite data layer for the Newsletter Tracker.

The schema itself lives in data/migrations.py. Writes go through data.connection.transaction()
and are logged in the same transaction.

Tables:
  people        - master directory of reps/members (one row per person)
  newsletters   - the publications being tracked (e.g. "MAAP Newsletter", "Our Health")
  subscriptions - join table: which people receive which newsletter, and how many copies
"""
from data import activity
from data.connection import connect, now, transaction  # noqa: F401  (re-exported for callers)

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


# ---------- People ----------

def list_people(conn, search="", recipient_type="All", state="All"):
    query = "SELECT * FROM people WHERE 1=1"
    params = []
    if search:
        query += (
            " AND (COALESCE(first_name,'') || ' ' || COALESCE(middle_name,'') || ' ' || COALESCE(last_name,'') || ' ' "
            "|| COALESCE(church_name,'') || ' ' || COALESCE(conference,'') LIKE ?)"
        )
        params.append(f"%{search}%")
    if recipient_type != "All":
        query += " AND recipient_type = ?"
        params.append(recipient_type)
    if state != "All":
        query += " AND state = ?"
        params.append(state)
    query += " ORDER BY last_name, first_name"
    return conn.execute(query, params).fetchall()


def get_person(conn, person_id):
    return conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()


def distinct_states(conn):
    rows = conn.execute(
        "SELECT DISTINCT state FROM people WHERE state IS NOT NULL AND state != '' ORDER BY state"
    ).fetchall()
    return [r["state"] for r in rows]


def upsert_person(conn, data, person_id=None):
    ts = now()
    with transaction(conn):
        if person_id:
            existing = get_person(conn, person_id)
            merged = {
                k: (data[k] if k in data else (existing[k] if existing else None))
                for k in PEOPLE_COLUMNS
            }
            merged["updated_at"] = ts
            set_clause = ", ".join(f"{col} = :{col}" for col in merged)
            merged["id"] = person_id
            conn.execute(f"UPDATE people SET {set_clause} WHERE id = :id", merged)
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


def delete_person(conn, person_id):
    with transaction(conn):
        conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        activity.log(conn, "person.delete", "Deleted a person", "person", person_id)


def person_subscriptions_summary(conn, person_id):
    row = conn.execute(
        "SELECT COUNT(*) AS newsletters, COALESCE(SUM(quantity),0) AS copies "
        "FROM subscriptions WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    return row["newsletters"], row["copies"]


# ---------- Newsletters ----------

def list_newsletters(conn):
    return conn.execute("SELECT * FROM newsletters ORDER BY name").fetchall()


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
    with transaction(conn):
        conn.execute("DELETE FROM newsletters WHERE id = ?", (newsletter_id,))
        activity.log(conn, "newsletter.delete", "Deleted a newsletter", "newsletter", newsletter_id)


# ---------- Subscriptions ----------

def list_subscribers(conn, newsletter_id, search=""):
    query = (
        "SELECT s.id AS sub_id, s.quantity, s.active AS sub_active, s.notes AS sub_notes, p.* "
        "FROM subscriptions s JOIN people p ON p.id = s.person_id "
        "WHERE s.newsletter_id = ?"
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
        "SELECT COUNT(*) AS recipients, COALESCE(SUM(quantity),0) AS copies "
        "FROM subscriptions WHERE newsletter_id = ? AND active = 1",
        (newsletter_id,),
    ).fetchone()
    return row["recipients"], row["copies"]


def people_not_subscribed(conn, newsletter_id, search=""):
    query = (
        "SELECT * FROM people WHERE id NOT IN "
        "(SELECT person_id FROM subscriptions WHERE newsletter_id = ?)"
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
            "ON CONFLICT(person_id, newsletter_id) DO UPDATE SET quantity=excluded.quantity, active=1",
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
