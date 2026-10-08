import os
import sqlite3
import sys
import tkinter as tk
from tkinter import messagebox, filedialog

import config
from app import App
from services import backup, startup

DEFAULT_DB_NAME = "newsletter_tracker.db"
DEFAULT_DB_DIR = os.path.dirname(os.path.abspath(__file__))


def choose_db_path():
    default_path = os.path.join(DEFAULT_DB_DIR, DEFAULT_DB_NAME)
    use_default = messagebox.askyesno(
        "Newsletter Tracker — first run",
        "Where should the shared data file live?\n\n"
        f"Use the default location next to the program?\n{default_path}\n\n"
        "Choose 'No' to pick an existing file or a different folder "
        "(e.g. one your whole team can already reach on OneDrive).",
    )
    if use_default:
        return default_path

    path = filedialog.askopenfilename(
        title="Open an existing Newsletter Tracker data file",
        filetypes=[("Newsletter Tracker database", "*.db"), ("All files", "*.*")],
    )
    if not path:
        path = filedialog.asksaveasfilename(
            title="Create a new Newsletter Tracker data file",
            defaultextension=".db",
            initialfile=DEFAULT_DB_NAME,
            filetypes=[("Newsletter Tracker database", "*.db")],
        )
    return path or None


def confirm_update():
    return messagebox.askokcancel(
        "Newsletter Tracker — one-time update",
        "The shared data file needs a one-time update.\n\n"
        "Everyone else must close Newsletter Tracker first. "
        "Two backups are made automatically before anything changes.\n\nContinue?",
    )


def open_or_ask(db_path):
    """Returns a Session, or None if the user chose to quit."""
    while True:
        try:
            return startup.open_session(db_path, confirm_update=confirm_update)
        except startup.UpdateDeclined:
            return None
        except startup.NewerVersionError as e:
            messagebox.showerror("Newsletter Tracker — update needed", str(e))
            return None
        except (startup.DamagedFileError, startup.MigrationError, backup.BackupError) as e:
            messagebox.showerror("Newsletter Tracker — stopped to protect your data", str(e))
            return None
        except (sqlite3.Error, OSError) as e:
            retry = messagebox.askyesno(
                "Newsletter Tracker — cannot open data file",
                f"Could not open the shared data file at:\n{db_path}\n\n"
                "It may not be synced yet, or the path may have changed.\n\n"
                f"({e})\n\nChoose a different file or location?",
            )
            if not retry:
                return None
            db_path = choose_db_path()
            if not db_path:
                return None
            config.save_db_path(db_path)


def main():
    root = tk.Tk()
    root.withdraw()
    db_path = config.get_saved_db_path()
    if not db_path:
        db_path = choose_db_path()
        if not db_path:
            sys.exit(0)
        config.save_db_path(db_path)

    session = open_or_ask(db_path)
    if session is None:
        root.destroy()
        sys.exit(1)

    App(root, session)
    root.deiconify()
    root.mainloop()
    session.close()


if __name__ == "__main__":
    main()
