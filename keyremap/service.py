"""`keyremap service` — keep the remap alive without thinking about it.

A remapper that stops silently is worse than one that never started: the keys
just quietly do the wrong thing and you assume the tool is fine. Each platform
gets the persistence it actually supports:

  Windows  a Scheduled Task at logon that RESTARTS on failure (the plain
           Startup-folder shortcut only ever runs once)
  Linux    a systemd --user unit with Restart=on-failure
  macOS    nothing to do — Karabiner-Elements is already a managed service
           that macOS relaunches; we only verify it

Every action prints the exact command it runs, and `--dry-run` shows without
doing.
"""

import os
import subprocess
import sys

from . import envinfo

TASK_NAME = "keyremap"


def _say(cmd, dry):
    print(f"  $ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    return dry


def _windows(cfg, dry: bool) -> int:
    from .backends import windows as be
    ahk = be.find_autohotkey()
    if not ahk:
        print("AutoHotkey v2 not found — install it first (keyremap setup).")
        return 1
    script = os.path.join(os.path.dirname(cfg.path), "out",
                          envinfo.detect(), "keyremap_interception.ahk")
    win_script = script
    try:
        from .envinfo import to_windows_path
        if script.startswith("/mnt/"):
            win_script = to_windows_path(script)
    except Exception:  # noqa: BLE001
        pass

    # A Scheduled Task is the only Windows mechanism that both runs at logon
    # and restarts the program if it dies; the Startup folder does neither.
    ps = (
        f"$a = New-ScheduledTaskAction -Execute '{ahk}' "
        f"-Argument '\"{win_script}\"'; "
        "$t = New-ScheduledTaskTrigger -AtLogOn; "
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -RestartCount 3 "
        "-RestartInterval (New-TimeSpan -Minutes 1) "
        "-ExecutionTimeLimit (New-TimeSpan -Seconds 0); "
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t "
        "-Settings $s -Force | Out-Null; "
        f"'registered: {TASK_NAME}'"
    )
    print("Registering a logon task that restarts the remap if it dies:")
    if _say(["powershell.exe", "-NoProfile", "-Command", "<register task>"], dry):
        return 0
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    print("  " + (r.stdout or r.stderr).strip())
    return r.returncode


SYSTEMD_UNIT = """\
[Unit]
Description=keyremap — per-device key remapping
After=graphical-session.target

[Service]
ExecStart={python} {remap} apply
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def _linux(cfg, dry: bool) -> int:
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    unit_path = os.path.join(unit_dir, "keyremap.service")
    remap = os.path.join(os.path.dirname(cfg.path), "remap.py")
    text = SYSTEMD_UNIT.format(python=sys.executable, remap=remap)

    print(f"Writing {unit_path}:\n")
    print("\n".join("    " + l for l in text.strip().splitlines()))
    if dry:
        print("\n  (dry-run — nothing written)")
        return 0
    os.makedirs(unit_dir, exist_ok=True)
    with open(unit_path, "w", encoding="utf-8") as f:
        f.write(text)
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", "keyremap.service"]):
        _say(cmd, False)
        subprocess.run(cmd, timeout=60)
    print("\nkeyremap will now start at login and restart if it dies.")
    print("  status: systemctl --user status keyremap")
    print("  stop:   systemctl --user disable --now keyremap")
    return 0


def _macos(cfg, dry: bool) -> int:
    from .backends import macos as mac
    print("macOS needs no service from keyremap: the rule lives inside\n"
          "Karabiner-Elements, which macOS already keeps running and\n"
          "relaunches for you.\n")
    # never let a missing binary crash a status command
    from . import doctor
    running = doctor._run(["pgrep", "-x", "karabiner_grabber"])[0] == 0
    print(f"  Karabiner engine: {'running' if running else 'NOT running'}")
    if not running:
        print("  → open Karabiner-Elements once and grant Input Monitoring")
    installed = os.path.exists(os.path.join(mac.ASSETS_DIR, "keyremap.json"))
    print(f"  rule installed:   {'yes' if installed else 'no (run keyremap apply)'}")
    return 0 if (running and installed) else 1


def run(cfg, dry_run: bool = False) -> int:
    env = envinfo.detect()
    print(f"keyremap service — {env}\n")
    if env == "macos":
        return _macos(cfg, dry_run)
    if env == "linux":
        return _linux(cfg, dry_run)
    return _windows(cfg, dry_run)
