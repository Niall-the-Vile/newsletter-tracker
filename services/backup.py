"""Verified backups made with sqlite3.backup(), plus restore."""
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import config
from data import activity
from data.connection import connect
from data.migrations import LEGACY_TABLES, check_not_newer

KEEP = 30
NEVER_ROTATED = ("before-update", "before-restore")


class BackupError(Exception):
    pass


def open_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)


def table_counts(conn):
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def legacy_counts(counts):
    return tuple(counts.get(t) for t in LEGACY_TABLES)


def beside_dir(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def _prefix(db_path):
    return Path(db_path).stem + "-backup-"


def make_backup(db_path, dest_dir, reason):
    """One verified copy. Raises BackupError (and leaves no file) if anything differs."""
    os.makedirs(dest_dir, exist_ok=True)
    t = datetime.now()
    stamp = t.strftime("%Y%m%d-%H%M%S") + f"{t.microsecond // 1000:03d}"
    final = os.path.join(dest_dir, f"{_prefix(db_path)}{stamp}-{reason}.db")
    tmp = final + ".tmp"
    src = dst = None
    try:
        src = open_readonly(db_path)
        src.execute("BEGIN")  # hold one snapshot so the counts match the copy
        before = table_counts(src)
        version = src.execute("PRAGMA user_version").fetchone()[0]
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        src.execute("COMMIT")
        if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("The backup copy failed its integrity check.")
        if table_counts(dst) != before or dst.execute("PRAGMA user_version").fetchone()[0] != version:
            raise BackupError("The backup copy does not match the original, so it was discarded.")
        dst.close()
        dst = None
        os.replace(tmp, final)
    except BackupError:
        raise
    except (sqlite3.Error, OSError) as e:
        raise BackupError(f"Could not make a backup: {e}") from e
    finally:
        for c in (src, dst):
            if c is not None:
                c.close()
        if os.path.exists(tmp):
            os.remove(tmp)
    _rotate(dest_dir, _prefix(db_path))
    return {"path": final, "counts": before}


def _rotate(dest_dir, prefix):
    files = sorted(f for f in os.listdir(dest_dir) if f.startswith(prefix) and f.endswith(".db")
                   and not f.endswith(tuple(f"-{r}.db" for r in NEVER_ROTATED)))
    for old in files[:-KEEP]:
        try:
            os.remove(os.path.join(dest_dir, old))
        except OSError:
            pass


def backup_both(db_path, reason):
    """Beside the data file and in %LOCALAPPDATA%. Both must succeed."""
    return [make_backup(db_path, beside_dir(db_path), reason),
            make_backup(db_path, config.local_backup_dir(), reason)]


def backed_up_today(db_path):
    folder, prefix = config.local_backup_dir(), _prefix(db_path)
    today = datetime.now().strftime("%Y%m%d")
    return os.path.isdir(folder) and any(
        f.startswith(prefix + today) and f.endswith(".db") for f in os.listdir(folder))


def ensure_daily_backup(db_path):
    if backed_up_today(db_path):
        return None
    return backup_both(db_path, "daily")


def list_backups(db_path):
    """Newest first, with row counts for the three main tables (None if unreadable)."""
    out = []
    for label, folder in (("Next to the data file", beside_dir(db_path)), ("On this PC", config.local_backup_dir())):
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            m = re.fullmatch(re.escape(_prefix(db_path)) + r"(\d{8})-(\d{6})\d{3}-(.+)\.db", f)
            if not m:
                continue
            path = os.path.join(folder, f)
            when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            try:
                c = open_readonly(path)
                try:
                    counts = legacy_counts(table_counts(c))
                finally:
                    c.close()
            except sqlite3.Error:
                counts = None
            out.append({"path": path, "where": label, "when": when, "reason": m.group(3), "counts": counts})
    return sorted(out, key=lambda e: e["when"], reverse=True)


def restore_backup(db_path, backup_path):
    """Safety backup first, then copy the chosen backup over the data file. Nobody else may be editing."""
    try:
        src = open_readonly(backup_path)
    except sqlite3.Error as e:
        raise BackupError(f"Could not open that backup: {e}") from e
    try:
        if src.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("That backup is damaged, so it was not restored.")
        check_not_newer(src.execute("PRAGMA user_version").fetchone()[0])
        expected = table_counts(src)
        backup_both(db_path, "before-restore")
        dst = sqlite3.connect(str(db_path), timeout=8.0, isolation_level=None)
        try:
            src.backup(dst)
        finally:
            dst.close()
    except sqlite3.Error as e:
        raise BackupError(f"Could not restore: {e}") from e
    finally:
        src.close()
    check = open_readonly(db_path)
    try:
        if table_counts(check) != expected:
            raise BackupError("The restored file does not match the backup. Your safety backup is untouched.")
    finally:
        check.close()
    if expected.get("activity_log") is not None:
        conn = connect(db_path)
        try:
            activity.log(conn, "restore", "Restored the data file from a backup")
        finally:
            conn.close()
    return legacy_counts(expected)

