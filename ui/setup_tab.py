"""Setup tab: Newsletters, Funds, Old subscriptions (the owner's review), Church grouping, Bounces, Activity. App adds Import after Church grouping."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import db
from data import activity
from services import funds, people, review
from ui.bounces_panel import BouncesPanel
from ui.grouping_panel import GroupingPanel
from ui.newsletters_panel import NewslettersPanel

NO_NEWSLETTER = "No newsletter (do not subscribe them)"


class FundsPanel(ttk.Frame):
    def __init__(self, master, conn):
        super().__init__(master, padding=8)
        self.conn = conn
        ttk.Label(self, text="Each person or church belongs to one fund. Set a person's fund from the "
                  "People & Churches tab.").pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(self, columns=("name", "people", "churches", "status"), show="headings",
                                 height=8, selectmode="browse")
        for col, head, w in (("name", "Fund", 180), ("people", "People", 80), ("churches", "Churches", 80),
                             ("status", "Status", 80)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill="x")
        row = ttk.Frame(self)
        row.pack(anchor="w", pady=6)
        ttk.Button(row, text="Add fund...", command=self.add).pack(side="left", padx=(0, 4))
        ttk.Button(row, text="Rename...", command=self.rename).pack(side="left", padx=4)
        ttk.Button(row, text="Turn off / on", command=self.toggle).pack(side="left", padx=4)
        ttk.Label(self, text="A fund that is turned off cannot be given to anyone new. Nothing is deleted.",
                  foreground="#666666").pack(anchor="w")
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for f in funds.list_funds(self.conn):
            self.tree.insert("", "end", iid=str(f["id"]),
                             values=(f["name"], f["people"], f["churches"], "On" if f["active"] else "Off"))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a fund", "Choose a fund first.")
            return None
        return int(sel[0])

    def _try(self, fn, *args):
        try:
            fn(self.conn, *args)
        except funds.FundError as e:
            messagebox.showwarning("Funds", str(e))
        self.refresh()

    def add(self):
        name = simpledialog.askstring("Add fund", "Fund name:", parent=self)
        if name is not None:
            self._try(funds.add_fund, name)

    def rename(self):
        fid = self._selected()
        if fid is None:
            return
        name = simpledialog.askstring("Rename fund", "New name:", initialvalue=self.tree.item(str(fid), "values")[0],
                                      parent=self)
        if name is not None:
            self._try(funds.rename_fund, fid, name)

    def toggle(self):
        fid = self._selected()
        if fid is not None:
            self._try(funds.set_fund_active, fid, self.tree.item(str(fid), "values")[3] == "Off")


class ReviewPanel(ttk.Frame):
    """Decide what the old MAAP subscriptions (one copy each) should become. Nothing moves until you apply."""

    def __init__(self, master, conn):
        super().__init__(master, padding=8)
        self.conn = conn
        self.target_ids = {}
        ttk.Label(self, text="The old program gave many people one copy of MAAP by default. Check them here, "
                  "then choose where they should go.", wraplength=800, justify="left").pack(anchor="w")
        self.copy_label = ttk.Label(self, text="")
        self.copy_label.pack(anchor="w", pady=(8, 0))
        self.copy_btn = ttk.Button(self, text="Copy them here for review", command=self.copy)
        self.copy_btn.pack(anchor="w", pady=4)
        self.count_label = ttk.Label(self, text="", font=("", 10, "bold"))
        self.count_label.pack(anchor="w", pady=(6, 2))

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("name", "church", "place"), show="headings", selectmode="extended")
        for col, head, w in (("name", "Name", 200), ("church", "Church", 200), ("place", "City/State", 140)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_preview())

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="Move to:").pack(side="left")
        self.target_var = tk.StringVar()
        self.target_box = ttk.Combobox(row, textvariable=self.target_var, state="readonly", width=40)
        self.target_box.pack(side="left", padx=6)
        self.target_box.bind("<<ComboboxSelected>>", lambda e: self.update_preview())
        self.apply_btn = ttk.Button(row, text="Apply", command=self.apply)
        self.apply_btn.pack(side="left", padx=6)
        self.preview_label = ttk.Label(self, text="", wraplength=800, justify="left")
        self.preview_label.pack(anchor="w", pady=4)
        undo = ttk.Frame(self)
        undo.pack(fill="x")
        self.undo_label = ttk.Label(undo, text="")
        self.undo_label.pack(side="left")
        self.undo_btn = ttk.Button(undo, text="Undo last move", command=self.undo)
        self.undo_btn.pack(side="left", padx=8)
        self.refresh()

    def _scope(self):
        sel = [int(i) for i in self.tree.selection()]
        return sel or None

    def _target(self):
        return self.target_ids.get(self.target_var.get())

    def refresh(self):
        to_copy = review.count_to_copy(self.conn)
        if review.maap_newsletter(self.conn) is None:
            self.copy_label.config(text="There is no MAAP newsletter, so there is nothing to copy.")
        else:
            self.copy_label.config(text=f"{to_copy} old MAAP subscriptions have not been copied here yet."
                                   if to_copy else "All old MAAP subscriptions have been copied here.")
        self.copy_btn.state(["!disabled"] if to_copy else ["disabled"])

        rows = review.pending_rows(self.conn)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", iid=str(r["person_id"]), values=(
                people.display_name(r["first_name"], r["last_name"]), r["church_name"] or "",
                ", ".join(filter(None, [r["city"], r["state"]]))))
        self.count_label.config(text=f"Waiting for review: {len(rows)}")

        self.target_ids = {NO_NEWSLETTER: None}
        names = [NO_NEWSLETTER]
        for nl in db.list_newsletters(self.conn):
            self.target_ids[nl["name"]] = nl["id"]
            names.append(nl["name"])
        self.target_box["values"] = names
        if self.target_var.get() not in names:
            self.target_var.set(names[0])
        last = review.last_move(self.conn)
        self.undo_label.config(text=f"Last move: {last['summary']}" if last else "")
        self.undo_btn.state(["!disabled"] if last else ["disabled"])
        self.update_preview()

    def update_preview(self):
        scope = self._scope()
        p = review.preview_move(self.conn, self._target(), scope)
        who = f"the {len(scope)} selected" if scope else f"all {p['waiting']} waiting"
        target = self.target_var.get()
        text = f"This will move {who} people: {p['will_move']} will be changed"
        if p["already_there"]:
            text += f", {p['already_there']} already have {target} and will be left as they are"
        self.preview_label.config(text=text + ". Nothing changes until you press Apply.")
        self.apply_btn.state(["!disabled"] if p["will_move"] else ["disabled"])

    def copy(self):
        try:
            n = review.copy_legacy(self.conn)
        except review.ReviewError as e:
            messagebox.showwarning("Old subscriptions", str(e))
            return
        messagebox.showinfo("Copied", f"{n} old MAAP subscriptions are now waiting for review. "
                            "The old list itself was not changed.")
        self.refresh()

    def apply(self):
        scope, target = self._scope(), self._target()
        p = review.preview_move(self.conn, target, scope)
        if not messagebox.askyesno("Apply", f"{p['will_move']} people will be set to: {self.target_var.get()}.\n"
                                   "You can undo this afterwards. Continue?", parent=self):
            return
        result = review.move_pending(self.conn, target, scope)
        messagebox.showinfo("Done", f"{result['moved']} moved, {result['skipped']} left as they were.", parent=self)
        self.refresh()

    def undo(self):
        last = review.last_move(self.conn)
        if last and messagebox.askyesno("Undo", f"Undo this?\n\n{last['summary']}", parent=self):
            result = review.undo_move(self.conn, last["id"])
            messagebox.showinfo("Undone", f"{result['restored']} put back, {result['skipped']} had changed since "
                                "and were left alone.", parent=self)
            self.refresh()


class ActivityPanel(ttk.Frame):
    def __init__(self, master, conn):
        super().__init__(master, padding=8)
        self.conn = conn
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("when", "who", "what"), show="headings")
        for col, head, w in (("when", "When", 140), ("who", "Who", 100), ("what", "What happened", 500)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in activity.recent(self.conn, 200):
            if r["parent_id"] is None:  # one line per action; the detail lines are on each person
                self.tree.insert("", "end", values=(r["ts"].replace("T", " ")[:16], r["user"] or "", r["summary"] or ""))


class SetupTab(ttk.Notebook):
    def __init__(self, master, conn, on_changed=None):
        super().__init__(master)
        self.newsletters_panel = NewslettersPanel(self, conn)
        self.funds_panel = FundsPanel(self, conn)
        self.review_panel = ReviewPanel(self, conn)
        self.grouping_panel = GroupingPanel(self, conn, on_changed)
        self.bounces_panel = BouncesPanel(self, conn)
        self.activity_panel = ActivityPanel(self, conn)
        self.add(self.newsletters_panel, text="Newsletters")
        self.add(self.funds_panel, text="Funds")
        self.add(self.review_panel, text="Old subscriptions")
        self.add(self.grouping_panel, text="Church grouping")
        self.add(self.bounces_panel, text="Bounces")
        self.add(self.activity_panel, text="Activity")
        self.bind("<<NotebookTabChanged>>", lambda e: self.refresh())

    def refresh(self):
        panel = self.nametowidget(self.select())
        if hasattr(panel, "refresh"):
            panel.refresh()
