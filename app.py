import os
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

import db
import exporter
from ui.chrome import Chrome
from ui.import_tab import ImportTab
from ui.people_tab import PeopleTab
from ui.setup_tab import SetupTab


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
        if messagebox.askyesno(
            "Delete Newsletter",
            f"Delete '{nl['name']}'? This will remove {recipients} recipient(s) "
            f"({copies} copies total). This cannot be undone.",
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


class App(ttk.Frame):
    def __init__(self, master, session):
        super().__init__(master)
        conn = self.conn = session.conn
        master.title(f"Newsletter Tracker — {session.db_path}")
        master.geometry("1200x700")
        self.pack(fill="both", expand=True)
        self.chrome = Chrome(master, self, session)

        find_bar = ttk.Frame(self)
        find_bar.pack(fill="x", padx=6, pady=(4, 0))
        ttk.Label(find_bar, text="Find:").pack(side="left")
        self.find_var = tk.StringVar()
        ttk.Entry(find_bar, textvariable=self.find_var, width=40).pack(side="left", padx=4)
        ttk.Button(find_bar, text="Clear", command=lambda: self.find_var.set("")).pack(side="left")

        self.notebook = notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.people_tab = PeopleTab(notebook, conn, self.find_var)
        self.mailings_tab = NewslettersTab(notebook, conn)
        self.setup_tab = SetupTab(notebook, conn, on_changed=self.people_tab.refresh)
        self.import_tab = ImportTab(self.setup_tab, conn, on_imported=self.people_tab.refresh)
        self.setup_tab.insert(3, self.import_tab, text="Import")

        notebook.add(self.people_tab, text="People & Churches")
        notebook.add(self.mailings_tab, text="Mailings")
        notebook.add(self.setup_tab, text="Setup")
        notebook.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        self.find_var.trace_add("write", lambda *a: notebook.select(self.people_tab))

    def _tab_changed(self):
        tab = self.nametowidget(self.notebook.select())
        if tab is self.mailings_tab:
            tab.refresh_newsletters()
        elif tab is self.setup_tab:
            tab.refresh()
        elif tab is self.people_tab:
            tab.refresh()
