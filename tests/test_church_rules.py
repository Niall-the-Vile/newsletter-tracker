import sqlite3

import pytest

import db
from services import churches, people
from tests import factories


def make_church(conn, name="Fake Church"):
    return conn.execute("INSERT INTO churches (name, name_key) VALUES (?, ?)", (name, name.lower())).lastrowid


def make_person(conn, i=0):
    return db.upsert_person(conn, factories.fake_person(i))


def reps_of(conn, church_id, default=None):
    sql = "SELECT person_id FROM church_reps WHERE church_id = ? AND active = 1"
    if default:
        sql += " AND is_default = 1"
    return sorted(r[0] for r in conn.execute(sql, (church_id,)))


def flag(conn, church_id):
    return conn.execute("SELECT no_contact FROM churches WHERE id = ?", (church_id,)).fetchone()[0]


def test_database_rejects_two_defaults_for_one_church(conn):
    ch, a, b = make_church(conn), make_person(conn, 1), make_person(conn, 2)
    ins = "INSERT INTO church_reps (church_id, person_id, is_default, active) VALUES (?, ?, 1, 1)"
    conn.execute(ins, (ch, a))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (ch, b))


def test_first_rep_becomes_default_second_does_not(conn):
    ch, a, b = make_church(conn), make_person(conn, 1), make_person(conn, 2)
    churches.add_rep(conn, ch, a)
    churches.add_rep(conn, ch, b)
    assert reps_of(conn, ch, default=True) == [a]
    with pytest.raises(churches.RepError):
        churches.add_rep(conn, ch, b)


def test_make_default_swaps_in_one_step(conn):
    ch, a, b = make_church(conn), make_person(conn, 1), make_person(conn, 2)
    churches.add_rep(conn, ch, a)
    churches.add_rep(conn, ch, b)
    churches.make_default(conn, ch, b)
    assert reps_of(conn, ch, default=True) == [b]
    with pytest.raises(churches.RepError):
        churches.make_default(conn, ch, make_person(conn, 3))
    assert reps_of(conn, ch, default=True) == [b]


def test_make_default_is_all_or_nothing(conn, monkeypatch):
    ch, a, b = make_church(conn), make_person(conn, 1), make_person(conn, 2)
    churches.add_rep(conn, ch, a)
    churches.add_rep(conn, ch, b)

    def boom(*args, **kwargs):
        raise RuntimeError("log failed")

    monkeypatch.setattr(churches.activity, "log", boom)
    with pytest.raises(RuntimeError):
        churches.make_default(conn, ch, b)
    assert reps_of(conn, ch, default=True) == [a]


def test_removing_default_promotes_the_only_other_rep(conn):
    ch, a, b = make_church(conn), make_person(conn, 1), make_person(conn, 2)
    churches.add_rep(conn, ch, a)
    churches.add_rep(conn, ch, b)
    assert churches.remove_rep(conn, ch, a) == "promoted"
    assert reps_of(conn, ch, default=True) == [b]


def test_removing_default_with_several_left_asks_and_changes_nothing(conn):
    ch = make_church(conn)
    a, b, c = (make_person(conn, i) for i in (1, 2, 3))
    for p in (a, b, c):
        churches.add_rep(conn, ch, p)
    with pytest.raises(churches.DefaultChoiceNeeded) as asked:
        churches.remove_rep(conn, ch, a)
    assert sorted(asked.value.candidates) == [b, c]
    assert reps_of(conn, ch) == [a, b, c] and reps_of(conn, ch, default=True) == [a]
    assert churches.remove_rep(conn, ch, a, new_default_id=c) == "chosen"
    assert reps_of(conn, ch, default=True) == [c]


def test_removing_the_last_rep_marks_no_contact_and_a_new_default_clears_it(conn):
    ch, a, b = make_church(conn), make_person(conn, 1), make_person(conn, 2)
    churches.add_rep(conn, ch, a)
    assert churches.remove_rep(conn, ch, a) == "no_contact"
    assert flag(conn, ch) == 1
    assert churches.list_churches(conn)[0]["rep_count"] == 0
    assert churches.church_detail(conn, ch)["contact"] == churches.NO_CONTACT
    churches.add_rep(conn, ch, b)
    assert flag(conn, ch) == 0 and reps_of(conn, ch, default=True) == [b]


def test_removing_a_person_applies_the_rule_without_asking(conn):
    ch = make_church(conn)
    a, b, c = (make_person(conn, i) for i in (1, 2, 3))
    for p in (a, b, c):
        churches.add_rep(conn, ch, p)
    people.remove_people(conn, [a])
    assert reps_of(conn, ch) == [b, c] and reps_of(conn, ch, default=True) == []
    assert [r["id"] for r in churches.needs_default(conn)] == [ch]
    people.remove_people(conn, [b])
    assert reps_of(conn, ch, default=True) == [c]
    assert churches.needs_default(conn) == []


def test_rep_moving_churches_end_dates_the_old_link(conn):
    old, new, p, other = make_church(conn, "Old"), make_church(conn, "New"), make_person(conn, 1), make_person(conn, 2)
    churches.add_rep(conn, old, p)
    churches.add_rep(conn, old, other)
    churches.move_rep(conn, p, old, new)
    link = conn.execute("SELECT * FROM church_reps WHERE church_id = ? AND person_id = ?", (old, p)).fetchone()
    assert link["active"] == 0 and link["end_date"] and link["is_default"] == 0
    assert reps_of(conn, new, default=True) == [p]
    assert reps_of(conn, old, default=True) == [other]


def test_several_reps_and_no_default_shows_in_needs_a_default_list(conn):
    ch = make_church(conn)
    for i in (1, 2):
        churches.add_rep(conn, ch, make_person(conn, i), auto_default=False)
    assert [r["id"] for r in churches.needs_default(conn)] == [ch]
    row = churches.list_churches(conn, needs_a_default=True)[0]
    assert churches.contact_label(row["default_name"], row["rep_count"]) == churches.NEEDS_DEFAULT


def test_rule_changes_are_logged(conn):
    ch, a, b = make_church(conn), make_person(conn, 1), make_person(conn, 2)
    churches.add_rep(conn, ch, a)
    churches.add_rep(conn, ch, b)
    churches.make_default(conn, ch, b)
    actions = [r[0] for r in conn.execute("SELECT action FROM activity_log WHERE entity_type = 'church'")]
    assert actions.count("church.rep_add") == 2 and "church.make_default" in actions
