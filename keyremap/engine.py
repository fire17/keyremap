"""The platform-independent remapping engine.

This is the part that can actually be *wrong*: deciding what a key press,
release, repeat or hold should emit. It is pure — events in, events out, no
kernel, no OS, no I/O — so it can be tested exhaustively on any machine, and
the Linux backend is a thin shell around it.

Windows and macOS delegate the same decisions to AutoHotkey timers and
Karabiner's to_if_alone/to_if_held_down, so the semantics tested here are the
contract all three platforms implement.
"""

from dataclasses import dataclass, field

DOWN, UP, REPEAT = 1, 0, 2

# emitted event kinds
TAP = "tap"        # press+release a target (mods wrapped around it)
HOLD_DOWN = "down"  # press and keep held (modifier targets)
HOLD_UP = "up"      # release a held target
PASS = "pass"       # forward the original event untouched


@dataclass
class Out:
    kind: str
    target: tuple | None = None   # (mods, key)
    raw: object = None            # original event for PASS

    def __repr__(self):  # nicer test failures
        if self.kind == PASS:
            return "PASS"
        mods, key = self.target
        return f"{self.kind}:{'+'.join(mods + [key])}"


@dataclass
class Engine:
    """Decides what to emit. `now` is supplied by the caller (monotonic ms).

    Deliberately holds no timers: the caller asks `due(now)` whenever it wakes,
    which keeps it testable and lets each backend use its own scheduler.
    """
    table: dict          # source key id -> Action (or None to swallow)
    passthrough: bool = True
    _down: set = field(default_factory=set)
    _held_mods: dict = field(default_factory=dict)   # key id -> target
    _pending: dict = field(default_factory=dict)     # key id -> (deadline, act)
    _hold_fired: set = field(default_factory=set)

    # -- helpers
    @staticmethod
    def _is_modifier(target) -> bool:
        mods, key = target
        return not mods and key in (
            "lctrl", "rctrl", "lshift", "rshift", "lalt", "ralt", "lwin", "rwin")

    def next_deadline(self):
        """Earliest pending hold deadline, or None."""
        return min((d for d, _ in self._pending.values()), default=None)

    def due(self, now: float) -> list[Out]:
        """Fire any hold actions whose deadline has passed."""
        out = []
        for kid in [k for k, (d, _) in self._pending.items() if d <= now]:
            _, act = self._pending.pop(kid)
            self._hold_fired.add(kid)
            for t in act.hold:
                out.append(Out(HOLD_DOWN, t) if self._is_modifier(t)
                           else Out(TAP, t))
        return out

    def feed(self, kid, value: int, now: float, raw=None) -> list[Out]:
        """Handle one input event. Returns the events to emit, in order."""
        if kid not in self.table:
            return [Out(PASS, raw=raw)] if self.passthrough else []

        act = self.table[kid]
        if act is None:            # explicitly swallowed (e.g. NumLock churn)
            return []

        # --- simple remap: mirror the physical key's lifecycle
        if act.is_simple:
            target = act.press[0]
            if self._is_modifier(target):
                if value == DOWN:
                    self._held_mods[kid] = target
                    return [Out(HOLD_DOWN, target)]
                if value == UP:
                    self._held_mods.pop(kid, None)
                    return [Out(HOLD_UP, target)]
                return []          # modifiers ignore auto-repeat
            if value == DOWN:
                return [Out(TAP, target)]
            if value == REPEAT:
                return [Out(TAP, target)] if self.passthrough else []
            return []

        # --- press / tap / hold
        out = []
        if value == DOWN:
            if kid in self._down:          # auto-repeat: never restarts a hold
                return []
            self._down.add(kid)
            self._hold_fired.discard(kid)
            for t in (act.press or []):
                out.append(Out(TAP, t))
            if act.hold:
                self._pending[kid] = (now + act.hold_ms, act)
        elif value == UP:
            self._down.discard(kid)
            self._pending.pop(kid, None)   # released early: cancel the hold
            fired = kid in self._hold_fired
            self._hold_fired.discard(kid)
            # release anything the hold left held down
            if fired and act.hold:
                for t in act.hold:
                    if self._is_modifier(t):
                        out.append(Out(HOLD_UP, t))
            if act.tap and not fired:
                for t in act.tap:
                    out.append(Out(TAP, t))
        return out

    def release_all(self) -> list[Out]:
        """Everything still held — used on shutdown so nothing sticks."""
        out = [Out(HOLD_UP, t) for t in self._held_mods.values()]
        self._held_mods.clear()
        self._pending.clear()
        self._down.clear()
        self._hold_fired.clear()
        return out
