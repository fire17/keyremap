"""keyremap TUI — the control room.

Five panes, one keystroke apart:
  [1] Dashboard  device + engine + layers at a glance
  [2] Mappings   every effective mapping, with the layer it came from
  [3] Capture    live keystrokes tagged with their source device
  [4] Doctor     what's missing on this machine and the exact fix
  [5] Apply      build + deploy for this OS, with the result inline

Runs at ~10 Hz on an idle loop that blocks in select(), so CPU is ~0%.
"""

import os
import queue
import threading
import time

from .. import config as cfgmod
from .. import doctor as doctormod
from .. import envinfo, state
from ..keys import KEYS
from .term import (ACCENT, BAD, GOOD, KEYC, MUTED, S, Term, WARN_C, DOWN, END,
                   ENTER, ESCAPE, HOME, LEFT, PGDN, PGUP, RIGHT, TAB, UP,
                   pad, truncate, visible_len)

TABS = ["Dashboard", "Mappings", "Capture", "Doctor", "Apply"]


class App:
    def __init__(self, cfg_path: str):
        self.cfg_path = cfg_path
        self.cfg = cfgmod.load(cfg_path)
        self.env = envinfo.detect()
        self.tab = 0
        self.sel = 0
        self.scroll = 0
        self.running = True
        self.status: state.Status | None = None
        self.flash = ""
        self.flash_until = 0.0
        self.capture_lines: list[str] = []
        self.capture_thread = None
        self.capture_stop = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.apply_output: list[str] = []
        self.doctor_rows: list[tuple] = []
        self.busy = ""
        self._status_at = 0.0
        self._cfg_mtime = self._mtime()

    # ---------------------------------------------------------------- utils
    def _mtime(self) -> float:
        try:
            return os.path.getmtime(self.cfg_path)
        except OSError:
            return 0.0

    def say(self, msg: str, secs: float = 3.0):
        self.flash = msg
        self.flash_until = time.time() + secs

    def reload_config(self):
        try:
            self.cfg = cfgmod.load(self.cfg_path)
            self._cfg_mtime = self._mtime()
            self.say("config reloaded")
        except Exception as e:  # noqa: BLE001
            self.say(f"config error: {e}", 6)

    def refresh_status(self, quick=False):
        try:
            self.status = state.gather(self.cfg, quick=quick)
        except Exception as e:  # noqa: BLE001
            self.say(f"status error: {type(e).__name__}", 4)
        self._status_at = time.time()

    # --------------------------------------------------------------- render
    def render(self) -> list[str]:
        w, h = Term.size()
        w = max(60, min(w, 200))
        lines = [self._titlebar(w), self._tabbar(w)]
        body_h = max(5, h - 5)
        body = {
            0: self._dashboard, 1: self._mappings, 2: self._capture,
            3: self._doctor, 4: self._apply,
        }[self.tab](w, body_h)
        body = body[:body_h] + [""] * max(0, body_h - len(body))
        lines += body
        lines.append(self._statusbar(w))
        return [truncate(l, w) for l in lines]

    def _titlebar(self, w):
        left = f"{S.BOLD}{ACCENT}keyremap{S.RESET}{MUTED} · per-device key mapping{S.RESET}"
        env = f"{MUTED}{self.env}{S.RESET}"
        host = f"{MUTED}{(self.status.host if self.status else '')}{S.RESET}"
        right = f"{env} {MUTED}·{S.RESET} {host}"
        gap = max(1, w - visible_len(left) - visible_len(right))
        return left + " " * gap + right

    def _tabbar(self, w):
        parts = []
        for i, name in enumerate(TABS):
            label = f" {i + 1} {name} "
            if i == self.tab:
                parts.append(f"{S.REV}{ACCENT}{label}{S.RESET}")
            else:
                parts.append(f"{MUTED}{label}{S.RESET}")
        return "".join(parts) + MUTED + "─" * max(0, w - visible_len("".join(parts))) + S.RESET

    def _statusbar(self, w):
        if self.busy:
            left = f"{WARN_C}⟳ {self.busy}{S.RESET}"
        elif time.time() < self.flash_until:
            left = f"{GOOD}{self.flash}{S.RESET}"
        else:
            left = f"{MUTED}1-5 switch · ↑↓ move · r reload · a apply · q quit{S.RESET}"
        cfg = f"{MUTED}{os.path.basename(self.cfg_path)}{S.RESET}"
        gap = max(1, w - visible_len(left) - visible_len(cfg))
        return left + " " * gap + cfg

    # -- dashboard
    def _dashboard(self, w, h):
        st = self.status
        out = [""]
        if not st:
            return ["  gathering status…"]

        def row(label, value, tone=MUTED):
            return f"  {MUTED}{pad(label, 18)}{S.RESET}{tone}{value}{S.RESET}"

        dev_tone = GOOD if st.device_present else WARN_C
        dev_txt = st.device_label or ("connected" if st.device_present else "not connected")
        eng_tone = GOOD if st.engine_running else WARN_C
        out.append(f"  {S.BOLD}Device{S.RESET}")
        out.append(row("configured", ", ".join(self.cfg.devices) or "none"))
        for name, fp in self.cfg.device_fingerprints().items():
            out.append(row("fingerprint", f"{name} → {fp}", KEYC))
        out.append(row("present", dev_txt, dev_tone))
        out.append("")
        out.append(f"  {S.BOLD}Engine{S.RESET}")
        out.append(row("backend", self.env))
        out.append(row("running", st.engine_detail or "-", eng_tone))
        out.append(row("last applied", st.deployment.applied_ago +
                       (f"  ({st.deployment.mappings} mappings)"
                        if st.deployment.mappings else "")))
        if st.config_changed:
            out.append(row("config", "changed since last apply — press 'a'", WARN_C))
        out.append("")
        out.append(f"  {S.BOLD}Profile layers{S.RESET}")
        for layer in self.cfg.layers_available:
            applied = layer in self.cfg.layers_applied
            mark = f"{GOOD}●{S.RESET}" if applied else f"{MUTED}○{S.RESET}"
            note = "" if applied else f"  {MUTED}(not this machine){S.RESET}"
            out.append(f"    {mark} {layer}{note}")
        total = sum(len(t) for t in self.cfg.mappings.values())
        out.append("")
        out.append(row("effective", f"{total} mappings", ACCENT))
        problems = cfgmod.lint(self.cfg)
        if problems:
            out.append("")
            out.append(f"  {WARN_C}{S.BOLD}Lint{S.RESET}")
            for p in problems[:4]:
                out.append(f"    {WARN_C}!{S.RESET} {p}")
        return out

    # -- mappings
    def _mapping_rows(self):
        rows = []
        for dev, table in self.cfg.mappings.items():
            for src, act in table.items():
                origin = self.cfg.origin.get(dev, {}).get(src, "base")
                rows.append((dev, src, act, origin))
        return rows

    def _mappings(self, w, h):
        rows = self._mapping_rows()
        if not rows:
            return ["", "  no mappings — add some to config.yaml"]
        self.sel = max(0, min(self.sel, len(rows) - 1))
        view_h = h - 3
        if self.sel < self.scroll:
            self.scroll = self.sel
        elif self.sel >= self.scroll + view_h:
            self.scroll = self.sel - view_h + 1
        out = ["",
               f"    {MUTED}{pad('KEY', 12)}{pad('DOES', 34)}"
               f"{pad('LAYER', 14)}SCANCODE{S.RESET}"]
        for i in range(self.scroll, min(len(rows), self.scroll + view_h)):
            dev, src, act, origin = rows[i]
            k = KEYS[src]
            sc = f"0x{k.sc | (0x100 if k.ext else 0):X}"
            cur = i == self.sel
            bullet = f"{ACCENT}▸{S.RESET}" if cur else " "
            name = f"{KEYC}{pad(src, 11)}{S.RESET}"
            does = pad(truncate(act.describe(), 33), 33)
            lay = f"{MUTED}{pad(origin, 13)}{S.RESET}"
            line = f"  {bullet} {name} {does} {lay} {MUTED}{sc}{S.RESET}"
            out.append(f"{S.REV}{truncate(line, w - 1)}{S.RESET}" if cur else line)
        out.append("")
        out.append(f"  {MUTED}↑↓ select · c capture a new code for this key{S.RESET}")
        return out

    # -- capture
    def _capture(self, w, h):
        out = ["", f"  {S.BOLD}Live capture{S.RESET}  "
                   f"{MUTED}every keystroke, tagged with its source device{S.RESET}", ""]
        if not self.capture_thread or not self.capture_thread.is_alive():
            out.append(f"  {MUTED}press{S.RESET} {KEYC}s{S.RESET} "
                       f"{MUTED}to start capturing (stops with{S.RESET} "
                       f"{KEYC}s{S.RESET}{MUTED}){S.RESET}")
            out.append("")
            out.append(f"  {MUTED}Use this to learn a key's real scancode before "
                       f"mapping it — guessing silently fails.{S.RESET}")
        while True:
            try:
                out_line = self.events.get_nowait()
            except queue.Empty:
                break
            self.capture_lines.append(out_line)
        self.capture_lines = self.capture_lines[-(h - 6):]
        for line in self.capture_lines:
            out.append("  " + line)
        return out

    # -- doctor
    def _doctor(self, w, h):
        out = ["", f"  {S.BOLD}Doctor{S.RESET}  {MUTED}what this machine needs{S.RESET}", ""]
        if not self.doctor_rows:
            out.append(f"  {MUTED}press{S.RESET} {KEYC}d{S.RESET} {MUTED}to run checks{S.RESET}")
            return out
        for status, label, detail, fix in self.doctor_rows:
            icon = {"ok": f"{GOOD}✓{S.RESET}", "warn": f"{WARN_C}!{S.RESET}",
                    "fail": f"{BAD}✗{S.RESET}"}[status]
            out.append(f"  {icon} {pad(label, 26)}{MUTED}{detail}{S.RESET}")
            if fix:
                out.append(f"      {ACCENT}→ {fix}{S.RESET}")
        return out

    # -- apply
    def _apply(self, w, h):
        out = ["", f"  {S.BOLD}Apply{S.RESET}  "
                   f"{MUTED}generate + deploy for {self.env}{S.RESET}", ""]
        if not self.apply_output:
            out.append(f"  {MUTED}press{S.RESET} {KEYC}a{S.RESET} "
                       f"{MUTED}to build and deploy{S.RESET}")
        for line in self.apply_output[-(h - 5):]:
            out.append("  " + line)
        return out

    # ---------------------------------------------------------------- actions
    def start_capture(self):
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_stop.set()
            self.say("capture stopped")
            return
        self.capture_stop.clear()
        self.capture_lines = []

        def worker():
            try:
                from ..backends import get_backend
                be = get_backend(self.env)
                for line in be.listen(self.cfg, seconds=120):
                    if self.capture_stop.is_set():
                        break
                    self.events.put(self._decorate(line))
            except Exception as e:  # noqa: BLE001
                self.events.put(f"{BAD}capture failed: {e}{S.RESET}")

        self.capture_thread = threading.Thread(target=worker, daemon=True)
        self.capture_thread.start()
        self.say("capturing — press keys on the device")

    def _decorate(self, line: str) -> str:
        low = line.lower()
        for name, dm in self.cfg.devices.items():
            vidpid = (dm.vendor_id is not None and dm.product_id is not None and
                      f"vid&02{dm.vendor_id:04x}_pid&{dm.product_id:04x}" in low)
            if vidpid or (dm.name_contains and dm.name_contains.lower() in low):
                head = line.split("|")[0].strip()
                return f"{GOOD}▸ {name}{S.RESET}  {head}"
        head = line.split("|")[0].strip()
        return f"{MUTED}  other   {head}{S.RESET}"

    def run_doctor(self):
        self.busy = "running checks"
        try:
            self.doctor_rows = doctormod.run(self.cfg)
            self.say("doctor complete")
        finally:
            self.busy = ""

    def do_apply(self):
        self.busy = "building"
        self.apply_output = []
        try:
            from ..backends import get_backend
            be = get_backend(self.env)
            out_dir = os.path.join(os.path.dirname(self.cfg_path), "out", self.env)
            if self.env in ("windows", "wsl"):
                path = be.apply(self.cfg, out_dir, mode="interception")
            elif self.env == "macos":
                path = be.apply(self.cfg, out_dir)
            else:
                self.apply_output.append(
                    f"{WARN_C}linux applies by running the daemon:{S.RESET} "
                    "sudo python3 remap.py apply")
                return
            self.apply_output.append(f"{GOOD}✓{S.RESET} wrote {path}")
            n = sum(len(t) for t in self.cfg.mappings.values())
            state.save_state(state.Deployment(
                applied_at=time.time(),
                config_sha=state.config_sha(self.cfg_path),
                backend=self.env, artifact=path, mappings=n))
            self.apply_output.append(f"{MUTED}{n} mappings · state recorded{S.RESET}")
            if self.env == "macos":
                self.apply_output.append(
                    f"{ACCENT}→ copy into ~/.config/karabiner/assets/"
                    f"complex_modifications/ and enable the rule{S.RESET}")
            self.say("applied")
        except Exception as e:  # noqa: BLE001
            self.apply_output.append(f"{BAD}✗ {type(e).__name__}: {e}{S.RESET}")
            self.say("apply failed", 5)
        finally:
            self.busy = ""
            self.refresh_status(quick=True)

    # ------------------------------------------------------------------ loop
    def handle(self, key):
        if key in ("q", "CTRL_C"):
            self.running = False
        elif key in ("1", "2", "3", "4", "5"):
            self.tab = int(key) - 1
        elif key == TAB:
            self.tab = (self.tab + 1) % len(TABS)
        elif key in (UP, "k"):
            self.sel = max(0, self.sel - 1)
        elif key in (DOWN, "j"):
            self.sel += 1
        elif key == PGUP:
            self.sel = max(0, self.sel - 10)
        elif key == PGDN:
            self.sel += 10
        elif key == HOME:
            self.sel = 0
        elif key == "r":
            self.reload_config()
            self.refresh_status(quick=True)
        elif key == "a":
            self.tab = 4
            self.do_apply()
        elif key == "d":
            self.tab = 3
            self.run_doctor()
        elif key in ("s", "c"):
            self.tab = 2
            self.start_capture()

    def loop(self):
        self.refresh_status()
        with Term() as term:
            while self.running:
                term.frame(self.render())
                key = term.read_key(timeout=0.15)
                if key is not None:
                    self.handle(key)
                now = time.time()
                if now - self._status_at > 2.0:
                    self.refresh_status(quick=True)
                if self._mtime() != self._cfg_mtime:  # hot reload
                    self.reload_config()


def main(cfg_path: str):
    App(cfg_path).loop()
    return 0
