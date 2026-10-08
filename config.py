"""Remembers where the shared database file lives, per Windows user account."""
import json
import os


def config_dir():
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NewsletterTracker")


def config_path():
    return os.path.join(config_dir(), "config.json")


def local_backup_dir():
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "NewsletterTracker", "backups")


def _load():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data):
    os.makedirs(config_dir(), exist_ok=True)
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f)


def get_saved_db_path():
    path = _load().get("db_path")
    return path if path and os.path.exists(path) else None


def save_db_path(path):
    _save({**_load(), "db_path": path})


def get_flag(name):
    """Small per-person yes/no settings, such as whether Get started has been shown."""
    return bool(_load().get(name))


def set_flag(name):
    try:
        _save({**_load(), name: True})
    except OSError:
        pass
