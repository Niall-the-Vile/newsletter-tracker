"""Bounced email addresses. A bounce belongs to the address, so correcting the address ends the skip.

Hard and Manual bounces skip the address at once. A Soft bounce skips it only after 3 in a row.
Nothing is sent from here: addresses are recorded by hand or pasted in from bounce notices.
"""
import getpass
import re

from data import activity
from data.connection import now, require_writable, transaction

KINDS = ("Hard", "Soft", "Manual")
SOFT_LIMIT = 3

_VALID = re.compile(r"[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"'.]+")
_FIND = re.compile(r"[^\s,;<>\"']+@[^\s,;<>\"']+")


class BounceError(Exception):
    pass


def normalize_email(text):
    return " ".join((text or "").split()).lower() or None


def is_valid_email(text):
    return bool(text) and _VALID.fullmatch((text or "").strip()) is not None


def _record(conn, email, kind, note, ts, user):
    conn.execute("INSERT INTO email_bounces (email_key, email, kind, noted_at, noted_by, note) VALUES (?, ?, ?, ?, ?, ?)",
                 (normalize_email(email), email.strip(), kind, ts, user, note))


def mark_bounced(conn, email, kind="Manual", note=None):
    """Records one bounce for this address."""
    require_writable(conn)
    if kind not in KINDS:
        raise BounceError("Choose Hard, Soft or Manual.")
    if not is_valid_email(email):
        raise BounceError("That is not an email address.")
    with transaction(conn):
        _record(conn, email, kind, note, now(), getpass.getuser())
        activity.log(conn, "bounce.mark", f"{kind} bounce recorded for {email.strip()}")


def paste_in(conn, text, kind="Hard"):
    """Records every address found in pasted text. Returns {'recorded': n, 'ignored': n}."""
    require_writable(conn)
    if kind not in KINDS:
        raise BounceError("Choose Hard, Soft or Manual.")
    found = _FIND.findall(text or "")
    good = [e.strip(".") for e in found if is_valid_email(e.strip("."))]
    if good:
        ts, user = now(), getpass.getuser()
        with transaction(conn):
            for e in good:
                _record(conn, e, kind, "pasted list", ts, user)
            activity.log(conn, "bounce.paste", f"{len(good)} {kind} bounces pasted in")
    return {"recorded": len(good), "ignored": len(found) - len(good)}


def clear(conn, email):
    """Forgets the bounces for this address (for a mistake). They stay on file, marked cleared."""
    require_writable(conn)
    with transaction(conn):
        conn.execute("UPDATE email_bounces SET cleared = 1 WHERE email_key = ?", (normalize_email(email),))
        activity.log(conn, "bounce.clear", f"Bounce cleared for {(email or '').strip()}")


def _summary(conn):
    """{email_key: {'email', 'kind', 'count', 'soft', 'skipped', 'last'}} from the live (not cleared) records."""
    out = {}
    for r in conn.execute("SELECT * FROM email_bounces WHERE cleared = 0 ORDER BY id"):
        e = out.setdefault(r["email_key"], {"email": r["email"], "count": 0, "soft": 0, "skipped": False})
        e.update(kind=r["kind"], last=r["noted_at"][:10], email=r["email"])
        e["count"] += 1
        if r["kind"] == "Soft":
            e["soft"] += 1
        e["skipped"] = e["skipped"] or r["kind"] != "Soft" or e["soft"] >= SOFT_LIMIT
    return out


def skipped_keys(conn):
    return {k for k, v in _summary(conn).items() if v["skipped"]}


def is_skipped(conn, email):
    return normalize_email(email) in skipped_keys(conn)


def list_bounces(conn):
    """One row per bounced address with how many people have it now, newest first."""
    holders = {}
    for r in conn.execute("SELECT lower(trim(email)) AS k, COUNT(*) AS n FROM people WHERE COALESCE(active, 1) = 1 "
                          "AND email IS NOT NULL GROUP BY 1"):
        holders[r["k"]] = r["n"]
    rows = [dict(v, key=k, holders=holders.get(k, 0)) for k, v in _summary(conn).items()]
    rows.sort(key=lambda r: (r["last"], r["email"]), reverse=True)
    return rows
