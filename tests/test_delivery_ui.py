from tkinter import messagebox

import pytest

from app import App
from services import bounces, mailings, subscriptions
from tests.test_mailings import Scene, read_csv, run_count
from ui import mailings_tab


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    shown = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, _n=name, **k: shown.append((_n, a)))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    return shown


def test_both_is_marked_on_person_and_church_rows_and_the_gets_text_names_the_delivery(root, session):
    scene = Scene(session.conn)
    conn = session.conn
    subscriptions.set_subscription(conn, "person", scene.p_maap, scene.news, 1, "Both")
    subscriptions.set_subscription(conn, "person", scene.p_none, scene.news, 1, "Email")
    subscriptions.set_subscription(conn, "church", scene.church_a, scene.news, 40, "Both")
    app = App(root, session)
    tab = app.people_tab
    root.update()
    both = tab.tree.item(str(scene.p_maap))
    assert both["values"][6] == "BOTH" and "both" in both["tags"] and "Both" in both["values"][5]
    assert "Email" in tab.tree.item(str(scene.p_none))["values"][5] and tab.tree.item(str(scene.p_none))["values"][6] == ""
    assert tab.tree.item(str(scene.p_amca))["values"][6] == ""
    tab.type_var.set("Church")
    tab.refresh()
    assert tab.tree.item(str(scene.church_a))["values"][6] == "BOTH"
    assert tab.tree.item(str(scene.church_b))["values"][6] == ""
    app.destroy()


def test_delivery_chips_have_text_and_colour_in_the_detail_pane(root, session):
    scene = Scene(session.conn)
    subscriptions.set_subscription(session.conn, "person", scene.p_maap, scene.news, 1, "Email")
    app = App(root, session)
    root.update()
    app.people_tab.tree.selection_set(str(scene.p_maap))
    root.update()
    box = app.people_tab.subs_box
    item = box.tree.item(box.tree.get_children()[0])
    assert item["values"][2] == "Email" and "Email" in item["tags"]
    assert box.tree.tag_configure("Email", "background")
    app.destroy()


def test_mark_bounced_button_and_the_bounces_list_under_setup(root, session):
    scene = Scene(session.conn)
    conn = session.conn
    app = App(root, session)
    tab = app.people_tab
    root.update()
    tab.tree.selection_set(str(scene.p_maap))
    root.update()
    assert tab.bounce_btn.cget("text") == "Mark email bounced"
    tab.toggle_bounce()
    root.update()
    assert bounces.list_bounces(conn)[0]["holders"] == 1
    assert tab.bounce_btn.cget("text") == "Clear email bounce" and "bounced" in tab.d_info.cget("text")
    panel = app.setup_tab.bounces_panel
    panel.refresh()
    assert len(panel.tree.get_children()) == 1
    assert panel.tree.item(panel.tree.get_children()[0])["values"][3] == "Left out"
    tab.toggle_bounce()
    root.update()
    assert tab.bounce_btn.cget("text") == "Mark email bounced"
    app.destroy()


def test_create_email_list_in_the_window_saves_the_list_and_the_left_out_file(root, session, tmp_path, quiet_dialogs):
    scene = Scene(session.conn)
    subscriptions.set_subscription(session.conn, "person", scene.p_maap, scene.news, 1, "Both")
    subscriptions.set_subscription(session.conn, "person", scene.p_none, scene.news, 1, "Email")
    app = App(root, session)
    tab = app.mailings_tab
    dlg = mailings_tab.CreateMailingDialog(tab, session.conn, tab._done)
    dlg.news_var.set("MAAP Newsletter")
    dlg.label_var.set("Fall 2026")
    dlg.channel_var.set(mailings_tab.EMAIL_LIST)
    dlg.to_summary()
    root.update()
    assert "1 email addresses" in dlg.summary.cget("text")
    path = str(tmp_path / "list.csv")
    dlg.save_to(path)
    assert quiet_dialogs[-1][0] == "showinfo" and run_count(session.conn) == 1
    assert len(read_csv(path)) == 2
    left_out = read_csv(str(tmp_path / "list left out.csv"))
    assert any(r[2] == mailings.NO_FUND for r in left_out[1:])
    assert tab.tree.item(tab.tree.get_children()[0])["values"][3] == "Email"
    run_dlg = mailings_tab.RunDialog(tab, session.conn, int(tab.tree.get_children()[0]))
    root.update()
    assert len(run_dlg.tree.get_children()) == 1
    run_dlg.destroy()
    app.destroy()
