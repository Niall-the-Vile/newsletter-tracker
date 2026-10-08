"""Church detail pane: reps with a star on the main contact, Make default, add, remove and move."""
import tkinter as tk
from tkinter import messagebox, ttk

from data.connection import ConflictError, ReadOnlyError
from services import churches, people
from ui.subscriptions_box import SubscriptionsBox

STAR = "★"


class ChooseDialog(tk.Toplevel):
    """Pick one item from a list that can be searched. load(search) returns [(id, label)]."""

    def __init__(self, master, title, prompt, load, searchable=True):
        super().__init__(master)
        self.title(title)
        self.load = load
        self.choice = None
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=prompt).pack(anchor="w")
        self.search_var = tk.StringVar()
        if searchable:
            entry = ttk.Entry(frame, textvariable=self.search_var, width=40)
            entry.pack(fill="x", pady=4)
            entry.bind("<KeyRelease>", lambda e: self.refresh())
        self.listbox = tk.Listbox(frame, height=10, width=50, exportselection=False)
        self.listbox.pack(fill="both", expand=True, pady=4)
        self.listbox.bind("<Double-1>", lambda e: self.ok())
        row = ttk.Frame(frame)
        row.pack(anchor="e")
        ttk.Button(row, text="OK", command=self.ok).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")
        self.refresh()
        self.transient(master)
        self.grab_set()

    def refresh(self):
        self.options = self.load(self.search_var.get().strip())[:200]
        self.listbox.delete(0, "end")
        for _, label in self.options:
            self.listbox.insert("end", label)

    def ok(self):
        sel = self.listbox.curselection()
        if sel:
            self.choice = self.options[sel[0]][0]
            self.destroy()


class ChurchDetail(ttk.Frame):
    def __init__(self, master, conn, on_change):
        super().__init__(master)
        self.conn = conn
        self.on_change = on_change
        self.church_id = None
        self.d_name = ttk.Label(self, text="", font=("", 12, "bold"), wraplength=300)
        self.d_name.pack(anchor="w")
        self.d_info = ttk.Label(self, text="", justify="left", wraplength=300)
        self.d_info.pack(anchor="w", pady=(2, 4))
        self.d_contact = ttk.Label(self, text="", font=("", 10, "bold"), wraplength=300)
        self.d_contact.pack(anchor="w", pady=(0, 8))
        ttk.Label(self, text="Reps", font=("", 10, "bold")).pack(anchor="w")
        self.reps_tree = ttk.Treeview(self, columns=("star", "name", "title"), show="headings", height=6,
                                      selectmode="browse")
        for col, head, w in (("star", "Main", 40), ("name", "Name", 150), ("title", "Title", 90)):
            self.reps_tree.heading(col, text=head)
            self.reps_tree.column(col, width=w, anchor="w")
        self.reps_tree.pack(fill="x", pady=(2, 4))
        row = ttk.Frame(self)
        row.pack(fill="x")
        for text, cmd in ((f"Make default {STAR}", self.make_default), ("Add rep...", self.add_rep),
                          ("Remove rep", self.remove_rep), ("Move...", self.move_rep)):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.returned_btn = ttk.Button(self, text="Mark mail returned", command=self.toggle_returned)
        self.returned_btn.pack(anchor="w", pady=(6, 0))
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, pady=(6, 0))
        self.subs_box = SubscriptionsBox(tabs, conn, self.after_change)
        history = ttk.Frame(tabs, padding=4)
        self.d_history = tk.Listbox(history, height=6, activestyle="none")
        self.d_history.pack(fill="both", expand=True, pady=(2, 4))
        self.d_note = ttk.Label(history, text="", foreground="#666666", wraplength=300)
        self.d_note.pack(anchor="w")
        tabs.add(self.subs_box, text="What they get")
        tabs.add(history, text="History")

    def show(self, church_id):
        self.church_id = church_id
        self.reps_tree.delete(*self.reps_tree.get_children())
        self.d_history.delete(0, "end")
        self.d_note.config(text="")
        self.subs_box.show("church", None, [])
        self.returned_btn.state(["disabled"])
        d = churches.church_detail(self.conn, church_id) if church_id else None
        if d is None:
            self.d_name.config(text="")
            self.d_info.config(text="Select a church to see its reps.")
            self.d_contact.config(text="")
            return
        c = d["church"]
        self.d_name.config(text=c["name"])
        place = ", ".join(filter(None, [c["city"], c["state"]]))
        self.d_info.config(text="\n".join(filter(None, [
            f"Church  |  Fund: {c['fund_name'] or 'none'}", c["conference"], place,
            f"{d['members']} members linked", "Mail was returned from this church's address." if c["returned_mail"] else ""])))
        self.returned_btn.config(text="Clear returned-mail flag" if c["returned_mail"] else "Mark mail returned")
        self.returned_btn.state(["!disabled"])
        self.subs_box.show("church", church_id, d["gets"])
        self.d_contact.config(text=f"Mailing contact: {d['contact']}")
        for r in d["reps"]:
            self.reps_tree.insert("", "end", iid=str(r["person_id"]), values=(
                STAR if r["is_default"] else "", people.display_name(r["first_name"], r["last_name"]),
                r["title"] or ""))
        for h in d["history"]:
            self.d_history.insert("end", f"{h['ts'][:10]}  {h['summary']}")
        starts = d["history_starts"]
        self.d_note.config(text=f"History starts {starts}." if starts
                           else "History starts with the first mailing made in this program.")

    def _selected_rep(self):
        sel = self.reps_tree.selection()
        if not sel:
            messagebox.showinfo("Select a rep", "Choose a rep in the list first.")
            return None
        return int(sel[0])

    def _run(self, fn, *args, **kwargs):
        try:
            return fn(self.conn, *args, **kwargs)
        except (churches.RepError, ReadOnlyError, ConflictError) as e:
            messagebox.showwarning("Church", str(e))

    def after_change(self):
        self.on_change()
        self.show(self.church_id)

    def toggle_returned(self):
        if self.church_id is None:
            return
        flagged = bool(self.conn.execute("SELECT returned_mail FROM churches WHERE id = ?", (self.church_id,)).fetchone()[0])
        self._run(people.set_returned_mail, "church", self.church_id, not flagged)
        self.after_change()

    def make_default(self):
        pid = self._selected_rep()
        if pid is not None:
            self._run(churches.make_default, self.church_id, pid)
            self.after_change()

    def add_rep(self):
        dlg = ChooseDialog(self, "Add rep", "Search for the person who is a rep of this church:",
                           lambda s: [(r["id"], f"{people.display_name(r['first_name'], r['last_name'])}"
                                       f"  ({r['city'] or ''} {r['state'] or ''})".rstrip())
                                      for r in people.list_people(self.conn, s)[:200]])
        self.wait_window(dlg)
        if dlg.choice is not None:
            self._run(churches.add_rep, self.church_id, dlg.choice)
            self.after_change()

    def _ask_new_default(self, candidates):
        names = {r["id"]: people.display_name(r["first_name"], r["last_name"]) for r in self.conn.execute(
            f"SELECT id, first_name, last_name FROM people WHERE id IN ({','.join('?' * len(candidates))})",
            candidates)}
        dlg = ChooseDialog(self, "Main contact", "Who should be the main contact for this church now?",
                           lambda s: [(i, n) for i, n in names.items()], searchable=False)
        self.wait_window(dlg)
        return dlg.choice

    def _with_choice(self, fn, *args):
        try:
            return fn(self.conn, *args)
        except churches.DefaultChoiceNeeded as ask:
            chosen = self._ask_new_default(ask.candidates)
            if chosen is None:
                return None
            return fn(self.conn, *args, new_default_id=chosen)
        except (churches.RepError, ReadOnlyError, ConflictError) as e:
            messagebox.showwarning("Church", str(e))
            return None

    def remove_rep(self):
        pid = self._selected_rep()
        if pid is None or not messagebox.askyesno("Remove rep", "Stop this person being a rep of this church? "
                                                  "They stay on file."):
            return
        outcome = self._with_choice(churches.remove_rep, self.church_id, pid)
        if outcome == "no_contact":
            messagebox.showinfo("No contact", "This church has no reps left, so it is marked No contact and "
                                "will be skipped in mailings.")
        self.after_change()

    def move_rep(self):
        pid = self._selected_rep()
        if pid is None:
            return
        dlg = ChooseDialog(self, "Move rep", "Move this rep to which church?",
                           lambda s: [(c["id"], c["name"]) for c in churches.list_churches(self.conn, s)
                                      if c["id"] != self.church_id])
        self.wait_window(dlg)
        if dlg.choice is not None:
            self._with_choice(churches.move_rep, pid, self.church_id, dlg.choice)
            self.after_change()
