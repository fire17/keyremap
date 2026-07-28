"""`keyremap setup` — walk a brand-new machine from nothing to working.

`doctor` tells you what is missing. `setup` fixes what it safely can and hands
you the exact click-path for what it cannot: macOS will not let any program
approve a driver extension or grant Input Monitoring on your behalf, by design.

Nothing here installs software without asking, and every step is skipped when
it is already done, so re-running is harmless.
"""

import os
import shutil
import subprocess
import sys

from . import doctor, envinfo

OK, WARN, FAIL = doctor.OK, doctor.WARN, doctor.FAIL


def _ask(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"  {question} -> yes (--yes)")
        return True
    if not sys.stdin.isatty():
        print(f"  {question} -> skipped (not a terminal; pass --yes to accept)")
        return False
    try:
        return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _run(cmd, dry: bool) -> bool:
    print(f"  $ {' '.join(cmd)}")
    if dry:
        print("    (dry-run)")
        return True
    try:
        return subprocess.run(cmd).returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"    failed: {type(e).__name__}: {e}")
        return False


def _macos(cfg, assume_yes: bool, dry: bool) -> list[str]:
    """Returns the list of things only the human can finish."""
    manual = []
    from .backends import macos as mac

    if not os.path.exists("/Applications/Karabiner-Elements.app"):
        print("\nKarabiner-Elements is not installed. keyremap uses it as the\n"
              "macOS interception engine — it is the supported way to remap a\n"
              "single device without touching the built-in keyboard.")
        if shutil.which("brew") and _ask("install it with Homebrew now?", assume_yes):
            _run(["brew", "install", "--cask", "karabiner-elements"], dry)
        else:
            manual.append("install Karabiner-Elements: "
                          "brew install --cask karabiner-elements")
    else:
        print("\n  Karabiner-Elements: installed")

    # macOS requires a human for these two — no API can grant them.
    rc, ext = doctor._run(["systemextensionsctl", "list"])
    if not (rc == 0 and "activated enabled" in ext.lower() and "org.pqrs" in ext):
        manual.append(
            "approve the driver extension: System Settings > General > "
            "Login Items & Extensions > Driver Extensions > enable Karabiner")
        if _ask("open that settings pane now?", assume_yes):
            _run(["open",
                  "x-apple.systempreferences:com.apple.ExtensionsPreferences"], dry)

    rc, _ = doctor._run(["pgrep", "-x", "karabiner_grabber"])
    if rc != 0:
        manual.append(
            "grant Input Monitoring to Karabiner (System Settings > Privacy & "
            "Security > Input Monitoring), then open Karabiner-Elements once")
        if _ask("open Karabiner-Elements now?", assume_yes):
            _run(["open", "-a", "Karabiner-Elements"], dry)

    if mac.find_karabiner_cli():
        print("  karabiner_cli: present (selftest will lint your rule with it)")
    return manual


def _linux(cfg, assume_yes: bool, dry: bool) -> list[str]:
    manual = []
    try:
        import evdev  # noqa: F401
        print("\n  python-evdev: present")
    except ImportError:
        print("\npython-evdev is required to grab the device on Linux.")
        if _ask("install it with pip now?", assume_yes):
            _run([sys.executable, "-m", "pip", "install", "--user", "evdev"], dry)
        else:
            manual.append("pip install evdev")

    if not os.path.exists("/dev/uinput"):
        manual.append("sudo modprobe uinput   (and add it to /etc/modules)")
    if not os.access("/dev/input", os.R_OK):
        manual.append("sudo usermod -aG input $USER, then log out and back in")
    return manual


def _windows(cfg, assume_yes: bool, dry: bool) -> list[str]:
    manual = []
    from .backends import windows as be
    if not be.find_autohotkey():
        manual.append("install AutoHotkey v2: winget install AutoHotkey.AutoHotkey")
    rc, txt = be.ps(["-Command",
                     "reg query 'HKLM\\SYSTEM\\CurrentControlSet\\Services\\keyboard'"
                     " /v Start"])
    if not (rc == 0 and "Start" in txt):
        manual.append("install the Interception driver "
                      "(install-interception.exe /install, as admin), then "
                      "restart just the device — no reboot needed")
    return manual


def run(cfg, assume_yes: bool = False, dry_run: bool = False) -> int:
    env = envinfo.detect()
    print(f"keyremap setup — {env}\n")
    print("This installs only what you approve, and skips anything already done.")

    if env == "macos":
        manual = _macos(cfg, assume_yes, dry_run)
    elif env == "linux":
        manual = _linux(cfg, assume_yes, dry_run)
    else:
        manual = _windows(cfg, assume_yes, dry_run)

    print("\n" + "-" * 62)
    rows = doctor.run(cfg)
    blockers = [r for r in rows if r[0] == FAIL]
    for status, label, detail, fix in rows:
        icon = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
        print(f"  {icon} {label:<34}{detail}")

    if manual:
        print("\nOnly you can finish these (macOS and Linux require a human for\n"
              "permission grants — that is the security model, not a limitation):")
        for i, step in enumerate(manual, 1):
            print(f"  {i}. {step}")

    if blockers:
        print("\nRe-run 'keyremap setup' after the steps above.")
        return 1
    print("\nNothing is blocking. Next:  keyremap selftest && keyremap apply")
    return 0
