import tkinter as tk
from tkinter import ttk

import config
from services import mailings
from ui.chrome import Chrome
from ui.health import GetStartedDialog, HealthBanner
from ui.import_tab import ImportTab
from ui.mailings_tab import MailingsTab
from ui.people_tab import PeopleTab
from ui.setup_tab import SetupTab
from version import APP_VERSION

SEARCH_DELAY_MS = 250
SEEN_GET_STARTED = "get_started_shown"


class App(ttk.Frame):
    def __init__(self, master, session):
        super().__init__(master)
        conn = self.conn = session.conn
        master.title(f"Newsletter Tracker {APP_VERSION} — {session.db_path}")
        master.geometry("1200x700")
        self.pack(fill="both", expand=True)
        self._card_after = self._first_run_after = None
        self.get_started = None
        self.chrome = Chrome(master, self, session, self.open_get_started)

        find_bar = ttk.Frame(self)
        find_bar.pack(fill="x", padx=6, pady=(4, 0))
        ttk.Label(find_bar, text="Find:").pack(side="left")
        self.find_var = tk.StringVar()
        ttk.Entry(find_bar, textvariable=self.find_var, width=40).pack(side="left", padx=4)
        ttk.Button(find_bar, text="Clear", command=lambda: self.find_var.set("")).pack(side="left")
        self.card = ttk.Label(find_bar, text="", font=("", 10, "bold"))
        self.card.pack(side="left", padx=12)
        self.health = HealthBanner(self, conn, self.show_list, before=find_bar)

        self.notebook = notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.people_tab = PeopleTab(notebook, conn, self.find_var)
        self.mailings_tab = MailingsTab(notebook, conn, on_changed=self.changed)
        self.setup_tab = SetupTab(notebook, conn, on_changed=self.changed)
        self.import_tab = ImportTab(self.setup_tab, conn, on_imported=self.changed)
        self.setup_tab.insert(4, self.import_tab, text="Import")

        notebook.add(self.people_tab, text="People & Churches")
        notebook.add(self.mailings_tab, text="Mailings")
        notebook.add(self.setup_tab, text="Setup")
        notebook.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        self.find_var.trace_add("write", lambda *a: notebook.select(self.people_tab))
        self.find_var.trace_add("write", lambda *a: self._card_soon())
        if not session.read_only and not config.get_flag(SEEN_GET_STARTED):
            self._first_run_after = self.after(400, self._first_run_checklist)

    def destroy(self):
        for job in (self._card_after, self._first_run_after):
            if job:
                self.after_cancel(job)
        super().destroy()

    def _card_soon(self):
        if self._card_after:
            self.after_cancel(self._card_after)
        self._card_after = self.after(SEARCH_DELAY_MS, self._show_card)

    def _show_card(self):
        self._card_after = None
        if self.winfo_exists():
            self.card.config(text=mailings.quick_answer(self.conn, self.find_var.get()) or "")

    def changed(self):
        self.people_tab.refresh()
        self.health.refresh()

    def _tab_changed(self):
        self.nametowidget(self.notebook.select()).refresh()
        self.health.refresh()

    def show_list(self, kind):
        """Banner buttons: open People & Churches on the ones that need attention."""
        tab = self.people_tab
        tab.type_var.set("Church" if kind == "churches" else "All")
        tab.attention_var.set(True)
        self.notebook.select(tab)
        tab.refresh()

    def _first_run_checklist(self):
        if self.winfo_exists():
            config.set_flag(SEEN_GET_STARTED)
            self.open_get_started()

    def open_get_started(self):
        if self.get_started is not None and self.get_started.winfo_exists():
            self.get_started.lift()
            return
        self.get_started = GetStartedDialog(self.winfo_toplevel(), self.conn, self.go_to)

    def go_to(self, where):
        if where == "Churches":
            self.show_list("churches")
        elif where == "People":
            self.notebook.select(self.people_tab)
        elif where == "Mailings":
            self.notebook.select(self.mailings_tab)
        else:
            self.notebook.select(self.setup_tab)
            self.setup_tab.select(self.import_tab if where == "Import" else self.setup_tab.grouping_panel)
