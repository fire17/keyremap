"""`keyremap doctor` — tell the user exactly what is missing and how to fix it.

Every check returns (ok, label, detail, fix). Nothing here mutates the system;
the point is that a fresh machine (especially a Mac) can be diagnosed in one
command instead of by trial and error.
"""

import os
import shutil
import subprocess

from . import envinfo

OK, WARN, FAIL = "ok", "warn", "fail"


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def _check_python():
    import sys
    v = sys.version_info
    ok = v >= (3, 10)
    return (OK if ok else FAIL, "Python >= 3.10",
            f"{v.major}.{v.minor}.{v.micro}",
            "" if ok else "install a newer Python 3")


def _check_yaml():
    try:
        import yaml  # noqa: F401
        return OK, "YAML parser", "pyyaml", ""
    except ImportError:
        return OK, "YAML parser", "built-in reader (no pyyaml needed)", ""


def _checks_macos(cfg):
    out = []
    kar = "/Applications/Karabiner-Elements.app"
    ok = os.path.exists(kar)
    out.append((OK if ok else FAIL, "Karabiner-Elements",
                "installed" if ok else "not installed",
                "" if ok else "brew install --cask karabiner-elements"))
    d = os.path.expanduser("~/.config/karabiner/assets/complex_modifications")
    ok2 = os.path.isdir(d)
    out.append((OK if ok2 else WARN, "Karabiner config dir",
                d if ok2 else "not created yet",
                "" if ok2 else "launch Karabiner-Elements once to create it"))
    rc, _ = _run(["pgrep", "-x", "karabiner_grabber"])
    out.append((OK if rc == 0 else WARN, "Karabiner engine",
                "running" if rc == 0 else "not running",
                "" if rc == 0 else "open Karabiner-Elements and grant Input Monitoring"))
    ok3 = shutil.which("hidutil") is not None
    out.append((OK if ok3 else WARN, "hidutil (device listing)",
                "present" if ok3 else "missing", ""))

    # The decisive check: hand our generated rule to Karabiner's own linter.
    from .backends import macos as mac
    cli = mac.find_karabiner_cli()
    if not cli:
        out.append((WARN, "karabiner_cli lint", "karabiner_cli not found",
                    "install Karabiner-Elements to enable the real linter"))
    else:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            f.write(mac.generate(cfg))
            tmp = f.name
        try:
            ok4, detail = mac.lint_with_karabiner(tmp)
            out.append((OK if ok4 else FAIL, "karabiner_cli lint",
                        detail[:70] if detail else "no output",
                        "" if ok4 else "the generated rule was rejected — "
                                       "please report this output"))
        finally:
            os.unlink(tmp)
    return out


def _checks_linux(cfg):
    out = []
    try:
        import evdev  # noqa: F401
        out.append((OK, "python-evdev", "present", ""))
    except ImportError:
        out.append((FAIL, "python-evdev", "missing", "pip install evdev"))
    readable = os.access("/dev/input", os.R_OK)
    out.append((OK if readable else FAIL, "/dev/input access",
                "readable" if readable else "permission denied",
                "" if readable else
                "sudo usermod -aG input $USER && re-login, or run with sudo"))
    ui = os.path.exists("/dev/uinput")
    out.append((OK if ui else FAIL, "/dev/uinput",
                "present" if ui else "missing",
                "" if ui else "sudo modprobe uinput"))
    return out


def _checks_windows(cfg):
    out = []
    from .backends import windows as be
    ahk = be.find_autohotkey()
    out.append((OK if ahk else FAIL, "AutoHotkey v2",
                ahk or "not found",
                "" if ahk else "winget install AutoHotkey.AutoHotkey"))
    rc, txt = be.ps(["-Command",
                     "reg query 'HKLM\\SYSTEM\\CurrentControlSet\\Services\\keyboard'"
                     " /v Start"])
    drv = rc == 0 and "Start" in txt
    out.append((OK if drv else WARN, "Interception driver",
                "installed" if drv else "not installed",
                "" if drv else
                "run install-interception.exe /install as admin, then "
                "restart the device (no reboot needed)"))
    lib = be.lib_dir_hint()
    out.append((OK if lib else WARN, "AutoHotInterception Lib",
                lib or "not found next to the generated script",
                "" if lib else
                "download AutoHotInterception and place Lib/ beside the .ahk"))
    return out


def run(cfg) -> list[tuple[str, str, str, str]]:
    env = envinfo.detect()
    results = [_check_python(), _check_yaml()]
    try:
        if env == "macos":
            results += _checks_macos(cfg)
        elif env == "linux":
            results += _checks_linux(cfg)
        else:
            results += _checks_windows(cfg)
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, "platform checks", f"{type(e).__name__}: {e}", ""))

    # device presence is the same question everywhere
    try:
        from .backends import get_backend
        devices = get_backend(env).detect(cfg)
        matched = [d for d in devices if d.get("matches")]
        results.append((OK if matched else WARN, "configured device",
                        matched[0]["name"] if matched else
                        f"not connected ({len(devices)} other input devices)",
                        "" if matched else "connect/pair the device, then re-run"))
    except ImportError as e:
        results.append((WARN, "configured device",
                        "cannot enumerate devices", str(e).split("\n")[0]))
    except Exception as e:  # noqa: BLE001
        results.append((WARN, "configured device",
                        f"detect failed: {type(e).__name__}", ""))
    return results
