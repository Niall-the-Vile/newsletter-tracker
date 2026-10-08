"""Single-writer lock file with a heartbeat, kept next to the data file."""
import getpass
import json
import os
import platform
import time
import uuid
from datetime import datetime

HEARTBEAT_SECONDS = 30
STALE_SECONDS = 120
RECHECK_SECONDS = 0.2


def lock_path(db_path):
    return str(db_path) + ".lock"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # Unreadable or half-written: counts as held until it has been quiet for the stale time.
        try:
            seen = os.path.getmtime(path)
        except OSError:
            seen = time.time()
        return {"user": "Someone", "machine": "", "token": None, "started": seen, "heartbeat": seen}


class WriterLock:
    """status: 'owner' (we can edit), 'held' (someone is editing), 'stale' (holder stopped beating)."""

    def __init__(self, db_path, stale_after=STALE_SECONDS):
        self.path = lock_path(db_path)
        self.stale_after = stale_after
        self.token = uuid.uuid4().hex
        self.status = "held"
        self.holder = None

    def _info(self, started=None):
        t = time.time()
        return {"user": getpass.getuser(), "machine": platform.node(), "token": self.token,
                "started": started or t, "heartbeat": t}

    def _write(self, info):
        tmp = f"{self.path}.{self.token}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(info, f)
        os.replace(tmp, self.path)

    def acquire(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self.holder = _read(self.path)
            if self.holder is None:  # released between our two looks
                return self.acquire()
            age = time.time() - float(self.holder.get("heartbeat") or 0)
            self.status = "stale" if age > self.stale_after else "held"
            return self.status
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            self.holder = self._info()
            json.dump(self.holder, f)
        self.status = "owner"
        return self.status

    def refresh(self):
        """Re-reads the holder without taking anything. A missing file counts as stale."""
        if self.status == "owner":
            return
        current = _read(self.path)
        if current is None:
            self.status = "stale"
            return
        self.holder = current
        age = time.time() - float(current.get("heartbeat") or 0)
        self.status = "stale" if age > self.stale_after else "held"

    def take_over(self):
        """Only allowed while the holder's heartbeat is stale."""
        if self.status == "owner":
            return True
        self.acquire()
        if self.status != "stale":
            return self.status == "owner"
        self.holder = self._info()
        self._write(self.holder)
        time.sleep(RECHECK_SECONDS)  # let a simultaneous take-over finish writing, then see who won
        current = _read(self.path)
        if current is None or current.get("token") != self.token:
            self.holder = current
            self.status = "held"
            return False
        self.status = "owner"
        return True

    def beat(self):
        """Refreshes the heartbeat. Returns False if someone else has taken the lock."""
        if self.status != "owner":
            return False
        current = _read(self.path)
        if current is None or current.get("token") != self.token:
            self.holder = current
            self.status = "held"
            return False
        self.holder = self._info(current.get("started"))
        try:
            self._write(self.holder)
        except OSError:
            pass  # OneDrive may hold the file briefly; the next beat retries
        return True

    def release(self):
        if self.status == "owner":
            current = _read(self.path)
            if current and current.get("token") == self.token:
                try:
                    os.remove(self.path)
                except OSError:
                    pass
        self.status = "held"

    def holder_text(self):
        h = self.holder or {}
        who = h.get("user") or "Someone"
        started = datetime.fromtimestamp(float(h.get("started") or 0))
        fmt = "%H:%M" if started.date() == datetime.now().date() else "%d %b %H:%M"
        return f"{who} is editing since {started.strftime(fmt)}"
