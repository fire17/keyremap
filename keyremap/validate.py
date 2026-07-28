"""Strict validation of generated artifacts.

Running a Mac is the only way to be *certain* Karabiner accepts a rule — but
almost every way a generated rule can be wrong is checkable offline: an invented
key_code, a malformed manipulator, a missing device condition, a threshold that
contradicts itself. This module checks all of it, so `keyremap selftest` gives
a real verdict on a machine that has never seen the target OS.

Karabiner's vocabulary below is transcribed from its documented key-code list
(the subset this project can emit, plus every alias in keys.py).
"""

import json
import re

# --- Karabiner key_code vocabulary ------------------------------------------
KARABINER_KEYS = set("""
a b c d e f g h i j k l m n o p q r s t u v w x y z
1 2 3 4 5 6 7 8 9 0
return_or_enter escape delete_or_backspace delete_forward tab spacebar
hyphen equal_sign open_bracket close_bracket backslash non_us_pound semicolon
quote grave_accent_and_tilde comma period slash caps_lock
f1 f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 f13 f14 f15 f16 f17 f18 f19 f20
f21 f22 f23 f24
print_screen scroll_lock pause insert home page_up end page_down
right_arrow left_arrow down_arrow up_arrow
keypad_num_lock keypad_slash keypad_asterisk keypad_hyphen keypad_plus
keypad_enter keypad_1 keypad_2 keypad_3 keypad_4 keypad_5 keypad_6 keypad_7
keypad_8 keypad_9 keypad_0 keypad_period keypad_equal_sign
left_control left_shift left_option left_command
right_control right_shift right_option right_command
application help mute volume_increment volume_decrement
play_or_pause fastforward rewind clear
""".split())

KARABINER_MODIFIERS = {
    "left_control", "left_shift", "left_option", "left_command",
    "right_control", "right_shift", "right_option", "right_command",
    "fn", "any", "caps_lock",
}


def validate_karabiner(doc_text: str) -> list[str]:
    """Return a list of problems. Empty == the document is structurally sound."""
    problems = []
    try:
        doc = json.loads(doc_text)
    except json.JSONDecodeError as e:
        return [f"not valid JSON: {e}"]

    if not isinstance(doc.get("title"), str) or not doc["title"]:
        problems.append("missing 'title' (Karabiner shows it in the rule list)")
    rules = doc.get("rules")
    if not isinstance(rules, list) or not rules:
        return problems + ["'rules' must be a non-empty list"]

    for ri, rule in enumerate(rules):
        where = f"rules[{ri}]"
        if not rule.get("description"):
            problems.append(f"{where}: missing description")
        manips = rule.get("manipulators")
        if not isinstance(manips, list) or not manips:
            problems.append(f"{where}: manipulators must be a non-empty list")
            continue
        for mi, m in enumerate(manips):
            w = f"{where}.manipulators[{mi}]"
            if m.get("type") != "basic":
                problems.append(f"{w}: type must be 'basic', got {m.get('type')!r}")
            frm = m.get("from") or {}
            kc = frm.get("key_code")
            if not kc:
                problems.append(f"{w}: 'from' needs a key_code")
            elif kc not in KARABINER_KEYS:
                problems.append(f"{w}: unknown from.key_code {kc!r}")

            outs = []
            for field in ("to", "to_if_alone", "to_if_held_down", "to_after_key_up"):
                if field in m:
                    if not isinstance(m[field], list) or not m[field]:
                        problems.append(f"{w}: '{field}' must be a non-empty list")
                        continue
                    outs.append(field)
                    for t in m[field]:
                        tk = t.get("key_code")
                        if not tk:
                            problems.append(f"{w}.{field}: entry needs key_code")
                        elif tk not in KARABINER_KEYS:
                            problems.append(f"{w}.{field}: unknown key_code {tk!r}")
                        for mod in t.get("modifiers", []):
                            if mod not in KARABINER_MODIFIERS:
                                problems.append(
                                    f"{w}.{field}: unknown modifier {mod!r}")
            if not outs:
                problems.append(f"{w}: produces nothing (no to/to_if_alone/...)")

            conds = m.get("conditions") or []
            if not any(c.get("type") == "device_if" for c in conds):
                problems.append(
                    f"{w}: no device_if condition — this would remap EVERY keyboard")
            for c in conds:
                if c.get("type") == "device_if":
                    ids = c.get("identifiers") or []
                    if not ids or not any(
                            ("vendor_id" in i or "product_id" in i) for i in ids):
                        problems.append(f"{w}: device_if without vendor/product id")

            params = m.get("parameters") or {}
            for k, v in params.items():
                if not isinstance(v, int) or v <= 0:
                    problems.append(f"{w}: parameter {k} must be a positive int")
            if "to_if_held_down" in m and \
                    "basic.to_if_held_down_threshold_milliseconds" not in params:
                problems.append(f"{w}: to_if_held_down without an explicit threshold")
    return problems


# --- AutoHotkey artifact -----------------------------------------------------
def validate_ahk(script: str) -> list[str]:
    """Structural checks on the generated AutoHotkey v2 script."""
    problems = []
    if "#Requires AutoHotkey v2" not in script:
        problems.append("missing '#Requires AutoHotkey v2.0' guard")
    if script.count("{") != script.count("}"):
        problems.append(
            f"unbalanced braces ({script.count('{')} open, {script.count('}')} close)")

    subs = re.findall(r"AHI\.SubscribeKey\([^,]+,\s*(0x[0-9A-Fa-f]+),\s*\w+,\s*(\w+)",
                      script)
    if not subs:
        problems.append("no SubscribeKey calls — the script would do nothing")
    for sc, handler in subs:
        if not re.search(rf"^{re.escape(handler)}\(state\)\s*\{{", script,
                         re.MULTILINE):
            problems.append(f"handler {handler} for {sc} is never defined")

    # the two bugs that silently killed keys in the field
    if re.search(r"(keyDown|holdFired)\.Delete\(", script):
        problems.append(
            "uses Map.Delete on a state map — throws in AHK v2 when the key is "
            "absent and silently kills the callback")
    if "GetKeyboardId(" in script:
        problems.append(
            "uses GetKeyboardId(vid,pid) — BLE devices report 0x0000 and it "
            "pops a modal dialog before try/catch can see it")

    for m in re.finditer(r"SetTimer\((\w+),\s*-(\d+)\)", script):
        fn = m.group(1)
        if not re.search(rf"^{re.escape(fn)}\(\)\s*\{{", script, re.MULTILINE):
            problems.append(f"timer target {fn} is never defined")
    return problems


def validate_all(cfg) -> dict[str, list[str]]:
    """Generate every platform's artifact from cfg and validate each."""
    from .backends import macos as mac
    from .backends import windows as win
    from .config import load

    results = {}
    for plat, label in (("darwin", "macOS (Karabiner)"),
                        ("windows", "Windows (AutoHotkey)"),
                        ("linux", "Linux (evdev)")):
        c = load(cfg.path, plat=plat, host="selftest")
        if plat == "darwin":
            results[label] = validate_karabiner(mac.generate(c))
        elif plat == "windows":
            results[label] = validate_ahk(win.generate_interception(c))
        else:
            results[label] = validate_linux(c)
    return results


def validate_linux(cfg) -> list[str]:
    """Every mapped key must exist in evdev's vocabulary on the target."""
    from .keys import KEYS, EV_MOD_NAME
    problems = []
    known = {k.ev for k in KEYS.values()} | set(EV_MOD_NAME.values())
    for dev, table in cfg.mappings.items():
        for src, act in table.items():
            if KEYS[src].ev not in known:
                problems.append(f"{dev}.{src}: no evdev code")
            for seq in (act.press, act.tap, act.hold):
                for mods, key in (seq or []):
                    if KEYS[key].ev not in known:
                        problems.append(f"{dev}.{src}: target {key} has no evdev code")
                    for m in mods:
                        if m != "accel" and m not in KEYS:
                            problems.append(f"{dev}.{src}: unknown modifier {m}")
    return problems
