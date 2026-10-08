"""Window furniture shared by every tab: banners, Back up now, Restore from backup and the Help menu."""
import tkinter as tk
from tkinter import messagebox, ttk

from services import backup, diagnostics, errors

HEARTBEAT_MS = 30_000
AMBER = "#fde7c8"
YELLOW = "#fff6c2"


def friendly_error_handler(root):
    def handler(exc_type, exc, tb):
        message = errors.friendly_message(exc)
        if message:
            messagebox.showwarning("Newsletter Tracker", message)
        else:
            messagebox.showerror("Something went wrong", f"{exc}\n\nIf it keeps happening, use Help > Copy "
                                 "diagnostics and send it to Niall.")
    root.report_callback_exception = handler


def _counts_text(counts):
    if counts is None:
        return "unreadable"
    return f"{counts[0]} people, {counts[1]} newsletters, {counts[2]} subscriptions"


class Chrome:
    def __init__(self, root, parent, session, on_get_started=None):
        self.root, self.session = root, session
        friendly_error_handler(root)

        self.top = ttk.Frame(parent)
        self.top.pack(fill="x")

        self.banner = tk.Frame(self.top, bg=AMBER)
        self.banner_label = tk.Label(self.banner, bg=AMBER, anchor="w", justify="left", wraplength=800)
        self.banner_label.pack(side="left", fill="x", expand=True, padx=8, pady=4)
        self.take_over_btn = ttk.Button(self.banner, text="Take over editing", command=self.take_over)
        self.take_over_btn.pack(side="right", padx=8)

        for w in session.warnings:
            frame = tk.Frame(self.top, bg=YELLOW)
            tk.Label(frame, text=w, bg=YELLOW, anchor="w", justify="left", wraplength=900).pack(
                fill="x", padx=8, pady=3)
            frame.pack(fill="x")

        self.bar = ttk.Frame(self.top)
        self.bar.pack(fill="x", padx=6, pady=(2, 0))
        self.backup_btn = ttk.Button(self.bar, text="Back up now", command=self.back_up_now)
        self.backup_btn.pack(side="right")

        menubar = tk.Menu(root)
        self.file_menu = tk.Menu(menubar, tearoff=0)
        self.file_menu.add_command(label="Back up now", command=self.back_up_now)
        self.file_menu.add_command(label="Restore from backup...", command=self.restore)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=root.destroy)
        menubar.add_cascade(label="File", menu=self.file_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        if on_get_started:
            help_menu.add_command(label="Get started", command=on_get_started)
        help_menu.add_command(label="Copy diagnostics", command=self.copy_diagnostics)
        menubar.add_cascade(label="Help", menu=help_menu)
        root.config(menu=menubar)

        self.refresh_banner()
        root.after(HEARTBEAT_MS, self._beat)

    def refresh_banner(self):
        text = self.session.banner()
        if text:
            self.banner_label.config(text=text)
            self.banner.pack(fill="x", before=self.bar)
            stale = self.session.lock.status == "stale"
            self.take_over_btn.state(["!disabled"] if stale else ["disabled"])
        else:
            self.banner.pack_forget()
        self.file_menu.entryconfig("Restore from backup...", state="disabled" if self.session.read_only else "normal")

    def _beat(self):
        if self.session.read_only:
            self.session.lock.refresh()
            self.refresh_banner()
        elif not self.session.heartbeat():
            self.refresh_banner()
        self.root.after(HEARTBEAT_MS, self._beat)

    def take_over(self):
        if not messagebox.askyesno(
                "Take over editing",
                f"{self.session.lock.holder_text()}, but their copy has gone quiet.\n\n"
                "Take over only if you are sure they have closed the program. Continue?"):
            return
        result = self.session.take_over()
        if result == "restart":
            messagebox.showinfo("One more step", "The data file needs a one-time update. "
                                "Please close the program and open it again.")
        elif result == "busy":
            messagebox.showinfo("Still editing", "That copy is active again, so it was not taken over.")
        self.refresh_banner()

    def back_up_now(self):
        results = backup.backup_both(self.session.db_path, "manual")
        messagebox.showinfo("Backed up", f"Saved two copies ({_counts_text(backup.legacy_counts(results[0]['counts']))}):\n\n"
                            + "\n".join(r["path"] for r in results))

    def copy_diagnostics(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(diagnostics.diagnostics_text(self.session))
        messagebox.showinfo("Copied", "The details are copied. Paste them into an email to Niall.")

    def restore(self):
        RestoreDialog(self.root, self.session)


class RestoreDialog(tk.Toplevel):
    def __init__(self, master, session):
        super().__init__(master)
        self.session = session
        self.title("Restore from backup")
        self.geometry("820x380")
        self.transient(master)
        ttk.Label(self, text="Pick a backup to go back to. A fresh backup of today's data is made first.",
                  padding=8).pack(anchor="w")
        cols = ("when", "where", "why", "counts")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("when", "Made", 150), ("where", "Where", 140), ("why", "Reason", 100), ("counts", "Contents", 400)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=8)
        self.items = backup.list_backups(session.db_path)
        for i, e in enumerate(self.items):
            self.tree.insert("", "end", iid=str(i), values=(
                e["when"].strftime("%d %b %Y %H:%M"), e["where"], e["reason"].replace("-", " "), _counts_text(e["counts"])))
        row = ttk.Frame(self)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Button(row, text="Restore this backup", command=self.do_restore).pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right", padx=6)

    def do_restore(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Choose a backup", "Select a backup first.", parent=self)
            return
        entry = self.items[int(sel[0])]
        if not messagebox.askyesno(
                "Restore", f"Go back to the backup made {entry['when']:%d %b %Y %H:%M}?\n"
                f"({_counts_text(entry['counts'])})\n\nChanges made since then will be undone. "
                "Everyone else must have closed the program.", parent=self):
            return
        self.session.conn.close()
        try:
            backup.restore_backup(self.session.db_path, entry["path"])
        finally:
            self.session.lock.release()
        messagebox.showinfo("Restored", "Done. The program will close now. Open it again to carry on.", parent=self)
        self.master.destroy()
