#!/usr/bin/env python3
"""Real-kernel test for the Linux backend. Needs /dev/uinput and root.

Creates a virtual keyboard carrying the configured vendor/product id, lets
keyremap grab it, injects key events, and asserts the *remapped* events come
out of keyremap's uinput device. Nothing is stubbed: real evdev, real grab,
real kernel event plumbing.

Run: sudo python3 tests/linux_live_test.py
"""

import os
import sys
import time
import multiprocessing as mp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evdev
from evdev import UInput, ecodes

VENDOR, PRODUCT = 0x045E, 0x0040
VIRT_NAME = "keyremap test keypad"

# The mapping under test, written as a config the daemon will load.
CONFIG = """{
  "version": 2,
  "devices": {"kp": {"match": {"vendor_id": 1118, "product_id": 64,
                              "name_contains": "keyremap test keypad"}}},
  "options": {"passthrough_unmapped": true, "swallow_numlock_quirk": false},
  "profiles": {"base": {"kp": {
      "esc": "home",
      "pagedown": "lctrl",
      "home": {"tap": "accel+a", "hold": ["accel+a", "accel+c"], "hold_ms": 300}
  }}}
}"""


def make_virtual_keypad():
    caps = {ecodes.EV_KEY: [
        ecodes.KEY_ESC, ecodes.KEY_HOME, ecodes.KEY_END, ecodes.KEY_A,
        ecodes.KEY_C, ecodes.KEY_LEFTCTRL, ecodes.KEY_PAGEDOWN,
        ecodes.KEY_PAGEUP, ecodes.KEY_TAB,
    ]}
    return UInput(caps, name=VIRT_NAME, vendor=VENDOR, product=PRODUCT,
                  version=0x300)


def find_output_device(exclude_paths, timeout=10):
    """keyremap's own uinput device — the one that is NOT our virtual keypad."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for path in evdev.list_devices():
            if path in exclude_paths:
                continue
            d = evdev.InputDevice(path)
            if d.name != VIRT_NAME and ecodes.EV_KEY in d.capabilities():
                # keyremap's UInput device is created after we start it
                if "py-evdev-uinput" in d.name or "keyremap" in d.name.lower():
                    return d
        time.sleep(0.2)
    return None


def daemon(config_path):
    from keyremap.backends import linux_evdev
    from keyremap.config import load
    try:
        linux_evdev.run(load(config_path, plat="linux", host="ci"))
    except Exception as e:  # noqa: BLE001
        print(f"daemon exited: {type(e).__name__}: {e}", flush=True)


def main():
    cfg_path = "/tmp/keyremap-live.json"
    with open(cfg_path, "w") as f:
        f.write(CONFIG)

    before = set(evdev.list_devices())
    kp = make_virtual_keypad()
    time.sleep(1.0)
    kp_path = kp.device.path
    print(f"virtual keypad: {kp_path} vendor=0x{VENDOR:04X} product=0x{PRODUCT:04X}")

    # sanity: keyremap must SEE it through its own detection
    from keyremap.backends import linux_evdev
    from keyremap.config import load
    cfg = load(cfg_path, plat="linux", host="ci")
    found = [d for d in linux_evdev.detect(cfg) if d["matches"]]
    assert found, "keyremap did not detect the virtual keypad"
    print(f"detected: {found[0]['name']} -> {found[0]['matches']}")

    proc = mp.Process(target=daemon, args=(cfg_path,), daemon=True)
    proc.start()
    time.sleep(2.5)  # let it grab and create its uinput device

    out = find_output_device(before | {kp_path})
    assert out is not None, "keyremap's uinput output device never appeared"
    print(f"output device: {out.path} ({out.name})")

    captured = []

    def drain(seconds=1.2):
        end = time.time() + seconds
        while time.time() < end:
            ev = out.read_one()
            if ev is None:
                time.sleep(0.01)
                continue
            if ev.type == ecodes.EV_KEY:
                captured.append((ev.code, ev.value))

    # 1) simple remap: ESC -> HOME
    kp.write(ecodes.EV_KEY, ecodes.KEY_ESC, 1); kp.syn()
    kp.write(ecodes.EV_KEY, ecodes.KEY_ESC, 0); kp.syn()
    drain()
    assert (ecodes.KEY_HOME, 1) in captured, f"ESC->HOME missing: {captured}"
    print("✓ ESC -> HOME on a real kernel device")

    # 2) modifier: PAGEDOWN held as LEFTCTRL
    captured.clear()
    kp.write(ecodes.EV_KEY, ecodes.KEY_PAGEDOWN, 1); kp.syn()
    drain(0.6)
    assert (ecodes.KEY_LEFTCTRL, 1) in captured, f"ctrl down missing: {captured}"
    kp.write(ecodes.EV_KEY, ecodes.KEY_PAGEDOWN, 0); kp.syn()
    drain(0.6)
    assert (ecodes.KEY_LEFTCTRL, 0) in captured, f"ctrl up missing: {captured}"
    print("✓ PAGEDOWN held as LEFTCTRL, released cleanly")

    # 3) tap vs hold on HOME
    captured.clear()
    kp.write(ecodes.EV_KEY, ecodes.KEY_HOME, 1); kp.syn()
    time.sleep(0.05)
    kp.write(ecodes.EV_KEY, ecodes.KEY_HOME, 0); kp.syn()   # quick tap
    drain()
    assert (ecodes.KEY_A, 1) in captured, f"tap ctrl+a missing: {captured}"
    assert (ecodes.KEY_C, 1) not in captured, f"tap should not copy: {captured}"
    print("✓ quick tap -> ctrl+a only")

    captured.clear()
    kp.write(ecodes.EV_KEY, ecodes.KEY_HOME, 1); kp.syn()
    time.sleep(0.6)                                          # hold past 300ms
    drain(0.4)
    assert (ecodes.KEY_C, 1) in captured, f"hold ctrl+c missing: {captured}"
    kp.write(ecodes.EV_KEY, ecodes.KEY_HOME, 0); kp.syn()
    drain(0.4)
    print("✓ hold -> ctrl+a then ctrl+c, at the threshold")

    proc.terminate()
    kp.close()
    print("\nALL LIVE KERNEL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
