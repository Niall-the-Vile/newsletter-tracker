import hashlib

import pytest

import db
import importer
from services import backup, importing, people
from tests import factories


def load(path):
    headers, rows = importer.load_file(str(path))
    return headers, rows, importer.build_mapping(headers)


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("people", "churches", "activity_log", "subscriptions")}


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(conn, path, **kw):
    headers, rows, mapping = load(path)
    return importing.apply_import(conn, headers, rows, mapping, str(path), **kw)


def test_dry_run_of_2000_rows_writes_nothing(conn, tmp_path):
    path = tmp_path / "people.csv"
    factories.write_people_csv(path, 2000)
    headers, rows, mapping = load(path)
    before, digest = counts(conn), file_hash(conn.db_path)
    version = conn.execute("SELECT value FROM meta WHERE key = 'data_version'").fetchone()[0]
    backups = len(backup.list_backups(conn.db_path))
    p = importing.plan_import(conn, headers, rows, mapping)
    assert counts(conn) == before and file_hash(conn.db_path) == digest
    assert conn.execute("SELECT value FROM meta WHERE key = 'data_version'").fetchone()[0] == version
    s = p["summary"]
    assert (s["new"], s["updated"], s["skipped"]) == (2000, 0, 0)
    assert p["ignored_columns"] == ["Spare Column"]
    assert s["unmatched_churches"] == 22  # 19 + chapel + 2 grace groups
    assert [{n["name"], n["like"]} for n in p["near_churches"]] == [{"Fake Chapel 18", "Fake Church 18"}]
    assert len(backup.list_backups(conn.db_path)) == backups  # a dry run makes no backup either


def test_import_writes_in_one_transaction_after_a_backup(conn, tmp_path):
    path = tmp_path / "people.csv"
    factories.write_people_csv(path, 60)
    backups = len(backup.list_backups(conn.db_path))
    s = run(conn, path)
    assert s["new"] == 60
    assert counts(conn)["people"] == 60
    assert len(backup.list_backups(conn.db_path)) == backups + 2
    summary = conn.execute("SELECT summary FROM activity_log WHERE action = 'import'").fetchone()[0]
    assert "people.csv" in summary and "60 new" in summary


def test_import_failing_part_way_leaves_nothing(conn, tmp_path, monkeypatch):
    path = tmp_path / "people.csv"
    factories.write_people_csv(path, 30)
    real = importing.activity.log
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] > 20:
            raise RuntimeError("disk trouble")
        return real(*a, **kw)

    monkeypatch.setattr(importing.activity, "log", flaky)
    with pytest.raises(RuntimeError):
        run(conn, path)
    assert counts(conn)["people"] == 0


def test_thinner_reimport_erases_nothing(conn, tmp_path):
    full = tmp_path / "full.csv"
    factories.write_people_csv(full, 20)
    run(conn, full)
    before = [tuple(r) for r in conn.execute("SELECT * FROM people ORDER BY id")]
    thin = tmp_path / "thin.csv"
    with open(thin, "w", newline="", encoding="utf-8") as f:
        f.write("First Name,Last Name,Address 1,Zip,Email,Title,City\n")
        for i in range(20):
            r = factories.file_row(i)
            f.write(f"{r[0]},{r[1]},{r[2]},{r[5]},,,\n")
    s = run(conn, thin)
    assert (s["new"], s["updated"], s["unchanged"]) == (0, 0, 20)
    assert [tuple(r) for r in conn.execute("SELECT * FROM people ORDER BY id")] == before


def test_update_fills_blanks_and_changes_only_what_the_file_says(conn):
    pid = db.upsert_person(conn, {"first_name": "Ann", "last_name": "Lee", "address1": "1 Elm St", "zip": "17000",
                                  "email": "ann@example.invalid", "city": "Faketown", "active": 1})
    headers = ["First Name", "Last Name", "Address 1", "Zip", "Email", "Cell Phone", "City"]
    rows = [dict(zip(headers, ["Ann", "Lee", "1 Elm St", "17000", "", "555-0100", "Newtown"]))]
    mapping = importer.build_mapping(headers)
    s = importing.apply_import(conn, headers, rows, mapping, "x.csv")
    assert s["updated"] == 1
    p = db.get_person(conn, pid)
    assert (p["email"], p["cell_phone"], p["city"]) == ("ann@example.invalid", "555-0100", "Newtown")


def test_skip_reasons_are_reported(conn):
    gone = db.upsert_person(conn, {"first_name": "Old", "last_name": "Timer", "address1": "9 Oak", "zip": "17001",
                                   "active": 1})
    people.remove_people(conn, [gone])
    headers = ["First Name", "Last Name", "Address 1", "Zip"]
    data = [["", "", "", ""], ["", "", "5 Pine", "17002"], ["New", "One", "1 A St", "17003"],
            ["New", "One", "1 A St", "17003"], ["Old", "Timer", "9 Oak", "17001"]]
    rows = [dict(zip(headers, r)) for r in data]
    p = importing.plan_import(conn, headers, rows, importer.build_mapping(headers))
    assert p["summary"]["new"] == 1
    assert p["summary"]["skip_reasons"] == {importing.SKIP_BLANK: 1, importing.SKIP_NO_NAME: 1,
                                            importing.SKIP_REPEAT: 1, importing.SKIP_REMOVED: 1}


def test_subscribe_needs_an_explicit_newsletter(conn, tmp_path):
    path = tmp_path / "p.csv"
    factories.write_people_csv(path, 5)
    with pytest.raises(importing.ImportError_):
        run(conn, path, subscribe=True)
    assert counts(conn)["people"] == 0
    run(conn, path)
    assert counts(conn)["subscriptions"] == 0  # off by default
    db.add_newsletter(conn, "Fake Letter")
    nid = conn.execute("SELECT id FROM newsletters WHERE name = 'Fake Letter'").fetchone()[0]
    run(conn, path, subscribe=True, newsletter_id=nid, quantity=2)
    assert counts(conn)["subscriptions"] == 5


def test_cp1252_csv_zip_plus_4_and_zero_padded_zips(conn, tmp_path):
    path = tmp_path / "w.csv"
    with open(path, "w", newline="", encoding="cp1252") as f:
        f.write("First Name,Last Name,Address 1,Zip,Church Name\n")
        f.write("Zoë,Müller,1 Main St,8801,Café Church\n")
        f.write("Al,Plus,2 Main St,12345-6789,\n")
        f.write("Bo,Nine,3 Main St,123456789,\n")
    run(conn, path)
    rows = {r["last_name"]: r for r in conn.execute("SELECT * FROM people")}
    assert rows["Müller"]["first_name"] == "Zoë" and rows["Müller"]["church_name"] == "Café Church"
    assert rows["Müller"]["zip"] == "08801"
    assert rows["Plus"]["zip"] == "12345-6789" and rows["Nine"]["zip"] == "12345-6789"


def test_xlsx_numeric_zip_is_padded(conn, tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["First Name", "Last Name", "Address 1", "Zip"])
    ws.append(["Xl", "Sheet", "1 Row St", 1234])
    path = tmp_path / "z.xlsx"
    wb.save(path)
    run(conn, path)
    assert conn.execute("SELECT zip FROM people").fetchone()[0] == "01234"


def test_suffix_and_last_first_names(conn):
    assert importer.split_name("John Q Public Jr.") == {"first_name": "John", "middle_name": "Q",
                                                         "last_name": "Public Jr."}
    assert importer.split_name("Smith, John") == {"first_name": "John", "last_name": "Smith"}
    assert importer.split_name("Smith Jr., John Q") == {"first_name": "John", "middle_name": "Q",
                                                         "last_name": "Smith Jr."}
    headers = ["Name", "PrimaryAddressLine1", "PrimaryZip"]
    rows = [dict(zip(headers, ["Lee, Ann III", "1 Elm", "17000"])),
            dict(zip(headers, ["Bob Roe Sr", "2 Elm", "17000"]))]
    importing.apply_import(conn, headers, rows, importer.build_mapping(headers), "o.csv")
    got = sorted((r["first_name"], r["last_name"]) for r in conn.execute("SELECT * FROM people"))
    assert got == [("Ann", "Lee III"), ("Bob", "Roe Sr")]


def test_fund_and_church_name_columns_and_file_fund(conn):
    maap = conn.execute("SELECT id FROM funds WHERE name = 'MAAP'").fetchone()[0]
    amca = conn.execute("SELECT id FROM funds WHERE name = 'AMCA'").fetchone()[0]
    headers = ["First Name", "Last Name", "Address 1", "Zip", "Fund", "Church Name"]
    rows = [dict(zip(headers, r)) for r in (["A", "One", "1 St", "17000", "amca", "Fake Church"],
                                            ["B", "Two", "2 St", "17000", "", "Fake Church"],
                                            ["C", "Three", "3 St", "17000", "Nonesuch", ""])]
    mapping = importer.build_mapping(headers)
    assert {"fund", "church_name"} <= set(mapping)
    p = importing.plan_import(conn, headers, rows, mapping, file_fund_id=maap)
    assert p["summary"]["unknown_funds"] == {"Nonesuch": 1}
    importing.apply_import(conn, headers, rows, mapping, "f.csv", file_fund_id=maap)
    got = {r["last_name"]: r["fund_id"] for r in conn.execute("SELECT * FROM people")}
    assert got == {"One": amca, "Two": maap, "Three": None}
    # a later file-wide fund never replaces a fund already on file
    importing.apply_import(conn, headers, rows[1:2], mapping, "g.csv", file_fund_id=amca)
    assert conn.execute("SELECT fund_id FROM people WHERE last_name = 'Two'").fetchone()[0] == maap


def test_import_links_people_to_an_existing_church_and_lists_near_matches(conn):
    conn.execute("INSERT INTO churches (name, name_key, conference) VALUES ('Fake Church 1', 'fake church 1', 'Conf')")
    headers = ["First Name", "Last Name", "Address 1", "Zip", "Church Name", "Conference"]
    rows = [dict(zip(headers, r)) for r in (["A", "One", "1 St", "17000", "fake church 1", "conf"],
                                            ["B", "Two", "2 St", "17000", "Fake Chapel 1", "Conf"])]
    mapping = importer.build_mapping(headers)
    p = importing.plan_import(conn, headers, rows, mapping)
    assert p["summary"]["linked_to_church"] == 1
    assert [n["name"] for n in p["near_churches"]] == ["Fake Chapel 1"]
    importing.apply_import(conn, headers, rows, mapping, "c.csv")
    linked = conn.execute("SELECT last_name FROM people WHERE church_id IS NOT NULL").fetchall()
    assert [r[0] for r in linked] == ["One"]
    assert conn.execute("SELECT church_name FROM people WHERE last_name = 'Two'").fetchone()[0] == "Fake Chapel 1"
