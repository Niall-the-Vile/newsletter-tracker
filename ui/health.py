"""The data-health banner above the tabs, and the Get started checklist."""
import tkinter as tk
from tkinter import ttk

from services import health

BANNER_BG = "#e8f1fb"
TICK, BOX = "✔", "☐"


class HealthBanner(tk.Frame):
    """One line saying what in the data needs fixing, with buttons that open the matching list."""

    def __init__(self, master, conn, show_list, before):
        super().__init__(master, bg=BANNER_BG)
        self.conn, self.show_list, self.before = conn, show_list, before
        self.hidden = False
        self.label = tk.Label(self, bg=BANNER_BG, anchor="w", justify="left", wraplength=800)
        self.label.pack(side="left", fill="x", expand=True, padx=8, pady=4)
        ttk.Button(self, text="Hide", command=self.hide).pack(side="right", padx=8)
        self.churches_btn = ttk.Button(self, text="Show churches", command=lambda: show_list("churches"))
        self.churches_btn.pack(side="right")
        self.people_btn = ttk.Button(self, text="Show people", command=lambda: show_list("people"))
        self.people_btn.pack(side="right", padx=4)
        self.refresh()

    def hide(self):
        self.hidden = True
        self.refresh()

    def refresh(self):
        issues = health.health_issues(self.conn)
        if self.hidden or not issues:
            self.pack_forget()
            return
        self.label.config(text="Worth checking: " + "; ".join(i["text"] for i in issues) + ".")
        shown = {i["show"] for i in issues}
        for kind, btn in (("people", self.people_btn), ("churches", self.churches_btn)):
            btn.state(["!disabled"] if kind in shown else ["disabled"])
        self.pack(fill="x", before=self.before)


class GetStartedDialog(tk.Toplevel):
    """The steps for setting the program up, ticked off from what is already in the data."""

    def __init__(self, master, conn, go):
        super().__init__(master)
        self.conn, self.go = conn, go
        self.title("Get started")
        self.resizable(False, False)
        self.frame = ttk.Frame(self, padding=14)
        self.frame.pack(fill="both", expand=True)
        self.bind("<FocusIn>", lambda e: e.widget is self and self.refresh())
        self.refresh()

    def refresh(self):
        for w in self.frame.winfo_children():
            w.destroy()
        ttk.Label(self.frame, text="Get started", font=("", 12, "bold")).grid(row=0, column=0, columnspan=3,
                                                                              sticky="w")
        ttk.Label(self.frame, text="Do these in order. Each one ticks itself off when it is done.").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        for i, step in enumerate(health.checklist(self.conn), start=2):
            ttk.Label(self.frame, text=TICK if step["done"] else BOX, width=3).grid(row=i, column=0, pady=3)
            ttk.Label(self.frame, text=step["text"]).grid(row=i, column=1, sticky="w", padx=(0, 16))
            if not step["done"]:
                ttk.Button(self.frame, text="Go", command=lambda g=step["go"]: self.go(g)).grid(row=i, column=2)
        ttk.Button(self.frame, text="Close", command=self.destroy).grid(row=20, column=2, pady=(12, 0))
