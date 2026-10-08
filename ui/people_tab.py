"""People & Churches tab: one list with filters, a detail pane, bulk Set Fund and Remove / Restore."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import db
from data.connection import ConflictError, ReadOnlyError
from services import bounces, churches, funds, people
from ui.church_detail import ChurchDetail
from ui.history_box import HistoryBox
from ui.person_dialog import PersonDialog
from ui.subscriptions_box import CHIP_COLOURS, SubscriptionsBox

SEARCH_DELAY_MS = 250
ALL_FUNDS, NO_FUND_LABEL = "All funds", "No fund"
CHURCH = "Church"
BOTH_MARK = "BOTH"  # mail and email; the whole row is shaded as well


class SetFundDialog(tk.Toplevel):
    def __init__(self, master, conn, count):
        super().__init__(master)
        self.title("Set fund")
        self.choice = None  # (fund_id or None) once chosen
        self.chosen = False
        self.options = [(f["name"], f["id"]) for f in funds.active_funds(conn)] + [("No fund", None)]
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Set the fund for {count} selected {'person' if count == 1 else 'people'}:").pack(
            anchor="w")
        self.var = tk.StringVar(value=self.options[0][0])
        ttk.Combobox(frame, textvariable=self.var, values=[o[0] for o in self.options],
                     state="readonly", width=28).pack(anchor="w", pady=8)
        row = ttk.Frame(frame)
        row.pack(anchor="e")
        ttk.Button(row, text="Set fund", command=self.ok).pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")
        self.transient(master)
        self.grab_set()

    def ok(self):
        self.choice = dict(self.options)[self.var.get()]
        self.chosen = True
        self.destroy()


class PeopleTab(ttk.Frame):
    def __init__(self, master, conn, find_var):
        super().__init__(master, padding=8)
        self.conn = conn
        self.find_var = find_var
        self._after = None
        self._fund_ids = {}
        self._build()
        find_var.trace_add("write", lambda *a: self._debounce())
        self.refresh()

    # ---------- layout ----------

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        self.type_var = tk.StringVar(value="All")
        self.fund_var = tk.StringVar(value=ALL_FUNDS)
        self.state_var = tk.StringVar(value="All")
        self.attention_var = tk.BooleanVar(value=False)
        self.removed_var = tk.BooleanVar(value=False)
        self.type_box = self._combo(bar, "Type:", self.type_var, 10)
        self.fund_box = self._combo(bar, "Fund:", self.fund_var, 12)
        self.state_box = self._combo(bar, "State:", self.state_var, 6)
        ttk.Checkbutton(bar, text="Needs attention", variable=self.attention_var,
                        command=self.refresh).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(bar, text="Show removed", variable=self.removed_var,
                        command=self.refresh).pack(side="left", padx=(10, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 6))
        self.person_only = []
        ttk.Button(actions, text="Add", command=self.add_person).pack(side="left", padx=(0, 4))
        for text, cmd in (("Edit", self.edit_person), ("Set fund...", self.set_fund)):
            btn = ttk.Button(actions, text=text, command=cmd)
            btn.pack(side="left", padx=4)
            self.person_only.append(btn)
        self.remove_btn = ttk.Button(actions, text="Remove", command=self.remove)
        self.remove_btn.pack(side="left", padx=4)
        self.restore_btn = ttk.Button(actions, text="Restore", command=self.restore)
        self.restore_btn.pack(side="left", padx=4)
        ttk.Button(actions, text="Select all shown", command=self.select_all).pack(side="left", padx=4)
        self.count_label = ttk.Label(actions, text="")
        self.count_label.pack(side="right")

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned)
        paned.add(left, weight=4)
        columns = ("name", "type", "fund", "contact", "place", "gets", "both")
        heads = ("Name", "Type", "Fund", "Mailing contact", "City/State", "Gets", "Both")
        widths = (150, 60, 70, 100, 110, 200, 50)
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        for col, head, w in zip(columns, heads, widths):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        self.tree.tag_configure("both", background=CHIP_COLOURS["Both"])
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())
        self.tree.bind("<Double-1>", lambda e: self.edit_person())
        self.tree.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])

        self.detail = ttk.Frame(paned, padding=(10, 0))
        paned.add(self.detail, weight=2)
        self.person_box = ttk.Frame(self.detail)
        self.person_box.pack(fill="both", expand=True)
        self.church_box = ChurchDetail(self.detail, self.conn, self.refresh_list_only)
        self._build_detail()

    def _combo(self, bar, label, var, width):
        ttk.Label(bar, text=label).pack(side="left", padx=(10, 0))
        box = ttk.Combobox(bar, textvariable=var, width=width, state="readonly")
        box.pack(side="left", padx=4)
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        return box

    def _build_detail(self):
        self.d_name = ttk.Label(self.person_box, text="", font=("", 12, "bold"), wraplength=300)
        self.d_name.pack(anchor="w")
        self.d_info = ttk.Label(self.person_box, text="Select a person to see their details.", justify="left",
                                wraplength=300)
        self.d_info.pack(anchor="w", pady=(2, 8))
        self.returned_btn = ttk.Button(self.person_box, text="Mark mail returned", command=self.toggle_returned)
        self.returned_btn.pack(anchor="w", pady=(0, 6))
        self.bounce_btn = ttk.Button(self.person_box, text="Mark email bounced", command=self.toggle_bounce)
        self.bounce_btn.pack(anchor="w", pady=(0, 6))
        tabs = ttk.Notebook(self.person_box)
        tabs.pack(fill="both", expand=True)
        self.subs_box = SubscriptionsBox(tabs, self.conn, self.refresh_list_only)
        self.gets_tree = self.subs_box.tree
        tabs.add(self.subs_box, text="What they get")
        self.history_box = HistoryBox(tabs, height=8)

    # ---------- data ----------

    def _debounce(self):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(SEARCH_DELAY_MS, self._debounced_refresh)

    def _debounced_refresh(self):
        if self.winfo_exists():
            self.refresh()

    def _fund_filter(self):
        v = self.fund_var.get()
        if v == ALL_FUNDS:
            return None
        return people.NO_FUND if v == NO_FUND_LABEL else self._fund_ids.get(v)

    def refresh(self):
        self._after = None
        types, states = people.types_and_states(self.conn)
        fund_rows = funds.list_funds(self.conn)
        self._fund_ids = {f["name"]: f["id"] for f in fund_rows}
        for box, var, values in ((self.type_box, self.type_var, ["All"] + types + [CHURCH]),
                                 (self.state_box, self.state_var, ["All"] + states),
                                 (self.fund_box, self.fund_var, [ALL_FUNDS, NO_FUND_LABEL] + list(self._fund_ids))):
            box["values"] = values
            if var.get() not in values:
                var.set(values[0])

        if self.church_mode():
            self._refresh_churches()
            return
        removed = self.removed_var.get()
        rows = people.list_people(self.conn, self.find_var.get().strip(), self.type_var.get(), self._fund_filter(),
                                  self.state_var.get(), self.attention_var.get(), removed)
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            place = ", ".join(filter(None, [r["city"], r["state"]]))
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                people.display_name(r["first_name"], r["last_name"]), r["recipient_type"] or "",
                r["fund_name"] or "", "Themselves", place, r["gets"], BOTH_MARK if r["is_both"] else ""),
                             tags=("both",) if r["is_both"] else ())
        self.count_label.config(text=f"{len(rows)} {'removed ' if removed else ''}people shown")
        self.person_buttons(True)
        self.remove_btn.state(["disabled"] if removed else ["!disabled"])
        self.restore_btn.state(["!disabled"] if removed else ["disabled"])
        self.show_detail()

    def church_mode(self):
        return self.type_var.get() == CHURCH

    def person_buttons(self, on):
        for b in self.person_only + [self.remove_btn, self.restore_btn]:
            b.state(["!disabled"] if on else ["disabled"])

    def _refresh_churches(self):
        rows = churches.list_churches(self.conn, self.find_var.get().strip(), self.state_var.get(),
                                      self._fund_filter(), self.attention_var.get())
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            place = ", ".join(filter(None, [r["city"], r["state"]]))
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["name"], CHURCH, r["fund_name"] or "", churches.contact_label(r["default_name"], r["rep_count"]),
                place, f"{r['rep_count']} reps, {r['member_count']} members", BOTH_MARK if r["is_both"] else ""),
                             tags=("both",) if r["is_both"] else ())
        self.count_label.config(text=f"{len(rows)} churches shown")
        self.person_buttons(False)
        self.show_detail()

    def refresh_list_only(self):
        """After a change in the church pane: redraw the list and keep the same church selected."""
        keep = self.tree.selection()
        self.refresh()
        keep = [i for i in keep if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)

    def selected_ids(self):
        return [int(i) for i in self.tree.selection()]

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def show_detail(self):
        ids = self.selected_ids()
        church = self.church_mode()
        (self.person_box.pack_forget if church else self.church_box.pack_forget)()
        (self.church_box if church else self.person_box).pack(fill="both", expand=True)
        if church:
            self.church_box.show(ids[0] if len(ids) == 1 else None)
            return
        self.subs_box.show("person", None, [])
        self.history_box.show(None)
        self.returned_btn.state(["disabled"])
        self.bounce_btn.state(["disabled"])
        if len(ids) != 1:
            self.d_name.config(text="")
            self.d_info.config(text=f"{len(ids)} people selected." if ids else "Select a person to see their details.")
            return
        d = people.person_detail(self.conn, ids[0], history=False)
        if d is None:
            return
        p = d["person"]
        self.d_name.config(text=people.display_name(p["first_name"], p["last_name"]))
        lines = [f"{p['recipient_type'] or ''}  |  Fund: {p['fund_name'] or 'none'}"]
        if p["church_name"]:
            lines.append(p["church_name"])
        lines.append(", ".join(filter(None, [p["address1"], p["address2"], p["city"], p["state"], p["zip"]])))
        lines.append("  ".join(filter(None, [p["email"], p["cell_phone"] or p["home_phone"]])))
        if p["returned_mail"]:
            lines.append("Mail was returned from this address.")
        skipped = bounces.is_valid_email(p["email"]) and bounces.is_skipped(self.conn, p["email"])
        if skipped:
            lines.append("This email address bounced, so email is skipped until the address is corrected.")
        self.d_info.config(text="\n".join(l for l in lines if l))
        self.returned_btn.config(text="Clear returned-mail flag" if p["returned_mail"] else "Mark mail returned")
        self.returned_btn.state(["!disabled"])
        self.bounce_btn.config(text="Clear email bounce" if skipped else "Mark email bounced")
        self.bounce_btn.state(["!disabled"] if bounces.is_valid_email(p["email"]) else ["disabled"])
        self.subs_box.show("person", ids[0], d["gets"])
        self.history_box.show(lambda: people.person_history(self.conn, ids[0]))

    # ---------- actions ----------

    def add_person(self):
        dlg = PersonDialog(self, self.conn)
        self.wait_window(dlg)
        if dlg.result:
            self.refresh()

    def edit_person(self):
        ids = self.selected_ids()
        if len(ids) != 1:
            messagebox.showinfo("Select a person", "Choose one person first.")
            return
        dlg = PersonDialog(self, self.conn, db.get_person(self.conn, ids[0]))
        self.wait_window(dlg)
        if dlg.result:
            self.refresh()

    def set_fund(self):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Select people", "Choose one or more people first.")
            return
        dlg = SetFundDialog(self, self.conn, len(ids))
        self.wait_window(dlg)
        if not dlg.chosen:
            return
        result = people.set_fund(self.conn, ids, dlg.choice)
        self.refresh()
        messagebox.showinfo("Fund set", f"{result['changed']} changed, {result['unchanged']} already had it.")

    def remove(self):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Select people", "Choose one or more people first.")
            return
        who = "this person" if len(ids) == 1 else f"these {len(ids)} people"
        if not messagebox.askyesno("Remove", f"Remove {who}? They stay on file and can be restored "
                                   "(tick Show removed, then Restore)."):
            return
        typed = None
        if len(ids) > people.REMOVE_TYPED_OVER:
            answer = simpledialog.askstring("Confirm", f"To remove {len(ids)} people, type the number {len(ids)}:",
                                            parent=self)
            if not answer or not answer.strip().isdigit():
                return
            typed = int(answer)
            if typed != len(ids):
                messagebox.showwarning("Not removed", "That is not the number of people selected, so nothing was removed.")
                return
        people.remove_people(self.conn, ids, typed)
        self.refresh()

    def toggle_returned(self):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        flagged = bool(db.get_person(self.conn, ids[0])["returned_mail"])
        try:
            people.set_returned_mail(self.conn, "person", ids[0], not flagged)
        except (ReadOnlyError, ConflictError) as e:
            messagebox.showwarning("Returned mail", str(e))
        self.refresh_list_only()

    def toggle_bounce(self):
        ids = self.selected_ids()
        if len(ids) != 1:
            return
        email = db.get_person(self.conn, ids[0])["email"]
        try:
            if bounces.is_skipped(self.conn, email):
                bounces.clear(self.conn, email)
            else:
                bounces.mark_bounced(self.conn, email, "Manual")
        except (bounces.BounceError, ReadOnlyError, ConflictError) as e:
            messagebox.showwarning("Email bounce", str(e))
        self.refresh_list_only()

    def restore(self):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Select people", "Choose one or more people first.")
            return
        people.restore_people(self.conn, ids)
        self.refresh()
