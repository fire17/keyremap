#!/usr/bin/env python3
"""keyremap - portable per-device key remapping.

  tui               open the control room (default with no arguments)
  gui               open the desktop app
  detect            list input devices, mark which match config
  listen [SECONDS]  stream keydowns tagged with their source device
  check             validate config against every backend
  doctor            what this machine is missing, and the exact fix
  apply             build/run remapping for this OS
  export / import   move your setup to another computer

Works on Windows, WSL (drives Windows via interop), Linux (evdev) and macOS
(Karabiner rules). Same config file everywhere.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keyremap import envinfo, state
from keyremap.config import load, find_config, lint

HERE = os.path.dirname(os.path.abspath(__file__))


def get_backend(env: str):
    from keyremap.backends import get_backend as gb
    return gb(env)


def cmd_detect(cfg, env, _args):
    devices = get_backend(env).detect(cfg)
    if not devices:
        print("no input devices found (permissions?)")
        return 1
    for d in devices:
        vid = f"0x{d['vid']:04X}" if d.get("vid") is not None else "----"
        pid = f"0x{d['pid']:04X}" if d.get("pid") is not None else "----"
        tag = f"  <-- matches: {', '.join(d['matches'])}" if d["matches"] else ""
        print(f"[{vid}:{pid}] {d['name']}{tag}")
        print(f"    {d['instance']}")
    return 0


def cmd_listen(cfg, env, args):
    be = get_backend(env)
    for line in be.listen(cfg, seconds=args.seconds):
        tag = ""
        low = line.lower()
        for logical, dm in cfg.devices.items():
            vidpid = (dm.vendor_id is not None and dm.product_id is not None and
                      f"vid&02{dm.vendor_id:04x}_pid&{dm.product_id:04x}" in low)
            byname = dm.name_contains and dm.name_contains.lower() in low
            if vidpid or byname:
                tag = f"   <== [{logical}]"
                break
        print(line + tag, flush=True)
    return 0


def cmd_check(cfg, env, _args):
    from keyremap.keys import KEYS
    n = sum(len(t) for t in cfg.mappings.values())
    print(f"config: {cfg.path}  (v{cfg.version})")
    print(f"env: {env}   layers applied: {' -> '.join(cfg.layers_applied) or 'none'}")
    if cfg.layers_available:
        inactive = [l for l in cfg.layers_available if l not in cfg.layers_applied]
        if inactive:
            print(f"layers present but not for this machine: {', '.join(inactive)}")
    print(f"devices: {len(cfg.devices)}  mappings: {n}")
    for dev, table in cfg.mappings.items():
        for src, act in table.items():
            origin = cfg.origin.get(dev, {}).get(src, "base")
            print(f"  {dev}: {src} ({KEYS[src].ev}) -> {act.describe()}   [{origin}]")
    problems = lint(cfg)
    for p in problems:
        print(f"  ! {p}")
    print("OK: all keys resolve in every backend table" if not problems
          else f"{len(problems)} lint note(s) above")
    return 0


def cmd_doctor(cfg, env, _args):
    from keyremap import doctor
    rows = doctor.run(cfg)
    icons = {"ok": "✓", "warn": "!", "fail": "✗"}
    worst = 0
    for status, label, detail, fix in rows:
        print(f" {icons[status]} {label:<26}{detail}")
        if fix:
            print(f"     -> {fix}")
        worst = max(worst, {"ok": 0, "warn": 1, "fail": 2}[status])
    return 0 if worst < 2 else 1


def cmd_apply(cfg, env, args):
    out_dir = args.out or os.path.join(HERE, "out", env)
    import time
    if env in ("windows", "wsl"):
        be = get_backend(env)
        res = be.apply(cfg, out_dir, mode=args.mode, dry_run=args.dry_run)
        if args.dry_run:
            print(res)
            return 0
        print(f"wrote {res}")
        print("Run it with AutoHotkey v2. Interception mode needs the driver + "
              "AutoHotInterception (see README).")
    elif env == "linux":
        be = get_backend(env)
        if args.dry_run:
            print("dry-run: would grab matched devices and remap live")
            return 0
        be.run(cfg)  # blocks
        return 0
    else:
        be = get_backend(env)
        res = be.apply(cfg, out_dir, dry_run=args.dry_run)
        if args.dry_run:
            print(res)
            return 0
        print(f"wrote {res}")
        print("Copy into ~/.config/karabiner/assets/complex_modifications/ "
              "and enable it in Karabiner-Elements.")
    n = sum(len(t) for t in cfg.mappings.values())
    state.save_state(state.Deployment(
        applied_at=time.time(), config_sha=state.config_sha(cfg.path),
        backend=env, artifact=str(res), mappings=n))
    return 0


def cmd_tui(cfg, env, args):
    from keyremap.tui import main as tui_main
    return tui_main(cfg.path)


def cmd_gui(cfg, env, args):
    """Native window when tkinter exists, otherwise the browser UI.

    Both are first-class and share the same view-model, so nobody is stuck
    without a GUI just because their Python was built without tcl/tk.
    """
    if not args.web:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            print("tkinter not available — opening the web UI instead "
                  "(use --web to skip this check)")
        else:
            from keyremap.gui import main as gui_main
            return gui_main(cfg.path)
    from keyremap.webgui import serve
    return serve(cfg.path, port=args.port, open_browser=not args.no_open)


def cmd_export(cfg, env, args):
    from keyremap.portable import export_bundle
    path = export_bundle(cfg, args.out)
    print(f"wrote {path}")
    print("Copy it to the other computer and run:  keyremap import <file>")
    return 0


def cmd_import(cfg, env, args):
    from keyremap.portable import import_bundle
    dest = import_bundle(args.bundle, os.path.dirname(cfg.path))
    print(f"imported -> {dest}")
    print("Run 'keyremap doctor' then 'keyremap apply' to activate it here.")
    return 0


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", "-c", help="path to config.yaml/json")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("tui")
    gp = sub.add_parser("gui")
    gp.add_argument("--web", action="store_true", help="force the browser UI")
    gp.add_argument("--port", type=int, default=0, help="web UI port (0 = auto)")
    gp.add_argument("--no-open", action="store_true",
                    help="don't launch a browser")
    sub.add_parser("detect")
    lp = sub.add_parser("listen")
    lp.add_argument("seconds", nargs="?", type=int, default=30)
    sub.add_parser("check")
    sub.add_parser("doctor")
    ap = sub.add_parser("apply")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mode", choices=["interception", "heuristic"],
                    default="interception")
    ap.add_argument("--out")
    ep = sub.add_parser("export")
    ep.add_argument("--out", default=None)
    ip = sub.add_parser("import")
    ip.add_argument("bundle")

    args = p.parse_args()
    cfg = load(args.config or find_config(HERE))
    env = envinfo.detect()
    cmd = args.cmd or "tui"
    return {
        "tui": cmd_tui, "gui": cmd_gui, "detect": cmd_detect, "listen": cmd_listen,
        "check": cmd_check, "doctor": cmd_doctor, "apply": cmd_apply,
        "export": cmd_export, "import": cmd_import,
    }[cmd](cfg, env, args)


if __name__ == "__main__":
    sys.exit(main())
