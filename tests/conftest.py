import os
import sys

import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import startup  # noqa: E402
from tests import factories  # noqa: E402


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Keeps backups and settings inside the test's temp folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))


@pytest.fixture(scope="session")
def _tk_root():
    # One Tk for the whole run: creating a second one in the same process can fail on this setup.
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def root(_tk_root):
    yield _tk_root
    for w in _tk_root.winfo_children():
        w.destroy()
    _tk_root.config(menu=tk.Menu(_tk_root))


@pytest.fixture
def db_path(tmp_path):
    folder = tmp_path / "shared"
    folder.mkdir()
    return str(folder / "newsletter_tracker.db")


@pytest.fixture
def legacy_db(db_path):
    counts = factories.make_legacy_db(db_path)
    return db_path, counts


@pytest.fixture
def session(db_path):
    s = startup.open_session(db_path)
    yield s
    s.close()


@pytest.fixture
def conn(session):
    return session.conn


@pytest.fixture
def raw(db_path):
    """A plain connection like an old program copy would use."""
    import sqlite3
    c = sqlite3.connect(db_path)
    yield c
    c.close()

