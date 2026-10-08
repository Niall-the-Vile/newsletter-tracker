"""Mailings tab: past mailings, open one, save its file again, and the 3-step Create Mailing flow."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import db
import exporter
from data.connection import ConflictError, ReadOnlyError
from services import backup, mailings

CSV_TYPES = [("CSV files", "*.csv")]


class SkippedDialog(tk.Toplevel):
    def __init__(self, master, skipped):
        super().__init__(master)
        self.title("Left out of this mailing")
        tree = ttk.Treeview(self, columns=("name", "kind", "reason", "copies"), show="headings", height=14)
        for col, head, w in (("name", "Name", 200), ("kind", "Type", 70), ("reason", "Why", 220), ("copies", "Copies", 60)):
            tree.heading(col, text=head)
            tree.column(col, width=w, anchor="w")
        for s in skipped:
            tree.insert("", "end", values=(s["name"], s["kind"].title(), s["reason"], s["copies"]))
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(self, text="Close", command=self.destroy).pack(pady=(0, 8))
        self.transient(master)


class CreateMailingDialog(tk.Toplevel):
    """Step 1: newsletter and issue label. Step 2: summary and checks. Step 3: save the sheet."""

    def __init__(self, master, conn, on_done):
        super().__init__(master)
        self.conn = conn
        self.on_done = on_done
        self.title("Create mailing")
        self.geometry("620x460")
        self.check = None
        self.allow_duplicate = False
        self.options = {n["name"]: n["id"] for n in db.list_newsletters(conn)}
        self.news_var = tk.StringVar(value=next(iter(self.options), ""))
        self.label_var = tk.StringVar()
        self.steps = [self._step1(), self._step2(), self._step3()]
        self.current = None
        self.show_step(0)
        self.transient(master)
        self.grab_set()

    def _frame(self, title):
        f = ttk.Frame(self, padding=14)
        ttk.Label(f, text=title, font=("", 11, "bold")).pack(anchor="w", pady=(0, 8))
        return f

    def _step1(self):
        f = self._frame("Step 1 of 3: which newsletter and issue?")
        ttk.Label(f, text="Newsletter:").pack(anchor="w")
        ttk.Combobox(f, textvariable=self.news_var, values=list(self.options), state="readonly",
                     width=36).pack(anchor="w", pady=(0, 8))
        ttk.Label(f, text="Issue label (for example Fall 2026):").pack(anchor="w")
        ttk.Entry(f, textvariable=self.label_var, width=40).pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=8)
        self.next1 = ttk.Button(row, text="Next", command=self.to_summary)
        self.next1.pack(side="left", padx=(0, 4))
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")
        return f

    def _step2(self):
        f = self._frame("Step 2 of 3: check the mailing")
        self.summary = ttk.Label(f, text="", justify="left", wraplength=560)
        self.summary.pack(anchor="w")
        self.problems = tk.Text(f, height=10, width=70, wrap="word", state="disabled")
        self.problems.tag_configure("red", foreground="#b00020")
        self.problems.tag_configure("yellow", foreground="#8a6d00")
        self.problems.pack(fill="both", expand=True, pady=8)
        row = ttk.Frame(f)
        row.pack(anchor="w")
        ttk.Button(row, text="Back", command=lambda: self.show_step(0)).pack(side="left", padx=(0, 4))
        self.show_skipped_btn = ttk.Button(row, text="Show skipped", command=self.show_skipped)
        self.show_skipped_btn.pack(side="left", padx=4)
        self.next2 = ttk.Button(row, text="Next", command=lambda: self.show_step(2))
        self.next2.pack(side="left", padx=4)
        return f

    def _step3(self):
        f = self._frame("Step 3 of 3: save the sheet")
        ttk.Label(f, text="Choose where to save the file. The mailing is only recorded once the file has saved.",
                  wraplength=560, justify="left").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(anchor="w", pady=10)
        ttk.Button(row, text="Back", command=lambda: self.show_step(1)).pack(side="left", padx=(0, 4))
        ttk.Button(row, text="Save the sheet...", command=self.choose_and_save).pack(side="left", padx=4)
        return f

    def show_step(self, index):
        if self.current is not None:
            self.steps[self.current].pack_forget()
        self.current = index
        self.steps[index].pack(fill="both", expand=True)

    def newsletter_id(self):
        return self.options.get(self.news_var.get())

    def to_summary(self):
        nid, label = self.newsletter_id(), self.label_var.get().strip()
        if nid is None or not label:
            messagebox.showinfo("Create mailing", "Choose a newsletter and enter an issue label.", parent=self)
            return
        dup = mailings.same_label_runs(self.conn, nid, label)
        if dup and not self.allow_duplicate:
            when = ", ".join(d["sent_on"] for d in dup)
            if not messagebox.askyesno("Already made", f"'{label}' was already made on {when}. Make another?",
                                       parent=self):
                return
            self.allow_duplicate = True
        self.check = mailings.preflight(self.conn, nid)
        self.summary.config(text=mailings.summary_text(self.check))
        self.problems.config(state="normal")
        self.problems.delete("1.0", "end")
        for tag, items, head in (("red", self.check["red"], "Fix before saving"),
                                 ("yellow", self.check["yellow"], "Worth a look")):
            if items:
                self.problems.insert("end", head + "\n", tag)
                for i in items:
                    self.problems.insert("end", f"  {i['who']}: {i['text']}\n", tag)
        d = self.check["diff"]
        for head, names in (("Added", d["added"]), ("Removed", d["removed"])):
            if names:
                self.problems.insert("end", f"{head} since the last issue: {', '.join(names[:30])}\n")
        if not self.problems.get("1.0", "end").strip():
            self.problems.insert("end", "No problems found.")
        self.problems.config(state="disabled")
        self.show_skipped_btn.state(["!disabled"] if self.check["skipped"] else ["disabled"])
        self.next2.state(["disabled"] if self.check["blocked"] or not self.check["rows"] else ["!disabled"])
        self.show_step(1)

    def show_skipped(self):
        SkippedDialog(self, self.check["skipped"])

    def choose_and_save(self):
        name = f"{self.news_var.get()} {self.label_var.get().strip()}.csv"
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=name, filetypes=CSV_TYPES)
        if path:
            self.save_to(path)

    def save_to(self, path):
        try:
            mailings.create_mailing(self.conn, self.newsletter_id(), self.label_var.get(), path, True)
        except mailings.PreflightBlocked as e:
            messagebox.showwarning("Not saved", str(e), parent=self)
        except (mailings.MailingError, ReadOnlyError, ConflictError, backup.BackupError) as e:
            messagebox.showwarning("Not saved", str(e), parent=self)
        except OSError as e:
            messagebox.showerror("Not saved", f"The file could not be saved, so no mailing was recorded.\n\n({e})",
                                 parent=self)
        else:
            messagebox.showinfo("Saved", f"Saved the sheet and recorded the mailing:\n{path}", parent=self)
            self.on_done()
            self.destroy()


class RunDialog(tk.Toplevel):
    """A saved mailing: its rows, the packing sheet, and a button to save the exact file again."""

    def __init__(self, master, conn, run_id):
        super().__init__(master)
        self.conn = conn
        self.run_id = run_id
        run = mailings.get_run(conn, run_id)
        self.title(f"{run['newsletter_name']} - {run['issue_label']}")
        self.geometry("860x520")
        ttk.Label(self, text=f"Sent {run['sent_on']} by {run['user'] or ''}: {run['row_count']} addresses, "
                  f"{run['copy_count']} copies, {run['skipped_count']} left out.", padding=8).pack(anchor="w")
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=8)
        rows = ttk.Frame(tabs)
        cols = ("name", "church", "place", "copies", "delivery")
        self.tree = ttk.Treeview(rows, columns=cols, show="headings")
        for col, head, w in zip(cols, ("Addressed to", "Church", "Address", "Copies", "Sent by"),
                                (160, 160, 280, 60, 70)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        vsb = ttk.Scrollbar(rows, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for r in mailings.run_rows(conn, run_id):
            place = ", ".join(filter(None, [r["address1"], r["city"], r["state"], r["zip"]]))
            self.tree.insert("", "end", values=(r["recipient_name"] or "", r["church_name"] or "", place, r["quantity"],
                                                exporter.PLAIN_DELIVERY.get(r["delivery"], r["delivery"] or "")))
        self.sheet_text = tk.Text(tabs, wrap="none", state="normal")
        self.sheet = mailings.packing_sheet(conn, run_id)
        self.sheet_text.insert("end", self._sheet_text())
        self.sheet_text.config(state="disabled")
        tabs.add(rows, text="Addresses")
        tabs.add(self.sheet_text, text="Packing sheet")
        row = ttk.Frame(self, padding=8)
        row.pack(fill="x")
        ttk.Button(row, text="Save the file again...", command=self.save_again).pack(side="left", padx=(0, 4))
        ttk.Button(row, text="Save packing sheet...", command=self.save_sheet).pack(side="left", padx=4)
        ttk.Button(row, text="Close", command=self.destroy).pack(side="right")
        self.transient(master)

    def _sheet_text(self):
        lines = []
        for g in self.sheet["groups"]:
            lines.append(f"{g['state']}  ({g['copies']} copies)")
            for b in g["bundles"]:
                lines.append(f"    {b['copies']:>4}  {b['church'] or '-':<32} {b['who']:<26} {b['city']}")
            lines.append("")
        lines.append(f"Issue total: {self.sheet['bundles']} bundles, {self.sheet['copies']} copies")
        return "\n".join(lines)

    def save_again(self):
        run = mailings.get_run(self.conn, self.run_id)
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=CSV_TYPES,
                                            initialfile=f"{run['newsletter_name']} {run['issue_label']}.csv")
        if path:
            self._save(lambda: mailings.export_run(self.conn, self.run_id, path), path)

    def save_sheet(self):
        run = mailings.get_run(self.conn, self.run_id)
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=CSV_TYPES,
                                            initialfile=f"{run['newsletter_name']} {run['issue_label']} packing.csv")
        if path:
            self._save(lambda: mailings.write_packing_csv(path, self.sheet), path)

    def _save(self, action, path):
        try:
            action()
        except (OSError, ReadOnlyError, ConflictError) as e:
            messagebox.showerror("Not saved", str(e), parent=self)
            return
        messagebox.showinfo("Saved", f"Saved:\n{path}", parent=self)


class MailingsTab(ttk.Frame):
    def __init__(self, master, conn, on_changed=None):
        super().__init__(master, padding=8)
        self.conn = conn
        self.on_changed = on_changed
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Create mailing...", command=self.create).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Open", command=self.open_run).pack(side="left", padx=4)
        self.note = ttk.Label(bar, text="", foreground="#666666")
        self.note.pack(side="right")
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        cols = ("sent", "news", "issue", "rows", "copies", "left_out", "by")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for col, head, w in zip(cols, ("Sent", "Newsletter", "Issue", "Addresses", "Copies", "Left out", "By"),
                                (90, 160, 140, 80, 70, 70, 100)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.open_run())
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in mailings.list_runs(self.conn):
            self.tree.insert("", "end", iid=str(r["id"]), values=(
                r["sent_on"], r["newsletter_name"], r["issue_label"], r["row_count"], r["copy_count"],
                r["skipped_count"], r["user"] or ""))
        starts = mailings.history_starts(self.conn)
        self.note.config(text=f"History starts {starts}." if starts
                         else "History starts with the first mailing made in this program.")

    def create(self):
        if not db.list_newsletters(self.conn):
            messagebox.showinfo("Create mailing", "Add a newsletter in Setup first.")
            return
        if self.conn.read_only:
            messagebox.showinfo("Create mailing", "This copy is open for viewing only, so it cannot make a mailing.")
            return
        dlg = CreateMailingDialog(self, self.conn, self._done)
        self.wait_window(dlg)

    def _done(self):
        self.refresh()
        if self.on_changed:
            self.on_changed()

    def open_run(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Open", "Choose a mailing in the list first.")
            return
        RunDialog(self, self.conn, int(sel[0]))
