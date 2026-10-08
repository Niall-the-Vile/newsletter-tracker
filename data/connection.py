"""Connections and the single write path (transaction) for the shared data file."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

UPDATE_MESSAGE = (
    "This copy of Newsletter Tracker is out of date. "
    "Please update your copy from the shared folder, then open it again."
)


class ReadOnlyError(Exception):
    """Raised when something tries to change data while this copy is read-only."""


class ConflictError(Exception):
    """Raised when the file was changed by someone else since this copy opened it."""


class Conn(sqlite3.Connection):
    db_path = None
    read_only = False
    data_version = None  # last meta.data_version this copy saw


def require_writable(conn):
    if conn.read_only:
        raise ReadOnlyError("This copy is open for viewing only, so nothing can be changed.")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def has_meta(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone() is not None


def _read_meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def connect(db_path, read_only=False):
    conn = sqlite3.connect(str(db_path), timeout=8.0, isolation_level=None, factory=Conn)
    conn.db_path = str(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 8000")
    conn.execute("PRAGMA foreign_keys = ON")
    set_read_only(conn, read_only)
    if has_meta(conn):
        conn.data_version = int(_read_meta(conn, "data_version") or 0)
    return conn


def set_read_only(conn, read_only):
    conn.read_only = read_only
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA query_only = OFF")
        conn.execute("PRAGMA journal_mode = DELETE")  # never WAL: its side files break on OneDrive
        conn.execute("PRAGMA synchronous = FULL")


@contextmanager
def transaction(conn):
    """One write transaction. Nested use joins the outer one."""
    if conn.in_transaction:
        yield conn
        return
    if conn.read_only:
        raise ReadOnlyError("This copy is open for viewing only, so nothing can be changed.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        versioned = has_meta(conn)
        if versioned:
            current = int(_read_meta(conn, "data_version") or 0)
            if conn.data_version is not None and current != conn.data_version:
                raise ConflictError(
                    "The data file was changed by someone else after you opened it. "
                    "Close the program and open it again."
                )
            # Only this program sets the flag, and only inside its own transaction.
            conn.execute("UPDATE meta SET value = '1' WHERE key = 'legacy_writes'")
        yield conn
        if versioned:
            conn.execute("UPDATE meta SET value = '0' WHERE key = 'legacy_writes'")
            conn.execute("UPDATE meta SET value = ? WHERE key = 'data_version'", (str(current + 1),))
        conn.execute("COMMIT")
        if versioned:
            conn.data_version = current + 1
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
