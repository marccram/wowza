"""WoWZA companion: answers questions copied out of WoW and hands answers back via the clipboard."""
import queue
import threading
import time
import tkinter as tk
import webbrowser
import winsound
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from bridge import (SYSTEM_PROMPT, History, addon_dir, addon_installed, answer_payload, build_messages,
                    ensure_transport_files, install_addon, old_addon_installed, parse_question, raise_signal,
                    reset_signals, wow_running, write_slots)
import questdata
import shortcuts
from providers import ProviderError, provider_label, stream_answer
from settings import (FIELD_LABELS, PROVIDERS, clone, is_wow_dir, load_settings, migrate_app_dir,
                      pick_wow_dir, save_settings)

CLIPBOARD_POLL_MS = 250
PARTIAL_UPDATE_SECONDS = 0.4
SHOW_ON_START = 10
SLOT_ANSWERS = 5  # latest answers kept in the slot files


class App:
    def __init__(self):
        self.settings = load_settings()
        self.history = History()
        self.jobs = queue.Queue()
        self.events = queue.Queue()
        self.seen_ids = self.history.ids()
        self.active = None          # {"id", "q", "text"} for the answer being streamed
        self.last_written = None    # last payload this app put on the clipboard
        self.last_answer = None     # final payload of the most recent answer
        self.last_partial_write = 0.0
        self.slot_answers = []      # latest answers, as written into the slot addons
        self.restart_needed = False # slot files were just created; WoW only sees them after a restart
        self._prepare_transport()

        self.root = tk.Tk()
        self.root.title("WoWZA")
        self.root.geometry("440x560")
        self.root.minsize(320, 300)
        self._build_ui()
        self._apply_window_settings()
        self._show_recent()
        self._refresh_status()

        self.questdata = questdata.QuestData()
        threading.Thread(target=self.questdata.load, daemon=True).start()
        threading.Thread(target=self._work, daemon=True).start()
        self.root.after(50, self._drain)
        self.root.after(CLIPBOARD_POLL_MS, self._poll_clipboard)
        if not is_wow_dir(self.settings["wow_dir"]) or not addon_installed(self.settings["wow_dir"]):
            self.root.after(300, self.open_settings)
        elif not questdata.downloaded() and not self.settings.get("questdata_asked"):
            self.root.after(500, self._offer_questdata)

    # --- quest data (QuestieDB) -------------------------------------------------------
    def _offer_questdata(self):
        self.settings["questdata_asked"] = True
        save_settings(self.settings)
        if messagebox.askyesno(
                "Download quest data?",
                "WoWZA can use QuestieDB's quest, mob, drop-rate and location data for WoW "
                f"Forever, so answers include real coordinates instead of guesses.\n\nDownload it now? "
                f"About {questdata.DOWNLOAD_MB} MB from github.com/Questie/QuestieDB, stored only on this PC. "
                "You can do this later in Settings.", parent=self.root):
            self.download_questdata()

    def download_questdata(self, done=None):
        def run():
            try:
                questdata.download(progress=lambda msg: self.post("status", msg))
                self.questdata = questdata.QuestData()
                ok = self.questdata.load()
                self.post("status", "Quest data ready" if ok else f"Quest data error: {self.questdata.error}")
            except Exception as e:
                self.post("status", f"Quest data download failed: {e}")
            if done:
                self.root.after(0, done)
        threading.Thread(target=run, daemon=True).start()

    def questdata_status(self):
        if self.questdata.ready:
            return "✓ Quest data ready (QuestieDB)"
        if self.questdata.error:
            return f"✗ Quest data error: {self.questdata.error}"
        if questdata.downloaded():
            return "Loading quest data..."
        return "Quest data not downloaded"

    # --- UI -----------------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        self.status = ttk.Label(top, text="", foreground="#666")
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="Settings", command=self.open_settings).pack(side="right")
        self.pin_var = tk.BooleanVar(value=self.settings["always_on_top"])
        ttk.Checkbutton(top, text="On top", variable=self.pin_var,
                        command=self._toggle_pin).pack(side="right", padx=6)

        body = ttk.Frame(self.root, padding=(8, 0))
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", state="disabled", relief="flat", padx=8, pady=6,
                            font=("Segoe UI", 10), background="#1b1b1f", foreground="#e8e6e3",
                            insertbackground="#e8e6e3")
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("you", foreground="#66ccff", font=("Segoe UI", 10, "bold"))
        self.text.tag_configure("claude", foreground="#ffd100", font=("Segoe UI", 10, "bold"))
        self.text.tag_configure("muted", foreground="#8a8a8a")
        self.text.tag_configure("error", foreground="#ff6b6b")

        bottom = ttk.Frame(self.root, padding=8)
        bottom.pack(fill="x")
        self.entry = ttk.Entry(bottom)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _: self.ask_from_pc())
        ttk.Button(bottom, text="Ask", command=self.ask_from_pc).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="Copy last answer for WoW",
                   command=self.copy_last_answer).pack(side="left", padx=(6, 0))

    def _append(self, text, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", text, tag)
        self.text.configure(state="disabled")
        self.text.see("end")

    def _show_recent(self):
        self._append("In WoW: open WoWZA (minimap button or /za), type a question, press Enter, "
                     "then Ctrl+C. The answer appears in the WoW window by itself.\n\n", "muted")
        for h in self.history.snapshot()[-SHOW_ON_START:]:
            self._append("You: ", "you")
            self._append(h["q"] + "\n")
            self._append("WoWZA: ", "claude")
            self._append(h["a"] + "\n\n")

    def _refresh_status(self):
        wow = self.settings["wow_dir"]
        if not is_wow_dir(wow):
            msg = "Set your WoW folder in Settings"
        elif not addon_installed(wow):
            msg = "Addon not installed: see Settings"
        elif self.restart_needed:
            msg = "Fully restart WoW once to get answers automatically"
        else:
            msg = f"Ready · {provider_label(self.settings)}"
        self.status.configure(text=msg)

    def _prepare_transport(self):
        """Make sure the slot addons and signal files exist, and start with every signal cleared."""
        wow = self.settings["wow_dir"]
        try:
            if old_addon_installed(wow):  # first run after the rename: switch the game over to WoWZA
                install_addon(wow)
                self.restart_needed = True
            if not addon_installed(wow):
                return
            self.restart_needed = ensure_transport_files(wow) > 0 or self.restart_needed
            reset_signals(wow)
        except OSError:
            pass

    def _signal(self, kind, sig):
        if sig and addon_installed(self.settings["wow_dir"]):
            try:
                raise_signal(self.settings["wow_dir"], kind, sig)
            except OSError:
                pass

    def _deliver_to_slots(self, entry):
        """Put a finished answer into the slot addons, then tell the game it's there."""
        wow = self.settings["wow_dir"]
        if not addon_installed(wow):
            return
        self.slot_answers = [a for a in self.slot_answers if a["id"] != entry["id"]][-(SLOT_ANSWERS - 1):]
        self.slot_answers.append(entry)
        try:
            write_slots(wow, self.slot_answers)
        except OSError as e:
            self._append(f"(Couldn't write the answer for WoW: {e})\n", "error")
            return
        self._signal("sig", entry.get("sig"))

    def _apply_window_settings(self):
        self.pin_var.set(self.settings["always_on_top"])
        self.root.attributes("-topmost", self.settings["always_on_top"])

    def _toggle_pin(self):
        self.settings["always_on_top"] = self.pin_var.get()
        save_settings(self.settings)
        self._apply_window_settings()

    def _pop_up(self):
        if not self.settings["popup_on_answer"]:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        if not self.settings["always_on_top"]:
            self.root.after(300, lambda: self.root.attributes("-topmost", False))

    def _chime(self):
        if self.settings["play_sound"]:
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)

    def open_settings(self):
        SettingsDialog(self)

    def apply_settings(self, new):
        folder_changed = new["wow_dir"] != self.settings["wow_dir"]
        self.settings = new
        save_settings(new)
        if folder_changed:
            self._prepare_transport()
        self._apply_window_settings()
        self._refresh_status()

    def ask_from_pc(self):
        q = self.entry.get().strip()
        if not q:
            return
        self.entry.delete(0, "end")
        self.jobs.put({"id": f"pc-{int(time.time() * 1000)}", "q": q, "ctx": None})

    # --- clipboard -------------------------------------------------------------
    def _read_clipboard(self):
        try:
            return self.root.clipboard_get()
        except tk.TclError:  # empty, or not text
            return None

    def _write_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.last_written = text

    def _owns_clipboard(self):
        """True if overwriting the clipboard won't clobber something the player copied."""
        current = self._read_clipboard()
        if current is None or current == self.last_written:
            return True
        q = parse_question(current)
        return q is not None and q["id"] in self.seen_ids

    def _poll_clipboard(self):
        q = parse_question(self._read_clipboard())
        if q and q["id"] not in self.seen_ids:
            self.seen_ids.add(q["id"])
            if q["ctx"]:
                self.history.set_ctx(q["ctx"])
            # Acknowledge right away so the game shows "thinking": a signal when those work, and a
            # "pending" entry in the slots for when the game has to poll.
            self._deliver_to_slots({"id": q["id"], "status": "pending", "q": q["q"], "a": "", "sig": None})
            self._signal("ack", q["sig"])
            self._write_clipboard(answer_payload(q["id"], "pending", "", q["q"]))
            self.jobs.put(q)
        self.root.after(CLIPBOARD_POLL_MS, self._poll_clipboard)

    def copy_last_answer(self):
        if self.last_answer:
            self._write_clipboard(self.last_answer)
            self.status.configure(text="Copied. Press Ctrl+V in the WoW window.")

    # --- events from the worker thread ----------------------------------------
    def post(self, kind, data=None):
        self.events.put((kind, data))

    def _drain(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "question":
                    self.active = {"id": data["id"], "q": data["q"], "sig": data.get("sig"), "text": "",
                                   "started": time.time()}
                    self._append("You: ", "you")
                    self._append(data["q"] + "\n")
                    self._append("WoWZA: ", "claude")
                elif kind == "status":
                    self.status.configure(text=data)
                elif kind == "chunk":
                    self.active["text"] += data
                    self._append(data)
                    if time.time() - self.last_partial_write > PARTIAL_UPDATE_SECONDS and self._owns_clipboard():
                        self._write_clipboard(answer_payload(self.active["id"], "partial",
                                                             self.active["text"], self.active["q"]))
                        self.last_partial_write = time.time()
                elif kind == "finished":
                    status, answer = data
                    if status == "error":
                        self._append(f"[{answer}]", "error")
                    self._deliver_to_slots({"id": self.active["id"], "status": status, "q": self.active["q"],
                                            "a": answer, "sig": self.active["sig"],
                                            "took": time.time() - self.active["started"]})
                    self.last_answer = answer_payload(self.active["id"], status, answer, self.active["q"])
                    if self._owns_clipboard():  # fallback for when the slots can't be used
                        self._write_clipboard(self.last_answer)
                    self._append("\n(Sent to WoW)\n\n", "muted")
                    self.active = None
                    self._chime()
                    self._pop_up()
        except queue.Empty:
            pass
        self.root.after(50, self._drain)

    def _work(self):
        while True:
            entry = self.jobs.get()
            question = entry["q"]
            ctx = entry.get("ctx") or self.history.last_ctx
            self.post("question", {"id": entry["id"], "q": question, "sig": entry.get("sig")})
            facts = None
            try:
                facts = self.questdata.facts_for(question, ctx)
            except Exception:
                pass  # answer without game data rather than not at all
            messages = build_messages(self.history.snapshot(), question, ctx, facts)
            parts = []
            try:
                for chunk in stream_answer(clone(self.settings), SYSTEM_PROMPT, messages):
                    parts.append(chunk)
                    self.post("chunk", chunk)
                answer = "".join(parts).strip() or "(No answer returned.)"
                self.history.add(entry["id"], question, answer)
                self.post("finished", ("done", answer))
            except ProviderError as e:
                self.post("finished", ("error", f"Error: {e}"))
            except Exception as e:  # keep the worker alive whatever a provider throws
                self.post("finished", ("error", f"Unexpected error: {e}"))

    def run(self):
        self.root.mainloop()


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        s = clone(app.settings)
        self.title("WoWZA Settings")
        self.transient(app.root)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.test_results = queue.Queue()

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        bold = ("Segoe UI", 10, "bold")

        # WoW folder
        ttk.Label(frm, text="WoW game folder", font=bold).grid(row=0, column=0, sticky="w")
        row = ttk.Frame(frm)
        row.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.wow_var = tk.StringVar(value=s["wow_dir"])
        ttk.Entry(row, textvariable=self.wow_var, width=56).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self.browse).pack(side="left", padx=(6, 0))
        row2 = ttk.Frame(frm)
        row2.grid(row=2, column=0, sticky="ew", pady=4)
        self.wow_status = ttk.Label(row2, text="")
        self.wow_status.pack(side="left")
        self.install_btn = ttk.Button(row2, text="Install / update addon", command=self.install)
        self.install_btn.pack(side="right")
        self.wow_var.trace_add("write", lambda *_: self.update_wow_status())

        ttk.Separator(frm).grid(row=3, column=0, sticky="ew", pady=8)

        # Provider
        ttk.Label(frm, text="AI provider", font=bold).grid(row=4, column=0, sticky="w")
        self.pids = list(PROVIDERS)
        self.provider_var = tk.StringVar(value=PROVIDERS[s["provider"]]["label"])
        cb = ttk.Combobox(frm, textvariable=self.provider_var, state="readonly", width=50,
                          values=[PROVIDERS[p]["label"] for p in self.pids])
        cb.grid(row=5, column=0, sticky="w", pady=(2, 6))
        cb.bind("<<ComboboxSelected>>", lambda _: self.show_provider())

        self.field_vars = {
            pid: {f: tk.StringVar(value=s["providers"][pid].get(f, "")) for f in meta["fields"]}
            for pid, meta in PROVIDERS.items()
        }
        self.fields = ttk.Frame(frm)
        self.fields.grid(row=6, column=0, sticky="ew")
        self.fields.columnconfigure(1, weight=1)

        bg = ttk.Style().lookup("TFrame", "background") or "SystemButtonFace"
        self.instructions = tk.Text(frm, height=10, width=64, wrap="word", relief="flat",
                                    background=bg, font=("Segoe UI", 9))
        self.instructions.grid(row=7, column=0, sticky="ew", pady=(8, 4))

        actions = ttk.Frame(frm)
        actions.grid(row=8, column=0, sticky="ew")
        self.link_btn = ttk.Button(actions, text="Open setup page", command=self.open_link)
        self.link_btn.pack(side="left")
        ttk.Button(actions, text="Test connection", command=self.test).pack(side="left", padx=6)
        self.test_label = ttk.Label(actions, text="", wraplength=260)
        self.test_label.pack(side="left")

        ttk.Separator(frm).grid(row=9, column=0, sticky="ew", pady=8)

        qrow = ttk.Frame(frm)
        qrow.grid(row=10, column=0, sticky="ew", pady=(0, 8))
        self.qd_label = ttk.Label(qrow, text=app.questdata_status())
        self.qd_label.pack(side="left")
        self.qd_button = ttk.Button(qrow, text="Update quest data" if questdata.downloaded()
                                    else f"Download quest data ({questdata.DOWNLOAD_MB} MB)",
                                    command=self.get_questdata)
        self.qd_button.pack(side="right")

        self.sound_var = tk.BooleanVar(value=s["play_sound"])
        self.popup_var = tk.BooleanVar(value=s["popup_on_answer"])
        self.top_var = tk.BooleanVar(value=s["always_on_top"])
        ttk.Checkbutton(frm, text="Also play a chime on this PC when an answer is ready",
                        variable=self.sound_var).grid(row=11, column=0, sticky="w")
        ttk.Checkbutton(frm, text="Also bring this window forward when an answer arrives",
                        variable=self.popup_var).grid(row=12, column=0, sticky="w")
        ttk.Checkbutton(frm, text="Keep this window on top of other windows",
                        variable=self.top_var).grid(row=13, column=0, sticky="w")
        self.startup_var = tk.BooleanVar(value=shortcuts.startup_enabled())
        start_row = ttk.Frame(frm)
        start_row.grid(row=14, column=0, sticky="ew")
        ttk.Checkbutton(start_row, text="Start WoWZA when Windows starts (it has to be running to answer)",
                        variable=self.startup_var).pack(side="left")
        ttk.Button(frm, text="Add desktop shortcut", command=self.desktop_shortcut).grid(
            row=15, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(frm)
        buttons.grid(row=16, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self.save).pack(side="right", padx=6)

        self.show_provider()
        self.update_wow_status()
        self.after(150, self._poll_test)
        self.grab_set()

    def current_pid(self):
        label = self.provider_var.get()
        return next(p for p in self.pids if PROVIDERS[p]["label"] == label)

    def show_provider(self):
        for w in self.fields.winfo_children():
            w.destroy()
        pid = self.current_pid()
        meta = PROVIDERS[pid]
        for i, field in enumerate(meta["fields"]):
            ttk.Label(self.fields, text=FIELD_LABELS[field]).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            var = self.field_vars[pid][field]
            if field == "model" and meta["models"]:
                w = ttk.Combobox(self.fields, textvariable=var, values=meta["models"], width=40)
            else:
                w = ttk.Entry(self.fields, textvariable=var, width=43, show="•" if field == "api_key" else "")
            w.grid(row=i, column=1, sticky="w", pady=2)
        self.instructions.configure(state="normal")
        self.instructions.delete("1.0", "end")
        self.instructions.insert("1.0", meta["instructions"])
        self.instructions.configure(state="disabled")
        self.link_btn.configure(state="normal" if meta["url"] else "disabled")
        self.test_label.configure(text="")

    def update_wow_status(self):
        wow = self.wow_var.get().strip()
        if not is_wow_dir(wow):
            self.wow_status.configure(text="✗ Not a WoW game folder (pick the one with Interface and WTF)",
                                      foreground="#c0392b")
            self.install_btn.configure(state="disabled")
        elif addon_installed(wow):
            self.wow_status.configure(text="✓ Addon installed", foreground="#27ae60")
            self.install_btn.configure(state="normal")
        else:
            self.wow_status.configure(text="Addon not installed yet", foreground="#d35400")
            self.install_btn.configure(state="normal")

    def browse(self):
        chosen = filedialog.askdirectory(parent=self, initialdir=self.wow_var.get() or "C:\\",
                                         title="Select your WoW game folder")
        if not chosen:
            return
        path = Path(chosen)
        if not is_wow_dir(path):  # user picked the "World of Warcraft" root: choose a flavor inside
            inner = pick_wow_dir([d for d in sorted(path.glob("_*_")) if is_wow_dir(d)])
            if inner:
                path = inner
        self.wow_var.set(str(path))

    def install(self):
        wow = self.wow_var.get().strip()
        if wow_running() and not messagebox.askokcancel(
                "WoW is running", "Quit WoW before installing: the game only finds new addon files when it "
                "starts, and it may write its old saved data back when you log out.\n\nInstall anyway?",
                parent=self):
            return
        try:
            new_files = install_addon(wow)
            reset_signals(wow)
        except OSError as e:
            messagebox.showerror("Install failed", str(e), parent=self)
            return
        if new_files:
            self.app.restart_needed = True
        self.update_wow_status()
        self.app._refresh_status()
        restart = ("Fully quit and restart WoW (a /reload isn't enough): the game only finds new "
                   "addon files when it starts." if new_files else "Type /reload in WoW to load the update.")
        messagebox.showinfo("Addon installed", f"Installed to:\n{addon_dir(wow)}\n\n{restart}", parent=self)

    def open_link(self):
        url = PROVIDERS[self.current_pid()]["url"]
        if url:
            webbrowser.open(url)

    def collect(self):
        s = clone(self.app.settings)
        s["wow_dir"] = self.wow_var.get().strip()
        s["provider"] = self.current_pid()
        for pid, fields in self.field_vars.items():
            for f, var in fields.items():
                s["providers"][pid][f] = var.get().strip()
        s["play_sound"] = self.sound_var.get()
        s["popup_on_answer"] = self.popup_var.get()
        s["always_on_top"] = self.top_var.get()
        return s

    def test(self):
        s = self.collect()
        self.test_label.configure(text="Testing...", foreground="#666")

        def run():
            try:
                reply = "".join(stream_answer(s, "Reply with exactly the word OK.",
                                              [{"role": "user", "content": "Connection test"}])).strip()
                self.test_results.put((True, f"✓ Working: {reply[:40]}"))
            except Exception as e:
                self.test_results.put((False, f"✗ {e}"))

        threading.Thread(target=run, daemon=True).start()

    def _poll_test(self):
        try:
            ok, msg = self.test_results.get_nowait()
            self.test_label.configure(text=msg, foreground="#27ae60" if ok else "#c0392b")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll_test)

    def get_questdata(self):
        self.qd_button.configure(state="disabled")
        self.qd_label.configure(text="Downloading from github.com/Questie/QuestieDB...")

        def finished():
            if self.winfo_exists():
                self.qd_label.configure(text=self.app.questdata_status())
                self.qd_button.configure(state="normal", text="Update quest data")
        self.app.download_questdata(done=finished)

    def desktop_shortcut(self):
        try:
            shortcuts.create_desktop_shortcut()
            messagebox.showinfo("Shortcut added", "A WoWZA shortcut is on your desktop.", parent=self)
        except Exception as e:
            messagebox.showerror("Couldn't add the shortcut", str(e), parent=self)

    def save(self):
        if self.startup_var.get() != shortcuts.startup_enabled():
            try:
                shortcuts.set_startup(self.startup_var.get())
            except Exception as e:
                messagebox.showerror("Couldn't change the startup setting", str(e), parent=self)
        self.app.apply_settings(self.collect())
        self.destroy()


def claim_single_instance():
    """Two companions would answer every question twice. If one is running, bring it forward instead."""
    import ctypes
    k32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    handle = k32.CreateMutexW(None, False, "Local\\WoWZACompanion")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(None, "WoWZA")
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return None
    return handle  # keep it referenced for the life of the process


if __name__ == "__main__":
    _instance = claim_single_instance()
    if _instance:
        migrate_app_dir()  # settings, history and quest data from before the rename
        try:
            shortcuts.migrate_old_shortcuts()
        except Exception:
            pass  # a missing shortcut isn't worth failing to start over
        App().run()
