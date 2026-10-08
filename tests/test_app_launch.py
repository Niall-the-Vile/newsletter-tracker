import tkinter as tk

import pytest

import db
from app import App


@pytest.fixture
def root():
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


def test_main_window_builds_on_a_fake_database(root, session):
    db.upsert_person(session.conn, {"first_name": "Fake", "last_name": "Person"})
    app = App(root, session)
    root.update()
    assert app.chrome.banner.winfo_manager() == ""  # no banner for the editing copy
    root.destroy()


def test_read_only_window_shows_banner_and_blocks_restore(root, db_path, session):
    from services import startup
    second = startup.open_session(db_path)
    app = App(root, second)
    root.update()
    assert app.chrome.banner.winfo_manager() == "pack"
    assert "is editing since" in app.chrome.banner_label.cget("text")
    assert app.chrome.file_menu.entrycget("Restore from backup...", "state") == "disabled"
    root.destroy()
    second.close()
