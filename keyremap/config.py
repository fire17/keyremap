"""Config loading + validation. YAML preferred, JSON fallback (zero-dep)."""

import json
import os
from dataclasses import dataclass, field

from .keys import canon, parse_target


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


DEFAULT_HOLD_MS = 1000


Target = tuple[list[str], str]  # (modifiers, key) from parse_target


@dataclass
class Action:
    """What a source key produces. Each field is a sequence of targets.

    press: sent immediately on key-down
    tap:   sent on RELEASE, only if released before hold_ms elapsed
    hold:  sent at the hold_ms mark while the key is still held
           (and the tap action is then suppressed on release)

    press is for plain remaps; tap/hold express "quick tap does X,
    holding does Y" — the hold action fires while the key is still down,
    so the user sees its effect as confirmation.
    """
    press: list[Target] | None = None
    tap: list[Target] | None = None
    hold: list[Target] | None = None
    hold_ms: int = DEFAULT_HOLD_MS

    @property
    def is_simple(self) -> bool:
        return self.tap is None and self.hold is None


@dataclass
class Config:
    devices: dict[str, DeviceMatch] = field(default_factory=dict)
    # device -> { source_key: Action }
    mappings: dict[str, dict[str, Action]] = field(default_factory=dict)
    passthrough_unmapped: bool = True
    swallow_numlock_quirk: bool = True
    path: str = ""


def _to_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    return int(str(v), 0)  # accepts "0x045E" and "1118"


def load(path: str) -> Config:
    with open(path) as f:
        text = f.read()
    try:
        import yaml
        raw = yaml.safe_load(text)
    except ImportError:
        raw = json.loads(text)

    cfg = Config(path=os.path.abspath(path))
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

    for dev, table in (raw.get("mappings") or {}).items():
        if dev not in cfg.devices:
            raise ValueError(f"mappings refer to unknown device {dev!r}")
        cfg.mappings[dev] = {}
        for src, dst in (table or {}).items():
            cfg.mappings[dev][canon(str(src))] = _parse_action(src, dst)
    return cfg


def _parse_seq(value) -> list[Target]:
    """One target ('accel+a') or a list of them (['accel+a', 'accel+c'])."""
    items = value if isinstance(value, list) else [value]
    return [parse_target(str(v)) for v in items]


def _parse_action(src, spec) -> Action:
    """Accept a plain target ('end', 'accel+v') or a dict:

        home:
          tap:  accel+a               # on release, if released quickly
          hold: [accel+a, accel+c]    # at the 1s mark, still held
          hold_ms: 1000               # default 1000
          # 'press:' is also available - fires immediately on key-down
    """
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


def find_config(start_dir: str) -> str:
    for name in ("config.yaml", "config.yml", "config.json"):
        p = os.path.join(start_dir, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no config.yaml/config.json found in {start_dir}")
