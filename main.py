import os
import sys
import tkinter as tk
from tkinter import messagebox, filedialog

import config
import db
from app import App

DEFAULT_DB_NAME = "newsletter_tracker.db"
DEFAULT_DB_DIR = os.path.dirname(os.path.abspath(__file__))


def choose_db_path():
    root = tk.Tk()
    root.withdraw()

    default_path = os.path.join(DEFAULT_DB_DIR, DEFAULT_DB_NAME)
    use_default = messagebox.askyesno(
        "Newsletter Tracker — first run",
        "Where should the shared data file live?\n\n"
        f"Use the default location next to the program?\n{default_path}\n\n"
        "Choose 'No' to pick an existing file or a different folder "
        "(e.g. one your whole team can already reach on OneDrive).",
    )
    if use_default:
        root.destroy()
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
    root.destroy()
    return path or None


def main():
    db_path = config.get_saved_db_path()
    if not db_path:
        db_path = choose_db_path()
        if not db_path:
            sys.exit(0)
        config.save_db_path(db_path)

    conn = None
    while conn is None:
        try:
            conn = db.connect(db_path)
            db.init_db(conn)
        except Exception as e:
            error_root = tk.Tk()
            error_root.withdraw()
            retry = messagebox.askyesno(
                "Newsletter Tracker — cannot open data file",
                f"Could not open the shared data file at:\n{db_path}\n\n"
                "It may not be synced yet, or the path may have changed.\n\n"
                f"({e})\n\nChoose a different file or location?",
            )
            error_root.destroy()
            conn = None
            if not retry:
                sys.exit(1)
            db_path = choose_db_path()
            if not db_path:
                sys.exit(0)
            config.save_db_path(db_path)

    root = tk.Tk()
    App(root, conn, db_path)
    root.mainloop()
    conn.close()


if __name__ == "__main__":
    main()
