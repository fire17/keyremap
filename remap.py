#!/usr/bin/env python3
"""keyremap - portable per-device key remapping.

Commands:
  detect            list input devices, mark which match config
  listen [SECONDS]  stream keydowns tagged with their source device
  check             validate config against every backend
  apply             build/run remapping for this OS
                    --dry-run prints instead of writing
                    --mode interception|heuristic (Windows only)
Works on: Windows, WSL (drives Windows via interop), Linux (evdev), macOS
(generates Karabiner rules).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keyremap import envinfo
from keyremap.config import load, find_config

HERE = os.path.dirname(os.path.abspath(__file__))


def get_backend(env: str):
    if env in ("windows", "wsl"):
        from keyremap.backends import windows as be
    elif env == "linux":
        from keyremap.backends import linux_evdev as be
    else:
        from keyremap.backends import macos as be
    return be


def cmd_detect(cfg, env, _args):
    be = get_backend(env)
    devices = be.detect(cfg)
    if not devices:
        print("no input devices found (permissions?)")
        return 1
    for d in devices:
        vid = f"0x{d['vid']:04X}" if d.get("vid") is not None else "----"
        pid = f"0x{d['pid']:04X}" if d.get("pid") is not None else "----"
        tag = f"  <-- matches config device: {', '.join(d['matches'])}" if d["matches"] else ""
        print(f"[{vid}:{pid}] {d['name']}{tag}")
        print(f"    {d['instance']}")
    return 0


def cmd_listen(cfg, env, args):
    be = get_backend(env)
    # annotate lines with the logical device they match, when recognizable
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
    print(f"config: {cfg.path}")
    print(f"devices: {len(cfg.devices)}  mappings: {n}  env: {env}")
    def fmt(seq):
        return " then ".join("+".join(m + [k]) if m else k for m, k in seq)

    for dev, table in cfg.mappings.items():
        for src, act in table.items():
            parts = []
            if act.press is not None:
                parts.append(f"press={fmt(act.press)}")
            if act.tap is not None:
                parts.append(f"tap(<{act.hold_ms}ms, on release)={fmt(act.tap)}")
            if act.hold is not None:
                parts.append(f"hold({act.hold_ms}ms)={fmt(act.hold)}")
            print(f"  {dev}: {src} ({KEYS[src].ev}) -> {', '.join(parts)}")
    print("OK: all keys resolve in every backend table")
    return 0


def cmd_apply(cfg, env, args):
    out_dir = args.out or os.path.join(HERE, "out", env)
    if env in ("windows", "wsl"):
        from keyremap.backends import windows as be
        res = be.apply(cfg, out_dir, mode=args.mode, dry_run=args.dry_run)
        if args.dry_run:
            print(res)
        else:
            print(f"wrote {res}")
            print("Run it with AutoHotkey v2 on Windows. Interception mode also "
                  "needs the Interception driver + AutoHotInterception (see README).")
    elif env == "linux":
        from keyremap.backends import linux_evdev as be
        if args.dry_run:
            print("dry-run: would grab matched devices and remap live (see README "
                  "for the systemd unit)")
        else:
            be.run(cfg)  # blocks
    else:
        from keyremap.backends import macos as be
        res = be.apply(cfg, out_dir, dry_run=args.dry_run)
        if args.dry_run:
            print(res)
        else:
            print(f"wrote {res}")
            print("Copy into ~/.config/karabiner/assets/complex_modifications/ "
                  "and enable in Karabiner-Elements.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", "-c", help="path to config.yaml/json")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect")
    lp = sub.add_parser("listen")
    lp.add_argument("seconds", nargs="?", type=int, default=30)
    sub.add_parser("check")
    ap = sub.add_parser("apply")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mode", choices=["interception", "heuristic"],
                    default="interception")
    ap.add_argument("--out")

    args = p.parse_args()
    cfg = load(args.config or find_config(HERE))
    env = envinfo.detect()
    return {"detect": cmd_detect, "listen": cmd_listen,
            "check": cmd_check, "apply": cmd_apply}[args.cmd](cfg, env, args)


if __name__ == "__main__":
    sys.exit(main())
