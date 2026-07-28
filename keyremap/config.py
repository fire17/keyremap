"""Config loading, validation and LAYERED profile resolution.

A config has one portable base layer plus optional overrides that only apply on
a given OS or a given machine, so the same file can travel between computers and
produce the same behaviour everywhere — while still allowing local deviations.

    profiles:
      base:            # everywhere
        keypad: {esc: home}
      os:
        darwin:        # only on macOS
          keypad: {esc: escape}
      host:
        tamis-mac:     # only on that machine (hostname, case-insensitive)
          keypad: {home: accel+space}

Resolution order (later wins, per source key): base -> os.<platform> -> host.<name>.
A layer may also *remove* an inherited mapping with `key: null`.

v1 configs (a top-level `mappings:` block) are still accepted and are treated as
the base layer, so nothing that worked before breaks.
"""

import json
import os
import platform
import socket
from dataclasses import dataclass, field

from .keys import canon, parse_target

DEFAULT_HOLD_MS = 1000

Target = tuple[list[str], str]  # (modifiers, key) from parse_target


@dataclass
class DeviceMatch:
    vendor_id: int | None = None
    product_id: int | None = None
    name_contains: str | None = None

    def matches(self, vid: int | None, pid: int | None, name: str = "") -> bool:
        if self.vendor_id is not None and self.product_id is not None:
            if vid == self.vendor_id and pid == self.product_id:
                return True
        if self.name_contains and self.name_contains.lower() in (name or "").lower():
            return True
        return False

    @property
    def fingerprint(self) -> str:
        """Stable identity of the physical device, portable across machines."""
        if self.vendor_id is not None and self.product_id is not None:
            return f"usb:{self.vendor_id:04x}:{self.product_id:04x}"
        return f"name:{(self.name_contains or '').lower()}"


@dataclass
class Action:
    """What a source key produces. Each field is a sequence of targets.

    press: sent immediately on key-down
    tap:   sent on RELEASE, only if released before hold_ms elapsed
    hold:  sent at the hold_ms mark while the key is still held
           (and the tap action is then suppressed on release)
    """
    press: list[Target] | None = None
    tap: list[Target] | None = None
    hold: list[Target] | None = None
    hold_ms: int = DEFAULT_HOLD_MS

    @property
    def is_simple(self) -> bool:
        return self.tap is None and self.hold is None

    def describe(self) -> str:
        def fmt(seq):
            return " then ".join("+".join(m + [k]) if m else k for m, k in seq)
        bits = []
        if self.press:
            bits.append(fmt(self.press))
        if self.tap:
            bits.append(f"tap:{fmt(self.tap)}")
        if self.hold:
            bits.append(f"hold {self.hold_ms}ms:{fmt(self.hold)}")
        return "  ".join(bits)


@dataclass
class Config:
    devices: dict[str, DeviceMatch] = field(default_factory=dict)
    # resolved: device -> { source_key: Action }
    mappings: dict[str, dict[str, Action]] = field(default_factory=dict)
    passthrough_unmapped: bool = True
    swallow_numlock_quirk: bool = True
    path: str = ""
    version: int = 1
    # provenance for the UI: which layers existed and which were applied
    layers_available: list[str] = field(default_factory=list)
    layers_applied: list[str] = field(default_factory=list)
    # device -> { source_key: layer_name } — where each effective mapping came from
    origin: dict[str, dict[str, str]] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def device_fingerprints(self) -> dict[str, str]:
        return {n: d.fingerprint for n, d in self.devices.items()}


def parse_text(text: str, path: str = ""):
    """Parse a config document: JSON, or YAML via pyyaml, else the built-in reader.

    A fresh machine has no pyyaml, so YAML must still work out of the box —
    otherwise the first thing a new user meets is a JSONDecodeError on their
    perfectly valid config (observed on a clean Windows Python).
    """
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")) or path.endswith(".json"):
        return json.loads(text) or {}
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        from . import miniyaml
        return miniyaml.safe_load(text) or {}


def _to_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    return int(str(v), 0)  # accepts "0x045E" and "1118"


def current_env() -> tuple[str, str]:
    """(platform_key, host_key) used to select override layers."""
    sysname = platform.system().lower()
    plat = {"darwin": "darwin", "windows": "windows", "linux": "linux"}.get(
        sysname, sysname)
    if plat == "linux":
        try:
            with open("/proc/sys/kernel/osrelease") as f:
                if "microsoft" in f.read().lower():
                    plat = "wsl"
        except OSError:
            pass
    return plat, socket.gethostname().lower()


def _parse_seq(value) -> list[Target]:
    """One target ('accel+a') or a list of them (['accel+a', 'accel+c'])."""
    items = value if isinstance(value, list) else [value]
    return [parse_target(str(v)) for v in items]


def _parse_action(src, spec) -> Action:
    """Accept a plain target ('end', 'accel+v') or a dict with press/tap/hold."""
    if not isinstance(spec, dict):
        return Action(press=_parse_seq(spec))
    press, tap, hold = spec.get("press"), spec.get("tap"), spec.get("hold")
    if press is None and tap is None and hold is None:
        raise ValueError(
            f"mapping for {src!r} needs at least one of press/tap/hold")
    hold_ms = int(spec.get("hold_ms", DEFAULT_HOLD_MS))
    if hold_ms <= 0:
        raise ValueError(f"hold_ms for {src!r} must be positive")
    return Action(
        press=_parse_seq(press) if press is not None else None,
        tap=_parse_seq(tap) if tap is not None else None,
        hold=_parse_seq(hold) if hold is not None else None,
        hold_ms=hold_ms,
    )


def parse_document(raw: dict) -> dict:
    """Split a raw config document into its layers. Returns {name: table}."""
    layers: dict[str, dict] = {}
    profiles = raw.get("profiles")
    if profiles:
        if profiles.get("base"):
            layers["base"] = profiles["base"]
        for plat, table in (profiles.get("os") or {}).items():
            layers[f"os:{str(plat).lower()}"] = table
        for host, table in (profiles.get("host") or {}).items():
            layers[f"host:{str(host).lower()}"] = table
    if raw.get("mappings"):  # v1 document, or v2 with an extra implicit base
        layers.setdefault("base", {})
        merged = dict(layers["base"])
        merged.update(raw["mappings"])
        layers["base"] = merged
    return layers


def resolve_layers(layers: dict[str, dict], plat: str, host: str
                   ) -> tuple[dict, list[str], dict]:
    """Merge base -> os:<plat> -> host:<host>. Returns (table, applied, origin)."""
    order = ["base", f"os:{plat}", f"host:{host}"]
    applied, merged, origin = [], {}, {}
    for name in order:
        table = layers.get(name)
        if table is None:
            continue
        applied.append(name)
        for dev, keys in (table or {}).items():
            dev_map = merged.setdefault(dev, {})
            dev_origin = origin.setdefault(dev, {})
            for src, spec in (keys or {}).items():
                if spec is None:          # explicit removal in this layer
                    dev_map.pop(src, None)
                    dev_origin.pop(src, None)
                    continue
                dev_map[src] = spec
                dev_origin[src] = name
    return merged, applied, origin


def load(path: str, plat: str | None = None, host: str | None = None) -> Config:
    with open(path) as f:
        text = f.read()
    raw = parse_text(text, path)

    if plat is None or host is None:
        cur_plat, cur_host = current_env()
        plat = plat or cur_plat
        host = host or cur_host

    cfg = Config(path=os.path.abspath(path), raw=raw)
    cfg.version = int(raw.get("version", 2 if raw.get("profiles") else 1))

    for name, spec in (raw.get("devices") or {}).items():
        m = (spec or {}).get("match") or {}
        cfg.devices[name] = DeviceMatch(
            vendor_id=_to_int(m.get("vendor_id")),
            product_id=_to_int(m.get("product_id")),
            name_contains=m.get("name_contains"),
        )

    opts = raw.get("options") or {}
    cfg.passthrough_unmapped = bool(opts.get("passthrough_unmapped", True))
    cfg.swallow_numlock_quirk = bool(opts.get("swallow_numlock_quirk", True))

    layers = parse_document(raw)
    cfg.layers_available = sorted(layers)
    merged, applied, origin = resolve_layers(layers, plat, host)
    cfg.layers_applied = applied
    cfg.origin = origin

    for dev, table in merged.items():
        if dev not in cfg.devices:
            raise ValueError(f"mappings refer to unknown device {dev!r}")
        cfg.mappings[dev] = {}
        for src, spec in (table or {}).items():
            cfg.mappings[dev][canon(str(src))] = _parse_action(src, spec)
    return cfg


def find_config(start_dir: str) -> str:
    for name in ("config.yaml", "config.yml", "config.json"):
        p = os.path.join(start_dir, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no config.yaml/config.json found in {start_dir}")


def lint(cfg: Config) -> list[str]:
    """Non-fatal problems worth showing the user. Empty list == clean."""
    problems = []
    for dev, table in cfg.mappings.items():
        seen: dict[str, str] = {}
        for src, act in table.items():
            for seq in (act.press, act.tap, act.hold):
                for mods, dst in (seq or []):
                    key = "+".join(mods + [dst])
                    if key in seen and seen[key] != src:
                        problems.append(
                            f"{dev}: '{src}' and '{seen[key]}' both produce {key}")
                    seen.setdefault(key, src)
            if act.tap and act.press:
                problems.append(
                    f"{dev}: '{src}' has both press and tap — press fires on "
                    "key-down and tap on release, which is rarely intended")
    for dev in cfg.devices:
        if dev not in cfg.mappings or not cfg.mappings[dev]:
            problems.append(f"device '{dev}' is declared but has no mappings")
    return problems
