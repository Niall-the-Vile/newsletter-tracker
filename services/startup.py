"""Opens the shared data file safely: version check, lock, backup, migration, integrity check."""
import os
import sqlite3

import db
from data import migrations
from data.connection import connect, set_read_only
from data.migrations import MigrationError, NewerVersionError  # noqa: F401  (re-exported for callers)
from services import backup, hygiene
from services.lock import WriterLock


class UpdateDeclined(Exception):
    pass


class DamagedFileError(Exception):
    pass


class Session:
    def __init__(self, conn, db_path, lock, warnings, needs_update):
        self.conn = conn
        self.db_path = db_path
        self.lock = lock
        self.warnings = warnings
        self.needs_update = needs_update  # read-only on an older file, so no update has happened yet

    @property
    def read_only(self):
        return self.conn.read_only

    def banner(self):
        if not self.read_only:
            return None
        if self.lock.status == "stale":
            text = (f"{self.lock.holder_text()}, but seems to have stopped. "
                    "You can look but not change anything until you take over.")
        else:
            text = f"{self.lock.holder_text()}. You can look but not change anything."
        if self.needs_update:
            text += " The data file also needs a one-time update, which happens when the person editing closes it."
        return text

    def heartbeat(self):
        """Keeps the lock fresh. If someone took it over, this copy turns read-only."""
        if self.lock.status == "owner" and not self.lock.beat():
            set_read_only(self.conn, True)
        return not self.read_only

    def take_over(self):
        """Returns 'ok', 'restart' (file needs updating first) or 'busy' (holder is still active)."""
        if not self.lock.take_over():
            return "busy"
        if self.needs_update:
            self.lock.release()
            return "restart"
        set_read_only(self.conn, False)
        self.conn.data_version = db_version(self.conn)
        return "ok"

    def close(self):
        self.conn.close()
        self.lock.release()


def db_version(conn):
    row = conn.execute("SELECT value FROM meta WHERE key = 'data_version'").fetchone()
    return int(row[0]) if row else None


def peek(db_path):
    """(user_version, is_empty) read without changing anything."""
    c = backup.open_readonly(db_path)
    try:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        empty = c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
        return version, empty
    finally:
        c.close()


def _seed_starter_newsletters(conn):
    with db.transaction(conn):
        for name, desc in db.DEFAULT_NEWSLETTERS:
            conn.execute("INSERT OR IGNORE INTO newsletters (name, description, created_at) VALUES (?, ?, ?)",
                         (name, desc, db.now()))


def open_session(db_path, confirm_update=lambda: True):
    """confirm_update() is asked before an existing file is updated; return False to cancel."""
    db_path = str(db_path)
    exists = os.path.exists(db_path)
    version, empty = peek(db_path) if exists else (0, True)
    migrations.check_not_newer(version)
    current = version == migrations.SCHEMA_VERSION

    lock = WriterLock(db_path)
    lock.acquire()
    can_write = lock.status == "owner"
    try:
        if can_write and not current:
            if not empty and not confirm_update():
                raise UpdateDeclined()
            counts_before = None
            if not empty:
                counts_before = backup.legacy_counts(backup.backup_both(db_path, "before-update")[0]["counts"])
            conn = connect(db_path)
            try:
                migrations.migrate(conn)
                if counts_before is not None:
                    after = backup.legacy_counts(backup.table_counts(conn))
                    if after != counts_before:
                        raise MigrationError(
                            "The update changed the number of people or subscriptions. "
                            "Nothing more was done. Restore from the backup made just before the update.")
            except BaseException:
                conn.close()
                raise
            if empty:
                _seed_starter_newsletters(conn)
            conn.close()
        conn = connect(db_path, read_only=not can_write)
        try:
            if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
                raise DamagedFileError(
                    "The data file did not pass its health check. Do not use it. "
                    "Restore from a backup (File > Restore from backup).")
            warnings = hygiene.startup_warnings(db_path)
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                warnings.append("Some records point at people or newsletters that no longer exist. Tell Niall.")
            if can_write:
                backup.ensure_daily_backup(db_path)
        except BaseException:
            conn.close()
            raise
    except BaseException:
        lock.release()
        raise
    return Session(conn, db_path, lock, warnings, needs_update=(not can_write and not current))
