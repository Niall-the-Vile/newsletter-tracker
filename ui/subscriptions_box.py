"""What a person or church gets: newsletter, copies, how it is sent and when it was last mailed, with add / change / stop."""
import tkinter as tk
from tkinter import messagebox, ttk

import db
from data.connection import ConflictError, ReadOnlyError
from services import subscriptions

CHIP_COLOURS = {"Mail": "#dbe9f7", "Email": "#dff2df", "Both": "#f6e0b8"}


class SubscriptionDialog(tk.Toplevel):
    """Pick a newsletter (or keep the given one), copies and how it is sent."""

    def __init__(self, master, conn, current=None):
        super().__init__(master)
        self.title("Change copies" if current else "Add a newsletter")
        self.result = None
        self.options = {n["name"]: n["id"] for n in db.list_newsletters(conn)}
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Newsletter:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.news_var = tk.StringVar(value=current["newsletter"] if current else (next(iter(self.options), "")))
        box = ttk.Combobox(frame, textvariable=self.news_var, values=list(self.options), state="readonly", width=28)
        box.grid(row=0, column=1, sticky="w", pady=4)
        if current:
            box.state(["disabled"])
            self.options.setdefault(current["newsletter"], current["newsletter_id"])
        ttk.Label(frame, text="Copies:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.copies_var = tk.StringVar(value=str(current["copies"]) if current else "1")
        ttk.Spinbox(frame, from_=1, to=999, textvariable=self.copies_var, width=6).grid(row=1, column=1, sticky="w")
        ttk.Label(frame, text="Sent by:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.delivery_var = tk.StringVar(value=current["delivery"] if current else "Mail")
        ttk.Combobox(frame, textvariable=self.delivery_var, values=list(subscriptions.DELIVERY_CODES),
                     state="readonly", width=8).grid(row=2, column=1, sticky="w")
        row = ttk.Frame(frame)
        row.grid(row=3, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(row, text="Save", command=self.ok).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")
        self.transient(master)
        self.grab_set()

    def ok(self):
        if self.news_var.get() not in self.options:
            messagebox.showinfo("Newsletter", "Choose a newsletter first.", parent=self)
            return
        self.result = (self.options[self.news_var.get()], self.copies_var.get(),
                       subscriptions.DELIVERY_CODES[self.delivery_var.get()])
        self.destroy()


class SubscriptionsBox(ttk.Frame):
    def __init__(self, master, conn, on_change):
        super().__init__(master)
        self.conn = conn
        self.on_change = on_change
        self.kind = None
        self.entity_id = None
        self.tree = ttk.Treeview(self, columns=("news", "copies", "delivery", "last"), show="headings", height=4,
                                 selectmode="browse")
        for col, head, w in (("news", "Newsletter", 120), ("copies", "Copies", 50), ("delivery", "Sent by", 55),
                             ("last", "Last mailed", 120)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        for name, colour in CHIP_COLOURS.items():
            self.tree.tag_configure(name, background=colour)
        self.tree.pack(fill="x", pady=(2, 4))
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.buttons = [ttk.Button(row, text=t, command=c) for t, c in (
            ("Add...", self.add), ("Change copies...", self.change), ("Stop sending", self.stop))]
        for b in self.buttons:
            b.pack(side="left", padx=(0, 4))
        self.gets = {}

    def show(self, kind, entity_id, gets):
        self.kind, self.entity_id = kind, entity_id
        self.gets = {}
        self.tree.delete(*self.tree.get_children())
        for i, g in enumerate(gets):
            label = g["newsletter"] + (" (pending review)" if g["pending"] else "")
            self.gets[str(i)] = g
            self.tree.insert("", "end", iid=str(i), values=(label, g["copies"], g["delivery"], g["last_sent"]),
                             tags=(g["delivery"],))
        for b in self.buttons:
            b.state(["!disabled"] if entity_id else ["disabled"])

    def _run(self, fn, *args):
        try:
            fn(self.conn, *args)
        except (subscriptions.SubscriptionError, ReadOnlyError, ConflictError) as e:
            messagebox.showwarning("Newsletter", str(e))
            return
        self.on_change()

    def _selected(self):
        sel = self.tree.selection()
        g = self.gets.get(sel[0]) if sel else None
        if g is None or g["sub_id"] is None:
            messagebox.showinfo("Select a newsletter", "Choose one of the newsletters in the list first "
                                "(old subscriptions waiting for review are handled in Setup).")
            return None
        return g

    def add(self):
        dlg = SubscriptionDialog(self, self.conn)
        self.wait_window(dlg)
        if dlg.result:
            self._run(subscriptions.set_subscription, self.kind, self.entity_id, *dlg.result)

    def change(self):
        g = self._selected()
        if g is None:
            return
        dlg = SubscriptionDialog(self, self.conn, g)
        self.wait_window(dlg)
        if dlg.result:
            self._run(subscriptions.set_subscription, self.kind, self.entity_id, *dlg.result)

    def stop(self):
        g = self._selected()
        if g is not None and messagebox.askyesno("Stop sending", f"Stop sending {g['newsletter']}? "
                                                 "It stays on record and can be added again."):
            self._run(subscriptions.stop_subscription, g["sub_id"])
