"""The text behind Help > Copy diagnostics. It holds no names or addresses of people on the list."""
import getpass
import platform

from data import migrations
from services import backup
from version import APP_VERSION


def _last_backup(db_path):
    found = backup.list_backups(db_path)
    if not found:
        return "none yet"
    e = found[0]
    return f"{e['when']:%d %b %Y %H:%M} ({e['where'].lower()}, {e['reason'].replace('-', ' ')})"


def _lock_text(session):
    if session.lock.status == "owner":
        return "this copy is editing"
    return session.lock.holder_text()


def diagnostics_text(session):
    conn = session.conn
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("people", "churches", "mailing_runs")}
    lines = [
        f"Newsletter Tracker {APP_VERSION}",
        f"Data file: {session.db_path}",
        f"Data file version: {migrations.get_version(conn)} (this program uses {migrations.SCHEMA_VERSION})",
        f"Editing: {_lock_text(session)}",
        f"This copy: {'view only' if session.read_only else 'can edit'}",
        f"Last backup: {_last_backup(session.db_path)}",
        f"People: {counts['people']}, churches: {counts['churches']}, mailings: {counts['mailing_runs']}",
        f"Windows user: {getpass.getuser()}",
        f"Python {platform.python_version()} on {platform.system()} {platform.release()}",
    ]
    return "\n".join(lines)
