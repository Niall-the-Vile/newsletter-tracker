import tkinter as tk
from tkinter import ttk

from services import mailings
from ui.chrome import Chrome
from ui.import_tab import ImportTab
from ui.mailings_tab import MailingsTab
from ui.people_tab import PeopleTab
from ui.setup_tab import SetupTab


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
        self.card = ttk.Label(find_bar, text="", font=("", 10, "bold"))
        self.card.pack(side="left", padx=12)

        self.notebook = notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.people_tab = PeopleTab(notebook, conn, self.find_var)
        self.mailings_tab = MailingsTab(notebook, conn, on_changed=self.people_tab.refresh)
        self.setup_tab = SetupTab(notebook, conn, on_changed=self.people_tab.refresh)
        self.import_tab = ImportTab(self.setup_tab, conn, on_imported=self.people_tab.refresh)
        self.setup_tab.insert(4, self.import_tab, text="Import")

        notebook.add(self.people_tab, text="People & Churches")
        notebook.add(self.mailings_tab, text="Mailings")
        notebook.add(self.setup_tab, text="Setup")
        notebook.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        self.find_var.trace_add("write", lambda *a: notebook.select(self.people_tab))
        self.find_var.trace_add("write", lambda *a: self.card.config(
            text=mailings.quick_answer(conn, self.find_var.get()) or ""))

    def _tab_changed(self):
        tab = self.nametowidget(self.notebook.select())
        if tab is self.mailings_tab:
            tab.refresh()
        elif tab is self.setup_tab:
            tab.refresh()
        elif tab is self.people_tab:
            tab.refresh()
