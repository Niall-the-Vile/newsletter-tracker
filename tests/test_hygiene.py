import os
import shutil
import subprocess

import pytest

from services import hygiene, startup

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_SUFFIXES = (".db", ".db-journal", ".db-wal", ".csv", ".xlsx", ".pdf", ".lock")


def test_forged_conflict_copy_triggers_warning(db_path):
    folder = os.path.dirname(db_path)
    open(os.path.join(folder, "newsletter_tracker-FAKE-PC.db"), "wb").close()
    assert hygiene.conflict_copies(db_path) == ["newsletter_tracker-FAKE-PC.db"]
    s = startup.open_session(db_path)
    try:
        assert any("extra copy" in w for w in s.warnings)
    finally:
        s.close()


def test_backups_are_not_mistaken_for_conflict_copies(session, db_path):
    from services import backup
    backup.backup_both(db_path, "manual")
    assert hygiene.conflict_copies(db_path) == []


def test_no_warning_for_a_clean_folder(session):
    assert session.warnings == []


def test_data_file_inside_a_git_tree_warns(tmp_path):
    repo = tmp_path / "code"
    (repo / ".git").mkdir(parents=True)
    path = str(repo / "data" / "newsletter_tracker.db")
    os.makedirs(os.path.dirname(path))
    s = startup.open_session(path)
    try:
        assert hygiene.git_root_of(path) == str(repo.resolve())
        assert any("code folder" in w for w in s.warnings)
    finally:
        s.close()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_tracks_no_data_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    tracked = out.split("\n")
    bad = [f for f in tracked if f.lower().endswith(DATA_SUFFIXES) or f.lower().endswith("scope.md")
           or f.startswith("backups/")]
    assert bad == []


def test_gitignore_covers_data_and_secrets():
    text = open(os.path.join(REPO, ".gitignore")).read().split()
    for pattern in ("*.db", "*.db-journal", "backups/", "*.csv", "*.xlsx", "*.pdf", "config*.json",
                    "msal_cache*", ".env", "SCOPE.md"):
        assert pattern in text
