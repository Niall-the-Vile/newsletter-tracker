import json
import os
import time

import pytest

import db
from data.connection import ReadOnlyError
from services import startup
from services.lock import WriterLock


def age_lock(db_path, seconds):
    p = db_path + ".lock"
    info = json.load(open(p))
    info["heartbeat"] -= seconds
    json.dump(info, open(p, "w"))


def test_first_copy_edits_second_is_read_only_with_banner(db_path):
    first = startup.open_session(db_path)
    second = startup.open_session(db_path)
    try:
        assert not first.read_only and second.read_only
        assert "is editing since" in second.banner()
        assert first.banner() is None
        with pytest.raises(ReadOnlyError):
            db.upsert_person(second.conn, {"first_name": "No"})
        db.upsert_person(first.conn, {"first_name": "Yes"})
        assert second.lock.status == "held"
    finally:
        second.close()
        first.close()


def test_closing_releases_the_lock(db_path):
    startup.open_session(db_path).close()
    assert not os.path.exists(db_path + ".lock")
    s = startup.open_session(db_path)
    assert not s.read_only
    s.close()


def test_read_only_copy_does_not_remove_the_other_copys_lock(db_path):
    first = startup.open_session(db_path)
    startup.open_session(db_path).close()
    assert os.path.exists(db_path + ".lock")
    first.close()


def test_stale_lock_can_be_taken_over(db_path):
    first = startup.open_session(db_path)
    age_lock(db_path, 600)
    second = startup.open_session(db_path)
    try:
        assert second.read_only and second.lock.status == "stale"
        assert "seems to have stopped" in second.banner()
        assert second.take_over() == "ok"
        assert not second.read_only
        db.upsert_person(second.conn, {"first_name": "Taken"})
        # the old copy notices on its next heartbeat and goes read-only
        assert first.heartbeat() is False and first.read_only
    finally:
        second.close()
        first.close()


def test_live_lock_cannot_be_taken_over(db_path):
    first = startup.open_session(db_path)
    second = startup.open_session(db_path)
    try:
        assert second.take_over() == "busy"
        assert second.read_only
    finally:
        second.close()
        first.close()


def test_heartbeat_refreshes_the_file(db_path):
    lock = WriterLock(db_path)
    assert lock.acquire() == "owner"
    age_lock(db_path, 50)
    old = json.load(open(db_path + ".lock"))["heartbeat"]
    time.sleep(0.01)
    assert lock.beat()
    assert json.load(open(db_path + ".lock"))["heartbeat"] > old
    lock.release()


def test_lock_holds_user_machine_and_heartbeat(db_path):
    lock = WriterLock(db_path)
    lock.acquire()
    info = json.load(open(db_path + ".lock"))
    assert info["user"] and info["machine"] and info["heartbeat"] and info["started"]
    lock.release()


def test_read_only_session_on_old_file_is_not_migrated(legacy_db):
    path, before = legacy_db
    first = startup.open_session(path)  # migrates
    first.close()
    # forge an older file held by someone else
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version = 0")
    c.close()
    holder = WriterLock(path)
    holder.acquire()
    second = startup.open_session(path)
    try:
        assert second.read_only and second.needs_update
        assert "one-time update" in second.banner()
        assert second.take_over() == "busy"
    finally:
        second.close()
        holder.release()


def test_old_file_held_by_someone_else_shows_a_plain_message_and_builds_no_ui(legacy_db, monkeypatch):
    import sqlite3
    from tkinter import messagebox

    import main
    path, _ = legacy_db
    holder = WriterLock(path)
    holder.acquire()
    shown = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, text, **k: shown.append(text))
    second = startup.open_session(path)
    try:
        assert second.needs_update
        assert main.ready_to_open(second) is False
        assert shown == ["Someone else is editing and the file still needs its one-time update. "
                         "Try again when they close it."]
        with pytest.raises(sqlite3.ProgrammingError):
            second.conn.execute("SELECT 1")  # closed, nothing left open
        assert os.path.exists(path + ".lock")  # the other person's lock is untouched
    finally:
        holder.release()


def test_unreadable_lock_file_counts_as_held_then_stale(db_path):
    path = db_path + ".lock"
    with open(path, "w") as f:
        f.write('{"user": "Pat", "heart')  # half written
    lock = WriterLock(db_path)
    assert lock.acquire() == "held"
    old = time.time() - 600
    os.utime(path, (old, old))
    assert WriterLock(db_path).acquire() == "stale"


def test_take_over_gives_up_if_someone_else_wrote_the_lock_after_us(db_path, monkeypatch):
    first = startup.open_session(db_path)
    age_lock(db_path, 600)
    lock = WriterLock(db_path)
    assert lock.acquire() == "stale"
    other = WriterLock(db_path)
    real = lock._write

    def write_then_lose(info):
        real(info)
        other._write(other._info())

    monkeypatch.setattr(lock, "_write", write_then_lose)
    assert lock.take_over() is False and lock.status == "held"
    assert json.load(open(db_path + ".lock"))["token"] == other.token
    first.close()
