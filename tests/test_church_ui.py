from tkinter import messagebox

import pytest

import db
from app import App
from services import churches, grouping
from tests import factories


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    shown = []
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda *a, _n=name, **k: shown.append((_n, a)))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    return shown


def test_import_tab_shows_the_dry_run_then_imports(root, session, tmp_path, quiet_dialogs):
    path = tmp_path / "people.csv"
    factories.write_people_csv(path, 50)
    app = App(root, session)
    tab = app.import_tab
    tab.load_path(str(path))
    root.update()
    report = tab.report.get("1.0", "end")
    assert "New people: 50" in report and "Spare Column" in report
    assert session.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
    assert tab.assign_var.get() is False and tab.newsletter_var.get() == ""
    tab.assign_var.set(True)
    tab.do_import()  # ticked with no newsletter chosen: refused
    assert session.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0
    assert quiet_dialogs[-1][0] == "showwarning"
    tab.assign_var.set(False)
    tab.do_import()
    assert session.conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 50
    app.destroy()


def test_grouping_panel_checks_then_creates_and_undoes(root, session, tmp_path):
    from services import importing
    import importer
    path = tmp_path / "people.csv"
    factories.write_people_csv(path, 100)
    headers, rows = importer.load_file(str(path))
    importing.apply_import(session.conn, headers, rows, importer.build_mapping(headers), str(path))
    app = App(root, session)
    panel = app.setup_tab.grouping_panel
    panel.check()
    root.update()
    assert "20 churches would be created" in panel.summary.cget("text")
    assert len(panel.conflict_tree.get_children()) == 2
    assert len(panel.near_tree.get_children()) == 1
    assert session.conn.execute("SELECT COUNT(*) FROM churches").fetchone()[0] == 0
    panel.near_tree.selection_set(panel.near_tree.get_children()[0])
    panel.merge(keep_first=True)
    assert "19 churches would be created" in panel.summary.cget("text")
    panel.confirm()
    assert session.conn.execute("SELECT COUNT(*) FROM churches").fetchone()[0] == 19
    assert session.conn.execute("SELECT COUNT(*) FROM church_aliases").fetchone()[0] == 1
    panel.run_tree.selection_set(panel.run_tree.get_children()[0])
    panel.undo()
    assert session.conn.execute("SELECT COUNT(*) FROM churches").fetchone()[0] == 0
    app.destroy()


def test_church_list_and_detail_with_make_default_star(root, session):
    conn = session.conn
    a = db.upsert_person(conn, {"first_name": "Ann", "last_name": "Alder", "church_name": "Fake Church",
                                "conference": "Conf", "title": "Pastor", "active": 1})
    b = db.upsert_person(conn, {"first_name": "Bob", "last_name": "Birch", "church_name": "Fake Church",
                                "conference": "Conf", "title": "Deacon", "active": 1})
    grouping.apply_grouping(conn)
    app = App(root, session)
    tab = app.people_tab
    tab.type_var.set("Church")
    tab.refresh()
    root.update()
    (iid,) = tab.tree.get_children()
    assert tab.tree.item(iid, "values")[3] == churches.NEEDS_DEFAULT
    tab.attention_var.set(True)
    tab.refresh()
    assert tab.tree.get_children() == (iid,)
    tab.attention_var.set(False)
    tab.refresh()
    tab.tree.selection_set(iid)
    root.update()
    detail = tab.church_box
    assert len(detail.reps_tree.get_children()) == 2
    detail.reps_tree.selection_set(str(b))
    detail.make_default()
    root.update()
    values = {i: detail.reps_tree.item(i, "values")[0] for i in detail.reps_tree.get_children()}
    assert values[str(b)] == "★" and values[str(a)] == ""
    assert "Bob Birch" in detail.d_contact.cget("text")
    tab.attention_var.set(True)
    tab.refresh()
    assert tab.tree.get_children() == ()
    tab.type_var.set("All")
    tab.refresh()
    assert tab.person_box.winfo_manager() == "pack" and tab.church_box.winfo_manager() == ""
    app.destroy()
