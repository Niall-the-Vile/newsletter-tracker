"""People & Churches list (members only for now), detail pane data, bulk Set Fund, Remove and Restore."""
from data import activity
from data.connection import now, require_writable, transaction
from services import backup

REMOVE_TYPED_OVER = 10
NO_FUND = 0  # fund filter value: people without a fund (real fund ids start at 1)
PENDING_NOTE = "legacy import default, pending review"

_PLAIN_DELIVERY = {"Print": "Mail", "Email": "Email", "Both": "Both"}

_COPIES = "n.name || CASE WHEN s.quantity > 1 THEN ' (' || s.quantity || ' copies)' ELSE '' END"

# New subscriptions if the person has any, else the old list that still drives the current mailing.
_GETS_SQL = f"""COALESCE(
    (SELECT group_concat({_COPIES}, ', ')
       FROM subscriptions_v2 s JOIN newsletters n ON n.id = s.newsletter_id
      WHERE s.person_id = p.id AND s.active = 1),
    CASE WHEN EXISTS (SELECT 1 FROM subscriptions_v2 s WHERE s.person_id = p.id)
         THEN CASE WHEN EXISTS (SELECT 1 FROM subscriptions_v2 s WHERE s.person_id = p.id AND s.active = 0
                                  AND s.notes = '{PENDING_NOTE}')
                   THEN 'Pending review' ELSE '' END
         ELSE (SELECT group_concat({_COPIES}, ', ')
                 FROM subscriptions s JOIN newsletters n ON n.id = s.newsletter_id
                WHERE s.person_id = p.id AND s.active = 1)
    END, '')"""


class ConfirmationRequired(Exception):
    pass


def display_name(first, last):
    return " ".join(filter(None, [(first or "").strip(), (last or "").strip()]))


def list_people(conn, search="", type_="All", fund=None, state="All", needs_attention=False, removed=False):
    """fund: None for all, NO_FUND for people without a fund, or a fund id."""
    sql = (
        "SELECT p.id, p.first_name, p.last_name, p.recipient_type, p.city, p.state, p.fund_id, p.active, "
        "f.name AS fund_name, "
        "EXISTS (SELECT 1 FROM subscriptions_v2 s WHERE s.person_id = p.id AND s.active = 1 "
        "        AND s.delivery = 'Both') AS is_both, "
        f"{_GETS_SQL} AS gets "
        "FROM people p LEFT JOIN funds f ON f.id = p.fund_id WHERE COALESCE(p.active, 1) = ?"
    )
    params = [0 if removed else 1]
    if search:
        sql += (" AND (COALESCE(p.first_name,'') || ' ' || COALESCE(p.middle_name,'') || ' ' || COALESCE(p.last_name,'')"
                " || ' ' || COALESCE(p.church_name,'') || ' ' || COALESCE(p.conference,'')"
                " || ' ' || COALESCE(p.city,'')) LIKE ?")
        params.append(f"%{search}%")
    if type_ != "All":
        sql += " AND p.recipient_type = ?"
        params.append(type_)
    if state != "All":
        sql += " AND p.state = ?"
        params.append(state)
    if fund == NO_FUND:
        sql += " AND p.fund_id IS NULL"
    elif fund is not None:
        sql += " AND p.fund_id = ?"
        params.append(fund)
    if needs_attention:
        sql += (" AND (p.fund_id IS NULL OR COALESCE(p.address1,'') = '' OR COALESCE(p.city,'') = ''"
                " OR COALESCE(p.zip,'') = '')")
    sql += " ORDER BY p.last_name, p.first_name"
    return conn.execute(sql, params).fetchall()


def types_and_states(conn):
    types = [r[0] for r in conn.execute(
        "SELECT DISTINCT recipient_type FROM people WHERE recipient_type IS NOT NULL ORDER BY 1")]
    states = [r[0] for r in conn.execute(
        "SELECT DISTINCT state FROM people WHERE state IS NOT NULL AND state != '' ORDER BY 1")]
    return types, states


def person_detail(conn, person_id):
    person = conn.execute(
        "SELECT p.*, f.name AS fund_name FROM people p LEFT JOIN funds f ON f.id = p.fund_id WHERE p.id = ?",
        (person_id,)).fetchone()
    if person is None:
        return None
    subs = conn.execute(
        "SELECT n.name, s.quantity, s.delivery, s.active, (s.active = 0 AND s.notes = ?) AS pending "
        "FROM subscriptions_v2 s JOIN newsletters n ON n.id = s.newsletter_id "
        "WHERE s.person_id = ? ORDER BY s.active DESC, n.name", (PENDING_NOTE, person_id)).fetchall()
    gets = [{"newsletter": r["name"], "copies": r["quantity"], "delivery": _PLAIN_DELIVERY[r["delivery"]],
             "pending": bool(r["pending"])} for r in subs if r["active"] or r["pending"]]
    if not subs:
        gets = [{"newsletter": r["name"], "copies": r["quantity"], "delivery": "Mail", "pending": False}
                for r in conn.execute(
                    "SELECT n.name, s.quantity FROM subscriptions s JOIN newsletters n ON n.id = s.newsletter_id "
                    "WHERE s.person_id = ? AND s.active = 1 ORDER BY n.name", (person_id,))]
    history = conn.execute(
        "SELECT ts, summary FROM activity_log WHERE entity_type = 'person' AND entity_id = ? "
        "ORDER BY id DESC LIMIT 20", (person_id,)).fetchall()
    first_run = conn.execute("SELECT MIN(sent_on) FROM mailing_runs").fetchone()[0]
    return {"person": person, "gets": gets, "history": history, "history_starts": first_run}


# ---------- Bulk changes ----------

def _unique(ids):
    return list(dict.fromkeys(int(i) for i in ids))


def _fund_name(conn, fund_id):
    if fund_id is None:
        return "no fund"
    return conn.execute("SELECT name FROM funds WHERE id = ?", (fund_id,)).fetchone()["name"]


def set_fund(conn, person_ids, fund_id):
    """Sets one fund on many people in one logged batch. fund_id None clears it."""
    require_writable(conn)
    ids = _unique(person_ids)
    if not ids:
        return {"changed": 0, "unchanged": 0}
    if fund_id is not None:
        row = conn.execute("SELECT active FROM funds WHERE id = ?", (fund_id,)).fetchone()
        if row is None or not row["active"]:
            raise ValueError("That fund is not available.")
    if len(ids) > 1:
        backup.backup_both(conn.db_path, "before-set-fund")
    ts, changed, unchanged = now(), 0, 0
    with transaction(conn):
        new_name = _fund_name(conn, fund_id)
        batch = activity.log(conn, "fund.set_bulk", f"Set fund to {new_name} for {len(ids)} people")
        for pid in ids:
            row = conn.execute("SELECT fund_id FROM people WHERE id = ?", (pid,)).fetchone()
            if row is None:
                continue
            if row["fund_id"] == fund_id:
                unchanged += 1
                continue
            old_name = _fund_name(conn, row["fund_id"])
            conn.execute("UPDATE people SET fund_id = ?, updated_at = ? WHERE id = ?", (fund_id, ts, pid))
            activity.log(conn, "fund.set", f"Fund changed from {old_name} to {new_name}", "person", pid, batch)
            changed += 1
        activity.set_summary(conn, batch, f"Set fund to {new_name}: {changed} changed, {unchanged} already had it")
    return {"changed": changed, "unchanged": unchanged}


def remove_people(conn, person_ids, confirm_count=None):
    """Soft remove (active = 0). More than REMOVE_TYPED_OVER people needs the count typed back."""
    require_writable(conn)
    ids = _unique(person_ids)
    if len(ids) > REMOVE_TYPED_OVER and confirm_count != len(ids):
        raise ConfirmationRequired(f"Type {len(ids)} to confirm removing that many people.")
    return _set_active(conn, ids, 0, "person.remove", "Removed", "before-remove")


def restore_people(conn, person_ids):
    require_writable(conn)
    return _set_active(conn, _unique(person_ids), 1, "person.restore", "Restored", "before-restore-people")


def _set_active(conn, ids, active, action, word, reason):
    if len(ids) > 1:
        backup.backup_both(conn.db_path, reason)
    ts, done = now(), 0
    with transaction(conn):
        batch = activity.log(conn, action + "_bulk", f"{word} {len(ids)} people")
        for pid in ids:
            cur = conn.execute(
                "UPDATE people SET active = ?, updated_at = ? WHERE id = ? AND COALESCE(active, 1) <> ?",
                (active, ts, pid, active))
            if cur.rowcount:
                activity.log(conn, action, f"{word} a person", "person", pid, batch)
                done += 1
        activity.set_summary(conn, batch, f"{word} {done} people")
    return done
