"""Setup > Bounces: email addresses that bounced, a manual Mark bounced and a paste-in list."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from data.connection import ConflictError, ReadOnlyError
from services import bounces


class PasteDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Paste in bounced addresses")
        self.result = None
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Paste the bounced email addresses, one per line or separated by commas:").pack(anchor="w")
        self.text = tk.Text(frame, width=60, height=12)
        self.text.pack(fill="both", expand=True, pady=6)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="These are:").pack(side="left")
        self.kind_var = tk.StringVar(value="Hard")
        ttk.Combobox(row, textvariable=self.kind_var, values=["Hard", "Soft"], state="readonly",
                     width=6).pack(side="left", padx=4)
        ttk.Label(row, text="(Hard: address does not exist. Soft: mailbox full or away; skipped after 3 in a row.)",
                  foreground="#666666").pack(side="left")
        btns = ttk.Frame(frame)
        btns.pack(anchor="e", pady=(8, 0))
        ttk.Button(btns, text="Add", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
        self.transient(master)
        self.grab_set()

    def ok(self):
        self.result = (self.text.get("1.0", "end"), self.kind_var.get())
        self.destroy()


class BouncesPanel(ttk.Frame):
    def __init__(self, master, conn):
        super().__init__(master, padding=8)
        self.conn = conn
        ttk.Label(self, text="A bounce belongs to the email address. Once the address is corrected on the person, "
                  "email goes to them again.", wraplength=800, justify="left").pack(anchor="w", pady=(0, 6))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        cols = ("email", "kind", "times", "status", "who", "last")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for col, head, w in zip(cols, ("Email address", "Type", "Times", "Email is", "People with it now", "Last"),
                                (260, 70, 60, 150, 120, 90)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        row = ttk.Frame(self)
        row.pack(anchor="w", pady=6)
        for text, cmd in (("Mark bounced...", self.mark), ("Paste in a list...", self.paste),
                          ("Clear bounce", self.clear)):
            ttk.Button(row, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for b in bounces.list_bounces(self.conn):
            status = "Skipped" if b["skipped"] else f"Watching ({b['soft']} of {bounces.SOFT_LIMIT} soft)"
            self.tree.insert("", "end", iid=b["key"], values=(
                b["email"], b["kind"], b["count"], status, b["holders"] or "Nobody now", b["last"]))

    def _try(self, fn, *args):
        try:
            return fn(self.conn, *args)
        except (bounces.BounceError, ReadOnlyError, ConflictError) as e:
            messagebox.showwarning("Bounces", str(e), parent=self)
        finally:
            self.refresh()

    def mark(self):
        email = simpledialog.askstring("Mark bounced", "Email address that bounced:", parent=self)
        if email:
            self._try(bounces.mark_bounced, email.strip(), "Manual")

    def paste(self):
        dlg = PasteDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            result = self._try(bounces.paste_in, *dlg.result)
            if result:
                messagebox.showinfo("Bounces", f"{result['recorded']} addresses recorded"
                                    + (f", {result['ignored']} did not look like email addresses." if result["ignored"]
                                       else "."), parent=self)

    def clear(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Bounces", "Choose an address in the list first.", parent=self)
            return
        if messagebox.askyesno("Clear bounce", f"Email {self.tree.item(sel[0], 'values')[0]} again?", parent=self):
            self._try(bounces.clear, self.tree.item(sel[0], "values")[0])
