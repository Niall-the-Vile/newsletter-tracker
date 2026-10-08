"""Setup > Church grouping: check first (nothing is saved), adjust, then confirm. Earlier groupings can be undone."""
import tkinter as tk
from tkinter import messagebox, ttk

from data.connection import ConflictError, ReadOnlyError
from services import grouping


def _tree(master, columns, height=8, selectmode="extended"):
    frame = ttk.Frame(master)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height,
                        selectmode=selectmode)
    for col, head, w in columns:
        tree.heading(col, text=head)
        tree.column(col, width=w, anchor="w")
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    return tree


class GroupingPanel(ttk.Frame):
    def __init__(self, master, conn, on_changed=None):
        super().__init__(master, padding=8)
        self.conn = conn
        self.on_changed = on_changed
        self.merges, self.separate, self.roles, self.plan = {}, set(), {}, None
        ttk.Label(self, text="Group people into churches by church name and conference. Check first: nothing is "
                  "saved until you press Create.", wraplength=800, justify="left").pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="Check churches", command=self.check).pack(side="left")
        self.create_btn = ttk.Button(row, text="Create churches and link people", command=self.confirm,
                                     state="disabled")
        self.create_btn.pack(side="left", padx=8)
        self.summary = ttk.Label(self, text="", font=("", 10, "bold"), wraplength=800, justify="left")
        self.summary.pack(anchor="w", pady=(0, 6))

        self.pages = ttk.Notebook(self)
        self.pages.pack(fill="both", expand=True)
        page = self._page("Needs review")
        ttk.Label(page, text="The same church name appears with different conferences. Pick the ones that really "
                  "are separate churches.").pack(anchor="w")
        self.conflict_tree = _tree(page, (("name", "Church", 220), ("conf", "Conference / state", 160),
                                          ("people", "People", 70), ("also", "Also seen with", 240)), 6)
        ttk.Button(page, text="Keep selected as separate churches", command=self.keep_separate).pack(anchor="w",
                                                                                                      pady=4)
        page = self._page("Similar names")
        ttk.Label(page, text="These names look alike. Merge them by hand if they are the same church.").pack(
            anchor="w")
        self.near_tree = _tree(page, (("a", "First name", 220), ("an", "People", 60), ("b", "Second name", 220),
                                      ("bn", "People", 60)), 6, "browse")
        row = ttk.Frame(page)
        row.pack(anchor="w", pady=4)
        ttk.Button(row, text="Merge second into first", command=lambda: self.merge(keep_first=True)).pack(
            side="left", padx=(0, 4))
        ttk.Button(row, text="Merge first into second", command=lambda: self.merge(keep_first=False)).pack(
            side="left")
        page = self._page("Reps and members")
        ttk.Label(page, text="Who looks like a church rep (they have a title) and who a member. Change any that "
                  "are wrong.").pack(anchor="w")
        self.role_tree = _tree(page, (("name", "Name", 200), ("church", "Church", 240), ("role", "Will be", 80)), 6)
        ttk.Button(page, text="Switch Rep / Member for selected", command=self.switch_roles).pack(anchor="w", pady=4)
        page = self._page("Earlier groupings")
        self.run_tree = _tree(page, (("tag", "Grouping", 160), ("n", "Churches created", 120), ("when", "When", 160)), 6,
                              "browse")
        ttk.Button(page, text="Undo selected grouping", command=self.undo).pack(anchor="w", pady=4)
        self.refresh_runs()

    def _page(self, title):
        page = ttk.Frame(self.pages, padding=6)
        self.pages.add(page, text=title)
        return page

    def refresh(self):
        self.refresh_runs()

    # ---------- check ----------

    def check(self):
        self.show(grouping.plan(self.conn, self.merges, self.separate, self.roles))

    def show(self, plan):
        self.plan = plan
        s = plan["summary"]
        self.summary.config(text=(
            f"{s['created']} churches would be created, {s['linked']} people linked, {s['unmatched']} people have "
            f"no church, {s['conflicts']} names held for review ({s['conflict_people']} people)."))
        self.create_btn.state(["!disabled"] if s["created"] or s["linked"] else ["disabled"])
        for tree in (self.conflict_tree, self.near_tree, self.role_tree):
            tree.delete(*tree.get_children())
        for c in plan["conflicts"]:
            self.conflict_tree.insert("", "end", iid=c["key"], values=(
                c["name"], c["conference"], c["members"], ", ".join(k.split("|")[1] or "no conference"
                                                                    for k in c["also"])))
        for i, pair in enumerate(plan["near"]):
            a, b = pair["a"], pair["b"]
            self.near_tree.insert("", "end", iid=str(i), values=(a["label"], a["members"], b["label"], b["members"]))
        for r in plan["people"]:
            self.role_tree.insert("", "end", iid=str(r["person_id"]), values=(r["name"], r["church"], r["role"]))

    def keep_separate(self):
        sel = self.conflict_tree.selection()
        if not sel:
            messagebox.showinfo("Select churches", "Choose the churches to keep separate first.")
            return
        self.separate.update(sel)
        self.check()

    def merge(self, keep_first):
        sel = self.near_tree.selection()
        if not sel or self.plan is None:
            messagebox.showinfo("Select a pair", "Choose a pair of similar names first.")
            return
        pair = self.plan["near"][int(sel[0])]
        keep, fold = (pair["a"], pair["b"]) if keep_first else (pair["b"], pair["a"])
        if not fold["is_new"]:
            messagebox.showinfo("Already set up", f"{fold['label']} is already a church. Merge the other name "
                                "into it instead.")
            return
        self.merges[fold["key"]] = keep["key"]
        self.check()

    def switch_roles(self):
        sel = self.role_tree.selection()
        if not sel or self.plan is None:
            messagebox.showinfo("Select people", "Choose one or more people first.")
            return
        current = {str(r["person_id"]): r["role"] for r in self.plan["people"]}
        for pid in sel:
            self.roles[int(pid)] = "Member" if current[pid] == "Rep" else "Rep"
        self.check()

    # ---------- confirm and undo ----------

    def confirm(self):
        s = self.plan["summary"]
        if not messagebox.askyesno("Create churches", f"Create {s['created']} churches and link {s['linked']} "
                                   "people? A backup is made first, and you can undo this afterwards.",
                                   parent=self):
            return
        try:
            result = grouping.apply_grouping(self.conn, self.merges, self.separate, self.roles)
        except (ReadOnlyError, ConflictError, grouping.GroupingError) as e:
            messagebox.showwarning("Church grouping", str(e), parent=self)
            return
        messagebox.showinfo("Done", f"{result['created']} churches created and {result['linked']} people linked. "
                            f"{result['defaults_set']} churches with one rep now send church mail to that rep; churches with "
                            "several reps need you to pick one (tick Needs attention, with Type set to Church).",
                            parent=self)
        self.merges, self.separate, self.roles = {}, set(), {}
        self.check()
        self.refresh_runs()
        if self.on_changed:
            self.on_changed()

    def refresh_runs(self):
        self.run_tree.delete(*self.run_tree.get_children())
        for r in grouping.list_groupings(self.conn):
            self.run_tree.insert("", "end", iid=r["tag"], values=(r["tag"], r["churches"],
                                                                  (r["created_at"] or "").replace("T", " ")[:16]))

    def undo(self):
        sel = self.run_tree.selection()
        if not sel:
            messagebox.showinfo("Select a grouping", "Choose a grouping to undo first.")
            return
        if not messagebox.askyesno("Undo", "Remove the churches this grouping created and unlink their people? "
                                   "People and their church names stay as they are.", parent=self):
            return
        try:
            r = grouping.rollback_grouping(self.conn, sel[0])
        except (ReadOnlyError, ConflictError, grouping.GroupingError) as e:
            messagebox.showwarning("Church grouping", str(e), parent=self)
            return
        messagebox.showinfo("Undone", f"{r['churches']} churches removed, {r['unlinked']} people unlinked.",
                            parent=self)
        self.refresh_runs()
        if self.on_changed:
            self.on_changed()
