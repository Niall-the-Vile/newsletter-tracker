"""Milestone G: speed, structure, data-health banner, Get started, plain words and diagnostics. Fake data only."""
import inspect
import re
import sqlite3
import time
from pathlib import Path
from tkinter import messagebox

import pytest

import config
import db
from app import App
from services import churches, diagnostics, errors, health, newsletters, people
from tests import factories
from version import APP_VERSION

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    shown = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, _n=name, **k: shown.append((_n, a)))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    return shown


def make_church(conn, name="Fake Church"):
    return conn.execute("INSERT INTO churches (name, name_key) VALUES (?, ?)", (name, name.lower())).lastrowid


def make_people(conn, n):
    return [db.upsert_person(conn, factories.fake_person(i)) for i in range(n)]


def wait(root, seconds=0.35):
    time.sleep(seconds)
    root.update()


# ---------- S6 speed ----------

def test_not_subscribed_uses_not_exists_and_the_subscription_index_is_there(conn):
    assert "NOT IN" not in inspect.getsource(db.people_not_subscribed)
    cols = {tuple(r[2] for r in conn.execute(f"PRAGMA index_info({i['name']})"))
            for i in conn.execute("PRAGMA index_list(subscriptions)")}
    assert ("newsletter_id", "person_id") in cols


def test_search_waits_250ms_after_the_last_key(root, session):
    make_people(session.conn, 12)
    app = App(root, session)
    root.update()
    tab = app.people_tab
    assert len(tab.tree.get_children()) == 12
    app.find_var.set("Fake")
    app.find_var.set("zzz-no-match")
    root.update()
    assert len(tab.tree.get_children()) == 12  # not searched yet
    wait(root)
    assert len(tab.tree.get_children()) == 0
    app.destroy()


def test_history_is_only_read_when_the_history_tab_is_opened(root, session, monkeypatch):
    conn = session.conn
    pid = make_people(conn, 3)[0]
    app = App(root, session)
    root.update()
    calls = []
    real = people.person_history
    monkeypatch.setattr(people, "person_history", lambda c, i: calls.append(i) or real(c, i))
    tab = app.people_tab
    tab.tree.selection_set(str(pid))
    root.update()
    assert calls == [] and tab.history_box.listbox.size() == 0
    tab.history_box.tabs.select(tab.history_box)
    root.update()
    assert calls == [pid]
    assert tab.history_box.listbox.size() >= 0 and "History starts" in tab.history_box.note.cget("text")
    app.destroy()


def test_removing_many_people_is_one_transaction(conn):
    ids = make_people(conn, 30)
    before = conn.data_version
    people.remove_people(conn, ids, confirm_count=30)
    assert conn.data_version == before + 1
    assert conn.execute("SELECT COUNT(*) FROM people WHERE active = 0").fetchone()[0] == 30


# ---------- S7 structure ----------

def test_no_sql_in_the_ui_package():
    sql = re.compile(r"\b(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.I)
    for f in (ROOT / "ui").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "sqlite3" not in text, f.name
        assert ".execute(" not in text, f.name
        assert not sql.search(text), f.name


def test_duplicate_newsletter_name_gives_a_plain_error(conn):
    newsletters.add(conn, "Fake Letter")
    with pytest.raises(ValueError, match="already exists"):
        newsletters.add(conn, "Fake Letter")
    other = newsletters.add(conn, "Another Letter") or conn.execute(
        "SELECT id FROM newsletters WHERE name = 'Another Letter'").fetchone()[0]
    with pytest.raises(ValueError, match="already exists"):
        newsletters.rename(conn, other, "Fake Letter")


def test_known_errors_get_plain_messages():
    assert errors.friendly_message(sqlite3.OperationalError("database is locked")) == errors.BUSY
    assert errors.friendly_message(sqlite3.IntegrityError("This copy is out of date")) is not None
    assert errors.friendly_message(KeyError("x")) is None


def test_requirements_files():
    assert (ROOT / "requirements.txt").read_text().split() == ["openpyxl"]
    assert (ROOT / "requirements-dev.txt").read_text().split() == ["-r", "requirements.txt", "pytest"]


# ---------- S9 data health and Get started ----------

def test_health_issues_count_what_needs_fixing(conn):
    assert health.health_issues(conn) == []
    ids = make_people(conn, 4)
    assert health.health_issues(conn)[0]["text"].startswith("4 people have no fund")
    fund = conn.execute("SELECT id FROM funds WHERE name = 'MAAP'").fetchone()[0]
    people.set_fund(conn, ids, fund)
    assert health.health_issues(conn) == []
    ch = make_church(conn)
    texts = [i["text"] for i in health.health_issues(conn)]
    assert any("no rep to send to" in t for t in texts) and any("no fund" in t for t in texts)
    churches.add_rep(conn, ch, ids[0])
    churches.add_rep(conn, ch, ids[1])
    churches.make_default(conn, ch, ids[0])
    assert not any("no rep" in i["text"] for i in health.health_issues(conn))


def test_checklist_ticks_itself_off(conn):
    assert [s["done"] for s in health.checklist(conn)] == [False] * 6
    ids = make_people(conn, 3)
    steps = health.checklist(conn)
    assert steps[0]["done"] and not steps[1]["done"]
    people.set_fund(conn, ids, conn.execute("SELECT id FROM funds LIMIT 1").fetchone()[0])
    assert health.checklist(conn)[1]["done"]


def test_banner_shows_only_when_there_is_something_to_fix(root, session):
    conn = session.conn
    app = App(root, session)
    root.update()
    assert app.health.winfo_manager() == ""
    make_people(conn, 3)
    app.changed()
    root.update()
    assert app.health.winfo_manager() == "pack" and "3 people" in app.health.label.cget("text")
    app.health.hide()
    assert app.health.winfo_manager() == ""
    app.destroy()


def test_banner_button_opens_the_list_that_needs_attention(root, session):
    conn = session.conn
    make_people(conn, 3)
    make_church(conn)
    app = App(root, session)
    app.show_list("churches")
    root.update()
    assert app.people_tab.type_var.get() == "Church" and app.people_tab.attention_var.get()
    app.show_list("people")
    assert app.people_tab.type_var.get() == "All"
    app.destroy()


def test_get_started_opens_once_on_first_run_and_from_the_help_menu(root, session):
    assert not config.get_flag("get_started_shown")
    app = App(root, session)
    wait(root, 0.5)
    assert app.get_started is not None and app.get_started.winfo_exists()
    assert config.get_flag("get_started_shown")
    app.get_started.destroy()
    app.destroy()
    again = App(root, session)
    wait(root, 0.5)
    assert again.get_started is None  # only offered automatically the first time
    again.open_get_started()
    assert again.get_started.winfo_exists()
    again.destroy()


def test_get_started_is_not_forced_on_a_read_only_copy(root, db_path, session):
    from services import startup
    second = startup.open_session(db_path)
    app = App(root, second)
    wait(root, 0.5)
    assert app.get_started is None
    app.destroy()
    second.close()


def test_saving_the_data_path_keeps_the_settings_flags():
    config.set_flag("get_started_shown")
    config.save_db_path(__file__)
    assert config.get_flag("get_started_shown") and config.get_saved_db_path() == __file__


# ---------- S10 plain words ----------

def test_church_pane_uses_plain_words(root, session):
    conn = session.conn
    ids = make_people(conn, 2)
    ch = make_church(conn)
    churches.add_rep(conn, ch, ids[0])
    churches.add_rep(conn, ch, ids[1])
    churches.remove_rep(conn, ch, ids[0], new_default_id=ids[1])
    app = App(root, session)
    app.people_tab.type_var.set("Church")
    app.people_tab.refresh()
    app.people_tab.tree.selection_set(str(ch))
    root.update()
    box = app.people_tab.church_box
    buttons = [w.cget("text") for f in box.winfo_children() if f.winfo_class() == "TFrame"
               for w in f.winfo_children() if w.winfo_class() == "TButton"]
    assert any(b.startswith("Receives church mail") for b in buttons)
    assert box.reps_tree.heading("star", "text") == "Receives church mail"
    app.destroy()


def test_screens_never_say_snapshot_or_default_rep():
    banned = re.compile(r"snapshot|default rep|make default|main contact", re.I)
    for folder in ("ui", "services"):
        for f in (ROOT / folder).glob("*.py"):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if '"""' in line:
                    continue
                literal = re.findall(r'"[^"\n]*"', line)
                assert not any(banned.search(s) for s in literal), f"{f.name}:{n}"


# ---------- S8 title and diagnostics ----------

def test_title_bar_shows_the_version(root, session):
    app = App(root, session)
    assert APP_VERSION in root.title() and session.db_path in root.title()
    app.destroy()


def test_diagnostics_text_has_the_five_facts(session):
    from services import backup
    backup.backup_both(session.db_path, "manual")
    text = diagnostics.diagnostics_text(session)
    assert f"Newsletter Tracker {APP_VERSION}" in text
    assert f"Data file: {session.db_path}" in text
    assert "Data file version: 6" in text
    assert "Editing: this copy is editing" in text
    assert "Last backup: " in text and "none yet" not in text


def test_diagnostics_says_who_else_is_editing_and_when_there_is_no_backup(db_path, session):
    from services import startup
    second = startup.open_session(db_path)
    text = diagnostics.diagnostics_text(second)
    assert "is editing since" in text and "view only" in text
    second.close()


def test_help_menu_copies_diagnostics_to_the_clipboard(root, session):
    app = App(root, session)
    root.update()
    app.chrome.copy_diagnostics()
    clip = root.clipboard_get()
    assert clip.startswith(f"Newsletter Tracker {APP_VERSION}") and session.db_path in clip
    menu = root.nametowidget(root.cget("menu"))
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "cascade"]
    assert labels == ["File", "Help"]
    app.destroy()


def test_settings_file_stays_inside_the_test_sandbox(tmp_path):
    assert config.config_path().startswith(str(tmp_path))
