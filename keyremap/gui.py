"""keyremap desktop app — tkinter, which ships with Python on macOS, Windows
and most Linux distros, so there is nothing to install.

Same config, same backends, same state file as the TUI: whichever you open,
you see one truth. The UI polls cheaply (a stat + a small JSON read) and only
touches subprocesses on explicit actions, so idle cost is negligible.
"""

import os
import queue
import threading
import time

from . import config as cfgmod
from . import doctor as doctormod
from . import envinfo, state
from .keys import KEYS

# --- palette (dark, high contrast, calm) ------------------------------------
BG = "#11131a"
BG2 = "#171a23"
BG3 = "#1e2230"
FG = "#e6e9f0"
MUTED = "#8b93a7"
ACCENT = "#5cc8ff"
GOOD = "#5ddba4"
WARN = "#ffc95c"
BAD = "#ff6b6b"
LILAC = "#c3a6ff"
MONO = ("SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "monospace")


def _font(size=11, weight="normal"):
    for fam in MONO:
        return (fam, size, weight)
    return ("monospace", size, weight)


# --- pure view-model helpers -------------------------------------------------
# Kept free of tkinter so they can be unit-tested headlessly (and so the TUI,
# GUI and CLI can never disagree about what a mapping "says").

def mapping_rows(cfg) -> list[tuple[str, str, str, str]]:
    """(key, does, layer, scancode) for every effective mapping."""
    rows = []
    for dev, table in cfg.mappings.items():
        for src, act in table.items():
            k = KEYS[src]
            sc = f"0x{k.sc | (0x100 if k.ext else 0):X}"
            origin = cfg.origin.get(dev, {}).get(src, "base")
            rows.append((src, act.describe(), origin, sc))
    return rows


def classify_capture_line(cfg, line: str) -> tuple[str, str]:
    """(display text, 'hit'|'other') — is this keystroke from a mapped device?"""
    low = line.lower()
    for dm in cfg.devices.values():
        vidpid = (dm.vendor_id is not None and dm.product_id is not None and
                  f"vid&02{dm.vendor_id:04x}_pid&{dm.product_id:04x}" in low)
        byname = dm.name_contains and dm.name_contains.lower() in low
        if vidpid or byname:
            return line.split("|")[0].strip(), "hit"
    return line.split("|")[0].strip(), "other"


def status_line(cfg, st) -> str:
    layers = " → ".join(cfg.layers_applied) or "none"
    extra = "  · config changed" if getattr(st, "config_changed", False) else ""
    return f"layers: {layers}   ·   applied {st.deployment.applied_ago}{extra}"


class Gui:
    def __init__(self, root, cfg_path):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = root
        self.cfg_path = cfg_path
        self.cfg = cfgmod.load(cfg_path)
        self.env = envinfo.detect()
        self.status = None
        self.events = queue.Queue()
        self.capture_stop = threading.Event()
        self.capture_thread = None
        self._status_thread = None      # one at a time; see _poll
        self._cfg_mtime = self._mtime()

        root.title("keyremap")
        root.configure(bg=BG)
        root.geometry("980x620")
        root.minsize(760, 480)

        self._build()
        self._poll()

    # ---------------------------------------------------------------- layout
    def _build(self):
        tk = self.tk
        head = tk.Frame(self.root, bg=BG, padx=18, pady=14)
        head.pack(fill="x")
        tk.Label(head, text="keyremap", bg=BG, fg=ACCENT,
                 font=_font(18, "bold")).pack(side="left")
        tk.Label(head, text="  per-device key mapping", bg=BG, fg=MUTED,
                 font=_font(11)).pack(side="left")
        self.env_lbl = tk.Label(head, text="", bg=BG, fg=MUTED, font=_font(10))
        self.env_lbl.pack(side="right")

        # status strip
        strip = tk.Frame(self.root, bg=BG2, padx=18, pady=10)
        strip.pack(fill="x")
        self.dev_lbl = tk.Label(strip, text="device…", bg=BG2, fg=FG,
                                font=_font(11), anchor="w")
        self.dev_lbl.pack(side="left")
        self.eng_lbl = tk.Label(strip, text="", bg=BG2, fg=MUTED,
                                font=_font(10), anchor="e")
        self.eng_lbl.pack(side="right")

        # tabs — hand-rolled so no theme draws its own borders
        style = self.ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass

        # NOTE: tuple padding is a pack/grid option, not a widget option —
        # tk.Frame(pady=(12, 0)) raises TclError: bad screen distance.
        tabbar = tk.Frame(self.root, bg=BG)
        tabbar.pack(fill="x", padx=14, pady=(12, 0))
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=14, pady=10)

        self.panes, self.tab_buttons = {}, {}
        for key, label in (("map", "Mappings"), ("cap", "Capture"),
                           ("doc", "Doctor")):
            b = tk.Button(tabbar, text=label, relief="flat", bd=0,
                          highlightthickness=0, cursor="hand2", padx=16, pady=7,
                          bg=BG, fg=MUTED, activebackground=BG3,
                          activeforeground=ACCENT, font=_font(11),
                          command=lambda k=key: self.show(k))
            b.pack(side="left", padx=(0, 4))
            self.tab_buttons[key] = b
            pane = tk.Frame(self.body, bg=BG2)
            self.panes[key] = pane

        self.map_tree = self._tree(self.panes["map"],
                                   ("key", "does", "layer", "scancode"),
                                   (150, 400, 150, 120))
        self._build_capture(self.panes["cap"])
        self.doc_tree = self._tree(self.panes["doc"], ("check", "result", "fix"),
                                   (250, 320, 400))
        self.doc_tree.tag_configure("ok", foreground=GOOD)
        self.doc_tree.tag_configure("warn", foreground=WARN)
        self.doc_tree.tag_configure("fail", foreground=BAD)
        self.show("map")

        # action bar
        bar = tk.Frame(self.root, bg=BG, padx=14, pady=10)
        bar.pack(fill="x")
        self._button(bar, "Apply", self.do_apply, primary=True)
        self._button(bar, "Reload config", self.reload_config)
        self._button(bar, "Run doctor", self.run_doctor)
        self._button(bar, "Export…", self.do_export)
        self.flash = tk.Label(bar, text="", bg=BG, fg=GOOD, font=_font(10))
        self.flash.pack(side="right")

    def _button(self, parent, text, cmd, primary=False):
        b = self.tk.Button(
            parent, text=text, command=cmd, relief="flat", cursor="hand2",
            bg=ACCENT if primary else BG3, fg="#0b0d12" if primary else FG,
            activebackground=GOOD if primary else BG2,
            activeforeground="#0b0d12" if primary else FG,
            font=_font(10, "bold" if primary else "normal"), padx=14, pady=6,
            highlightthickness=0, bd=0)
        b.pack(side="left", padx=(0, 8))
        return b

    def _tree(self, parent, columns, widths):
        style = self.ttk.Style()
        style.configure("kr.Treeview", background=BG2, fieldbackground=BG2,
                        foreground=FG, borderwidth=0, relief="flat",
                        rowheight=27, font=_font(10))
        style.layout("kr.Treeview", [("kr.Treeview.treearea",
                                      {"sticky": "nswe"})])  # drop the border
        style.configure("kr.Treeview.Heading", background=BG3, foreground=MUTED,
                        borderwidth=0, font=_font(9, "bold"))
        style.map("kr.Treeview", background=[("selected", BG3)],
                  foreground=[("selected", ACCENT)])
        t = self.ttk.Treeview(parent, columns=columns, show="headings",
                              style="kr.Treeview")
        for c, w in zip(columns, widths):
            t.heading(c, text=c.upper())
            t.column(c, width=w, anchor="w")
        t.pack(fill="both", expand=True, padx=10, pady=10)
        return t

    def show(self, key):
        for k, pane in self.panes.items():
            pane.pack_forget()
            self.tab_buttons[k].configure(
                bg=BG3 if k == key else BG,
                fg=ACCENT if k == key else MUTED)
        self.panes[key].pack(fill="both", expand=True)

    def _build_capture(self, f):
        tk = self.tk
        top = tk.Frame(f, bg=BG2, padx=10, pady=10)
        top.pack(fill="x")
        self.cap_btn = tk.Button(
            top, text="Start capture", command=self.toggle_capture, relief="flat",
            bg=BG3, fg=FG, font=_font(10), padx=12, pady=5, bd=0,
            highlightthickness=0, cursor="hand2")
        self.cap_btn.pack(side="left")
        tk.Label(top, text="  press keys on the device to learn their codes",
                 bg=BG2, fg=MUTED, font=_font(10)).pack(side="left")
        self.cap_text = tk.Text(f, bg=BG2, fg=FG, insertbackground=FG,
                                font=_font(10), relief="flat", height=18,
                                padx=10, pady=6, bd=0, highlightthickness=0)
        self.cap_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.cap_text.tag_configure("hit", foreground=GOOD)
        self.cap_text.tag_configure("other", foreground=MUTED)

    # --------------------------------------------------------------- helpers
    def _mtime(self):
        try:
            return os.path.getmtime(self.cfg_path)
        except OSError:
            return 0.0

    def say(self, msg, colour=GOOD):
        self.flash.configure(text=msg, fg=colour)
        self.root.after(4000, lambda: self.flash.configure(text=""))

    # --------------------------------------------------------------- actions
    def reload_config(self):
        try:
            self.cfg = cfgmod.load(self.cfg_path)
            self._cfg_mtime = self._mtime()
            self.refresh_mappings()
            self.say("config reloaded")
        except Exception as e:  # noqa: BLE001
            self.say(f"config error: {e}", BAD)

    def refresh_mappings(self):
        self.map_tree.delete(*self.map_tree.get_children())
        for row in mapping_rows(self.cfg):
            self.map_tree.insert("", "end", values=row)

    def run_doctor(self):
        self.doc_tree.delete(*self.doc_tree.get_children())

        def worker():
            rows = doctormod.run(self.cfg)
            self.root.after(0, lambda: self._show_doctor(rows))

        threading.Thread(target=worker, daemon=True).start()
        self.say("running checks…", WARN)

    def _show_doctor(self, rows):
        for status, label, detail, fix in rows:
            self.doc_tree.insert("", "end", values=(label, detail, fix),
                                 tags=(status,))
        self.show("doc")
        self.say("doctor complete")

    def do_apply(self):
        def worker():
            try:
                from .backends import get_backend
                be = get_backend(self.env)
                out_dir = os.path.join(os.path.dirname(self.cfg_path),
                                       "out", self.env)
                if self.env == "linux":
                    msg = "run: sudo python3 remap.py apply"
                    self.root.after(0, lambda: self.say(msg, WARN))
                    return
                path = (be.apply(self.cfg, out_dir, mode="interception")
                        if self.env in ("windows", "wsl")
                        else be.apply(self.cfg, out_dir))
                n = sum(len(t) for t in self.cfg.mappings.values())
                state.save_state(state.Deployment(
                    applied_at=time.time(),
                    config_sha=state.config_sha(self.cfg_path),
                    backend=self.env, artifact=str(path), mappings=n))
                self.root.after(0, lambda: self.say(f"applied · {n} mappings"))
            except Exception as e:  # noqa: BLE001
                self.root.after(0, lambda: self.say(f"apply failed: {e}", BAD))

        threading.Thread(target=worker, daemon=True).start()

    def do_export(self):
        from .portable import export_bundle
        try:
            path = export_bundle(self.cfg)
            self.say(f"exported → {os.path.basename(path)}")
        except Exception as e:  # noqa: BLE001
            self.say(f"export failed: {e}", BAD)

    def toggle_capture(self):
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_stop.set()
            self.cap_btn.configure(text="Start capture")
            return
        self.capture_stop.clear()
        self.cap_text.delete("1.0", "end")
        self.cap_btn.configure(text="Stop capture")

        def worker():
            try:
                from .backends import get_backend
                for line in get_backend(self.env).listen(self.cfg, seconds=120):
                    if self.capture_stop.is_set():
                        break
                    self.events.put(line)
            except Exception as e:  # noqa: BLE001
                self.events.put(f"capture failed: {e}")

        self.capture_thread = threading.Thread(target=worker, daemon=True)
        self.capture_thread.start()

    # ------------------------------------------------------------------ poll
    def _poll(self):
        # drain capture events
        drained = 0
        while drained < 50:
            try:
                line = self.events.get_nowait()
            except queue.Empty:
                break
            drained += 1
            text, tag = classify_capture_line(self.cfg, line)
            self.cap_text.insert("end", text + "\n", tag)
            self.cap_text.see("end")

        if self._mtime() != self._cfg_mtime:  # hot reload
            self.reload_config()

        # If the previous refresh is still running (a slow or hung platform
        # helper), skip this tick rather than stacking a thread every 1.5s.
        if self._status_thread is None or not self._status_thread.is_alive():
            def worker():
                try:
                    st = state.gather(self.cfg, quick=True)
                except Exception:  # noqa: BLE001 - a status probe must not kill the UI
                    return
                self.root.after(0, lambda: self._show_status(st))

            self._status_thread = threading.Thread(target=worker, daemon=True)
            self._status_thread.start()
        self.root.after(1500, self._poll)

    def _show_status(self, st):
        self.status = st
        dot = "●"
        col = GOOD if st.device_present else WARN
        label = st.device_label or "not connected"
        self.dev_lbl.configure(text=f"{dot} {label}", fg=col)
        self.eng_lbl.configure(text=status_line(self.cfg, st))
        self.env_lbl.configure(text=f"{st.env} · {st.host}")


def main(cfg_path: str) -> int:
    try:
        import tkinter as tk
    except ImportError:
        print("tkinter is not available in this Python build.\n"
              "  macOS  : brew install python-tk   (or use python.org Python)\n"
              "  Debian : sudo apt install python3-tk\n"
              "  Windows: reinstall Python with the tcl/tk option\n"
              "Meanwhile the full-featured TUI works everywhere: keyremap tui")
        return 1
    root = tk.Tk()
    app = Gui(root, cfg_path)
    app.refresh_mappings()
    root.mainloop()
    return 0
