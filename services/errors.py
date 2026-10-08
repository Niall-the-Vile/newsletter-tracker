"""Turns known errors into the plain message shown to staff. Returns None for anything unexpected."""
import sqlite3

from data.connection import ConflictError, ReadOnlyError
from data.migrations import NewerVersionError
from services import backup

BUSY = "Someone else is saving right now. Wait a few seconds and try again."


def friendly_message(exc):
    if isinstance(exc, (ReadOnlyError, ConflictError, NewerVersionError, backup.BackupError)):
        return str(exc)
    if isinstance(exc, sqlite3.IntegrityError) and "out of date" in str(exc):
        return str(exc)
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc):
        return BUSY
    return None
