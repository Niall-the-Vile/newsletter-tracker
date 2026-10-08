import db
from app import App
from tests import factories


def test_main_window_builds_on_a_fake_database(root, session):
    db.upsert_person(session.conn, {"first_name": "Fake", "last_name": "Person"})
    app = App(root, session)
    root.update()
    assert app.chrome.banner.winfo_manager() == ""  # no banner for the editing copy
    app.destroy()


def test_read_only_window_shows_banner_and_blocks_restore(root, db_path, session):
    from services import startup
    second = startup.open_session(db_path)
    app = App(root, second)
    root.update()
    assert app.chrome.banner.winfo_manager() == "pack"
    assert "is editing since" in app.chrome.banner_label.cget("text")
    assert app.chrome.file_menu.entrycget("Restore from backup...", "state") == "disabled"
    app.destroy()
    second.close()


def test_window_has_three_tabs_and_setup_panels(root, session):
    app = App(root, session)
    root.update()
    assert [app.notebook.tab(i, "text") for i in range(3)] == ["People & Churches", "Mailings", "Setup"]
    assert [app.setup_tab.tab(i, "text") for i in range(6)] == [
        "Newsletters", "Funds", "Old subscriptions", "Church grouping", "Import", "Activity"]
    app.destroy()


def test_people_list_filters_set_fund_and_remove_in_the_window(root, session):
    from services import people
    conn = session.conn
    ids = [db.upsert_person(conn, factories.fake_person(i)) for i in range(14)]
    app = App(root, session)
    tab = app.people_tab
    root.update()
    assert len(tab.tree.get_children()) == 14
    tab.fund_var.set("No fund")
    tab.refresh()
    assert len(tab.tree.get_children()) == 14
    tab.select_all()
    people.set_fund(conn, [int(i) for i in tab.tree.selection()], conn.execute(
        "SELECT id FROM funds WHERE name = 'MAAP'").fetchone()[0])
    tab.refresh()
    assert len(tab.tree.get_children()) == 0
    tab.fund_var.set("MAAP")
    tab.refresh()
    assert len(tab.tree.get_children()) == 14
    tab.tree.selection_set(str(ids[0]))
    root.update()
    assert "Fund: MAAP" in tab.d_info.cget("text")
    assert tab.tree.item(str(ids[0]), "values")[2] == "MAAP"
    people.remove_people(conn, [ids[0]])
    tab.removed_var.set(True)
    tab.fund_var.set("All funds")
    tab.refresh()
    assert tab.tree.get_children() == (str(ids[0]),)
    app.find_var.set("zzz-no-match")
    app.destroy()
