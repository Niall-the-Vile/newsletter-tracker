"""Remembers where the shared database file lives, per Windows user account."""
import json
import os

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NewsletterTracker")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def local_backup_dir():
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "NewsletterTracker", "backups")


def get_saved_db_path():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        path = data.get("db_path")
        if path and os.path.exists(path):
            return path
    except (json.JSONDecodeError, OSError):
        pass
    return None


def save_db_path(path):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"db_path": path}, f)
