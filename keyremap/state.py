"""Runtime state: what is deployed, what is running, is the device here.

Everything is cheap (a stat + a small JSON read) so the TUI can poll it at
10 Hz without being noticeable.
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict

from . import envinfo

STATE_DIR = os.path.expanduser("~/.keyremap")
STATE_FILE = os.path.join(STATE_DIR, "state.json")


@dataclass
class Deployment:
    applied_at: float = 0.0
    config_sha: str = ""
    backend: str = ""
    artifact: str = ""
    mappings: int = 0

    @property
    def applied_ago(self) -> str:
        if not self.applied_at:
            return "never"
        d = int(time.time() - self.applied_at)
        for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
            if d >= n:
                return f"{d // n}{unit} ago"
        return f"{d}s ago"


def load_state() -> Deployment:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return Deployment(**json.load(f))
    except (OSError, TypeError, ValueError):
        return Deployment()


def save_state(dep: Deployment) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(dep), f, indent=2)
    os.replace(tmp, STATE_FILE)  # atomic


def config_sha(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read())
    except OSError:
        return ""
    return h.hexdigest()[:12]


@dataclass
class Status:
    env: str = ""
    host: str = ""
    device_present: bool = False
    device_label: str = ""
    engine_running: bool = False
    engine_detail: str = ""
    deployment: Deployment = field(default_factory=Deployment)
    config_changed: bool = False


def _win_engine_running() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='AutoHotkey64.exe'\" |"
             " Where-Object { $_.CommandLine -match 'keyremap' }).ProcessId"],
            capture_output=True, text=True, timeout=15)
        pids = [x for x in r.stdout.split() if x.strip().isdigit()]
        return bool(pids), ("pid " + ", ".join(pids)) if pids else "not running"
    except Exception as e:  # noqa: BLE001 - status must never crash the UI
        return False, f"unknown ({type(e).__name__})"


def _mac_engine_running() -> tuple[bool, str]:
    try:
        r = subprocess.run(["pgrep", "-x", "karabiner_grabber"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return True, "Karabiner-Elements active"
        return False, "Karabiner-Elements not running"
    except Exception:  # noqa: BLE001
        return False, "Karabiner-Elements not installed"


def _linux_engine_running() -> tuple[bool, str]:
    try:
        r = subprocess.run(["pgrep", "-af", "remap.py apply"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return True, r.stdout.split("\n")[0][:60]
    except Exception:  # noqa: BLE001
        pass
    return False, "daemon not running"


def engine_status(env: str) -> tuple[bool, str]:
    if env in ("windows", "wsl"):
        return _win_engine_running()
    if env == "macos":
        return _mac_engine_running()
    return _linux_engine_running()


def gather(cfg, quick: bool = False) -> Status:
    """Full status. quick=True skips the subprocess-backed engine probe."""
    env = envinfo.detect()
    plat, host = __import__(
        "keyremap.config", fromlist=["current_env"]).current_env()
    st = Status(env=env, host=host)
    st.deployment = load_state()
    st.config_changed = bool(
        st.deployment.config_sha and
        st.deployment.config_sha != config_sha(cfg.path))

    try:
        from .backends import get_backend
        devices = get_backend(env).detect(cfg)
        matched = [d for d in devices if d.get("matches")]
        st.device_present = bool(matched)
        if matched:
            st.device_label = matched[0]["name"]
        elif devices:
            st.device_label = f"{len(devices)} input devices, none matching"
    except Exception as e:  # noqa: BLE001
        st.device_label = f"detect failed: {type(e).__name__}"

    if not quick:
        st.engine_running, st.engine_detail = engine_status(env)
    return st
