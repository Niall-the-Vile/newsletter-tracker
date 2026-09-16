import os
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

import db
import importer
import exporter

RECIPIENT_TYPES = ["Member", "Rep", "Church", "Other"]

PERSON_FIELDS = [
    ("first_name", "First Name"),
    ("middle_name", "Middle Name"),
    ("last_name", "Last Name"),
    ("recipient_type", "Type"),
    ("church_name", "Church Name"),
    ("conference", "Conference"),
    ("title", "Title"),
    ("address1", "Address 1"),
    ("address2", "Address 2"),
    ("city", "City"),
    ("state", "State"),
    ("zip", "Zip"),
    ("home_phone", "Home Phone"),
    ("cell_phone", "Cell Phone"),
    ("email", "Email"),
    ("notes", "Notes"),
]


class PersonDialog(tk.Toplevel):
    def __init__(self, master, conn, person=None):
        super().__init__(master)
        self.conn = conn
        self.person = person
        self.result = False
        self.title("Edit Recipient" if person else "Add Recipient")
        self.resizable(False, False)
        self.vars = {}

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)

        for i, (field, label) in enumerate(PERSON_FIELDS):
            ttk.Label(form, text=label + ":").grid(row=i, column=0, sticky="e", padx=4, pady=3)
            current = person[field] if person else ""
            if field == "recipient_type":
                var = tk.StringVar(value=current or "Member")
                widget = ttk.Combobox(form, textvariable=var, values=RECIPIENT_TYPES, width=27, state="readonly")
            else:
                var = tk.StringVar(value=current or "")
                widget = ttk.Entry(form, textvariable=var, width=30)
            widget.grid(row=i, column=1, sticky="w", padx=4, pady=3)
            self.vars[field] = var

        btns = ttk.Frame(form)
        btns.grid(row=len(PERSON_FIELDS), column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btns, text="Save", command=self.on_save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        self.transient(master)
        self.grab_set()

    def on_save(self):
        data = {field: self.vars[field].get().strip() for field, _ in PERSON_FIELDS}
        if not data["first_name"] and not data["last_name"]:
            messagebox.showerror("Missing name", "Enter at least a first or last name.", parent=self)
            return
        data["active"] = 1
        person_id = self.person["id"] if self.person else None
        db.upsert_person(self.conn, data, person_id)
        self.result = True
        self.destroy()


class RecipientsTab(ttk.Frame):
    def __init__(self, master, conn, on_change=None):
        super().__init__(master, padding=8)
        self.conn = conn
        self.on_change = on_change
        self._build()
        self.refresh()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 6))

        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search_var, width=30)
        entry.pack(side="left", padx=4)
        entry.bind("<KeyRelease>", lambda e: self.refresh())

        ttk.Label(top, text="Type:").pack(side="left", padx=(10, 0))
        self.type_var = tk.StringVar(value="All")
        type_box = ttk.Combobox(top, textvariable=self.type_var, values=["All"] + RECIPIENT_TYPES,
                                 width=10, state="readonly")
        type_box.pack(side="left", padx=4)
        type_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        ttk.Label(top, text="State:").pack(side="left", padx=(10, 0))
        self.state_var = tk.StringVar(value="All")
        self.state_box = ttk.Combobox(top, textvariable=self.state_var, width=6, state="readonly")
        self.state_box.pack(side="left", padx=4)
        self.state_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        ttk.Button(top, text="Add", command=self.add_person).pack(side="right", padx=2)
        ttk.Button(top, text="Edit", command=self.edit_person).pack(side="right", padx=2)
        ttk.Button(top, text="Delete", command=self.delete_person).pack(side="right", padx=2)

        columns = ("name", "type", "church", "conference", "title", "city", "state", "phone", "email")
        headers = ["Name", "Type", "Church", "Conference", "Title", "City", "State", "Phone", "Email"]
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        for col, head in zip(columns, headers):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=110, anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.edit_person())

        self.count_label = ttk.Label(self, text="")
        self.count_label.pack(anchor="w", pady=(4, 0))

    def refresh(self):
        states = ["All"] + db.distinct_states(self.conn)
        self.state_box["values"] = states
        if self.state_var.get() not in states:
            self.state_var.set("All")

        for row in self.tree.get_children():
            self.tree.delete(row)
        people = db.list_people(self.conn, self.search_var.get().strip(),
                                  self.type_var.get(), self.state_var.get())
        for p in people:
            name = " ".join(filter(None, [p["first_name"], p["last_name"]]))
            self.tree.insert("", "end", iid=str(p["id"]), values=(
                name, p["recipient_type"] or "", p["church_name"] or "", p["conference"] or "",
                p["title"] or "", p["city"] or "", p["state"] or "", p["cell_phone"] or p["home_phone"] or "",
                p["email"] or "",
            ))
        self.count_label.config(text=f"{len(people)} recipient(s)")
        if self.on_change:
            self.on_change()

    def selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def selected_ids(self):
        return [int(i) for i in self.tree.selection()]

    def add_person(self):
        dlg = PersonDialog(self, self.conn)
        self.wait_window(dlg)
        if dlg.result:
            self.refresh()

    def edit_person(self):
        pid = self.selected_id()
        if not pid:
            messagebox.showinfo("Select a recipient", "Choose a row first.")
            return
        person = db.get_person(self.conn, pid)
        dlg = PersonDialog(self, self.conn, person)
        self.wait_window(dlg)
        if dlg.result:
            self.refresh()

    def delete_person(self):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Select a recipient", "Choose one or more rows first.")
            return
        total_nl, total_copies = 0, 0
        for pid in ids:
            nl_count, copies = db.person_subscriptions_summary(self.conn, pid)
            total_nl += nl_count
            total_copies += copies
        who = "this person" if len(ids) == 1 else f"these {len(ids)} people"
        detail = f" This will also remove them from {total_nl} newsletter subscription(s) ({total_copies} copies total)." \
            if total_nl else ""
        title = "Delete recipient" if len(ids) == 1 else "Delete recipients"
        if messagebox.askyesno(title, f"Remove {who}?{detail} This cannot be undone."):
            for pid in ids:
                db.delete_person(self.conn, pid)
            self.refresh()


class PickRecipientsDialog(tk.Toplevel):
    def __init__(self, master, conn, newsletter_id):
        super().__init__(master)
        self.conn = conn
        self.newsletter_id = newsletter_id
        self.added = False
        self.title("Add Recipients to Newsletter")
        self.geometry("620x440")

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Search (name, church, or conference):").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search_var, width=28)
        entry.pack(side="left", padx=4)
        entry.bind("<KeyRelease>", lambda e: self.refresh())

        ttk.Label(top, text="Quantity for selected:").pack(side="left", padx=(14, 4))
        self.qty_var = tk.StringVar(value="1")
        ttk.Spinbox(top, from_=1, to=999, textvariable=self.qty_var, width=5).pack(side="left")

        columns = ("name", "church", "conference", "city", "state")
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        for col, head in zip(columns, ["Name", "Church", "Conference", "City", "State"]):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=110, anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        btns = ttk.Frame(self, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="Select All Shown", command=self.select_all).pack(side="left")
        ttk.Button(btns, text="Add Selected", command=self.on_add).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left")

        self.refresh()
        self.transient(master)
        self.grab_set()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        people = db.people_not_subscribed(self.conn, self.newsletter_id, self.search_var.get().strip())
        for p in people:
            name = " ".join(filter(None, [p["first_name"], p["last_name"]]))
            self.tree.insert("", "end", iid=str(p["id"]), values=(
                name, p["church_name"] or "", p["conference"] or "", p["city"] or "", p["state"] or "",
            ))

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def on_add(self):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            qty = max(1, int(self.qty_var.get()))
        except ValueError:
            qty = 1
        for pid in sel:
            db.add_subscription(self.conn, int(pid), self.newsletter_id, qty)
        self.added = True
        count = len(sel)
        self.refresh()
        messagebox.showinfo("Added", f"Added {count} recipient(s).", parent=self)


class NewslettersTab(ttk.Frame):
    def __init__(self, master, conn):
        super().__init__(master, padding=8)
        self.conn = conn
        self._build()
        self.refresh_newsletters()

    def _build(self):
        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        ttk.Label(left, text="Newsletters").pack(anchor="w")
        nl_frame = ttk.Frame(left)
        nl_frame.pack(fill="both", expand=True, pady=4)
        self.nl_list = tk.Listbox(nl_frame, height=15, exportselection=False)
        nl_vsb = ttk.Scrollbar(nl_frame, orient="vertical", command=self.nl_list.yview)
        self.nl_list.configure(yscrollcommand=nl_vsb.set)
        self.nl_list.pack(side="left", fill="both", expand=True)
        nl_vsb.pack(side="right", fill="y")
        self.nl_list.bind("<<ListboxSelect>>", lambda e: self.refresh_subscribers())
        nl_btns = ttk.Frame(left)
        nl_btns.pack(fill="x")
        ttk.Button(nl_btns, text="New", command=self.new_newsletter).pack(side="left")
        ttk.Button(nl_btns, text="Rename", command=self.rename_newsletter).pack(side="left", padx=4)
        ttk.Button(nl_btns, text="Delete", command=self.delete_newsletter).pack(side="left")

        right = ttk.Frame(paned, padding=4)
        paned.add(right, weight=3)

        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search_var, width=25)
        entry.pack(side="left", padx=4)
        entry.bind("<KeyRelease>", lambda e: self.refresh_subscribers())
        ttk.Button(top, text="Add Recipients...", command=self.open_picker).pack(side="right")
        ttk.Button(top, text="Export Mailing List (CSV)", command=self.export_csv).pack(side="right", padx=6)

        columns = ("name", "type", "church", "city", "state", "qty")
        sub_tree_frame = ttk.Frame(right)
        sub_tree_frame.pack(fill="both", expand=True, pady=6)
        self.sub_tree = ttk.Treeview(sub_tree_frame, columns=columns, show="headings", selectmode="browse")
        for col, head, w in zip(columns, ["Name", "Type", "Church", "City", "State", "Qty"],
                                 [140, 70, 140, 100, 60, 50]):
            self.sub_tree.heading(col, text=head)
            self.sub_tree.column(col, width=w, anchor="w")
        sub_vsb = ttk.Scrollbar(sub_tree_frame, orient="vertical", command=self.sub_tree.yview)
        self.sub_tree.configure(yscrollcommand=sub_vsb.set)
        self.sub_tree.pack(side="left", fill="both", expand=True)
        sub_vsb.pack(side="right", fill="y")
        self.sub_tree.bind("<Double-1>", lambda e: self.edit_quantity())

        sub_btns = ttk.Frame(right)
        sub_btns.pack(fill="x")
        ttk.Button(sub_btns, text="Edit Quantity", command=self.edit_quantity).pack(side="left")
        ttk.Button(sub_btns, text="Remove from Newsletter", command=self.remove_subscriber).pack(side="left", padx=6)

        self.totals_label = ttk.Label(right, text="", font=("", 10, "bold"))
        self.totals_label.pack(anchor="w", pady=(6, 0))

    def refresh_newsletters(self):
        previous = self.selected_newsletter()
        previous_id = previous["id"] if previous else None
        self.newsletters = db.list_newsletters(self.conn)
        self.nl_list.delete(0, "end")
        for nl in self.newsletters:
            self.nl_list.insert("end", nl["name"])
        if self.newsletters:
            idx = 0
            if previous_id is not None:
                for i, nl in enumerate(self.newsletters):
                    if nl["id"] == previous_id:
                        idx = i
                        break
            self.nl_list.selection_clear(0, "end")
            self.nl_list.selection_set(idx)
        self.refresh_subscribers()

    def selected_newsletter(self):
        sel = self.nl_list.curselection()
        if not sel:
            return None
        return self.newsletters[sel[0]]

    def refresh_subscribers(self):
        for row in self.sub_tree.get_children():
            self.sub_tree.delete(row)
        nl = self.selected_newsletter()
        if not nl:
            self.totals_label.config(text="")
            return
        rows = db.list_subscribers(self.conn, nl["id"], self.search_var.get().strip())
        for r in rows:
            name = " ".join(filter(None, [r["first_name"], r["last_name"]]))
            self.sub_tree.insert("", "end", iid=str(r["sub_id"]), values=(
                name, r["recipient_type"] or "", r["church_name"] or "", r["city"] or "",
                r["state"] or "", r["quantity"],
            ))
        recipients, copies = db.subscription_totals(self.conn, nl["id"])
        self.totals_label.config(text=f"{recipients} recipient(s)  —  {copies} total copies needed")

    def new_newsletter(self):
        name = simpledialog.askstring("New Newsletter", "Newsletter name:", parent=self)
        if name:
            try:
                db.add_newsletter(self.conn, name.strip())
            except sqlite3.IntegrityError:
                messagebox.showerror(
                    "Error", f"A newsletter named '{name.strip()}' already exists — pick a different name.",
                    parent=self,
                )
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=self)
            self.refresh_newsletters()

    def rename_newsletter(self):
        nl = self.selected_newsletter()
        if not nl:
            return
        name = simpledialog.askstring("Rename Newsletter", "New name:", initialvalue=nl["name"], parent=self)
        if name:
            db.rename_newsletter(self.conn, nl["id"], name.strip(), nl["description"] or "")
            self.refresh_newsletters()

    def delete_newsletter(self):
        nl = self.selected_newsletter()
        if not nl:
            return
        recipients, copies = db.subscription_totals(self.conn, nl["id"])
        warning = ""
        if nl["name"] in dict(db.DEFAULT_NEWSLETTERS):
            warning = ("\n\nNote: this is one of the built-in starter newsletters and will be "
                       "recreated automatically (empty) the next time the app is opened.")
        if messagebox.askyesno(
            "Delete Newsletter",
            f"Delete '{nl['name']}'? This will remove {recipients} recipient(s) "
            f"({copies} copies total). This cannot be undone.{warning}",
        ):
            db.delete_newsletter(self.conn, nl["id"])
            self.refresh_newsletters()

    def open_picker(self):
        nl = self.selected_newsletter()
        if not nl:
            messagebox.showinfo("Select a newsletter", "Choose a newsletter first.")
            return
        dlg = PickRecipientsDialog(self, self.conn, nl["id"])
        self.wait_window(dlg)
        self.refresh_subscribers()

    def selected_sub_id(self):
        sel = self.sub_tree.selection()
        return int(sel[0]) if sel else None

    def edit_quantity(self):
        sub_id = self.selected_sub_id()
        if not sub_id:
            return
        current = self.sub_tree.item(str(sub_id), "values")[-1]
        qty = simpledialog.askinteger("Quantity", "Copies needed:", initialvalue=int(current), minvalue=1, parent=self)
        if qty:
            db.update_subscription_quantity(self.conn, sub_id, qty)
            self.refresh_subscribers()

    def remove_subscriber(self):
        sub_id = self.selected_sub_id()
        if not sub_id:
            return
        if messagebox.askyesno("Remove", "Remove this recipient from the newsletter?"):
            db.remove_subscription(self.conn, sub_id)
            self.refresh_subscribers()

    def export_csv(self):
        nl = self.selected_newsletter()
        if not nl:
            messagebox.showinfo("Select a newsletter", "Choose a newsletter first.")
            return
        default_name = f"{nl['name']} mailing list.csv"
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=default_name,
                                             filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        count = exporter.export_newsletter_csv(self.conn, nl["id"], path)
        messagebox.showinfo("Exported", f"Wrote {count} recipient(s) to:\n{path}")


class ImportTab(ttk.Frame):
    def __init__(self, master, conn, on_imported=None):
        super().__init__(master, padding=12)
        self.conn = conn
        self.on_imported = on_imported
        self.rows = []
        self.mapping = {}
        self._build()

    def _build(self):
        ttk.Label(self, text="Import recipients from an existing CSV or Excel mailing list.",
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(self, text="Matching people (same name + address) are updated instead of duplicated.").pack(
            anchor="w", pady=(0, 10))

        ttk.Button(self, text="Choose File...", command=self.choose_file).pack(anchor="w")

        self.mapping_label = ttk.Label(self, text="", justify="left")
        self.mapping_label.pack(anchor="w", pady=10)

        preview_frame = ttk.Frame(self)
        preview_frame.pack(fill="both", expand=True)
        self.preview_tree = ttk.Treeview(preview_frame, show="headings", height=8)
        pv_vsb = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_tree.yview)
        pv_hsb = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=pv_vsb.set, xscrollcommand=pv_hsb.set)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        pv_vsb.grid(row=0, column=1, sticky="ns")
        pv_hsb.grid(row=1, column=0, sticky="ew")
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        assign_frame = ttk.Frame(self)
        assign_frame.pack(fill="x", pady=10)
        self.assign_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(assign_frame, text="Also subscribe all imported people to:",
                         variable=self.assign_var).pack(side="left")
        self.newsletter_var = tk.StringVar()
        self.newsletter_box = ttk.Combobox(assign_frame, textvariable=self.newsletter_var,
                                            state="readonly", width=25)
        self.newsletter_box.pack(side="left", padx=6)
        ttk.Label(assign_frame, text="Qty each:").pack(side="left", padx=(10, 2))
        self.assign_qty_var = tk.StringVar(value="1")
        ttk.Spinbox(assign_frame, from_=1, to=999, textvariable=self.assign_qty_var, width=5).pack(side="left")

        self.import_btn = ttk.Button(self, text="Import", command=self.do_import, state="disabled")
        self.import_btn.pack(anchor="w")

        self.refresh_newsletter_choices()

    def refresh_newsletter_choices(self):
        self.newsletters = db.list_newsletters(self.conn)
        self.newsletter_box["values"] = [nl["name"] for nl in self.newsletters]
        if self.newsletters:
            self.newsletter_var.set(self.newsletters[0]["name"])

    def choose_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Mailing lists", "*.csv *.xlsx *.xlsm"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            headers, rows = importer.load_file(path)
        except PermissionError:
            messagebox.showerror(
                "File is in use",
                f"Windows won't let this program read:\n{path}\n\n"
                "This almost always means the file is currently open in Excel (or another "
                "program). Close it there, then click Choose File... again.",
            )
            return
        except Exception as e:
            messagebox.showerror("Could not read file", str(e))
            return
        self.mapping = importer.build_mapping(headers)
        self.rows = rows

        name_cols = {"first_name", "last_name", "full_name"}
        if not (name_cols & self.mapping.keys()) and "church_name" not in self.mapping:
            messagebox.showerror("No name column found",
                                  "Couldn't find a Name / First Name / Last Name / Church Name column in this file.")
            self.import_btn.config(state="disabled")
            return

        mapped_desc = ", ".join(f"{field} ← {header}" for field, header in self.mapping.items())
        self.mapping_label.config(text=f"Loaded {len(rows)} row(s) from {os.path.basename(path)}\n"
                                        f"Detected columns: {mapped_desc}")

        preview_cols = list(self.mapping.keys())
        self.preview_tree.delete(*self.preview_tree.get_children())
        self.preview_tree["columns"] = preview_cols
        for col in preview_cols:
            self.preview_tree.heading(col, text=col)
            self.preview_tree.column(col, width=100, anchor="w")
        for row in rows[:20]:
            mapped = importer.map_row(row, self.mapping)
            self.preview_tree.insert("", "end", values=[mapped.get(c, "") for c in preview_cols])

        self.import_btn.config(state="normal")

    def do_import(self):
        added, updated = 0, 0
        newsletter_id = None
        if self.assign_var.get() and self.newsletter_var.get():
            match = next((nl for nl in self.newsletters if nl["name"] == self.newsletter_var.get()), None)
            newsletter_id = match["id"] if match else None
        try:
            qty = max(1, int(self.assign_qty_var.get()))
        except ValueError:
            qty = 1

        for row in self.rows:
            mapped = importer.map_row(row, self.mapping)
            if not mapped.get("first_name") and not mapped.get("last_name"):
                if mapped.get("church_name"):
                    # No named contact on file yet -- use the church itself as the recipient.
                    mapped["last_name"] = mapped["church_name"]
                else:
                    continue
            existing = db.find_duplicate_person(
                self.conn, mapped.get("first_name"), mapped.get("last_name"),
                mapped.get("address1"), mapped.get("zip"),
            )
            if existing:
                person_id = db.upsert_person(self.conn, mapped, existing["id"])
                updated += 1
            else:
                mapped.setdefault("recipient_type", "Church" if not mapped.get("first_name") else "Member")
                mapped["active"] = 1
                person_id = db.upsert_person(self.conn, mapped)
                added += 1
            if newsletter_id:
                if existing and db.is_subscribed(self.conn, person_id, newsletter_id):
                    pass  # keep the existing, possibly custom, quantity
                else:
                    db.add_subscription(self.conn, person_id, newsletter_id, qty)

        messagebox.showinfo("Import complete", f"Added {added} new, updated {updated} existing recipient(s).")
        if self.on_imported:
            self.on_imported()


class App(ttk.Frame):
    def __init__(self, master, conn, db_path):
        super().__init__(master)
        self.conn = conn
        master.title(f"Newsletter Tracker — {db_path}")
        master.geometry("1000x600")
        self.pack(fill="both", expand=True)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.newsletters_tab = NewslettersTab(notebook, conn)
        self.recipients_tab = RecipientsTab(notebook, conn, on_change=self.newsletters_tab.refresh_newsletters)
        self.import_tab = ImportTab(notebook, conn, on_imported=self._on_imported)

        notebook.add(self.recipients_tab, text="Recipients")
        notebook.add(self.newsletters_tab, text="Newsletters")
        notebook.add(self.import_tab, text="Import")

    def _on_imported(self):
        self.recipients_tab.refresh()
        self.newsletters_tab.refresh_newsletters()
