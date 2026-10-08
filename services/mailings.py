"""Mailing runs: who gets a newsletter (build_mailing), the checks before saving, and saved runs.

A run and its rows are plain copies with no links to live rows, written in one transaction. Eligibility is
worked out by query every time, so fund rules are never stored on subscriptions.
"""
import csv
import getpass
import os
import re
from datetime import date

import exporter
from data import activity
from data.connection import now, require_writable, transaction
from importer import normalize_zip
from services import backup, bounces

NO_FUND = "No fund"
WRONG_FUND = "Fund doesn't match"
REMOVED = "Removed from the list"
NO_CONTACT = "No contact"
NEEDS_DEFAULT = "No main contact chosen"
EMAIL_ONLY = "Gets it by email only"
MAIL_ONLY = "Gets it by mail only"
BOUNCED = "Email address bounced"
NO_EMAIL = "No valid email address"
VIA_CHURCH = "Already gets it for their church"

_PLAIN_DELIVERY = exporter.PLAIN_DELIVERY
_ROW_COLUMNS = ["person_id", "church_id", "recipient_name", "church_name", "address1", "address2", "city", "state",
                "zip", "quantity", "delivery", "email", "fund_name", "first_name", "middle_name", "last_name",
                "recipient_type"]


class MailingError(Exception):
    pass


class DuplicateIssue(MailingError):
    def __init__(self, label, sent_on):
        super().__init__(f"'{label}' was already made on {', '.join(sent_on)}.")
        self.label = label
        self.sent_on = sent_on


class PreflightBlocked(MailingError):
    def __init__(self, check):
        super().__init__("Fix the red items first: " + "; ".join(f"{r['who']}: {r['text']}" for r in check["red"][:5]))
        self.check = check


def _t(value):
    text = "" if value is None else str(value).strip()
    return text or None


def _name(first, last):
    return " ".join(filter(None, [_t(first), _t(last)]))


def _key(row):
    return ("church", row["church_id"]) if row["church_id"] else ("person", row["person_id"])


def _label(row):
    return row["church_name"] if row["church_id"] else row["recipient_name"]


def _problems(row, channel="Print"):
    out = []
    if channel == "Email":
        if not bounces.is_valid_email(row["email"]):
            out.append(NO_EMAIL)
    elif not (row["address1"] and row["city"] and row["zip"]):
        out.append("No address")
    if row["quantity"] < 1:
        out.append("Copies under 1")
    return out


# ---------- who is in the mailing ----------

_CHURCH_SQL = """
SELECT s.quantity, s.delivery, c.id AS church_id, c.name AS church_name, c.fund_id, c.active AS c_active,
       c.no_contact, c.returned_mail AS c_returned, cf.name AS fund_name,
       (SELECT COUNT(*) FROM church_reps r2 WHERE r2.church_id = c.id AND r2.active = 1) AS rep_count,
       p.id AS rep_id, p.first_name, p.middle_name, p.last_name, p.address1, p.address2, p.city, p.state, p.zip,
       p.email, p.active AS p_active, p.returned_mail AS p_returned
FROM subscriptions_v2 s JOIN churches c ON c.id = s.church_id
LEFT JOIN funds cf ON cf.id = c.fund_id
LEFT JOIN church_reps r ON r.church_id = c.id AND r.is_default = 1 AND r.active = 1
LEFT JOIN people p ON p.id = r.person_id
WHERE s.newsletter_id = ? AND s.active = 1"""

_PERSON_SQL = """
SELECT s.quantity, s.delivery, p.id AS person_id, p.first_name, p.middle_name, p.last_name, p.address1, p.address2,
       p.city, p.state, p.zip, p.email, p.church_name, p.recipient_type, p.fund_id, p.returned_mail,
       COALESCE(p.active, 1) AS p_active, pf.name AS fund_name
FROM subscriptions_v2 s JOIN people p ON p.id = s.person_id
LEFT JOIN funds pf ON pf.id = p.fund_id
WHERE s.newsletter_id = ? AND s.active = 1"""


def _newsletter(conn, newsletter_id):
    nl = conn.execute("SELECT n.*, f.name AS fund_name FROM newsletters n LEFT JOIN funds f ON f.id = n.fund_id "
                      "WHERE n.id = ?", (newsletter_id,)).fetchone()
    if nl is None:
        raise MailingError("That newsletter was not found.")
    return nl


def build_mailing(conn, newsletter_id, channel="Print"):
    """Everyone who gets this newsletter in a mailed (Print) or emailed (Email) run right now, and who was left out.

    Delivery Both is in both runs. Each row holds the saved columns plus 'problems' (red) and 'returned' (a
    yellow warning).
    """
    nl = _newsletter(conn, newsletter_id)
    wanted = nl["fund_id"]
    rows, skipped = [], []
    in_run = ("Print", "Both") if channel == "Print" else ("Email", "Both")
    other_only = EMAIL_ONLY if channel == "Print" else MAIL_ONLY
    bounced = bounces.skipped_keys(conn) if channel == "Email" else set()

    def by_delivery_or_bounce(delivery, email):
        if delivery not in in_run:
            return other_only
        if bounces.is_valid_email(email) and bounces.normalize_email(email) in bounced:
            return BOUNCED
        return None

    def fund_reason(entity_fund):
        if wanted is None:
            return None
        if entity_fund is None:
            return NO_FUND
        return None if entity_fund == wanted else WRONG_FUND

    def skip(kind, ident, name, reason, copies):
        skipped.append({"kind": kind, "id": ident, "name": name, "reason": reason, "copies": copies})

    emitted_reps = set()
    for c in conn.execute(_CHURCH_SQL, (newsletter_id,)).fetchall():
        reason = REMOVED if not c["c_active"] else fund_reason(c["fund_id"])
        if reason is None:
            if c["no_contact"] or not c["rep_count"]:
                reason = NO_CONTACT
            elif c["rep_id"] is None or not (c["p_active"] is None or c["p_active"]):
                reason = NEEDS_DEFAULT
            else:
                reason = by_delivery_or_bounce(c["delivery"], c["email"])
        if reason:
            skip("church", c["church_id"], c["church_name"], reason, c["quantity"])
            continue
        emitted_reps.add(c["rep_id"])
        rows.append({
            "person_id": c["rep_id"], "church_id": c["church_id"],
            "recipient_name": _name(c["first_name"], c["last_name"]), "church_name": _t(c["church_name"]),
            "address1": _t(c["address1"]), "address2": _t(c["address2"]), "city": _t(c["city"]),
            "state": _t(c["state"]), "zip": _t(normalize_zip(c["zip"])), "quantity": c["quantity"],
            "delivery": c["delivery"], "email": _t(c["email"]), "fund_name": _t(c["fund_name"]),
            "first_name": _t(c["first_name"]), "middle_name": _t(c["middle_name"]), "last_name": _t(c["last_name"]),
            "recipient_type": "Church", "returned": bool(c["c_returned"] or c["p_returned"]),
        })

    for p in conn.execute(_PERSON_SQL, (newsletter_id,)).fetchall():
        name = _name(p["first_name"], p["last_name"])
        reason = REMOVED if not p["p_active"] else fund_reason(p["fund_id"])
        if reason is None:
            reason = by_delivery_or_bounce(p["delivery"], p["email"])
            if reason is None and p["person_id"] in emitted_reps:
                reason = VIA_CHURCH
        if reason:
            skip("person", p["person_id"], name, reason, p["quantity"])
            continue
        rows.append({
            "person_id": p["person_id"], "church_id": None, "recipient_name": name,
            "church_name": _t(p["church_name"]), "address1": _t(p["address1"]), "address2": _t(p["address2"]),
            "city": _t(p["city"]), "state": _t(p["state"]), "zip": _t(normalize_zip(p["zip"])),
            "quantity": p["quantity"], "delivery": p["delivery"], "email": _t(p["email"]),
            "fund_name": _t(p["fund_name"]), "first_name": _t(p["first_name"]), "middle_name": _t(p["middle_name"]),
            "last_name": _t(p["last_name"]), "recipient_type": _t(p["recipient_type"]) or "Member",
            "returned": bool(p["returned_mail"]),
        })

    rows.sort(key=lambda r: ((r["last_name"] or "").lower(), (r["first_name"] or "").lower(),
                             (r["church_name"] or "").lower(), r["church_id"] or 0, r["person_id"] or 0))
    for r in rows:
        r["problems"] = _problems(r, channel)
    skipped.sort(key=lambda s: (s["reason"], s["name"].lower(), s["id"]))
    return {"newsletter": nl, "rows": rows, "skipped": skipped}


# ---------- checks before saving ----------

def previous_run(conn, newsletter_id, channel="Print"):
    return conn.execute("SELECT * FROM mailing_runs WHERE newsletter_id = ? AND channel = ? "
                        "ORDER BY id DESC LIMIT 1", (newsletter_id, channel)).fetchone()


def _address_text(r):
    zip5 = (r["zip"] or "")[:5]
    parts = [r["address1"], r["address2"], r["city"], r["state"], zip5]
    return " ".join(" ".join((p or "").split()).lower() for p in parts)


def preflight(conn, newsletter_id, channel="Print"):
    """Red items block the mailing. Yellow ones warn. Also the counts and the change since the last issue.

    A mailed run warns about anyone who gets email but has no valid address. An email run blocks on it.
    """
    mailing = build_mailing(conn, newsletter_id, channel)
    rows, red, yellow = mailing["rows"], [], []
    mail = channel == "Print"
    if mail:
        for r in build_mailing(conn, newsletter_id, "Email")["rows"]:
            if r["problems"]:
                yellow.append({"who": _label(r), "text": "Gets email but has no valid email address"})
    prev = previous_run(conn, newsletter_id, channel)
    before = {}
    if prev:
        for r in conn.execute("SELECT * FROM mailing_run_rows WHERE run_id = ?", (prev["id"],)):
            before[_key(r)] = r
    seen = {}
    for r in rows:
        who = _label(r)
        for text in r["problems"]:
            red.append({"who": who, "text": text})
        old = before.get(_key(r)) if mail else None
        if old is not None:
            if _address_text(old) != _address_text(r):
                yellow.append({"who": who, "text": "Address changed since the last issue"})
            if old["quantity"] != r["quantity"]:
                yellow.append({"who": who, "text": f"Copies changed since the last issue "
                                                   f"({old['quantity']} to {r['quantity']})"})
        if not mail:
            if r["email"]:
                seen.setdefault(bounces.normalize_email(r["email"]), []).append(who)
            continue
        if r["zip"] and not re.fullmatch(r"\d{5}(-\d{4})?", r["zip"]):
            yellow.append({"who": who, "text": f"ZIP code looks wrong ({r['zip']})"})
        if r["returned"]:
            yellow.append({"who": who, "text": "Mail was returned from this address"})
        if r["address1"]:
            seen.setdefault(" ".join(r["address1"].lower().split()) + "|" + (r["zip"] or "")[:5], []).append(who)
    for names in seen.values():
        if len(names) > 1:
            yellow.extend({"who": who, "text": "Same address as another row" if mail else
                           "Same email address as another row"} for who in names)

    by_delivery, by_fund, by_reason = {}, {}, {}
    for r in rows:
        d = _PLAIN_DELIVERY[r["delivery"]]
        by_delivery[d] = by_delivery.get(d, 0) + 1
        f = r["fund_name"] or NO_FUND
        by_fund[f] = by_fund.get(f, 0) + 1
    for s in mailing["skipped"]:
        by_reason[s["reason"]] = by_reason.get(s["reason"], 0) + 1

    now_keys = {_key(r): r for r in rows}
    diff = {"previous": dict(prev) if prev else None, "added": [], "removed": [], "changed": []}
    if prev:
        diff["added"] = [_label(r) for k, r in now_keys.items() if k not in before]
        diff["removed"] = [_label(r) for k, r in before.items() if k not in now_keys]
        if mail:
            diff["changed"] = [(_label(r), before[k]["quantity"], r["quantity"]) for k, r in now_keys.items()
                               if k in before and before[k]["quantity"] != r["quantity"]]
    return {"channel": channel, "mailing": mailing, "rows": len(rows), "copies": sum(r["quantity"] for r in rows), "red": red,
            "yellow": yellow, "blocked": bool(red), "by_delivery": by_delivery, "by_fund": by_fund,
            "skipped_by_reason": by_reason, "skipped": mailing["skipped"], "diff": diff}


def summary_text(check):
    """The plain-words summary for step 2 of Create Mailing."""
    email = check["channel"] == "Email"
    lines = [f"{check['rows']} email addresses." if email
             else f"{check['rows']} addresses, {check['copies']} copies in total."]
    if check["by_delivery"]:
        lines.append("Sent by: " + ", ".join(f"{k} {v}" for k, v in sorted(check["by_delivery"].items())) + ".")
    if check["by_fund"]:
        lines.append("By fund: " + ", ".join(f"{k} {v}" for k, v in sorted(check["by_fund"].items())) + ".")
    n = sum(check["skipped_by_reason"].values())
    if n:
        lines.append(f"{n} left out: " + ", ".join(
            f"{v} ({k.lower()})" for k, v in sorted(check["skipped_by_reason"].items())) + ".")
    d = check["diff"]
    if d["previous"]:
        lines.append(f"Since {d['previous']['issue_label']} ({d['previous']['sent_on']}): {len(d['added'])} added, "
                     f"{len(d['removed'])} removed" + ("." if email else f", {len(d['changed'])} with different copies."))
    else:
        lines.append("This is the first mailing recorded for this newsletter.")
    return "\n".join(lines)


# ---------- saving a run ----------

def same_label_runs(conn, newsletter_id, label, channel="Print"):
    return conn.execute("SELECT id, sent_on FROM mailing_runs WHERE newsletter_id = ? AND channel = ? "
                        "AND lower(trim(issue_label)) = ? ORDER BY id",
                        (newsletter_id, channel, " ".join(label.split()).lower())).fetchall()


def create_mailing(conn, newsletter_id, issue_label, out_path, allow_duplicate=False, channel="Print"):
    """Saves the sheet (or, for channel Email, the email list) first and records the run only if that worked.

    Returns the new run id. Nothing is sent: an email run is a saved list of addresses.
    """
    require_writable(conn)
    label = " ".join((issue_label or "").split())
    if not label:
        raise MailingError("Enter an issue label, for example Fall 2026.")
    nl = _newsletter(conn, newsletter_id)
    if nl["archived"]:
        raise MailingError("That newsletter is archived.")
    dup = same_label_runs(conn, newsletter_id, label, channel)
    if dup and not allow_duplicate:
        raise DuplicateIssue(label, [d["sent_on"] for d in dup])
    check = preflight(conn, newsletter_id, channel)
    if check["blocked"]:
        raise PreflightBlocked(check)
    rows = [{k: r[k] for k in _ROW_COLUMNS} for r in check["mailing"]["rows"]]
    if not rows:
        raise MailingError(f"Nobody gets this newsletter by {'email' if channel == 'Email' else 'mail'} right now, "
                           "so there is nothing to save.")
    run = {"newsletter_id": newsletter_id, "newsletter_name": nl["name"], "fund_name": nl["fund_name"],
           "issue_label": label, "sent_on": date.today().isoformat(), "channel": channel}
    copies = len(rows) if channel == "Email" else sum(r["quantity"] for r in rows)  # one message per address
    backup.backup_both(conn.db_path, "before-mailing")
    exporter.write_run_csv(out_path, run, rows)  # a failure here means no run is recorded
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO mailing_runs (newsletter_id, newsletter_name, fund_name, issue_label, channel, sent_on, "
                "user, row_count, copy_count, skipped_count, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'saved', ?)",
                (newsletter_id, nl["name"], nl["fund_name"], label, channel, run["sent_on"], getpass.getuser(), len(rows),
                 copies, len(check["skipped"]), now()))
            run_id = cur.lastrowid
            conn.executemany(
                f"INSERT INTO mailing_run_rows (run_id, {', '.join(_ROW_COLUMNS)}) "
                f"VALUES (?, {', '.join('?' * len(_ROW_COLUMNS))})",
                [(run_id, *[r[k] for k in _ROW_COLUMNS]) for r in rows])
            activity.log(conn, "mailing.create", f"Saved {'email list' if channel == 'Email' else 'mailing'} "
                         f"{nl['name']} {label}: {len(rows)} addresses" + ("" if channel == "Email" else f", {copies} copies"),
                         "newsletter", newsletter_id)
    except BaseException:
        if os.path.exists(out_path):
            os.remove(out_path)
        raise
    return run_id


# ---------- saved runs ----------

def list_runs(conn):
    return conn.execute("SELECT * FROM mailing_runs ORDER BY id DESC").fetchall()


def get_run(conn, run_id):
    return conn.execute("SELECT * FROM mailing_runs WHERE id = ?", (run_id,)).fetchone()


def run_rows(conn, run_id):
    return conn.execute("SELECT * FROM mailing_run_rows WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()


def write_skipped_csv(out_path, skipped):
    """The people left out of a run and why, for the person making the list."""
    return exporter.write_skipped_csv(out_path, skipped)


def export_run(conn, run_id, out_path):
    """Writes the saved mailing again, from its saved rows only."""
    run = get_run(conn, run_id)
    if run is None:
        raise MailingError("That mailing was not found.")
    count = exporter.write_run_csv(out_path, run, run_rows(conn, run_id))
    if not conn.read_only:
        activity.log(conn, "mailing.resave", f"Saved the file again for {run['newsletter_name']} {run['issue_label']}",
                     "newsletter", run["newsletter_id"])
    return count


# ---------- history ----------

def history_starts(conn):
    return conn.execute("SELECT MIN(sent_on) FROM mailing_runs").fetchone()[0]


def _entity_rows(kind):
    return "r.church_id = ?" if kind == "church" else "r.person_id = ? AND r.church_id IS NULL"


def last_sent(conn, kind, entity_id):
    """{newsletter_id: 'Fall 2026 (2026-10-03)'} for the latest saved mailing that included this entity."""
    out = {}
    for r in conn.execute(
            "SELECT m.newsletter_id, m.issue_label, m.sent_on FROM mailing_run_rows r JOIN mailing_runs m "
            f"ON m.id = r.run_id WHERE {_entity_rows(kind)} ORDER BY m.id", (entity_id,)):
        out[r["newsletter_id"]] = f"{r['issue_label']} ({r['sent_on']})"
    return out


def history(conn, kind, entity_id, limit=30):
    """Mailings and logged changes for one person or church, newest first, as {'ts', 'summary'}."""
    items = [{"ts": r["sent_on"], "summary": (f"Emailed {r['issue_label']} ({r['newsletter_name']})"
                                               if r["channel"] == "Email" else
                                               f"Mailed {r['issue_label']} ({r['newsletter_name']}): {r['quantity']} copies")}
             for r in conn.execute(
                 "SELECT m.sent_on, m.issue_label, m.newsletter_name, m.channel, r.quantity FROM mailing_run_rows r "
                 f"JOIN mailing_runs m ON m.id = r.run_id WHERE {_entity_rows(kind)} ORDER BY m.id DESC LIMIT ?",
                 (entity_id, limit))]
    items += [{"ts": r["ts"], "summary": r["summary"]} for r in conn.execute(
        "SELECT ts, summary FROM activity_log WHERE entity_type = ? AND entity_id = ? ORDER BY id DESC LIMIT ?",
        (kind, entity_id, limit))]
    items.sort(key=lambda i: i["ts"], reverse=True)
    return items[:limit]


# ---------- quick answer (Find box) ----------

def quick_answer(conn, text):
    """One line for the Find box when the text names exactly one church (or, failing that, one person)."""
    text = (text or "").strip()
    if len(text) < 2:
        return None
    like = f"%{text}%"
    found = conn.execute("SELECT id, name FROM churches WHERE active = 1 AND name LIKE ?", (like,)).fetchall()
    kind = "church"
    if len(found) != 1:
        if found:
            return None
        found = conn.execute(
            "SELECT id, TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) AS name FROM people "
            "WHERE COALESCE(active, 1) = 1 AND (COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) LIKE ?",
            (like,)).fetchall()
        kind = "person"
        if len(found) != 1:
            return None
    ent = found[0]
    subs = conn.execute(
        f"SELECT s.newsletter_id, n.name, s.quantity FROM subscriptions_v2 s JOIN newsletters n "
        f"ON n.id = s.newsletter_id WHERE s.{kind}_id = ? AND s.active = 1 ORDER BY n.name", (ent["id"],)).fetchall()
    if not subs:
        return f"{ent['name']}: not on any mailing list."
    parts = []
    for s in subs:
        last = conn.execute(
            "SELECT m.issue_label, r.recipient_name FROM mailing_run_rows r JOIN mailing_runs m ON m.id = r.run_id "
            f"WHERE m.newsletter_id = ? AND {_entity_rows(kind)} ORDER BY m.id DESC LIMIT 1",
            (s["newsletter_id"], ent["id"])).fetchone()
        copies = f"{s['quantity']} {'copy' if s['quantity'] == 1 else 'copies'}"
        if len(subs) > 1:
            copies = f"{s['name']} {copies}"
        mailed = f"last mailed {last['issue_label']} to {last['recipient_name']}" if last else "not mailed yet"
        parts.append(f"{copies}, {mailed}")
    return f"{ent['name']}: " + "; ".join(parts)


# ---------- packing sheet ----------

def packing_sheet(conn, run_id):
    """Copies by state and church for a saved run, from its saved rows only."""
    run = get_run(conn, run_id)
    if run is None:
        raise MailingError("That mailing was not found.")
    if run["channel"] == "Email":
        raise MailingError("An email list has no packing sheet.")
    states = {}
    for r in run_rows(conn, run_id):
        states.setdefault(r["state"] or "No state", []).append(
            {"church": r["church_name"] or "", "who": r["recipient_name"] or "", "city": r["city"] or "",
             "copies": r["quantity"]})
    groups = []
    for state in sorted(states):
        bundles = sorted(states[state], key=lambda b: (b["church"].lower(), b["who"].lower()))
        groups.append({"state": state, "bundles": bundles, "copies": sum(b["copies"] for b in bundles)})
    return {"run": run, "groups": groups, "bundles": run["row_count"], "copies": run["copy_count"]}


def write_packing_csv(out_path, sheet):
    safe = exporter.csv_safe
    run = sheet["run"]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([safe(f"{run['newsletter_name']} - {run['issue_label']}"), f"Sent {run['sent_on']}"])
        w.writerow(["State", "Church", "Addressed to", "City", "Copies"])
        for g in sheet["groups"]:
            for b in g["bundles"]:
                w.writerow([safe(g["state"]), safe(b["church"]), safe(b["who"]), safe(b["city"]), b["copies"]])
            w.writerow([safe(g["state"]) + " total", "", "", "", g["copies"]])
        w.writerow(["Issue total", "", "", f"{sheet['bundles']} bundles", sheet["copies"]])
