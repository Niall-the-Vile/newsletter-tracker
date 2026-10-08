import os
import sqlite3

import pytest

from services import backup, startup


def test_manual_backup_makes_two_verified_copies(session, db_path):
    results = backup.backup_both(db_path, "manual")
    assert len(results) == 2
    for r in results:
        assert os.path.exists(r["path"])
        c = sqlite3.connect(r["path"])
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        c.close()
    assert results[0]["counts"] == results[1]["counts"]


def test_first_open_each_day_backs_up_once(db_path):
    startup.open_session(db_path).close()
    n = len(backup.list_backups(db_path))
    assert n == 2  # the fresh file's daily backup, in both places
    startup.open_session(db_path).close()
    assert len(backup.list_backups(db_path)) == n


def test_only_the_newest_30_are_kept(session, db_path):
    for _ in range(33):
        backup.make_backup(db_path, backup.beside_dir(db_path), "manual")
    names = [f for f in os.listdir(backup.beside_dir(db_path)) if f.endswith(".db")]
    assert len(names) == backup.KEEP


def test_backup_never_leaves_temp_files(session, db_path):
    backup.backup_both(db_path, "manual")
    for folder in (backup.beside_dir(db_path), os.environ["LOCALAPPDATA"]):
        for _root, _dirs, files in os.walk(folder):
            assert not [f for f in files if f.endswith(".tmp")]


def test_read_only_copy_never_runs_automatic_backup(db_path):
    first = startup.open_session(db_path)
    for f in os.listdir(backup.beside_dir(db_path)):
        os.remove(os.path.join(backup.beside_dir(db_path), f))
    for f in os.listdir(backup.config.local_backup_dir()):
        os.remove(os.path.join(backup.config.local_backup_dir(), f))
    second = startup.open_session(db_path)
    assert second.read_only and backup.list_backups(db_path) == []
    second.close()
    first.close()


def test_unreadable_backup_is_listed_without_counts(session, db_path):
    folder = backup.beside_dir(db_path)
    os.makedirs(folder, exist_ok=True)
    bad = os.path.join(folder, "newsletter_tracker-backup-20260101-010101000-manual.db")
    open(bad, "wb").write(b"not a database")
    entry = next(b for b in backup.list_backups(db_path) if b["path"] == bad)
    assert entry["counts"] is None
    with pytest.raises(backup.BackupError):
        backup.restore_backup(db_path, bad)
