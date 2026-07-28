"""`keyremap adopt` — point the config at the device THIS machine actually sees.

The failure this prevents: a device's vendor/product id is read through a
different stack on each OS (SetupAPI on Windows, IOKit on macOS, evdev on
Linux). If they disagree even slightly, the generated rule matches nothing and
the keypad silently does nothing — the worst possible failure, because it looks
like the tool ran fine.

`adopt` lists what this machine reports, picks the device, and rewrites just
the vendor_id/product_id lines for that device in the config. Everything else
in the file — comments, layers, formatting — is left byte-for-byte alone.
"""

import re


def candidates(cfg):
    from .backends import get_backend
    from . import envinfo
    devices = get_backend(envinfo.detect()).detect(cfg)
    # keyboards with usable ids, most-likely-keypad first
    scored = []
    for d in devices:
        if d.get("vid") is None or d.get("pid") is None:
            continue
        name = (d.get("name") or "").lower()
        score = 0
        if "keypad" in name:
            score += 3
        if "keyboard" in name:
            score += 1
        if d.get("matches"):
            score += 5
        scored.append((-score, d))
    scored.sort(key=lambda t: t[0])
    return [d for _, d in scored]


def rewrite_ids(text: str, device: str, vid: int, pid: int) -> tuple[str, bool]:
    """Set vendor_id/product_id inside `device:`'s match block.

    Line-oriented on purpose: the config is hand-written and full of comments
    worth keeping, and there is no YAML writer in the stdlib.
    """
    lines = text.splitlines(keepends=True)
    out, in_device, changed_v, changed_p = [], False, False, False
    dev_indent = None

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if re.match(rf"^{re.escape(device)}\s*:\s*$", stripped):
            in_device, dev_indent = True, indent
            out.append(line)
            continue

        if in_device and stripped and indent <= dev_indent and \
                not stripped.startswith("#"):
            in_device = False   # left the device block

        if in_device:
            m = re.match(r"^(\s*vendor_id\s*:\s*)(\S+)(.*)$", line)
            if m:
                out.append(f"{m.group(1)}0x{vid:04X}{m.group(3)}\n"
                           if line.endswith("\n")
                           else f"{m.group(1)}0x{vid:04X}{m.group(3)}")
                changed_v = True
                continue
            m = re.match(r"^(\s*product_id\s*:\s*)(\S+)(.*)$", line)
            if m:
                out.append(f"{m.group(1)}0x{pid:04X}{m.group(3)}\n"
                           if line.endswith("\n")
                           else f"{m.group(1)}0x{pid:04X}{m.group(3)}")
                changed_p = True
                continue
        out.append(line)

    return "".join(out), (changed_v and changed_p)


def run(cfg, device: str | None = None, index: int | None = None,
        apply_changes: bool = True) -> int:
    devs = candidates(cfg)
    if not devs:
        # Be specific: "found nothing" and "found it but it reports no ids"
        # are different problems with different fixes.
        from . import envinfo
        from .backends import get_backend
        seen = get_backend(envinfo.detect()).detect(cfg)
        print("no input device reported a vendor/product id.")
        if seen:
            print(f"\n{len(seen)} input device(s) are visible, without ids:")
            for d in seen:
                print(f"  - {d['name']}")
            print("\nA Bluetooth device usually only exposes its HID ids while "
                  "it is awake and connected.\nPress a key on it, then run "
                  "'keyremap adopt' again.")
        else:
            print("connect the device, then run 'keyremap adopt' again.")
        return 1

    target_name = device or (list(cfg.devices) or ["keypad"])[0]
    if target_name not in cfg.devices:
        print(f"config has no device named {target_name!r}; "
              f"known: {', '.join(cfg.devices) or 'none'}")
        return 1

    print(f"devices this machine reports (adopting into '{target_name}'):\n")
    for i, d in enumerate(devs):
        mark = "  <-- currently matches" if d.get("matches") else ""
        print(f"  [{i}] 0x{d['vid']:04X}:0x{d['pid']:04X}  {d['name']}{mark}")

    if index is None:
        already = [d for d in devs if d.get("matches")]
        if already:
            print(f"\n'{target_name}' already matches "
                  f"0x{already[0]['vid']:04X}:0x{already[0]['pid']:04X} — "
                  "nothing to change.")
            return 0
        index = 0
        print(f"\npicking [0] (best guess). Re-run with --index N to choose.")

    if index < 0 or index >= len(devs):
        print(f"no device at index {index}")
        return 1
    chosen = devs[index]

    with open(cfg.path, encoding="utf-8") as f:
        text = f.read()
    new_text, ok = rewrite_ids(text, target_name, chosen["vid"], chosen["pid"])
    if not ok:
        print(f"could not find vendor_id/product_id under '{target_name}' — "
              "edit the config by hand.")
        return 1
    if not apply_changes:
        print("\n--dry-run: would write:")
        print(f"  {target_name}: 0x{chosen['vid']:04X}:0x{chosen['pid']:04X}")
        return 0

    backup = cfg.path + ".before-adopt"
    with open(backup, "w", encoding="utf-8") as f:
        f.write(text)
    with open(cfg.path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"\nadopted 0x{chosen['vid']:04X}:0x{chosen['pid']:04X} "
          f"({chosen['name']}) into '{target_name}'")
    print(f"previous config saved to {backup}")
    print("next: keyremap selftest && keyremap apply")
    return 0
