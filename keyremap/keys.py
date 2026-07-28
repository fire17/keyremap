"""Canonical key table shared by every backend.

Each canonical key name maps to its representation in each backend:
  vk  - Windows virtual-key code
  sc  - Windows set-1 scancode (ext=True means E0-prefixed)
  ahk - AutoHotkey v2 key name (usable in Send "{name}")
  ev  - Linux evdev KEY_* name
  kb  - Karabiner-Elements key_code

Targets may also be combos like "ctrl+shift+p".
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    vk: int
    sc: int
    ahk: str
    ev: str
    kb: str
    ext: bool = False  # E0-prefixed scancode on Windows


KEYS: dict[str, Key] = {}


def _add(name: str, vk: int, sc: int, ahk: str, ev: str, kb: str, ext: bool = False):
    KEYS[name] = Key(vk, sc, ahk, ev, kb, ext)


# Letters
for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    sc = {"a":0x1E,"b":0x30,"c":0x2E,"d":0x20,"e":0x12,"f":0x21,"g":0x22,"h":0x23,
          "i":0x17,"j":0x24,"k":0x25,"l":0x26,"m":0x32,"n":0x31,"o":0x18,"p":0x19,
          "q":0x10,"r":0x13,"s":0x1F,"t":0x14,"u":0x16,"v":0x2F,"w":0x11,"x":0x2D,
          "y":0x15,"z":0x2C}[ch]
    _add(ch, 0x41 + i, sc, ch, f"KEY_{ch.upper()}", ch)

# Digits (top row)
for i, ch in enumerate("1234567890"):
    _add(ch, 0x30 + (int(ch) if ch != "0" else 0), 0x02 + i, ch, f"KEY_{ch}", ch)

# Function keys F1..F24
for n in range(1, 25):
    if n <= 10:
        sc = 0x3B + (n - 1)
    elif n == 11:
        sc = 0x57
    elif n == 12:
        sc = 0x58
    else:
        sc = 0x64 + (n - 13)  # F13..F24
    _add(f"f{n}", 0x70 + (n - 1), sc, f"F{n}", f"KEY_F{n}", f"f{n}")

# Control / whitespace
_add("esc",        0x1B, 0x01, "Esc",       "KEY_ESC",        "escape")
_add("tab",        0x09, 0x0F, "Tab",       "KEY_TAB",        "tab")
_add("capslock",   0x14, 0x3A, "CapsLock",  "KEY_CAPSLOCK",   "caps_lock")
_add("space",      0x20, 0x39, "Space",     "KEY_SPACE",      "spacebar")
_add("enter",      0x0D, 0x1C, "Enter",     "KEY_ENTER",      "return_or_enter")
_add("backspace",  0x08, 0x0E, "Backspace", "KEY_BACKSPACE",  "delete_or_backspace")

# Navigation cluster (extended scancodes on Windows)
_add("insert",     0x2D, 0x52, "Insert",   "KEY_INSERT",   "insert",     ext=True)
_add("delete",     0x2E, 0x53, "Delete",   "KEY_DELETE",   "delete_forward", ext=True)
_add("home",       0x24, 0x47, "Home",     "KEY_HOME",     "home",       ext=True)
_add("end",        0x23, 0x4F, "End",      "KEY_END",      "end",        ext=True)
_add("pageup",     0x21, 0x49, "PgUp",     "KEY_PAGEUP",   "page_up",    ext=True)
_add("pagedown",   0x22, 0x51, "PgDn",     "KEY_PAGEDOWN", "page_down",  ext=True)
_add("up",         0x26, 0x48, "Up",       "KEY_UP",       "up_arrow",   ext=True)
_add("down",       0x28, 0x50, "Down",     "KEY_DOWN",     "down_arrow", ext=True)
_add("left",       0x25, 0x4B, "Left",     "KEY_LEFT",     "left_arrow", ext=True)
_add("right",      0x27, 0x4D, "Right",    "KEY_RIGHT",    "right_arrow", ext=True)

# Locks / system
_add("numlock",     0x90, 0x45, "NumLock",     "KEY_NUMLOCK",     "keypad_num_lock")
_add("scrolllock",  0x91, 0x46, "ScrollLock",  "KEY_SCROLLLOCK",  "scroll_lock")
_add("printscreen", 0x2C, 0x37, "PrintScreen", "KEY_SYSRQ",       "print_screen", ext=True)
_add("pause",       0x13, 0x45, "Pause",       "KEY_PAUSE",       "pause")
_add("menu",        0x5D, 0x5D, "AppsKey",     "KEY_COMPOSE",     "application", ext=True)
_add("clear",       0x0C, 0x4C, "Clear",       "KEY_CLEAR",       "clear")  # often numpad-5 w/o NumLock

# Punctuation
_add("minus",      0xBD, 0x0C, "-",  "KEY_MINUS",      "hyphen")
_add("equal",      0xBB, 0x0D, "=",  "KEY_EQUAL",      "equal_sign")
_add("lbracket",   0xDB, 0x1A, "[",  "KEY_LEFTBRACE",  "open_bracket")
_add("rbracket",   0xDD, 0x1B, "]",  "KEY_RIGHTBRACE", "close_bracket")
_add("backslash",  0xDC, 0x2B, "\\", "KEY_BACKSLASH",  "backslash")
_add("semicolon",  0xBA, 0x27, ";",  "KEY_SEMICOLON",  "semicolon")
_add("quote",      0xDE, 0x28, "'",  "KEY_APOSTROPHE", "quote")
_add("grave",      0xC0, 0x29, "`",  "KEY_GRAVE",      "grave_accent_and_tilde")
_add("comma",      0xBC, 0x33, ",",  "KEY_COMMA",      "comma")
_add("dot",        0xBE, 0x34, ".",  "KEY_DOT",        "period")
_add("slash",      0xBF, 0x35, "/",  "KEY_SLASH",      "slash")

# Numpad
for n in range(10):
    sc = {0:0x52,1:0x4F,2:0x50,3:0x51,4:0x4B,5:0x4C,6:0x4D,7:0x47,8:0x48,9:0x49}[n]
    _add(f"kp{n}", 0x60 + n, sc, f"Numpad{n}", f"KEY_KP{n}", f"keypad_{n}")
_add("kpdot",    0x6E, 0x53, "NumpadDot",   "KEY_KPDOT",      "keypad_period")
_add("kpenter",  0x0D, 0x1C, "NumpadEnter", "KEY_KPENTER",    "keypad_enter", ext=True)
_add("kpplus",   0x6B, 0x4E, "NumpadAdd",   "KEY_KPPLUS",     "keypad_plus")
_add("kpminus",  0x6D, 0x4A, "NumpadSub",   "KEY_KPMINUS",    "keypad_hyphen")
_add("kpmul",    0x6A, 0x37, "NumpadMult",  "KEY_KPASTERISK", "keypad_asterisk")
_add("kpdiv",    0x6F, 0x35, "NumpadDiv",   "KEY_KPSLASH",    "keypad_slash", ext=True)
_add("kpequal",  0x92, 0x59, "vk92",        "KEY_KPEQUAL",    "keypad_equal_sign")

# Modifiers (sided)
_add("lshift", 0xA0, 0x2A, "LShift", "KEY_LEFTSHIFT",  "left_shift")
_add("rshift", 0xA1, 0x36, "RShift", "KEY_RIGHTSHIFT", "right_shift")
_add("lctrl",  0xA2, 0x1D, "LCtrl",  "KEY_LEFTCTRL",   "left_control")
_add("rctrl",  0xA3, 0x1D, "RCtrl",  "KEY_RIGHTCTRL",  "right_control", ext=True)
_add("lalt",   0xA4, 0x38, "LAlt",   "KEY_LEFTALT",    "left_option")
_add("ralt",   0xA5, 0x38, "RAlt",   "KEY_RIGHTALT",   "right_option", ext=True)
_add("lwin",   0x5B, 0x5B, "LWin",   "KEY_LEFTMETA",   "left_command", ext=True)
_add("rwin",   0x5C, 0x5C, "RWin",   "KEY_RIGHTMETA",  "right_command", ext=True)

# Media
_add("mute",      0xAD, 0x20, "Volume_Mute", "KEY_MUTE",         "mute", ext=True)
_add("volumeup",  0xAF, 0x30, "Volume_Up",   "KEY_VOLUMEUP",     "volume_increment", ext=True)
_add("volumedown",0xAE, 0x2E, "Volume_Down", "KEY_VOLUMEDOWN",   "volume_decrement", ext=True)
_add("playpause", 0xB3, 0x22, "Media_Play_Pause", "KEY_PLAYPAUSE", "play_or_pause", ext=True)
_add("nexttrack", 0xB0, 0x19, "Media_Next",  "KEY_NEXTSONG",     "fastforward", ext=True)
_add("prevtrack", 0xB1, 0x10, "Media_Prev",  "KEY_PREVIOUSSONG", "rewind", ext=True)

# Karabiner's own spellings are accepted verbatim: a Mac user reads a key code
# out of Karabiner's EventViewer and should be able to paste it straight into
# the config without translating it first.
KARABINER_ALIASES = {
    "delete_or_backspace": "backspace", "delete_forward": "delete",
    "return_or_enter": "enter", "spacebar": "space",
    "page_up": "pageup", "page_down": "pagedown",
    "up_arrow": "up", "down_arrow": "down",
    "left_arrow": "left", "right_arrow": "right",
    "equal_sign": "equal", "hyphen": "minus",
    "open_bracket": "lbracket", "close_bracket": "rbracket",
    "grave_accent_and_tilde": "grave", "period": "dot",
    "keypad_num_lock": "numlock", "keypad_period": "kpdot",
    "keypad_enter": "kpenter", "keypad_plus": "kpplus",
    "keypad_hyphen": "kpminus", "keypad_asterisk": "kpmul",
    "keypad_slash": "kpdiv", "keypad_equal_sign": "kpequal",
    "left_control": "lctrl", "right_control": "rctrl",
    "left_shift": "lshift", "right_shift": "rshift",
    "left_option": "lalt", "right_option": "ralt",
    "left_command": "lwin", "right_command": "rwin",
    "application": "menu", "print_screen": "printscreen",
    "scroll_lock": "scrolllock", "caps_lock": "capslock",
    **{f"keypad_{n}": f"kp{n}" for n in range(10)},
}

ALIASES = {
    **KARABINER_ALIASES,
    "escape": "esc", "return": "enter", "win": "lwin", "cmd": "lwin",
    "super": "lwin", "meta": "lwin", "pgup": "pageup", "pgdn": "pagedown",
    "del": "delete", "ins": "insert", "bs": "backspace", "caps": "capslock",
    "plus": "kpplus", "equals": "equal", "period": "dot", "kpstar": "kpmul",
}

# Modifier names allowed in combos -> canonical sided key used when emitting.
# "accel" is the portable shortcut modifier: Ctrl on Windows/Linux, Command on
# macOS. Each backend resolves it natively, so one config expresses "select
# all" (accel+a) correctly on every platform.
ACCEL = "accel"
COMBO_MODS = {
    "ctrl": "lctrl", "shift": "lshift", "alt": "lalt", "win": "lwin",
    "cmd": "lwin", "lctrl": "lctrl", "rctrl": "rctrl", "lshift": "lshift",
    "rshift": "rshift", "lalt": "lalt", "ralt": "ralt", "lwin": "lwin",
    "rwin": "rwin",
    "accel": ACCEL, "mod": ACCEL, "cmdctrl": ACCEL, "super": ACCEL,
}

# AHK Send modifier prefixes / Karabiner modifier names.
# accel resolves per platform: Ctrl on Windows (^) and Linux, Command on macOS.
AHK_MOD_PREFIX = {"lctrl": "^", "rctrl": "^", "lshift": "+", "rshift": "+",
                  "lalt": "!", "ralt": "!", "lwin": "#", "rwin": "#",
                  ACCEL: "^"}
KB_MOD_NAME = {"lctrl": "left_control", "rctrl": "right_control",
               "lshift": "left_shift", "rshift": "right_shift",
               "lalt": "left_option", "ralt": "right_option",
               "lwin": "left_command", "rwin": "right_command",
               ACCEL: "left_command"}
# Linux evdev: accel is Ctrl
EV_MOD_NAME = {ACCEL: "KEY_LEFTCTRL"}


def canon(name: str) -> str:
    n = name.strip().lower()
    n = ALIASES.get(n, n)
    if n not in KEYS:
        raise KeyError(f"unknown key name: {name!r}")
    return n


def parse_target(spec: str) -> tuple[list[str], str]:
    """Parse 'ctrl+shift+p' -> (['lctrl','lshift'], 'p'). Plain key -> ([], key)."""
    parts = [p.strip().lower() for p in str(spec).split("+")]
    mods, key = parts[:-1], parts[-1]
    resolved_mods = []
    for m in mods:
        if m not in COMBO_MODS:
            raise KeyError(f"unknown modifier in {spec!r}: {m!r}")
        resolved_mods.append(COMBO_MODS[m])
    return resolved_mods, canon(key)


def find_by_vk(vk: int) -> str | None:
    for name, k in KEYS.items():
        if k.vk == vk:
            return name
    return None
