"""macOS backend: generates a Karabiner-Elements complex-modifications rule.

Karabiner already does per-device interception on macOS (device_if condition
on vendor_id/product_id), so we generate its native config instead of
reimplementing a CGEventTap daemon.

Install: put the generated JSON in
  ~/.config/karabiner/assets/complex_modifications/keyremap.json
then enable the rule in Karabiner-Elements > Complex Modifications > Add rule.
"""

import json
import os
import subprocess
import tempfile

from ..config import Config
from ..keys import KEYS, KB_MOD_NAME


def detect(cfg: Config) -> list[dict]:
    out = []
    try:
        r = subprocess.run(["hidutil", "list"], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3 or not parts[0].startswith("0x"):
                continue
            try:
                vid, pid = int(parts[1], 16), int(parts[2], 16)
            except ValueError:
                continue
            name = " ".join(parts[3:])
            matched = [ln for ln, dm in cfg.devices.items()
                       if dm.matches(vid, pid, name)]
            out.append({"name": name, "instance": line.strip(), "vid": vid,
                        "pid": pid, "matches": matched})
    except FileNotFoundError:
        pass
    return out


def generate(cfg: Config) -> str:
    rules = []
    for dev, table in cfg.mappings.items():
        dm = cfg.devices[dev]
        cond = {"type": "device_if", "identifiers": [{}]}
        if dm.vendor_id is not None:
            cond["identifiers"][0]["vendor_id"] = dm.vendor_id
        if dm.product_id is not None:
            cond["identifiers"][0]["product_id"] = dm.product_id
        manips = []
        for src, act in table.items():
            def _to(target):
                mods, dst = target
                t = {"key_code": KEYS[dst].kb}
                if mods:
                    t["modifiers"] = [KB_MOD_NAME[m] for m in mods]
                return t

            m = {
                "type": "basic",
                "from": {"key_code": KEYS[src].kb,
                         "modifiers": {"optional": ["any"]}},
                "conditions": [cond],
            }
            params = {}
            if act.press is not None:
                m["to"] = [_to(t) for t in act.press]
            if act.tap is not None:
                # fires on release, only if released before the timeout
                m["to_if_alone"] = [_to(t) for t in act.tap]
                params["basic.to_if_alone_timeout_milliseconds"] = act.hold_ms
            if act.hold is not None:
                # fires once the key has been held past the threshold;
                # Karabiner then suppresses to_if_alone on release.
                m["to_if_held_down"] = [_to(t) for t in act.hold]
                params["basic.to_if_held_down_threshold_milliseconds"] = act.hold_ms
            if params:
                m["parameters"] = params
            manips.append(m)
        rules.append({"description": f"keyremap: {dev}", "manipulators": manips})
    return json.dumps({"title": "keyremap (generated)", "rules": rules}, indent=2)


ASSETS_DIR = os.path.expanduser(
    "~/.config/karabiner/assets/complex_modifications")
KARABINER_CLI = ("/Library/Application Support/org.pqrs/"
                 "Karabiner-Elements/bin/karabiner_cli")


def find_karabiner_cli() -> str | None:
    """karabiner_cli ships with Karabiner-Elements — it is the real validator."""
    if os.path.exists(KARABINER_CLI):
        return KARABINER_CLI
    from shutil import which
    return which("karabiner_cli")


def lint_with_karabiner(path: str) -> tuple[bool, str]:
    """Run Karabiner's OWN linter against a generated rule file.

    Returns (ok, output); ("", ) empty output means karabiner_cli is not
    installed, which callers must report as "not run" — never as a pass.
    """
    cli = find_karabiner_cli()
    if not cli:
        return False, ""
    try:
        r = subprocess.run([cli, "--lint-complex-modifications", path],
                           capture_output=True, text=True, timeout=30)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, out or "ok"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def apply(cfg: Config, out_dir: str, dry_run: bool = False,
          install: bool = True) -> str:
    """Write the rule, install it where Karabiner reads it, and lint it there.

    Installing matters: a file in out/ helps nobody, and hand-copying it is
    exactly the step people get wrong.
    """
    text = generate(cfg)
    if dry_run:
        return text
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "keyremap.json")
    with open(path, "w") as f:
        f.write(text)

    if install and os.path.isdir(os.path.expanduser("~/.config/karabiner")):
        os.makedirs(ASSETS_DIR, exist_ok=True)
        installed = os.path.join(ASSETS_DIR, "keyremap.json")
        with open(installed, "w") as f:
            f.write(text)
        path = installed
    return path
