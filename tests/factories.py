"""Generated FAKE data only. Never read or copy the real database in tests (except the live-copy test)."""
import random
import sqlite3

# The schema exactly as the old program created it, before versioning existed.
LEGACY_SCHEMA = [
    """CREATE TABLE people (
        id INTEGER PRIMARY KEY AUTOINCREMENT, first_name TEXT, middle_name TEXT, last_name TEXT,
        address1 TEXT, address2 TEXT, city TEXT, state TEXT, zip TEXT, home_phone TEXT, cell_phone TEXT,
        email TEXT, church_name TEXT, conference TEXT, title TEXT, recipient_type TEXT DEFAULT 'Member',
        verified TEXT, notes TEXT, source TEXT, active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE newsletters (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, description TEXT, created_at TEXT)""",
    """CREATE TABLE subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        newsletter_id INTEGER NOT NULL REFERENCES newsletters(id) ON DELETE CASCADE,
        quantity INTEGER DEFAULT 1, active INTEGER DEFAULT 1, notes TEXT,
        UNIQUE(person_id, newsletter_id))""",
]

FIRST = ["Ada", "Ben", "Cora", "Dan", "Eli", "Fay", "Gus", "Hana", "Ivan", "Jo"]
LAST = ["Alder", "Birch", "Cedar", "Dunn", "Elm", "Fir", "Grove", "Hazel", "Ivy", "Juniper"]
STATES = ["PA", "OH", "IN", "VA"]


def fake_person(i, rng=None):
    rng = rng or random.Random(i)
    return {
        "first_name": rng.choice(FIRST), "last_name": f"{rng.choice(LAST)}{i}",
        "address1": f"{100 + i} Test Lane", "city": "Faketown", "state": rng.choice(STATES),
        "zip": f"{10000 + i:05d}", "email": f"person{i}@example.invalid",
        "church_name": f"Fake Church {i % 7}", "recipient_type": "Member", "active": 1,
    }


def make_legacy_db(path, people=25, newsletters=2):
    """A fake file shaped like the old program's, with user_version 0. Returns the three row counts."""
    conn = sqlite3.connect(str(path))
    for stmt in LEGACY_SCHEMA:
        conn.execute(stmt)
    for n in range(newsletters):
        conn.execute("INSERT INTO newsletters (name, description) VALUES (?, ?)", (f"Fake Newsletter {n}", "x"))
    for i in range(people):
        p = fake_person(i)
        cols = ", ".join(p)
        conn.execute(f"INSERT INTO people ({cols}) VALUES ({', '.join('?' * len(p))})", list(p.values()))
        conn.execute("INSERT INTO subscriptions (person_id, newsletter_id, quantity) VALUES (?, 1, ?)",
                     (i + 1, 1 + i % 3))
    conn.commit()
    conn.close()
    return people, newsletters, people


def church_spec(i):
    """(church name, conference) for generated row i. Of every 25 rows: 20 normal, 2 same name in two
    conferences, 3 with no church."""
    k = i % 25
    if k < 19:
        return f"Fake Church {k}", "Conf Alpha"
    if k == 19:
        return "Fake Chapel 18", "Conf Alpha"  # a near match of Fake Church 18
    if k in (20, 21):
        return "Grace Fellowship", "Conf Alpha" if k == 20 else "Conf Beta"
    return ["None", "", "N/A"][k - 22], ""


HEADERS = ["First Name", "Last Name", "Address 1", "City", "State", "Zip", "Email", "Church Name", "Conference",
           "Title", "Spare Column"]


def file_row(i):
    church, conference = church_spec(i)
    return [f"Pat{i}", f"Fake{i}", f"{i} Test Road", "Faketown", "PA", f"{i % 100000:05d}",
            f"pat{i}@example.invalid", church, conference, "Pastor" if i % 10 == 0 else "", "x"]


def write_people_csv(path, count=2000, encoding="utf-8-sig"):
    import csv
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        for i in range(count):
            w.writerow(file_row(i))
