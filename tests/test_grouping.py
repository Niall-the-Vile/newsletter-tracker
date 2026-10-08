import hashlib

import pytest

import db
import importer
from services import churches, grouping, importing, people
from tests import factories


def import_generated(conn, tmp_path, count=2000):
    path = tmp_path / "people.csv"
    factories.write_people_csv(path, count)
    headers, rows = importer.load_file(str(path))
    importing.apply_import(conn, headers, rows, importer.build_mapping(headers), str(path))


def file_hash(conn):
    with open(conn.db_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def add_person(conn, church, conference="", state="", title="", **extra):
    p = {"first_name": "F", "last_name": f"L{church}{conference}{db_count(conn)}", "church_name": church,
         "conference": conference, "state": state, "title": title, "active": 1}
    p.update(extra)
    return db.upsert_person(conn, p)


def db_count(conn):
    return conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]


def test_dry_run_on_2000_rows_reports_counts_and_writes_nothing(conn, tmp_path):
    import_generated(conn, tmp_path)
    digest = file_hash(conn)
    tables = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("people", "churches", "church_reps", "church_aliases", "activity_log")}
    p = grouping.plan(conn)
    assert file_hash(conn) == digest
    s = p["summary"]
    assert (s["created"], s["linked"], s["unmatched"], s["conflicts"]) == (20, 1600, 240, 2)
    assert s["conflict_people"] == 160
    assert tables == {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert [c["name"] for c in p["conflicts"]] == ["Grace Fellowship", "Grace Fellowship"]
    assert len(p["near"]) == 1


def test_confirm_creates_churches_links_people_and_tags_the_run(conn, tmp_path):
    import_generated(conn, tmp_path)
    s = grouping.apply_grouping(conn)
    assert s["created"] == 20 and s["tag"].startswith("group-")
    assert conn.execute("SELECT COUNT(*) FROM churches WHERE import_tag = ?", (s["tag"],)).fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM people WHERE church_id IS NOT NULL").fetchone()[0] == 1600
    # raw names are kept
    assert conn.execute("SELECT COUNT(*) FROM people WHERE church_name = 'Fake Chapel 18'").fetchone()[0] == 80
    # a second run has nothing left to create apart from the review items
    again = grouping.plan(conn)["summary"]
    assert (again["created"], again["linked"], again["conflicts"]) == (0, 0, 2)


def test_reps_come_from_titles_and_several_reps_stay_without_a_default(conn, tmp_path):
    import_generated(conn, tmp_path)
    p = grouping.plan(conn)
    assert {r["role"] for r in p["people"]} == {"Rep", "Member"}
    grouping.apply_grouping(conn)
    # rows 0, 10, 20... have a title. Church k gets reps from rows k, k+25, ... with i % 10 == 0.
    rows = conn.execute("SELECT c.id, COUNT(r.id) AS n, SUM(r.is_default) AS d FROM churches c "
                        "JOIN church_reps r ON r.church_id = c.id GROUP BY c.id").fetchall()
    assert rows
    for r in rows:
        assert (r["d"] or 0) == (1 if r["n"] == 1 else 0)
    assert len(churches.needs_default(conn)) == sum(1 for r in rows if r["n"] > 1)


def test_staff_can_change_a_proposed_role(conn):
    pid = add_person(conn, "Fake Church", "Conf", title="Pastor")
    row = grouping.plan(conn)["people"][0]
    assert row["proposed"] == "Rep"
    grouping.apply_grouping(conn, roles={pid: "Member"})
    assert conn.execute("SELECT COUNT(*) FROM church_reps").fetchone()[0] == 0
    assert conn.execute("SELECT church_id FROM people WHERE id = ?", (pid,)).fetchone()[0] is not None


def test_none_na_member_and_blank_mean_no_church(conn):
    for name in ("None", "N/A", "n/a", "Member", "  ", ""):
        add_person(conn, name)
    s = grouping.plan(conn)["summary"]
    assert (s["created"], s["linked"], s["unmatched"]) == (0, 0, 6)


def test_same_name_in_another_conference_goes_to_review_until_staff_keep_them_apart(conn):
    add_person(conn, "Grace", "Conf A")
    add_person(conn, "Grace", "Conf B")
    p = grouping.plan(conn)
    assert p["summary"]["created"] == 0 and p["summary"]["conflicts"] == 2
    keys = [c["key"] for c in p["conflicts"]]
    s = grouping.apply_grouping(conn, separate=keys)
    assert s["created"] == 2
    assert conn.execute("SELECT COUNT(*) FROM churches").fetchone()[0] == 2


def test_state_is_used_when_the_conference_is_blank(conn):
    add_person(conn, "Hope Church", "", "PA")
    add_person(conn, "hope church", "", "pa")
    add_person(conn, "Hope Church", "", "OH")
    p = grouping.plan(conn)
    assert (p["summary"]["conflicts"], p["summary"]["linked"], p["summary"]["created"]) == (2, 0, 0)
    both = grouping.plan(conn, separate=[c["key"] for c in p["conflicts"]])["summary"]
    assert (both["created"], both["linked"], both["conflicts"]) == (2, 3, 0)


def test_near_match_hand_merge_remembers_an_alias(conn):
    for _ in range(3):
        add_person(conn, "Bethel Church", "Conf")
    add_person(conn, "Bethel Chapel", "Conf")
    p = grouping.plan(conn)
    assert p["summary"]["created"] == 2 and len(p["near"]) == 1
    near = p["near"][0]
    assert sorted([near["a"]["members"], near["b"]["members"]]) == [1, 3]
    big = near["a"] if near["a"]["members"] == 3 else near["b"]
    small = near["b"] if big is near["a"] else near["a"]
    merged = grouping.plan(conn, merges={small["key"]: big["key"]})
    assert merged["summary"]["created"] == 1 and merged["summary"]["linked"] == 4 and merged["near"] == []
    grouping.apply_grouping(conn, merges={small["key"]: big["key"]})
    assert conn.execute("SELECT COUNT(*) FROM churches").fetchone()[0] == 1
    assert conn.execute("SELECT alias_key FROM church_aliases").fetchone()[0] == small["key"]
    # the remembered merge links a later person without asking
    add_person(conn, "Bethel Chapel", "Conf")
    later = grouping.plan(conn)
    assert later["summary"]["created"] == 0 and later["summary"]["linked"] == 1


def test_existing_churches_are_linked_not_duplicated(conn):
    conn.execute("INSERT INTO churches (name, name_key, conference) VALUES ('Fake Church', 'fake church', 'Conf')")
    add_person(conn, "The FAKE church!", "conf")
    s = grouping.plan(conn)["summary"]
    assert (s["created"], s["linked"]) == (0, 1)


def test_rollback_by_tag_removes_only_that_run(conn, tmp_path):
    add_person(conn, "Old Church", "Conf", title="Pastor")
    first = grouping.apply_grouping(conn)
    add_person(conn, "New Church", "Conf", title="Pastor")
    second = grouping.apply_grouping(conn)
    assert [g["tag"] for g in grouping.list_groupings(conn)] == [second["tag"], first["tag"]]
    result = grouping.rollback_grouping(conn, second["tag"])
    assert result == {"churches": 1, "unlinked": 1}
    names = [r[0] for r in conn.execute("SELECT name FROM churches")]
    assert names == ["Old Church"]
    assert conn.execute("SELECT COUNT(*) FROM church_reps").fetchone()[0] == 1
    assert conn.execute("SELECT church_name FROM people WHERE church_name = 'New Church'").fetchone()
    with pytest.raises(grouping.GroupingError):
        grouping.rollback_grouping(conn, second["tag"])


def test_rollback_refuses_once_a_church_has_a_newsletter(conn):
    add_person(conn, "Old Church", "Conf")
    tag = grouping.apply_grouping(conn)["tag"]
    nl = db.add_newsletter(conn, "Fake Letter")
    cid = conn.execute("SELECT id FROM churches").fetchone()[0]
    nid = conn.execute("SELECT id FROM newsletters WHERE name = 'Fake Letter'").fetchone()[0]
    conn.execute("INSERT INTO subscriptions_v2 (church_id, newsletter_id, quantity) VALUES (?, ?, 3)", (cid, nid))
    with pytest.raises(grouping.GroupingError):
        grouping.rollback_grouping(conn, tag)
    assert conn.execute("SELECT COUNT(*) FROM churches").fetchone()[0] == 1


def test_grouping_is_logged_and_removed_people_are_left_out(conn):
    keep = add_person(conn, "Fake Church", "Conf")
    gone = add_person(conn, "Fake Church", "Conf")
    people.remove_people(conn, [gone])
    s = grouping.apply_grouping(conn)
    assert s["linked"] == 1
    assert conn.execute("SELECT church_id FROM people WHERE id = ?", (gone,)).fetchone()[0] is None
    assert conn.execute("SELECT summary FROM activity_log WHERE action = 'church.group'").fetchone()[0].startswith(
        "Grouped churches: 1 created")
