from tkinter import messagebox

import pytest

import db
from app import App
from services import mailings, subscriptions
from tests.test_mailings import Scene, read_csv, run_count
from ui import mailings_tab


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    shown = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, _n=name, **k: shown.append((_n, a)))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    return shown


def test_create_mailing_flow_saves_the_file_then_records_the_run(root, session, tmp_path, quiet_dialogs):
    scene = Scene(session.conn)
    app = App(root, session)
    tab = app.mailings_tab
    dlg = mailings_tab.CreateMailingDialog(tab, session.conn, tab._done)
    dlg.news_var.set("MAAP Newsletter")
    dlg.label_var.set("Fall 2026")
    dlg.to_summary()
    root.update()
    assert dlg.current == 1 and "2 addresses, 41 copies" in dlg.summary.cget("text")
    assert str(dlg.next2.cget("state")) != "disabled"
    path = str(tmp_path / "out.csv")
    dlg.save_to(path)
    assert quiet_dialogs[-1][0] == "showinfo" and run_count(session.conn) == 1
    assert len(read_csv(path)) == 3
    assert len(tab.tree.get_children()) == 1

    run_dlg = mailings_tab.RunDialog(tab, session.conn, int(tab.tree.get_children()[0]))
    root.update()
    assert len(run_dlg.tree.get_children()) == 2 and "Issue total: 2 bundles, 41 copies" in run_dlg.sheet_text.get(
        "1.0", "end")
    run_dlg.destroy()
    assert scene.news
    app.destroy()


def test_a_failed_save_in_the_window_records_nothing(root, session, tmp_path, quiet_dialogs):
    Scene(session.conn)
    app = App(root, session)
    dlg = mailings_tab.CreateMailingDialog(app.mailings_tab, session.conn, app.mailings_tab._done)
    dlg.news_var.set("MAAP Newsletter")
    dlg.label_var.set("Fall 2026")
    dlg.save_to(str(tmp_path / "no such folder" / "out.csv"))
    assert quiet_dialogs[-1][0] == "showerror" and run_count(session.conn) == 0
    dlg.destroy()
    app.destroy()


def test_find_box_shows_the_quick_answer_and_detail_panes_manage_subscriptions(root, session):
    conn = session.conn
    scene = Scene(conn)
    app = App(root, session)
    app.find_var.set("Alpha Church")
    root.update()
    assert app.card.cget("text") == "Alpha Church: 40 copies, not mailed yet"
    app.find_var.set("")

    app.people_tab.type_var.set("Church")
    app.people_tab.refresh()
    app.people_tab.tree.selection_set(str(scene.church_a))
    root.update()
    box = app.people_tab.church_box.subs_box
    assert [box.tree.item(i, "values")[:3] for i in box.tree.get_children()] == [("MAAP Newsletter", "40", "Mail")]
    subscriptions.set_subscription(conn, "church", scene.church_a, scene.health, 3, "Both")
    app.people_tab.refresh_list_only()
    root.update()
    assert len(box.tree.get_children()) == 2

    app.people_tab.type_var.set("All")
    app.people_tab.refresh()
    app.people_tab.tree.selection_set(str(scene.p_maap))
    root.update()
    assert app.people_tab.subs_box.entity_id == scene.p_maap
    app.people_tab.toggle_returned()
    root.update()
    assert db.get_person(conn, scene.p_maap)["returned_mail"] == 1
    assert "Mail was returned" in app.people_tab.d_info.cget("text")
    app.destroy()


def test_newsletters_panel_sets_fund_and_archives(root, session, monkeypatch):
    conn = session.conn
    scene = Scene(conn)
    app = App(root, session)
    panel = app.setup_tab.newsletters_panel
    panel.refresh()
    assert panel.tree.item(str(scene.news), "values")[1] == "MAAP"
    panel.tree.selection_set(str(scene.news))
    panel.toggle_archive()
    assert panel.tree.item(str(scene.news), "values")[4] == "Archived"
    app.destroy()
