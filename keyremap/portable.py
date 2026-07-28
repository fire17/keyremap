"""Move a setup between computers.

`export` writes ONE self-contained file: the config document plus provenance
(which machine it came from, which device it targets, when). `import` drops it
in place on the new machine. Because devices are identified by hardware id and
the mappings live in the portable `base` layer, the same file reproduces the
same behaviour on macOS, Windows and Linux without edits.
"""

import json
import os
import time

from .config import current_env

MAGIC = "keyremap-bundle"
VERSION = 1


def export_bundle(cfg, out_path: str | None = None) -> str:
    plat, host = current_env()
    bundle = {
        "magic": MAGIC,
        "bundle_version": VERSION,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "exported_from": {"platform": plat, "host": host},
        "devices": {
            name: {"fingerprint": dm.fingerprint,
                   "vendor_id": dm.vendor_id, "product_id": dm.product_id,
                   "name_contains": dm.name_contains}
            for name, dm in cfg.devices.items()
        },
        "summary": {
            "mappings": sum(len(t) for t in cfg.mappings.values()),
            "layers": cfg.layers_available,
        },
        "config": cfg.raw,   # the document verbatim — layers and all
    }
    out_path = out_path or os.path.join(
        os.path.dirname(cfg.path), f"keyremap-{host}.keyremap")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)
    return out_path


def read_bundle(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("magic") != MAGIC:
        raise ValueError(f"{path} is not a keyremap bundle")
    if int(data.get("bundle_version", 0)) > VERSION:
        raise ValueError("bundle was written by a newer keyremap — upgrade first")
    return data


def import_bundle(path: str, dest_dir: str, filename: str = "config.yaml") -> str:
    """Write the bundle's config into dest_dir. Existing config is backed up."""
    data = read_bundle(path)
    doc = data["config"]
    dest = os.path.join(dest_dir, filename)

    if os.path.exists(dest):
        backup = dest + time.strftime(".bak-%Y%m%d-%H%M%S")
        os.replace(dest, backup)

    # The destination extension decides the format — writing YAML into a
    # .json file (or vice versa) produces a config nothing can read.
    want_json = dest.endswith(".json")
    if not want_json:
        try:
            import yaml
            text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
        except ImportError:
            from . import miniyaml  # noqa: F401  (reader only, no writer)
            want_json = True        # fall back to a format we can always write
            dest = os.path.splitext(dest)[0] + ".json"
    if want_json:
        text = json.dumps(doc, indent=2)

    header = (f"# imported from {data['exported_from']['host']} "
              f"({data['exported_from']['platform']}) "
              f"on {data['exported_at']}\n")
    with open(dest, "w", encoding="utf-8") as f:
        if not want_json:
            f.write(header)   # JSON has no comments
        f.write(text)
    return dest
