"""Setup > Newsletters: add, rename, the fund a newsletter draws from, archive and delete."""
import sqlite3
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import db
from data.connection import ConflictError, ReadOnlyError
from services import funds, newsletters

ANY_FUND = "Any fund"


class NewslettersPanel(ttk.Frame):
    def __init__(self, master, conn):
        super().__init__(master, padding=8)
        self.conn = conn
        ttk.Label(self, text="A newsletter with a fund only goes to people and churches in that fund. "
                  "Newsletters that have been mailed are archived instead of deleted.", wraplength=800,
                  justify="left").pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(self, columns=("name", "fund", "subs", "runs", "status"), show="headings", height=10,
                                 selectmode="browse")
        for col, head, w in (("name", "Newsletter", 220), ("fund", "Fund", 110), ("subs", "Subscribers", 90),
                             ("runs", "Mailings", 70), ("status", "Status", 80)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill="x")
        row = ttk.Frame(self)
        row.pack(anchor="w", pady=6)
        for text, cmd in (("New...", self.new), ("Rename...", self.rename), ("Set fund...", self.set_fund),
                          ("Archive / bring back", self.toggle_archive), ("Delete", self.delete)):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for n in newsletters.list_all(self.conn):
            self.tree.insert("", "end", iid=str(n["id"]), values=(
                n["name"], n["fund_name"] or ANY_FUND, n["subscribers"], n["runs"],
                "Archived" if n["archived"] else "In use"))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a newsletter", "Choose a newsletter first.")
            return None
        return int(sel[0])

    def _try(self, fn, *args):
        try:
            return fn(self.conn, *args)
        except sqlite3.IntegrityError:
            messagebox.showerror("Newsletter", "A newsletter with that name already exists. Pick a different name.")
        except (ReadOnlyError, ConflictError, ValueError) as e:
            messagebox.showwarning("Newsletter", str(e))
        finally:
            self.refresh()

    def new(self):
        name = simpledialog.askstring("New newsletter", "Newsletter name:", parent=self)
        if name and name.strip():
            self._try(db.add_newsletter, name.strip())

    def rename(self):
        nid = self._selected()
        if nid is None:
            return
        name = simpledialog.askstring("Rename newsletter", "New name:", parent=self,
                                      initialvalue=self.tree.item(str(nid), "values")[0])
        if name and name.strip():
            self._try(db.rename_newsletter, nid, name.strip())

    def set_fund(self):
        nid = self._selected()
        if nid is None:
            return
        options = {ANY_FUND: None, **{f["name"]: f["id"] for f in funds.active_funds(self.conn)}}
        dlg = tk.Toplevel(self)
        dlg.title("Set fund")
        var = tk.StringVar(value=self.tree.item(str(nid), "values")[1])
        frame = ttk.Frame(dlg, padding=12)
        frame.pack()
        ttk.Label(frame, text="This newsletter goes only to people and churches in:").pack(anchor="w")
        ttk.Combobox(frame, textvariable=var, values=list(options), state="readonly", width=24).pack(anchor="w", pady=8)
        chosen = []

        def ok():
            chosen.append(var.get())
            dlg.destroy()
        ttk.Button(frame, text="Save", command=ok).pack(side="left", padx=4)
        ttk.Button(frame, text="Cancel", command=dlg.destroy).pack(side="left")
        dlg.transient(self)
        dlg.grab_set()
        self.wait_window(dlg)
        if not chosen:
            return
        mismatched = self._try(newsletters.set_fund, nid, options.get(chosen[0]))
        if mismatched:
            messagebox.showinfo("Fund set", f"{mismatched} current subscribers are not in that fund. They stay "
                                "subscribed, but they are left out of mailings until their fund matches.")

    def toggle_archive(self):
        nid = self._selected()
        if nid is not None:
            self._try(newsletters.set_archived, nid, self.tree.item(str(nid), "values")[4] != "Archived")

    def delete(self):
        nid = self._selected()
        if nid is None:
            return
        name = self.tree.item(str(nid), "values")[0]
        if messagebox.askyesno("Delete newsletter", f"Delete '{name}'? If it has been mailed or has subscribers it is "
                               "archived instead, so nothing on record is lost."):
            if self._try(db.delete_newsletter, nid) == "archived":
                messagebox.showinfo("Archived", f"'{name}' has history, so it was archived instead of deleted.")
