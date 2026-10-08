import tkinter as tk
from tkinter import messagebox, ttk

import db

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
        person_id = self.person["id"] if self.person else None
        if person_id is None:
            data["active"] = 1
        db.upsert_person(self.conn, data, person_id)
        self.result = True
        self.destroy()
