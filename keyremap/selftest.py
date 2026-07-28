"""`keyremap selftest` — prove the install is sound on THIS machine.

Runs four independent checks and prints one verdict:
  1. engine semantics   — the tap/hold/repeat/modifier contract, simulated
  2. artifact validity  — generate for every platform and validate strictly
  3. round-trip         — export a bundle and re-import it, compare resolution
  4. environment        — doctor's platform checks

Exit code 0 means everything that can be verified without special hardware
passed. Anything that genuinely needs the OS (a real Karabiner load, a real
kernel grab) is reported as such instead of being claimed.
"""

from . import validate
from .config import load


def _engine_checks() -> list[str]:
    """Simulate the engine's contract. Returns failures."""
    from .config import Action
    from .engine import DOWN, HOLD_DOWN, HOLD_UP, PASS, REPEAT, TAP, UP, Engine

    fails = []

    def check(name, got, want):
        got_r = [repr(o) for o in got]
        if got_r != list(want):
            fails.append(f"{name}: got {got_r}, want {list(want)}")

    # simple remap
    e = Engine({1: Action(press=[([], "end")])})
    check("simple down", e.feed(1, DOWN, 0), [f"tap:end"])
    check("simple up", e.feed(1, UP, 0), [])

    # unmapped key passes through
    check("passthrough", e.feed(99, DOWN, 0, raw="raw"), ["PASS"])
    e2 = Engine({1: Action(press=[([], "end")])}, passthrough=False)
    check("no passthrough", e2.feed(99, DOWN, 0), [])

    # swallowed key
    check("swallow", Engine({5: None}).feed(5, DOWN, 0), [])

    # modifier target is held, not tapped, and ignores auto-repeat
    e = Engine({2: Action(press=[([], "lctrl")])})
    check("mod down", e.feed(2, DOWN, 0), ["down:lctrl"])
    check("mod repeat", e.feed(2, REPEAT, 5), [])
    check("mod up", e.feed(2, UP, 10), ["up:lctrl"])

    # tap fires on release only when released before the threshold
    act = Action(tap=[(["accel"], "a")], hold=[(["accel"], "a"), (["accel"], "c")],
                 hold_ms=400)
    e = Engine({3: act})
    check("hold: nothing on press", e.feed(3, DOWN, 0), [])
    check("hold: repeat ignored", e.feed(3, REPEAT, 50), [])
    check("hold: not due yet", e.due(399), [])
    check("hold: fires at threshold", e.due(400),
          ["tap:accel+a", "tap:accel+c"])
    check("hold: tap suppressed on release", e.feed(3, UP, 500), [])

    e = Engine({3: act})
    e.feed(3, DOWN, 0)
    check("tap: fires on quick release", e.feed(3, UP, 100), ["tap:accel+a"])
    check("tap: nothing left pending", e.due(1000), [])

    # a second press after a hold behaves like a fresh press
    e = Engine({3: act})
    e.feed(3, DOWN, 0)
    e.due(400)
    e.feed(3, UP, 500)
    e.feed(3, DOWN, 600)
    check("re-press: tap works again", e.feed(3, UP, 650), ["tap:accel+a"])

    # press action fires immediately, hold still arrives later
    e = Engine({4: Action(press=[([], "end")], hold=[([], "home")], hold_ms=200)})
    check("press+hold: press is immediate", e.feed(4, DOWN, 0), ["tap:end"])
    check("press+hold: hold at threshold", e.due(200), ["tap:home"])

    # nothing is left stuck
    e = Engine({2: Action(press=[([], "lshift")])})
    e.feed(2, DOWN, 0)
    check("release_all frees held mods", e.release_all(), ["up:lshift"])

    # deadline reporting drives the caller's select() timeout
    e = Engine({3: act})
    e.feed(3, DOWN, 1000)
    if e.next_deadline() != 1400:
        fails.append(f"next_deadline: got {e.next_deadline()}, want 1400")
    return fails


def _roundtrip_checks(cfg) -> list[str]:
    import os
    import tempfile
    from . import portable
    fails = []
    with tempfile.TemporaryDirectory() as d:
        b = portable.export_bundle(cfg, os.path.join(d, "t.keyremap"))
        dest = portable.import_bundle(b, d, filename="config.json")
        for plat in ("darwin", "windows", "linux"):
            a = load(cfg.path, plat=plat, host="selftest")
            r = load(dest, plat=plat, host="selftest")
            av = {k: v.describe() for t in a.mappings.values() for k, v in t.items()}
            rv = {k: v.describe() for t in r.mappings.values() for k, v in t.items()}
            if av != rv:
                fails.append(f"{plat}: resolution changed after export/import")
    return fails


def run(cfg, verbose: bool = True) -> int:
    sections: list[tuple[str, list[str], str]] = []

    sections.append(("engine semantics", _engine_checks(),
                     "tap/hold/repeat/modifier contract (simulated)"))

    for label, problems in validate.validate_all(cfg).items():
        sections.append((f"artifact: {label}", problems, "generated + validated"))

    sections.append(("export/import round-trip", _roundtrip_checks(cfg),
                     "same behaviour after moving machines"))

    # On a Mac, stop guessing: hand the rule to Karabiner's own linter.
    from . import envinfo
    karabiner_note = None
    if envinfo.detect() == "macos":
        import os
        import tempfile
        from .backends import macos as mac
        if mac.find_karabiner_cli():
            with tempfile.NamedTemporaryFile("w", suffix=".json",
                                             delete=False) as f:
                f.write(mac.generate(cfg))
                tmp = f.name
            try:
                ok_lint, detail = mac.lint_with_karabiner(tmp)
                sections.append(("karabiner_cli lint",
                                 [] if ok_lint else [detail],
                                 "validated by Karabiner itself"))
            finally:
                os.unlink(tmp)
        else:
            karabiner_note = ("karabiner_cli not installed — the real linter "
                              "did NOT run")

    ok = True
    if verbose:
        print("keyremap selftest\n")
    for name, problems, note in sections:
        if problems:
            ok = False
            if verbose:
                print(f"  ✗ {name}")
                for p in problems[:8]:
                    print(f"      {p}")
        elif verbose:
            print(f"  ✓ {name:<34}{note}")

    if verbose:
        from . import doctor, envinfo
        env = envinfo.detect()
        print(f"\n  environment: {env}")
        blockers = 0
        for status, label, detail, fix in doctor.run(cfg):
            icon = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
            print(f"  {icon} {label:<34}{detail}")
            if fix:
                print(f"      → {fix}")
            blockers += status == "fail"
        print()
        if ok and not blockers:
            print("  ALL GREEN — safe to apply on this machine.")
        elif ok:
            print("  Logic is sound; the environment needs the fixes above "
                  "before 'keyremap apply' will work here.")
        else:
            print("  FAILURES above — do not apply until they are fixed.")
        if karabiner_note:
            print(f"  ! {karabiner_note}")
        print("\n  Note: a real Karabiner load and a real kernel grab can only be\n"
              "  verified on the target OS; everything else is checked above.")
    return 0 if ok else 1
