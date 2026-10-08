"""Funds: the list in Setup. One fund per member or church; funds are rows, not code."""
from data import activity
from data.connection import now, require_writable, transaction


class FundError(Exception):
    pass


def list_funds(conn):
    return conn.execute(
        "SELECT f.id, f.name, f.active, "
        "(SELECT COUNT(*) FROM people p WHERE p.fund_id = f.id AND COALESCE(p.active, 1) = 1) AS people, "
        "(SELECT COUNT(*) FROM churches c WHERE c.fund_id = f.id AND c.active = 1) AS churches "
        "FROM funds f ORDER BY f.name").fetchall()


def active_funds(conn):
    return conn.execute("SELECT id, name FROM funds WHERE active = 1 ORDER BY name").fetchall()


def _clean(name):
    name = " ".join((name or "").split())
    if not name:
        raise FundError("Enter a name for the fund.")
    return name


def _check_unique(conn, name, own_id=None):
    row = conn.execute("SELECT id FROM funds WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row and row["id"] != own_id:
        raise FundError(f"There is already a fund called '{name}'.")


def add_fund(conn, name):
    require_writable(conn)
    name = _clean(name)
    with transaction(conn):
        _check_unique(conn, name)
        cur = conn.execute("INSERT INTO funds (name, created_at) VALUES (?, ?)", (name, now()))
        activity.log(conn, "fund.add", f"Added fund {name}", "fund", cur.lastrowid)
    return cur.lastrowid


def rename_fund(conn, fund_id, name):
    require_writable(conn)
    name = _clean(name)
    with transaction(conn):
        _check_unique(conn, name, fund_id)
        old = conn.execute("SELECT name FROM funds WHERE id = ?", (fund_id,)).fetchone()
        conn.execute("UPDATE funds SET name = ? WHERE id = ?", (name, fund_id))
        activity.log(conn, "fund.rename", f"Renamed fund {old['name']} to {name}", "fund", fund_id)


def set_fund_active(conn, fund_id, active):
    require_writable(conn)
    with transaction(conn):
        row = conn.execute("SELECT name FROM funds WHERE id = ?", (fund_id,)).fetchone()
        conn.execute("UPDATE funds SET active = ? WHERE id = ?", (1 if active else 0, fund_id))
        activity.log(conn, "fund.active", f"{'Turned on' if active else 'Turned off'} fund {row['name']}",
                     "fund", fund_id)
