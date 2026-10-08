"""Builds every screen and dialog on fake data and runs update(), so a crash on open is caught."""
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import pytest

import db
from app import App
from services import bounces, mailings, startup
from tests.test_mailings import Scene, make_run
from ui import bounces_panel, church_detail, chrome, health, mailings_tab, people_tab, person_dialog, subscriptions_box


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: None)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda *a, **k: "")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda *a, **k: "")


def test_every_dialog_opens_on_fake_data(root, session, tmp_path):
    conn = session.conn
    scene = Scene(conn)
    run_id, _ = make_run(scene, tmp_path)
    run_id = run_id["id"] if isinstance(run_id, dict) else run_id
    app = App(root, session)
    root.update()
    made = [
        person_dialog.PersonDialog(app, conn),
        person_dialog.PersonDialog(app, conn, db.get_person(conn, scene.p_maap)),
        subscriptions_box.SubscriptionDialog(app, conn),
        people_tab.SetFundDialog(app, conn, 3),
        church_detail.ChooseDialog(app, "Pick", "Pick one", lambda s: [(1, "One")]),
        bounces_panel.PasteDialog(app),
        chrome.RestoreDialog(app, session),
        mailings_tab.SkippedDialog(app, [{"name": "A", "kind": "church", "reason": "No contact", "copies": 2}]),
        mailings_tab.RunDialog(app, conn, run_id),
        health.GetStartedDialog(app, conn, lambda where: None),
    ]
    root.update()
    for w in made:
        w.destroy()
    app.destroy()


def test_every_tab_and_setup_panel_can_be_selected(root, session):
    Scene(session.conn)
    app = App(root, session)
    for tab in (app.people_tab, app.mailings_tab, app.setup_tab):
        app.notebook.select(tab)
        root.update()
    for i in range(len(app.setup_tab.tabs())):
        app.setup_tab.select(i)
        root.update()
    for kind in ("churches", "people"):
        app.show_list(kind)
        root.update()
    for where in ("Churches", "People", "Mailings", "Import", "Other"):
        app.go_to(where)
        root.update()
    app.destroy()


def test_buttons_with_nothing_chosen_do_not_crash(root, session):
    Scene(session.conn)
    app = App(root, session)
    root.update()
    pt = app.people_tab
    for name in dir(pt):
        if name in ("remove", "restore", "set_fund", "edit_person", "toggle_returned", "toggle_bounce"):
            getattr(pt, name)()
            root.update()
    app.mailings_tab.open_run()
    app.chrome.back_up_now()
    app.chrome.copy_diagnostics()
    root.update()
    app.destroy()


def test_read_only_copy_builds_every_screen(root, db_path, session):
    second = startup.open_session(db_path)
    app = App(root, second)
    for tab in (app.people_tab, app.mailings_tab, app.setup_tab):
        app.notebook.select(tab)
        root.update()
    app.mailings_tab.create()
    root.update()
    app.destroy()
    second.close()
