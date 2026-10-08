"""Versioned schema steps. Each step is one transaction, with user_version set last."""
from data import activity
from data.connection import UPDATE_MESSAGE, now

LEGACY_TABLES = ("people", "newsletters", "subscriptions")


class NewerVersionError(Exception):
    pass


class MigrationError(Exception):
    pass


def _v1():
    return [
        """CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT, middle_name TEXT, last_name TEXT,
            address1 TEXT, address2 TEXT, city TEXT, state TEXT, zip TEXT,
            home_phone TEXT, cell_phone TEXT, email TEXT,
            church_name TEXT, conference TEXT, title TEXT,
            recipient_type TEXT DEFAULT 'Member',
            verified TEXT, notes TEXT, source TEXT,
            active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS newsletters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL, description TEXT, created_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            newsletter_id INTEGER NOT NULL REFERENCES newsletters(id) ON DELETE CASCADE,
            quantity INTEGER DEFAULT 1, active INTEGER DEFAULT 1, notes TEXT,
            UNIQUE(person_id, newsletter_id))""",
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('data_version', '0')",
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('legacy_writes', '0')",
    ]


def _seed_funds(conn):
    for name in ("MAAP", "AMCA"):
        conn.execute("INSERT OR IGNORE INTO funds (name, created_at) VALUES (?, ?)", (name, now()))


def _v2():
    return [
        """CREATE TABLE funds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            created_at TEXT)""",
        """CREATE TABLE churches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            conference TEXT, city TEXT, state TEXT,
            fund_id INTEGER REFERENCES funds(id) ON DELETE RESTRICT,
            no_contact INTEGER NOT NULL DEFAULT 0 CHECK (no_contact IN (0, 1)),
            import_tag TEXT,
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            created_at TEXT, updated_at TEXT)""",
        "CREATE INDEX idx_churches_name_key ON churches(name_key)",
        """CREATE TABLE church_reps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            church_id INTEGER NOT NULL REFERENCES churches(id) ON DELETE RESTRICT,
            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
            is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            start_date TEXT, end_date TEXT, created_at TEXT)""",
        "CREATE UNIQUE INDEX uq_church_default_rep ON church_reps(church_id) WHERE is_default = 1 AND active = 1",
        "CREATE INDEX idx_church_reps_person ON church_reps(person_id)",
        "ALTER TABLE people ADD COLUMN fund_id INTEGER REFERENCES funds(id)",
        "ALTER TABLE people ADD COLUMN email_key TEXT",
        "ALTER TABLE people ADD COLUMN address_key TEXT",
        "ALTER TABLE people ADD COLUMN external_id TEXT",
        "ALTER TABLE newsletters ADD COLUMN fund_id INTEGER REFERENCES funds(id)",
        "ALTER TABLE newsletters ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_newsletter ON subscriptions(newsletter_id, person_id)",
        """CREATE TABLE subscriptions_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER REFERENCES people(id) ON DELETE RESTRICT,
            church_id INTEGER REFERENCES churches(id) ON DELETE RESTRICT,
            newsletter_id INTEGER NOT NULL REFERENCES newsletters(id) ON DELETE RESTRICT,
            quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
            delivery TEXT NOT NULL DEFAULT 'Print' CHECK (delivery IN ('Print', 'Email', 'Both')),
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            notes TEXT,
            created_at TEXT, updated_at TEXT,
            CHECK ((person_id IS NULL) <> (church_id IS NULL)))""",
        "CREATE UNIQUE INDEX uq_sub_v2_person ON subscriptions_v2(person_id, newsletter_id) WHERE person_id IS NOT NULL",
        "CREATE UNIQUE INDEX uq_sub_v2_church ON subscriptions_v2(church_id, newsletter_id) WHERE church_id IS NOT NULL",
        """CREATE VIEW v_entities AS
            SELECT 'Church' AS entity_type, c.id AS entity_id, c.name AS name, c.fund_id AS fund_id,
                   c.city AS city, c.state AS state, c.active AS active
            FROM churches c
            UNION ALL
            SELECT 'Member', p.id, TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')), p.fund_id,
                   p.city, p.state, COALESCE(p.active, 1)
            FROM people p""",
        """CREATE TABLE mailing_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            newsletter_id INTEGER NOT NULL REFERENCES newsletters(id) ON DELETE RESTRICT,
            newsletter_name TEXT NOT NULL,
            fund_name TEXT,
            issue_label TEXT NOT NULL,
            channel TEXT NOT NULL CHECK (channel IN ('Print', 'Email')),
            sent_on TEXT NOT NULL,
            user TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            copy_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            source_run_id INTEGER REFERENCES mailing_runs(id),
            status TEXT NOT NULL DEFAULT 'saved' CHECK (status IN ('saved', 'sending', 'sent', 'partial', 'failed')),
            created_at TEXT)""",
        """CREATE TABLE mailing_run_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES mailing_runs(id),
            person_id INTEGER, church_id INTEGER,
            recipient_name TEXT, church_name TEXT,
            address1 TEXT, address2 TEXT, city TEXT, state TEXT, zip TEXT,
            quantity INTEGER NOT NULL,
            delivery TEXT,
            email TEXT,
            fund_name TEXT,
            send_status TEXT NOT NULL DEFAULT 'pending')""",
        "CREATE INDEX idx_run_rows_run ON mailing_run_rows(run_id)",
        """CREATE TABLE activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            user TEXT, app_version TEXT,
            action TEXT NOT NULL,
            entity_type TEXT, entity_id INTEGER,
            summary TEXT,
            parent_id INTEGER)""",
        """CREATE TABLE church_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_key TEXT NOT NULL UNIQUE,
            church_id INTEGER NOT NULL REFERENCES churches(id) ON DELETE CASCADE,
            created_at TEXT)""",
        """CREATE TABLE subscription_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT, new_value TEXT,
            changed_at TEXT NOT NULL,
            changed_by TEXT,
            note TEXT)""",
        _seed_funds,
    ]


def _v3():
    msg = UPDATE_MESSAGE.replace(",", "")
    stmts = []
    for table in ("people", "subscriptions"):
        for event in ("INSERT", "UPDATE", "DELETE"):
            stmts.append(
                f"CREATE TRIGGER legacy_{table}_no_{event.lower()} BEFORE {event} ON {table} "
                "WHEN (SELECT value FROM meta WHERE key = 'legacy_writes') <> '1' "
                f"BEGIN SELECT RAISE(ABORT, '{msg}'); END"
            )
    return stmts


def _v4():
    return [
        "ALTER TABLE people ADD COLUMN church_id INTEGER REFERENCES churches(id)",
        "CREATE INDEX idx_people_church ON people(church_id)",
    ]


def _v5():
    return [
        "ALTER TABLE people ADD COLUMN returned_mail INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE churches ADD COLUMN returned_mail INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE mailing_run_rows ADD COLUMN first_name TEXT",
        "ALTER TABLE mailing_run_rows ADD COLUMN middle_name TEXT",
        "ALTER TABLE mailing_run_rows ADD COLUMN last_name TEXT",
        "ALTER TABLE mailing_run_rows ADD COLUMN recipient_type TEXT",
        "CREATE INDEX idx_run_rows_person ON mailing_run_rows(person_id)",
        "CREATE INDEX idx_run_rows_church ON mailing_run_rows(church_id)",
        "CREATE INDEX idx_runs_newsletter ON mailing_runs(newsletter_id)",
    ]


STEPS = [(1, _v1()), (2, _v2()), (3, _v3()), (4, _v4()), (5, _v5())]
SCHEMA_VERSION = STEPS[-1][0]


def get_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def check_not_newer(version):
    if version > SCHEMA_VERSION:
        raise NewerVersionError(
            "This data file was saved by a newer version of Newsletter Tracker. " + UPDATE_MESSAGE
        )


def migrate(conn, steps=None):
    """Applies pending steps, each atomically. Returns the versions applied."""
    steps = STEPS if steps is None else steps
    check_not_newer(get_version(conn))
    applied = []
    for version, items in steps:
        if version <= get_version(conn):
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in items:
                if callable(item):
                    item(conn)
                else:
                    conn.execute(item)
            if version >= 2:
                activity.log(conn, "migrate", f"Updated the data file to version {version}")
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied
