# Testing

Python 3.14, Tkinter and SQLite. The program needs only `openpyxl`; tests add `pytest`.

```
pip install -r requirements-dev.txt
python -m pytest -q
```

The whole suite takes under a minute. It needs a display (Tk windows are built and destroyed, never left open).

## Rules

- **Fake data only.** Generate people with `tests/factories.py`. Never point a test at the real data file.
  The one exception is `tests/test_live_copy.py`, which migrates a scratch copy of the real file (set
  `NT_LIVE_COPY`, or place it at `%TEMP%\nt-build\live-copy.db`) and is skipped when there is none. It prints
  counts and ids only. Never commit or share that copy.
- Every test runs with `APPDATA` and `LOCALAPPDATA` pointed at its own temp folder (`conftest.py`), so backups
  and the per-user settings file never touch the real ones.
- Use the `session` / `conn` fixtures for a fresh migrated file, `legacy_db` for a file shaped like the
  old program's, and `root` for the shared Tk root.
- Message boxes are patched in UI tests so nothing blocks.

## Layout

```
data/       connection, transaction(), migrations, activity log
services/   all rules and every SQL statement (people, churches, mailings, backup, lock, health, ...)
ui/         Tkinter screens only; no SQL (tests/test_polish.py checks this)
tests/      one file per area
```

## Starting the program against a copy

Never run it against the shared OneDrive file while developing. Copy a fake or scratch file somewhere local and
set `%APPDATA%\NewsletterTracker\config.json` to `{"db_path": "<that copy>"}`, or run `python main.py` and pick it
on first run.

## Launch check

After any change, make sure the window still builds:

```python
import tkinter as tk, db
from app import App
from services import startup
s = startup.open_session("fake.db")
root = tk.Tk(); App(root, s); root.update(); root.destroy(); s.close()
```

`tests/test_app_launch.py` does this on a fake file.
