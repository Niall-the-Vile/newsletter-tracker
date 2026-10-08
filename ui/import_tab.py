"""Setup > Import: read a file, show what would happen (nothing is written), then import on request."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import db
import importer
from data.connection import ConflictError, ReadOnlyError
from services import funds, importing

NO_FUND_CHANGE = "Leave funds as they are"


class ImportTab(ttk.Frame):
    def __init__(self, master, conn, on_imported=None):
        super().__init__(master, padding=12)
        self.conn = conn
        self.on_imported = on_imported
        self.headers, self.rows, self.mapping, self.path = [], [], {}, None
        self.fund_ids, self.newsletter_ids = {}, {}
        self._build()
        self.refresh()

    def _build(self):
        ttk.Label(self, text="Import people from an existing CSV or Excel mailing list.",
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(self, text="Choose a file to see what would change. Nothing is saved until you press Import. "
                  "Blank cells never erase what is already on file.").pack(anchor="w", pady=(0, 8))
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Choose File...", command=self.choose_file).pack(side="left")
        self.file_label = ttk.Label(top, text="")
        self.file_label.pack(side="left", padx=8)

        fund_row = ttk.Frame(self)
        fund_row.pack(fill="x", pady=(8, 0))
        ttk.Label(fund_row, text="Set fund for this file (fills people with no fund):").pack(side="left")
        self.fund_var = tk.StringVar(value=NO_FUND_CHANGE)
        self.fund_box = ttk.Combobox(fund_row, textvariable=self.fund_var, state="readonly", width=26)
        self.fund_box.pack(side="left", padx=6)
        self.fund_box.bind("<<ComboboxSelected>>", lambda e: self.show_plan())

        self.report = tk.Text(self, height=11, wrap="word", state="disabled", background="#f7f7f7")
        self.report.pack(fill="x", pady=8)
        ttk.Label(self, text="Church names in this file that look like another church (check these in "
                  "Setup > Church grouping):").pack(anchor="w")
        near_frame = ttk.Frame(self)
        near_frame.pack(fill="both", expand=True, pady=(2, 0))
        self.near_tree = ttk.Treeview(near_frame, columns=("name", "like", "people"), show="headings", height=5)
        for col, head, w in (("name", "In this file", 220), ("like", "Looks like", 220), ("people", "People", 70)):
            self.near_tree.heading(col, text=head)
            self.near_tree.column(col, width=w, anchor="w")
        vsb = ttk.Scrollbar(near_frame, orient="vertical", command=self.near_tree.yview)
        self.near_tree.configure(yscrollcommand=vsb.set)
        self.near_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        assign = ttk.Frame(self)
        assign.pack(fill="x", pady=10)
        self.assign_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(assign, text="Also subscribe everyone imported to:", variable=self.assign_var).pack(side="left")
        self.newsletter_var = tk.StringVar()
        self.newsletter_box = ttk.Combobox(assign, textvariable=self.newsletter_var, state="readonly", width=25)
        self.newsletter_box.pack(side="left", padx=6)
        ttk.Label(assign, text="Copies each:").pack(side="left", padx=(10, 2))
        self.assign_qty_var = tk.StringVar(value="1")
        ttk.Spinbox(assign, from_=1, to=999, textvariable=self.assign_qty_var, width=5).pack(side="left")
        self.import_btn = ttk.Button(self, text="Import", command=self.do_import, state="disabled")
        self.import_btn.pack(anchor="w")

    def refresh(self):
        """Reloads the fund and newsletter choices. The newsletter is never picked for you."""
        self.fund_ids = {f["name"]: f["id"] for f in funds.active_funds(self.conn)}
        self.fund_box["values"] = [NO_FUND_CHANGE] + list(self.fund_ids)
        if self.fund_var.get() not in self.fund_box["values"]:
            self.fund_var.set(NO_FUND_CHANGE)
        self.newsletter_ids = {nl["name"]: nl["id"] for nl in db.list_newsletters(self.conn)}
        self.newsletter_box["values"] = list(self.newsletter_ids)
        if self.newsletter_var.get() not in self.newsletter_ids:
            self.newsletter_var.set("")

    def _set_report(self, lines):
        self.report.config(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("end", "\n".join(lines))
        self.report.config(state="disabled")

    def choose_file(self):
        path = filedialog.askopenfilename(filetypes=[("Mailing lists", "*.csv *.xlsx *.xlsm"), ("All files", "*.*")])
        if path:
            self.load_path(path)

    def load_path(self, path):
        try:
            headers, rows = importer.load_file(path)
        except PermissionError:
            messagebox.showerror(
                "File is in use",
                f"Windows won't let this program read:\n{path}\n\n"
                "This almost always means the file is currently open in Excel (or another "
                "program). Close it there, then click Choose File... again.")
            return
        except Exception as e:
            messagebox.showerror("Could not read file", str(e))
            return
        mapping = importer.build_mapping(headers)
        if not ({"first_name", "last_name", "full_name", "church_name"} & mapping.keys()):
            messagebox.showerror("No name column found",
                                 "Couldn't find a Name / First Name / Last Name / Church Name column in this file.")
            self.import_btn.config(state="disabled")
            return
        self.path, self.headers, self.rows, self.mapping = path, headers, rows, mapping
        self.file_label.config(text=f"{os.path.basename(path)}: {len(rows)} rows")
        self.show_plan()

    def _file_fund(self):
        return self.fund_ids.get(self.fund_var.get())

    def show_plan(self):
        if not self.path:
            return
        p = importing.plan_import(self.conn, self.headers, self.rows, self.mapping, self._file_fund())
        s = p["summary"]
        lines = [f"What would happen with {os.path.basename(self.path)} (nothing has been saved):",
                 f"  New people: {s['new']}", f"  People updated: {s['updated']}",
                 f"  Already up to date: {s['unchanged']}", f"  Skipped: {s['skipped']}"]
        lines += [f"      {n}  {reason}" for reason, n in s["skip_reasons"].items()]
        lines.append(f"  Linked to a church already on file: {s['linked_to_church']}")
        lines.append(f"  Church names not on file yet: {s['unmatched_churches']}")
        if s["unknown_funds"]:
            lines.append("  Fund names not recognised (these rows get no fund): "
                         + ", ".join(f"{k} ({v})" for k, v in s["unknown_funds"].items()))
        if s["unreadable_rows"]:
            lines.append(f"  Rows with unreadable characters (the file may not be saved as UTF-8): {s['unreadable_rows']}")
        if s["churches_changing_fund"]:
            lines.append(f"  Churches that would change fund because a rep's row has a different Fund: "
                         f"{s['churches_changing_fund']}")
        lines.append("  Columns that will be ignored: " + (", ".join(map(str, p["ignored_columns"])) or "none"))
        self._set_report(lines)
        self.near_tree.delete(*self.near_tree.get_children())
        for n in p["near_churches"]:
            self.near_tree.insert("", "end", values=(n["name"], n["like"], n["members"]))
        self.import_btn.config(state="normal" if s["new"] or s["updated"] else "disabled")

    def do_import(self):
        subscribe = self.assign_var.get()
        newsletter_id = self.newsletter_ids.get(self.newsletter_var.get())
        if subscribe and newsletter_id is None:
            messagebox.showwarning("Choose a newsletter", "You ticked the subscribe box. Choose which newsletter "
                                   "to use, or untick the box.")
            return
        try:
            qty = max(1, int(self.assign_qty_var.get()))
        except ValueError:
            qty = 1
        if not messagebox.askyesno("Import", "Import this file now? A backup is made first and you can restore "
                                   "it from the File menu."):
            return
        try:
            s = importing.apply_import(self.conn, self.headers, self.rows, self.mapping, self.path,
                                       self._file_fund(), subscribe, newsletter_id, qty)
        except (importing.ImportError_, ReadOnlyError, ConflictError) as e:
            messagebox.showwarning("Import", str(e))
            return
        except Exception as e:
            messagebox.showerror("Import failed", f"Nothing was changed.\n\n{e}")
            return
        messagebox.showinfo("Import complete", f"Added {s['new']} new, updated {s['updated']}, "
                            f"{s['unchanged']} already up to date, skipped {s['skipped']}.")
        self.path = None
        self.import_btn.config(state="disabled")
        self.file_label.config(text="")
        self._set_report([])
        self.near_tree.delete(*self.near_tree.get_children())
        if self.on_imported:
            self.on_imported()
