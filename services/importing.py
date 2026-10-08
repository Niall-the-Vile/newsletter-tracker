"""Import (M14): a dry-run report first, then one transaction after a backup.

plan_import() only reads. apply_import() plans again inside the transaction, so it writes what the data says
now. A blank cell never overwrites something already on file.
"""
import os
from collections import Counter

import db
import importer
from data import activity
from data.connection import now, require_writable, transaction
from services import backup, grouping

UNREADABLE = chr(0xFFFD)  # what the cp1252 fallback leaves where it could not read a character
SKIP_BLANK = "blank row"
SKIP_NO_NAME = "no name"
SKIP_REPEAT = "repeats an earlier row in this file"
SKIP_REMOVED = "matches a person who was removed"


class ImportError_(Exception):
    pass


def _norm(text):
    return " ".join((text or "").split()).lower()


def _person_key(m):
    return (_norm(m.get("first_name")), _norm(m.get("last_name")), _norm(m.get("address1")),
            (m.get("zip") or "")[:5])


def _changes(existing, mapped):
    return {c: v for c, v in mapped.items()
            if c in db.PEOPLE_COLUMNS and c != "active" and v is not None and (existing[c] or "") != v}


def plan_import(conn, headers, rows, mapping, file_fund_id=None):
    """Reads only. Returns the summary plus one entry per row saying what would happen."""
    fund_ids = {r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM funds WHERE active = 1")}
    church_ids = grouping.existing_church_map(conn)
    seen, entries = set(), []
    skipped, unknown_funds = Counter(), Counter()
    file_churches = Counter()  # group key -> rows, with one label each
    labels = {}
    garbled, fund_churches = 0, set()
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        mapped = importer.map_row(row, mapping)
        if any(UNREADABLE in str(v) for v in row.values() if v is not None):
            garbled += 1
        if not any(v for v in mapped.values()):
            skipped[SKIP_BLANK] += 1
            continue
        if not mapped.get("first_name") and not mapped.get("last_name"):
            if not mapped.get("church_name"):
                skipped[SKIP_NO_NAME] += 1
                continue
            mapped["last_name"] = mapped["church_name"]  # a church with no named contact: the church is the recipient
        key = _person_key(mapped)
        if key in seen:
            skipped[SKIP_REPEAT] += 1
            continue
        seen.add(key)
        existing = db.find_duplicate_person(conn, mapped.get("first_name"), mapped.get("last_name"),
                                            mapped.get("address1"), mapped.get("zip"))
        if existing is not None and not (existing["active"] is None or existing["active"]):
            skipped[SKIP_REMOVED] += 1
            continue

        fund, explicit = None, False
        raw_fund = mapped.get("fund")
        if raw_fund:
            fund, explicit = fund_ids.get(raw_fund.lower()), True
            if fund is None:
                unknown_funds[raw_fund] += 1
        elif file_fund_id:
            fund = file_fund_id

        church_id, church_key = None, None
        church = mapped.get("church_name")
        if church and not grouping.no_church(church):
            church_key = grouping.group_key(church, mapped.get("conference"), mapped.get("state"))
            church_id = church_ids.get(church_key)
            if church_id is None:
                file_churches[church_key] += 1
                labels.setdefault(church_key, " ".join(church.split()))
        if existing is not None and explicit and fund:
            rep = conn.execute("SELECT c.id, c.fund_id FROM church_reps r JOIN churches c ON c.id = r.church_id "
                               "WHERE r.person_id = ? AND r.active = 1 ORDER BY r.id LIMIT 1",
                               (existing["id"],)).fetchone()
            if rep and rep["fund_id"] is not None and rep["fund_id"] != fund:
                fund_churches.add(rep["id"])
        entry = {"row": number, "mapped": mapped, "existing": existing, "fund": fund, "explicit_fund": explicit,
                 "church_id": church_id}
        if existing is None:
            entry["action"] = "new"
        else:
            entry["changes"] = _changes(existing, mapped)
            entry["action"] = "update" if entry["changes"] else "unchanged"
        entries.append(entry)

    items = [{"key": k, "label": labels[k], "members": n, "is_new": True} for k, n in file_churches.items()]
    for c in conn.execute("SELECT c.name, c.name_key, c.conference, c.state FROM churches c WHERE c.active = 1"):
        items.append({"key": f"{c['name_key']}|{grouping.clean(c['conference']) or grouping.clean(c['state'])}",
                      "label": c["name"], "members": 0, "is_new": False})
    near = [{"name": a["label"] if a["is_new"] else b["label"], "members": a["members"] or b["members"],
             "like": b["label"] if a["is_new"] else a["label"]} for a, b in grouping.near_pairs(items)]
    mapped_headers = set(mapping.values())
    counts = Counter(e["action"] for e in entries)
    return {
        "entries": entries,
        "summary": {"new": counts["new"], "updated": counts["update"], "unchanged": counts["unchanged"],
                    "skipped": sum(skipped.values()), "skip_reasons": dict(skipped),
                    "linked_to_church": sum(1 for e in entries if e["church_id"]),
                    "unmatched_churches": len(file_churches), "unknown_funds": dict(unknown_funds),
                    "unreadable_rows": garbled, "churches_changing_fund": len(fund_churches)},
        "near_churches": near,
        "ignored_columns": [h for h in headers if h is not None and str(h).strip() and h not in mapped_headers],
    }


def _apply_fund(conn, person_id, fund, explicit, ts):
    """A rep's fund belongs to the church. Without a Fund column a fund only fills an empty one."""
    rep = conn.execute("SELECT church_id FROM church_reps WHERE person_id = ? AND active = 1 ORDER BY id LIMIT 1",
                       (person_id,)).fetchone()
    table, row_id = ("churches", rep["church_id"]) if rep else ("people", person_id)
    current = conn.execute(f"SELECT fund_id FROM {table} WHERE id = ?", (row_id,)).fetchone()["fund_id"]
    if current == fund or (current is not None and not explicit):
        return False
    conn.execute(f"UPDATE {table} SET fund_id = ?, updated_at = ? WHERE id = ?", (fund, ts, row_id))
    return True


def apply_import(conn, headers, rows, mapping, file_name, file_fund_id=None, subscribe=False, newsletter_id=None,
                 quantity=1):
    require_writable(conn)
    if subscribe and newsletter_id is None:
        raise ImportError_("Choose which newsletter to subscribe the imported people to.")
    backup.backup_both(conn.db_path, "before-import")
    ts = now()
    cols_ok = set(db.PEOPLE_COLUMNS)
    with transaction(conn):
        p = plan_import(conn, headers, rows, mapping, file_fund_id)
        s = p["summary"]
        batch = activity.log(conn, "import", "Importing")
        funds_set = 0
        for e in p["entries"]:
            mapped = e["mapped"]
            if e["action"] == "new":
                data = {c: v for c, v in mapped.items() if c in cols_ok and v is not None}
                data.setdefault("recipient_type", "Church" if not data.get("first_name") else "Member")
                data.update(active=1, created_at=ts, updated_at=ts)
                if e["church_id"]:
                    data["church_id"] = e["church_id"]
                cur = conn.execute(f"INSERT INTO people ({', '.join(data)}) VALUES ({', '.join(':' + c for c in data)})",
                                   data)
                person_id = cur.lastrowid
                activity.log(conn, "person.import_new", "Added by import", "person", person_id, batch)
            else:
                person_id = e["existing"]["id"]
                changes = dict(e["changes"])
                if e["church_id"] and e["existing"]["church_id"] is None:
                    changes["church_id"] = e["church_id"]
                if changes:
                    changes["updated_at"] = ts
                    conn.execute(f"UPDATE people SET {', '.join(c + ' = :' + c for c in changes)} WHERE id = :id",
                                 {**changes, "id": person_id})
                    if any(k in changes for k in ("address1", "address2", "city", "state", "zip")):
                        conn.execute("UPDATE people SET returned_mail = 0 WHERE id = ?", (person_id,))
                    activity.log(conn, "person.import_update", "Updated by import", "person", person_id, batch)
            if e["fund"] and _apply_fund(conn, person_id, e["fund"], e["explicit_fund"], ts):
                funds_set += 1
            if subscribe:  # keeps an existing, possibly custom, quantity
                conn.execute("INSERT OR IGNORE INTO subscriptions_v2 (person_id, newsletter_id, quantity, active, "
                             "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                             (person_id, newsletter_id, quantity, ts, ts))
        s["funds_set"] = funds_set
        activity.set_summary(conn, batch, (
            f"Imported {os.path.basename(file_name)}: {s['new']} new, {s['updated']} updated, "
            f"{s['unchanged']} unchanged, {s['skipped']} skipped"))
    return s
