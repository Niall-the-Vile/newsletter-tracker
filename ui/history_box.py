"""The History list in a detail pane. It is only read from the data file when the History tab is opened."""
import tkinter as tk
from tkinter import ttk

NO_HISTORY_YET = "History starts with the first mailing made in this program."


class HistoryBox(ttk.Frame):
    def __init__(self, tabs, height=6):
        super().__init__(tabs, padding=4)
        self.tabs = tabs
        self.fetch = None
        self.listbox = tk.Listbox(self, height=height, activestyle="none")
        self.listbox.pack(fill="both", expand=True, pady=(2, 4))
        self.note = ttk.Label(self, text="", foreground="#666666", wraplength=300)
        self.note.pack(anchor="w")
        tabs.add(self, text="History")
        tabs.bind("<<NotebookTabChanged>>", lambda e: self._load_if_open(), add="+")

    def show(self, fetch):
        """fetch() returns (items, history_starts), or pass None to leave the list empty."""
        self.fetch = fetch
        self.listbox.delete(0, "end")
        self.note.config(text="")
        self._load_if_open()

    def _load_if_open(self):
        if self.fetch is None or self.tabs.select() != str(self):
            return
        items, starts = self.fetch()
        self.fetch = None
        for h in items:
            self.listbox.insert("end", f"{h['ts'][:10]}  {h['summary']}")
        self.note.config(text=f"History starts {starts}." if starts else NO_HISTORY_YET)
