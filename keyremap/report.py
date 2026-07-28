"""`keyremap report` — one pasteable block containing everything a helper needs.

When something misbehaves on a machine nobody else can touch, the slow part is
the back-and-forth: what OS, which device, which layers, what does doctor say,
what did it actually generate. This gathers all of it at once, redacts the
obvious personal bits, and prints markdown you can paste anywhere.

Read-only: it never changes a config, installs anything, or contacts a network.
"""

import getpass
import hashlib
import os
import platform
import sys


def _redact(text: str) -> str:
    """Keep paths useful without leaking a username.

    Must handle BOTH conventions: a WSL session reports POSIX paths for itself
    and Windows paths for the tools it drives, and the two usernames differ —
    redacting only the local one leaked the Windows account name.
    """
    import re
    out = text.replace(os.path.expanduser("~"), "~")
    user = getpass.getuser()
    if user and len(user) > 2:
        out = out.replace(user, "<user>")
    # C:\Users\Someone\... and /Users/someone/... and /home/someone/...
    out = re.sub(r"(?i)([A-Z]:\\Users\\)[^\\\s]+", r"\1<user>", out)
    out = re.sub(r"(/Users/|/home/)[^/\s]+", r"\1<user>", out)
    return out


def build(cfg) -> str:
    from . import doctor, envinfo, validate
    from .config import load
    from .keys import KEYS

    L = []
    add = L.append

    add("### keyremap report\n")
    add("| field | value |")
    add("|---|---|")
    add(f"| os | {platform.platform()} |")
    add(f"| python | {sys.version.split()[0]} |")
    add(f"| env | {envinfo.detect()} |")
    add(f"| config | `{_redact(cfg.path)}` (v{cfg.version}) |")
    add(f"| layers applied | {' → '.join(cfg.layers_applied) or 'none'} |")
    add(f"| layers present | {', '.join(cfg.layers_available) or 'none'} |")
    add(f"| mappings | {sum(len(t) for t in cfg.mappings.values())} |")
    for name, fp in cfg.device_fingerprints().items():
        add(f"| device `{name}` | {fp} |")

    add("\n**Devices this machine reports**\n")
    try:
        from .backends import get_backend
        devices = get_backend(envinfo.detect()).detect(cfg)
        if not devices:
            add("_none found_")
        for d in devices:
            vid = f"0x{d['vid']:04X}" if d.get("vid") is not None else "----"
            pid = f"0x{d['pid']:04X}" if d.get("pid") is not None else "----"
            mark = " **← matches config**" if d.get("matches") else ""
            add(f"- `{vid}:{pid}` {_redact(d['name'])}{mark}")
    except Exception as e:  # noqa: BLE001
        add(f"_detection failed: {type(e).__name__}: {e}_")

    add("\n**Doctor**\n")
    try:
        for status, label, detail, fix in doctor.run(cfg):
            icon = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
            add(f"- {icon} **{label}** — {_redact(detail)}"
                + (f"  _fix: {fix}_" if fix else ""))
    except Exception as e:  # noqa: BLE001
        add(f"_doctor failed: {type(e).__name__}: {e}_")

    # A fingerprint of RESOLVED BEHAVIOUR, independent of platform artifacts.
    # Compare it between two machines: identical hash == identical mapping,
    # which is exactly the promise this project makes.
    behaviour = "\n".join(
        f"{dev}.{src}={act.describe()}"
        for dev, table in sorted(cfg.mappings.items())
        for src, act in sorted(table.items()))
    add(f"\n**Behaviour hash**: `"
        f"{hashlib.sha256(behaviour.encode()).hexdigest()[:16]}`  "
        f"— same hash on two machines means the same effective mapping\n")

    add("\n**Generated artifact**\n")
    try:
        plat = {"macos": "darwin", "wsl": "windows",
                "windows": "windows", "linux": "linux"}[envinfo.detect()]
        c = load(cfg.path, plat=plat, host="report")
        if plat == "darwin":
            from .backends import macos as be
            text = be.generate(c)
            problems = validate.validate_karabiner(text)
        elif plat == "windows":
            from .backends import windows as be
            text = be.generate_interception(c)
            problems = validate.validate_ahk(text)
        else:
            text = ""
            problems = validate.validate_linux(c)
        add(f"- target: `{plat}`")
        if text:
            add(f"- sha256: `{hashlib.sha256(text.encode()).hexdigest()[:16]}` "
                f"({len(text)} bytes)")
        add(f"- validation: {'clean' if not problems else str(problems[:3])}")
    except Exception as e:  # noqa: BLE001
        add(f"_generation failed: {type(e).__name__}: {e}_")

    add("\n**Effective mappings**\n")
    add("| key | does | layer | scancode |")
    add("|---|---|---|---|")
    for dev, table in cfg.mappings.items():
        for src, act in table.items():
            k = KEYS[src]
            sc = f"0x{k.sc | (0x100 if k.ext else 0):X}"
            origin = cfg.origin.get(dev, {}).get(src, "base")
            add(f"| `{src}` | {act.describe()} | {origin} | `{sc}` |")

    return "\n".join(L) + "\n"


def run(cfg) -> int:
    print(build(cfg))
    print("(paste the block above — it contains no usernames, keys or tokens)")
    return 0
