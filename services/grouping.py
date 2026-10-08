"""Church grouping wizard (M15).

plan() is a dry run: it only reads. apply_grouping() re-plans inside one transaction, so what is written is
what the current data says, then creates churches tagged with that run so the run can be rolled back.
Names are matched on a normalized name plus conference (state when the conference is blank). The raw
people.church_name is never changed.
"""
import re
from collections import Counter
from difflib import SequenceMatcher

from data import activity
from data.connection import now, require_writable, transaction
from services import backup, churches

NO_CHURCH_WORDS = {"none", "na", "n a", "member", "members"}
_GENERIC = {"church", "the", "of", "and", "congregation", "fellowship", "chapel"}
_ABBREV = {"st": "saint", "mt": "mount", "ch": "church"}
NEAR_RATIO = 0.88


class GroupingError(Exception):
    pass


def clean(text):
    text = (text or "").lower().replace("&", " and ")
    return " ".join(re.sub(r"[\W_]+", " ", text).split())


def name_key(name):
    key = clean(name)
    return key[4:] if key.startswith("the ") else key


def no_church(name):
    return name_key(name) in NO_CHURCH_WORDS or not name_key(name)


def group_key(name, conference, state):
    return f"{name_key(name)}|{clean(conference) or clean(state)}"


def _core(key):
    words = [_ABBREV.get(w, w) for w in key.split()]
    return " ".join(w for w in words if w not in _GENERIC)


def is_near(key_a, key_b):
    if key_a == key_b or re.findall(r"\d+", key_a) != re.findall(r"\d+", key_b):
        return False
    core_a, core_b = _core(key_a), _core(key_b)
    if core_a and core_a == core_b:
        return True
    a, b = key_a.replace(" ", ""), key_b.replace(" ", "")
    m = SequenceMatcher(None, a, b)
    return m.real_quick_ratio() >= NEAR_RATIO and m.quick_ratio() >= NEAR_RATIO and m.ratio() >= NEAR_RATIO


def near_pairs(items):
    """items: dicts with key (name|conference), label, members, is_new. Pairs in the same conference/state."""
    buckets = {}
    for it in items:
        buckets.setdefault(it["key"].split("|", 1)[1], []).append(it)
    out = []
    for bucket in buckets.values():
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if (a["is_new"] or b["is_new"]) and is_near(a["key"].split("|")[0], b["key"].split("|")[0]):
                    out.append((a, b))
    return out


def existing_church_map(conn):
    """group key -> church id, from churches and from remembered merges (aliases)."""
    mapping = {}
    for c in conn.execute("SELECT id, name_key, conference, state FROM churches WHERE active = 1 ORDER BY id"):
        mapping.setdefault(f"{c['name_key']}|{clean(c['conference']) or clean(c['state'])}", c["id"])
    for a in conn.execute("SELECT a.alias_key, a.church_id FROM church_aliases a "
                          "JOIN churches c ON c.id = a.church_id WHERE c.active = 1"):
        mapping.setdefault(a["alias_key"], a["church_id"])
    return mapping


def _top(counter):
    return counter.most_common(1)[0][0] if counter else None


def _proposed_role(p):
    return "Rep" if (p["title"] or "").strip() or p["recipient_type"] in ("Rep", "Church") else "Member"


def plan(conn, merges=None, separate=None, roles=None):
    """merges: {group key: group key to fold it into}. separate: conflict group keys to keep as their own churches.
    roles: {person id: 'Rep' or 'Member'} staff changes. Reads only."""
    merges, separate, roles = merges or {}, set(separate or ()), roles or {}
    existing = existing_church_map(conn)
    groups, unmatched = {}, 0
    rows = conn.execute(
        "SELECT id, first_name, last_name, title, recipient_type, church_name, conference, state, city FROM people p "
        "WHERE COALESCE(active, 1) = 1 AND church_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM church_reps r WHERE r.person_id = p.id AND r.active = 1)").fetchall()
    for p in rows:
        if no_church(p["church_name"]):
            unmatched += 1
            continue
        g = groups.setdefault(group_key(p["church_name"], p["conference"], p["state"]), {
            "people": [], "names": Counter(), "conf": Counter(), "state": Counter(), "city": Counter()})
        g["people"].append(p)
        g["names"][" ".join(p["church_name"].split())] += 1
        for field in ("conf", "state", "city"):
            value = (p["conference" if field == "conf" else field] or "").strip()
            if value:
                g[field][value] += 1

    def target_of(key):
        t = merges.get(key)
        return t if t and t != key and (t in existing or (t in groups and t not in merges)) else None

    folded = {k: target_of(k) for k in groups if target_of(k) and k not in existing}
    own = {k for k in groups if k not in folded and k not in existing}
    targets = set(folded.values())
    by_name = {}
    for k in list(own) + list(existing):
        by_name.setdefault(k.split("|")[0], set()).add(k)
    conflicts = {k for k in own if len(by_name[k.split("|")[0]]) > 1 and k not in separate and k not in targets}
    new_keys = sorted(own - conflicts)

    summary = {"created": len(new_keys), "linked": 0, "unmatched": unmatched, "conflicts": len(conflicts),
               "conflict_people": sum(len(groups[k]["people"]) for k in conflicts)}
    people_rows, destination = [], {}
    for k, g in groups.items():
        if k in conflicts:
            continue
        dest = folded.get(k) or k
        destination[k] = dest
        label = _top(groups[dest]["names"]) if dest in groups else None
        for p in g["people"]:
            summary["linked"] += 1
            proposed = _proposed_role(p)
            people_rows.append({"person_id": p["id"], "name": " ".join(filter(None, [p["first_name"], p["last_name"]])),
                                "church": label or _top(g["names"]), "key": k, "proposed": proposed,
                                "role": roles.get(p["id"], proposed)})
    members_of = Counter()
    for k, d in destination.items():
        members_of[d] += len(groups[k]["people"])

    items = [{"key": k, "label": _top(groups[k]["names"]), "members": members_of[k], "is_new": True,
              "church_id": None} for k in new_keys]
    for c in conn.execute("SELECT c.id, c.name, c.name_key, c.conference, c.state, "
                          "(SELECT COUNT(*) FROM people p WHERE p.church_id = c.id AND COALESCE(p.active, 1) = 1) AS n "
                          "FROM churches c WHERE c.active = 1"):
        items.append({"key": f"{c['name_key']}|{clean(c['conference']) or clean(c['state'])}", "label": c["name"],
                      "members": c["n"], "is_new": False, "church_id": c["id"]})
    near = [{"a": a, "b": b} for a, b in near_pairs(items)]

    conflict_rows = [{"key": k, "name": _top(groups[k]["names"]), "conference": _top(groups[k]["conf"]) or
                      _top(groups[k]["state"]) or "", "members": len(groups[k]["people"]),
                      "also": sorted(by_name[k.split("|")[0]] - {k})} for k in sorted(conflicts)]
    return {"summary": summary, "new_keys": new_keys, "groups": groups, "destination": destination,
            "conflicts": conflict_rows, "near": near, "people": people_rows, "existing": existing,
            "folded": folded}


# ---------- confirm and roll back ----------

def apply_grouping(conn, merges=None, separate=None, roles=None):
    """Creates the planned churches and links people, in one transaction after a backup."""
    require_writable(conn)
    backup.backup_both(conn.db_path, "before-church-grouping")
    ts = now()
    with transaction(conn):
        p = plan(conn, merges, separate, roles)
        s = p["summary"]
        batch = activity.log(conn, "church.group", "Grouping churches")
        tag = f"group-{batch}"
        church_ids = dict(p["existing"])
        for k in p["new_keys"]:
            g = p["groups"][k]
            name = _top(g["names"])
            cur = conn.execute(
                "INSERT INTO churches (name, name_key, conference, city, state, import_tag, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, name_key(name), _top(g["conf"]), _top(g["city"]), _top(g["state"]), tag, ts, ts))
            church_ids[k] = cur.lastrowid
            activity.log(conn, "church.add", f"Added church {name} from grouping", "church", cur.lastrowid, batch)
        for k, target in p["folded"].items():
            conn.execute("INSERT OR IGNORE INTO church_aliases (alias_key, church_id, created_at) VALUES (?, ?, ?)",
                         (k, church_ids[target], ts))
        touched = set()
        for row in p["people"]:
            cid = church_ids[p["destination"][row["key"]]]
            conn.execute("UPDATE people SET church_id = ?, updated_at = ? WHERE id = ?", (cid, ts, row["person_id"]))
            if row["role"] == "Rep":
                churches.add_rep(conn, cid, row["person_id"], auto_default=False, parent_id=batch)
                touched.add(cid)
        defaults = sum(1 for cid in touched if churches.settle_single_rep(conn, cid))
        activity.set_summary(conn, batch, f"Grouped churches: {s['created']} created, {s['linked']} people linked, "
                             f"{s['conflicts']} held for review")
    s.update({"tag": tag, "defaults_set": defaults})
    return s


def list_groupings(conn):
    return conn.execute(
        "SELECT import_tag AS tag, COUNT(*) AS churches, MIN(created_at) AS created_at FROM churches "
        "WHERE import_tag IS NOT NULL GROUP BY import_tag ORDER BY MIN(id) DESC").fetchall()


def rollback_grouping(conn, tag):
    """Removes the churches a run created, with their rep links, remembered merges and member links."""
    require_writable(conn)
    ids = [r["id"] for r in conn.execute("SELECT id FROM churches WHERE import_tag = ?", (tag,))]
    if not ids:
        raise GroupingError("There is nothing to undo for that run.")
    marks = ",".join("?" * len(ids))
    if conn.execute(f"SELECT 1 FROM subscriptions_v2 WHERE church_id IN ({marks}) LIMIT 1", ids).fetchone():
        raise GroupingError("Some of these churches already have newsletters set up, so the run cannot be undone.")
    backup.backup_both(conn.db_path, "before-undo-grouping")
    with transaction(conn):
        conn.execute(f"DELETE FROM church_reps WHERE church_id IN ({marks})", ids)
        unlinked = conn.execute(f"UPDATE people SET church_id = NULL WHERE church_id IN ({marks})", ids).rowcount
        conn.execute(f"DELETE FROM church_aliases WHERE church_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM churches WHERE id IN ({marks})", ids)
        activity.log(conn, "church.group_undo", f"Undid church grouping: {len(ids)} churches removed, "
                     f"{unlinked} people unlinked")
    return {"churches": len(ids), "unlinked": unlinked}
