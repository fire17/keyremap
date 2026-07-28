"""Linux backend: live per-device remapping via evdev grab + uinput.

Works on any real Linux (not WSL - WSL has no /dev/input; there the Windows
backend is used via interop). Requires: pip install evdev, and read access to
/dev/input (root or an 'input'-group udev rule) plus /dev/uinput.
"""

from ..config import Config
from ..keys import KEYS, EV_MOD_NAME


def _import_evdev():
    try:
        import evdev  # noqa
        return evdev
    except ImportError:
        raise SystemExit(
            "python-evdev is required on Linux: pip install evdev\n"
            "Also ensure access to /dev/input/event* and /dev/uinput.")


def _device_ids(dev) -> tuple[int, int, str]:
    info = dev.info  # BusType, vendor, product, version
    return info.vendor, info.product, dev.name


def detect(cfg: Config) -> list[dict]:
    evdev = _import_evdev()
    out = []
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        caps = d.capabilities()
        if evdev.ecodes.EV_KEY not in caps:
            continue
        vid, pid, name = _device_ids(d)
        matched = [ln for ln, dm in cfg.devices.items()
                   if dm.matches(vid, pid, name)]
        out.append({"name": name, "instance": path, "vid": vid, "pid": pid,
                    "matches": matched})
    return out


def listen(cfg: Config, seconds: int = 30):
    import select
    import time
    evdev = _import_evdev()
    devs = {}
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if evdev.ecodes.EV_KEY in d.capabilities():
            devs[d.fd] = d
    if not devs:
        yield "ERROR: no readable keyboards (run as root or join 'input' group)"
        return
    yield f"LISTENING on {len(devs)} device(s)..."
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        r, _, _ = select.select(list(devs), [], [], 0.5)
        for fd in r:
            d = devs[fd]
            for ev in d.read():
                if ev.type == evdev.ecodes.EV_KEY and ev.value == 1:
                    name = evdev.ecodes.KEY[ev.code] if ev.code in evdev.ecodes.KEY else ev.code
                    yield f"KEYDOWN {name} | device={d.name} ({d.path})"
    yield "DONE listening."


def run(cfg: Config):
    """Grab matched devices and remap live. Blocks; run under systemd."""
    import select
    evdev = _import_evdev()
    from evdev import UInput, ecodes

    ev_of = {name: getattr(ecodes, k.ev) for name, k in KEYS.items()
             if hasattr(ecodes, k.ev)}
    # Portable "accel" modifier resolves to Ctrl on Linux.
    for virt, evname in EV_MOD_NAME.items():
        if hasattr(ecodes, evname):
            ev_of[virt] = getattr(ecodes, evname)

    # source evdev code -> Action, per matched device
    grabbed = {}
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if ecodes.EV_KEY not in d.capabilities():
            continue
        vid, pid, name = _device_ids(d)
        for logical, dm in cfg.devices.items():
            if logical in cfg.mappings and dm.matches(vid, pid, name):
                table = {ev_of[src]: act
                         for src, act in cfg.mappings[logical].items()}
                if cfg.swallow_numlock_quirk:
                    table.setdefault(ecodes.KEY_NUMLOCK, None)  # None = swallow
                d.grab()
                grabbed[d.fd] = (d, table)
                print(f"grabbed {name} ({path}) for '{logical}' "
                      f"({len(cfg.mappings[logical])} mappings)")
    if not grabbed:
        raise SystemExit("no configured device found - check `remap.py detect`")

    ui = UInput()  # all standard keys

    def tap(target):
        """Press and release a (mods, key) target."""
        mods, dst = target
        code = ev_of[dst]
        for m in mods:
            ui.write(ecodes.EV_KEY, ev_of[m], 1)
        ui.write(ecodes.EV_KEY, code, 1)
        ui.write(ecodes.EV_KEY, code, 0)
        for m in reversed(mods):
            ui.write(ecodes.EV_KEY, ev_of[m], 0)
        ui.syn()

    # source code -> (deadline, Action) for a pending hold
    pending: dict[int, tuple[float, object]] = {}
    held: set[int] = set()        # physically down (auto-repeat guard)
    hold_fired: set[int] = set()  # hold ran; suppress tap on release

    try:
        while True:
            now = time.monotonic()
            # fire any hold actions whose deadline passed
            for code in [c for c, (dl, _) in pending.items() if dl <= now]:
                _, act_due = pending.pop(code)
                hold_fired.add(code)
                for t in act_due.hold:
                    tap(t)
            timeout = min((dl - now for dl, _ in pending.values()), default=None)
            r, _, _ = select.select(list(grabbed), [], [], timeout)
            for fd in r:
                d, table = grabbed[fd]
                for ev in d.read():
                    if ev.type != ecodes.EV_KEY or ev.code not in table:
                        if cfg.passthrough_unmapped or ev.type != ecodes.EV_KEY:
                            ui.write_event(ev)
                            ui.syn()
                        continue
                    act = table[ev.code]
                    if act is None:  # swallowed (numlock quirk)
                        continue
                    if not act.is_simple:
                        if ev.value == 1:
                            if ev.code in pending or ev.code in held:
                                continue  # auto-repeat
                            held.add(ev.code)
                            for t in (act.press or []):
                                tap(t)
                            if act.hold is not None:
                                pending[ev.code] = (
                                    time.monotonic() + act.hold_ms / 1000, act)
                        elif ev.value == 0:
                            held.discard(ev.code)
                            was_pending = pending.pop(ev.code, None) is not None
                            fired = ev.code in hold_fired
                            hold_fired.discard(ev.code)
                            # tap fires only if the hold never fired
                            if act.tap is not None and not fired and (
                                    was_pending or act.hold is None):
                                for t in act.tap:
                                    tap(t)
                        continue
                    mods, dst = act.press[0]
                    dst_code = ev_of[dst]
                    if ev.value == 1:  # down: press mods, then key
                        for m in mods:
                            ui.write(ecodes.EV_KEY, ev_of[m], 1)
                        ui.write(ecodes.EV_KEY, dst_code, 1)
                    elif ev.value == 0:  # up: release key, then mods
                        ui.write(ecodes.EV_KEY, dst_code, 0)
                        for m in reversed(mods):
                            ui.write(ecodes.EV_KEY, ev_of[m], 0)
                    else:  # repeat
                        ui.write(ecodes.EV_KEY, dst_code, 2)
                    ui.syn()
    finally:
        for d, _ in grabbed.values():
            try:
                d.ungrab()
            except OSError:
                pass
        ui.close()


SYSTEMD_UNIT = """\
[Unit]
Description=keyremap per-device key remapper
After=multi-user.target

[Service]
ExecStart={python} {remap_py} apply
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""
