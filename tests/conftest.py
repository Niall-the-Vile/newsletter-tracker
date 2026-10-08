import hashlib
import os
import sys

import tkinter as tk

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import startup  # noqa: E402
from tests import factories  # noqa: E402


def _real_settings_file():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "NewsletterTracker", "config.json")


REAL_SETTINGS = _real_settings_file()  # read once at import, before any test redirects the profile


def _fingerprint(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


@pytest.fixture(scope="session", autouse=True)
def _real_profile_untouched():
    before = _fingerprint(REAL_SETTINGS)
    yield
    assert _fingerprint(REAL_SETTINGS) == before, "a test changed the real per-user settings file"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Every per-user location points inside the test's temp folder."""
    home = tmp_path / "home"
    home.mkdir()
    for name, value in (("LOCALAPPDATA", tmp_path / "local"), ("APPDATA", tmp_path / "roaming"),
                        ("HOME", home), ("USERPROFILE", home)):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)


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

