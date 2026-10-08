"""Church and rep rules (M10), the church list and church detail.

Every change here runs in one transaction and is logged in it. A church has several reps and at most one
default rep (the database enforces that). When the default goes away: one other rep is promoted, several
means ask which, none marks the church "No contact".
"""
from datetime import date

from data import activity
from data.connection import now, require_writable, transaction
from services import mailings, subscriptions

NO_CONTACT = "No contact"
NEEDS_DEFAULT = "Needs a default"

_DEFAULT_NAME = (
    "(SELECT TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')) FROM church_reps r "
    "JOIN people p ON p.id = r.person_id WHERE r.church_id = c.id AND r.is_default = 1 AND r.active = 1)")


class RepError(Exception):
    pass


class DefaultChoiceNeeded(Exception):
    """Several reps are left and none was chosen. candidates are the person ids to choose from."""

    def __init__(self, church_id, candidates):
        super().__init__("Choose which rep should be the main contact.")
        self.church_id = church_id
        self.candidates = candidates


# ---------- helpers ----------

def _church(conn, church_id):
    row = conn.execute("SELECT * FROM churches WHERE id = ?", (church_id,)).fetchone()
    if row is None:
        raise RepError("That church was not found.")
    return row


def _person_name(conn, person_id):
    row = conn.execute("SELECT first_name, last_name FROM people WHERE id = ?", (person_id,)).fetchone()
    return " ".join(filter(None, [row["first_name"], row["last_name"]])) if row else "a person"


def _active_reps(conn, church_id):
    return [r["person_id"] for r in conn.execute(
        "SELECT person_id FROM church_reps WHERE church_id = ? AND active = 1 ORDER BY id", (church_id,))]


def _link(conn, church_id, person_id):
    return conn.execute("SELECT * FROM church_reps WHERE church_id = ? AND person_id = ? AND active = 1",
                        (church_id, person_id)).fetchone()


def _set_default(conn, church_id, person_id):
    conn.execute("UPDATE church_reps SET is_default = 0 WHERE church_id = ? AND is_default = 1 AND active = 1",
                 (church_id,))
    conn.execute("UPDATE church_reps SET is_default = 1 WHERE church_id = ? AND person_id = ? AND active = 1",
                 (church_id, person_id))
    conn.execute("UPDATE churches SET no_contact = 0, returned_mail = 0, updated_at = ? WHERE id = ?",
                 (now(), church_id))


def settle_single_rep(conn, church_id):
    """A church with exactly one rep and no default gets that rep as default. Several reps are left alone."""
    reps = _active_reps(conn, church_id)
    has_default = conn.execute("SELECT 1 FROM church_reps WHERE church_id = ? AND is_default = 1 AND active = 1",
                               (church_id,)).fetchone()
    if len(reps) == 1 and not has_default:
        _set_default(conn, church_id, reps[0])
        return True
    return False


def _end_link(conn, church_id, person_id, chosen, ask, parent_id=None):
    """Ends one rep link and applies the default rule. Returns what happened."""
    link = _link(conn, church_id, person_id)
    if link is None:
        return "none"
    conn.execute("UPDATE church_reps SET active = 0, is_default = 0, end_date = ? WHERE id = ?",
                 (date.today().isoformat(), link["id"]))
    others = _active_reps(conn, church_id)
    outcome = "none"
    if not others:
        conn.execute("UPDATE churches SET no_contact = 1, updated_at = ? WHERE id = ?", (now(), church_id))
        outcome = "no_contact"
    elif link["is_default"]:
        if len(others) == 1:
            _set_default(conn, church_id, others[0])
            outcome = "promoted"
        elif chosen in others:
            _set_default(conn, church_id, chosen)
            outcome = "chosen"
        elif ask:
            raise DefaultChoiceNeeded(church_id, others)
        else:
            outcome = "needs_default"
    elif settle_single_rep(conn, church_id):
        outcome = "promoted"
    activity.log(conn, "church.rep_end", f"{_person_name(conn, person_id)} is no longer a rep ({outcome})",
                 "church", church_id, parent_id)
    return outcome


# ---------- rules ----------

def add_rep(conn, church_id, person_id, make_default=False, auto_default=True, parent_id=None):
    require_writable(conn)
    with transaction(conn):
        church = _church(conn, church_id)
        person = conn.execute("SELECT active FROM people WHERE id = ?", (person_id,)).fetchone()
        if person is None or not (person["active"] is None or person["active"]):
            raise RepError("That person is not available.")
        if _link(conn, church_id, person_id):
            raise RepError(f"That person is already a rep of {church['name']}.")
        was_empty = not _active_reps(conn, church_id)
        ts = now()
        conn.execute("INSERT INTO church_reps (church_id, person_id, is_default, active, start_date, created_at) "
                     "VALUES (?, ?, 0, 1, ?, ?)", (church_id, person_id, date.today().isoformat(), ts))
        if make_default or (auto_default and was_empty):
            _set_default(conn, church_id, person_id)
        activity.log(conn, "church.rep_add", f"{_person_name(conn, person_id)} is now a rep", "church",
                     church_id, parent_id)


def make_default(conn, church_id, person_id):
    """Clears the old main contact and sets the new one together, or changes nothing."""
    require_writable(conn)
    with transaction(conn):
        _church(conn, church_id)
        if _link(conn, church_id, person_id) is None:
            raise RepError("That person is not a rep of this church.")
        _set_default(conn, church_id, person_id)
        activity.log(conn, "church.make_default", f"{_person_name(conn, person_id)} is now the main contact",
                     "church", church_id)


def remove_rep(conn, church_id, person_id, new_default_id=None):
    """Raises DefaultChoiceNeeded (nothing changed) if the default leaves and several reps remain."""
    require_writable(conn)
    with transaction(conn):
        _church(conn, church_id)
        return _end_link(conn, church_id, person_id, new_default_id, ask=True)


def move_rep(conn, person_id, from_church_id, to_church_id, new_default_id=None):
    """The old link is end-dated and a new one added at the other church."""
    require_writable(conn)
    with transaction(conn):
        _church(conn, to_church_id)
        outcome = _end_link(conn, from_church_id, person_id, new_default_id, ask=True)
        add_rep(conn, to_church_id, person_id)
        return outcome


def person_removed(conn, person_id):
    """Called when a person is removed. Several reps left and no way to ask means the church needs a default."""
    for r in conn.execute("SELECT church_id FROM church_reps WHERE person_id = ? AND active = 1",
                          (person_id,)).fetchall():
        _end_link(conn, r["church_id"], person_id, None, ask=False)


# ---------- lists ----------

def needs_default(conn):
    return conn.execute(
        "SELECT c.id, c.name FROM churches c WHERE c.active = 1 "
        "AND EXISTS (SELECT 1 FROM church_reps r WHERE r.church_id = c.id AND r.active = 1) "
        "AND NOT EXISTS (SELECT 1 FROM church_reps r WHERE r.church_id = c.id AND r.active = 1 AND r.is_default = 1) "
        "ORDER BY c.name").fetchall()


def contact_label(default_name, rep_count):
    return default_name or (NEEDS_DEFAULT if rep_count else NO_CONTACT)


def list_churches(conn, search="", state="All", fund=None, needs_a_default=False):
    """fund: None for all, 0 for churches without a fund, or a fund id."""
    sql = (
        "SELECT c.id, c.name, c.conference, c.city, c.state, c.fund_id, f.name AS fund_name, "
        f"{_DEFAULT_NAME} AS default_name, "
        "(SELECT COUNT(*) FROM church_reps r WHERE r.church_id = c.id AND r.active = 1) AS rep_count, "
        "(SELECT COUNT(*) FROM people p WHERE p.church_id = c.id AND COALESCE(p.active, 1) = 1) AS member_count "
        "FROM churches c LEFT JOIN funds f ON f.id = c.fund_id WHERE c.active = 1")
    params = []
    if search:
        sql += " AND (c.name || ' ' || COALESCE(c.conference, '') || ' ' || COALESCE(c.city, '')) LIKE ?"
        params.append(f"%{search}%")
    if state != "All":
        sql += " AND c.state = ?"
        params.append(state)
    if fund == 0:
        sql += " AND c.fund_id IS NULL"
    elif fund is not None:
        sql += " AND c.fund_id = ?"
        params.append(fund)
    if needs_a_default:
        sql += (" AND EXISTS (SELECT 1 FROM church_reps r WHERE r.church_id = c.id AND r.active = 1)"
                " AND NOT EXISTS (SELECT 1 FROM church_reps r WHERE r.church_id = c.id AND r.active = 1"
                " AND r.is_default = 1)")
    sql += " ORDER BY c.name"
    return conn.execute(sql, params).fetchall()


def church_states(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT state FROM churches WHERE active = 1 AND state IS NOT NULL AND state != '' ORDER BY 1")]


def church_detail(conn, church_id):
    church = conn.execute(
        "SELECT c.*, f.name AS fund_name FROM churches c LEFT JOIN funds f ON f.id = c.fund_id WHERE c.id = ?",
        (church_id,)).fetchone()
    if church is None:
        return None
    reps = conn.execute(
        "SELECT r.person_id, r.is_default, p.first_name, p.last_name, p.email, p.title "
        "FROM church_reps r JOIN people p ON p.id = r.person_id WHERE r.church_id = ? AND r.active = 1 "
        "ORDER BY r.is_default DESC, p.last_name, p.first_name", (church_id,)).fetchall()
    members = conn.execute("SELECT COUNT(*) FROM people WHERE church_id = ? AND COALESCE(active, 1) = 1",
                           (church_id,)).fetchone()[0]
    history = mailings.history(conn, "church", church_id, 20)
    default_name = next((" ".join(filter(None, [r["first_name"], r["last_name"]])) for r in reps
                         if r["is_default"]), None)
    return {"church": church, "reps": reps, "members": members, "history": history,
            "gets": subscriptions.gets(conn, "church", church_id), "history_starts": mailings.history_starts(conn),
            "contact": contact_label(default_name, len(reps))}
