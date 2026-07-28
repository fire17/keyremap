"""Test suite — stdlib unittest, no deps, runs in well under a second.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keyremap import config as cfgmod
from keyremap import portable
from keyremap.backends import macos as mac
from keyremap.backends import windows as win
from keyremap.keys import KEYS, canon, parse_target
from keyremap.tui import term


def write_cfg(doc: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f)
    return path


BASE_DOC = {
    "version": 2,
    "devices": {"kp": {"match": {"vendor_id": "0x045E", "product_id": "0x0040"}}},
    "profiles": {
        "base": {"kp": {"esc": "home", "tab": "pageup"}},
        "os": {"darwin": {"kp": {"esc": "end"}},
               "windows": {"kp": {"tab": None}}},
        "host": {"lappy": {"kp": {"esc": "accel+a"}}},
    },
}


class TestKeyTable(unittest.TestCase):
    def test_aliases_and_canon(self):
        self.assertEqual(canon("ESCAPE"), "esc")
        self.assertEqual(canon("PgDn"), "pagedown")
        with self.assertRaises(KeyError):
            canon("nosuchkey")

    def test_every_key_has_all_backend_names(self):
        for name, k in KEYS.items():
            self.assertTrue(k.ahk, f"{name} missing AHK name")
            self.assertTrue(k.ev.startswith("KEY_"), f"{name} bad evdev name")
            self.assertTrue(k.kb, f"{name} missing Karabiner name")

    def test_extended_flags_match_reality(self):
        # captured from real hardware: E0-prefixed nav keys
        for name, sc in (("home", 0x47), ("end", 0x4F), ("pageup", 0x49),
                         ("pagedown", 0x51), ("insert", 0x52), ("delete", 0x53)):
            k = KEYS[name]
            self.assertTrue(k.ext, f"{name} should be extended")
            self.assertEqual(k.sc, sc)

    def test_parse_target_combo(self):
        mods, key = parse_target("ctrl+shift+p")
        self.assertEqual(mods, ["lctrl", "lshift"])
        self.assertEqual(key, "p")

    def test_accel_is_virtual_and_platform_resolved(self):
        mods, key = parse_target("accel+a")
        self.assertEqual(mods, ["accel"])
        self.assertEqual(win.AHK_MOD_PREFIX["accel"], "^")        # Ctrl
        self.assertEqual(mac.KB_MOD_NAME["accel"], "left_command")  # Cmd


class TestLayering(unittest.TestCase):
    def test_base_only(self):
        p = write_cfg(BASE_DOC)
        c = cfgmod.load(p, plat="linux", host="nobody")
        self.assertEqual(c.mappings["kp"]["esc"].press[0][1], "home")
        self.assertIn("tab", c.mappings["kp"])
        self.assertEqual(c.layers_applied, ["base"])

    def test_os_override_wins(self):
        p = write_cfg(BASE_DOC)
        c = cfgmod.load(p, plat="darwin", host="nobody")
        self.assertEqual(c.mappings["kp"]["esc"].press[0][1], "end")
        self.assertEqual(c.origin["kp"]["esc"], "os:darwin")
        self.assertEqual(c.origin["kp"]["tab"], "base")  # untouched inherits

    def test_null_removes_inherited_mapping(self):
        p = write_cfg(BASE_DOC)
        c = cfgmod.load(p, plat="windows", host="nobody")
        self.assertNotIn("tab", c.mappings["kp"])
        self.assertIn("esc", c.mappings["kp"])

    def test_host_layer_beats_os_layer(self):
        p = write_cfg(BASE_DOC)
        c = cfgmod.load(p, plat="darwin", host="lappy")
        act = c.mappings["kp"]["esc"]
        self.assertEqual(act.press[0], (["accel"], "a"))
        self.assertEqual(c.origin["kp"]["esc"], "host:lappy")
        self.assertEqual(c.layers_applied, ["base", "os:darwin", "host:lappy"])

    def test_v1_config_still_loads(self):
        p = write_cfg({"devices": BASE_DOC["devices"],
                       "mappings": {"kp": {"esc": "home"}}})
        c = cfgmod.load(p, plat="linux", host="x")
        self.assertEqual(c.version, 1)
        self.assertEqual(c.mappings["kp"]["esc"].press[0][1], "home")

    def test_unknown_device_is_rejected(self):
        p = write_cfg({"devices": BASE_DOC["devices"],
                       "mappings": {"ghost": {"esc": "home"}}})
        with self.assertRaises(ValueError):
            cfgmod.load(p, plat="linux", host="x")


class TestActions(unittest.TestCase):
    def _load(self, mapping):
        doc = dict(BASE_DOC)
        doc = json.loads(json.dumps(BASE_DOC))
        doc["profiles"] = {"base": {"kp": mapping}}
        return cfgmod.load(write_cfg(doc), plat="linux", host="x")

    def test_press_tap_hold_and_sequences(self):
        c = self._load({"home": {"tap": "accel+a",
                                 "hold": ["accel+a", "accel+c"],
                                 "hold_ms": 400}})
        a = c.mappings["kp"]["home"]
        self.assertIsNone(a.press)
        self.assertEqual(a.hold_ms, 400)
        self.assertEqual([k for _, k in a.hold], ["a", "c"])
        self.assertFalse(a.is_simple)

    def test_plain_target_is_a_press(self):
        c = self._load({"home": "end"})
        a = c.mappings["kp"]["home"]
        self.assertTrue(a.is_simple)
        self.assertEqual(a.press[0][1], "end")

    def test_action_requires_something(self):
        with self.assertRaises(ValueError):
            self._load({"home": {"hold_ms": 100}})

    def test_bad_hold_ms_rejected(self):
        with self.assertRaises(ValueError):
            self._load({"home": {"tap": "end", "hold_ms": 0}})


class TestWindowsCodegen(unittest.TestCase):
    def _script(self, mapping):
        doc = json.loads(json.dumps(BASE_DOC))
        doc["profiles"] = {"base": {"kp": mapping}}
        cfg = cfgmod.load(write_cfg(doc), plat="windows", host="x")
        return win.generate_interception(cfg)

    def test_extended_scancode_uses_0x100_flag(self):
        s = self._script({"home": "end"})
        self.assertIn("0x147", s)   # E0 47, NOT 0x247

    def test_modifier_target_is_held_not_tapped(self):
        s = self._script({"pagedown": "lctrl"})
        self.assertIn("{LCtrl Down}", s)
        self.assertIn("{LCtrl Up}", s)

    def test_autorepeat_guard_present(self):
        s = self._script({"home": {"tap": "accel+a", "hold": "accel+c"}})
        self.assertIn("auto-repeat", s)
        self.assertIn("keyDown", s)

    def test_never_calls_map_delete_on_state_maps(self):
        """AHK v2 Map.Delete throws when absent and kills the callback."""
        s = self._script({"home": {"tap": "accel+a", "hold": "accel+c"}})
        self.assertNotIn("holdFired.Delete(", s)
        self.assertNotIn("keyDown.Delete(", s)

    def test_callbacks_are_error_wrapped(self):
        s = self._script({"home": {"tap": "accel+a", "hold": "accel+c"}})
        self.assertIn("catch as e", s)
        self.assertIn("ERROR in", s)

    def test_ble_device_matched_by_handle_not_vidpid(self):
        s = self._script({"home": "end"})
        self.assertIn("GetDeviceList", s)
        self.assertIn("VID&02045e_PID&0040", s)
        self.assertNotIn("GetKeyboardId(", s)

    def test_hold_timer_is_cancellable(self):
        s = self._script({"home": {"tap": "accel+a", "hold": "accel+c",
                                   "hold_ms": 400}})
        self.assertIn("SetTimer(Hold_kp_home, -400)", s)
        self.assertIn("SetTimer(Hold_kp_home, 0)", s)


class TestMacCodegen(unittest.TestCase):
    def _rules(self, mapping):
        doc = json.loads(json.dumps(BASE_DOC))
        doc["profiles"] = {"base": {"kp": mapping}}
        cfg = cfgmod.load(write_cfg(doc), plat="darwin", host="x")
        return json.loads(mac.generate(cfg))

    def test_device_condition_uses_vid_pid(self):
        r = self._rules({"home": "end"})
        cond = r["rules"][0]["manipulators"][0]["conditions"][0]
        self.assertEqual(cond["type"], "device_if")
        self.assertEqual(cond["identifiers"][0]["vendor_id"], 0x045E)

    def test_accel_becomes_command_on_mac(self):
        r = self._rules({"home": "accel+a"})
        to = r["rules"][0]["manipulators"][0]["to"][0]
        self.assertEqual(to["modifiers"], ["left_command"])

    def test_tap_and_hold_use_native_karabiner_fields(self):
        r = self._rules({"home": {"tap": "accel+a",
                                  "hold": ["accel+a", "accel+c"],
                                  "hold_ms": 400}})
        m = r["rules"][0]["manipulators"][0]
        self.assertIn("to_if_alone", m)
        self.assertIn("to_if_held_down", m)
        self.assertEqual(len(m["to_if_held_down"]), 2)  # sequence preserved
        self.assertEqual(
            m["parameters"]["basic.to_if_held_down_threshold_milliseconds"], 400)
        self.assertEqual(
            m["parameters"]["basic.to_if_alone_timeout_milliseconds"], 400)

    def test_output_is_valid_karabiner_document(self):
        r = self._rules({"home": "end"})
        self.assertIn("title", r)
        self.assertIsInstance(r["rules"], list)


class TestLint(unittest.TestCase):
    def test_duplicate_targets_flagged(self):
        doc = json.loads(json.dumps(BASE_DOC))
        doc["profiles"] = {"base": {"kp": {"esc": "home", "tab": "home"}}}
        cfg = cfgmod.load(write_cfg(doc), plat="linux", host="x")
        self.assertTrue(any("both produce" in p for p in cfgmod.lint(cfg)))

    def test_clean_config_has_no_complaints(self):
        doc = json.loads(json.dumps(BASE_DOC))
        doc["profiles"] = {"base": {"kp": {"esc": "home", "tab": "pageup"}}}
        cfg = cfgmod.load(write_cfg(doc), plat="linux", host="x")
        self.assertEqual(cfgmod.lint(cfg), [])


class TestPortability(unittest.TestCase):
    def test_export_import_roundtrip_preserves_behaviour(self):
        src = write_cfg(BASE_DOC)
        cfg = cfgmod.load(src, plat="linux", host="x")
        with tempfile.TemporaryDirectory() as d:
            bundle = portable.export_bundle(cfg, os.path.join(d, "b.keyremap"))
            data = portable.read_bundle(bundle)
            self.assertEqual(data["devices"]["kp"]["fingerprint"],
                             "usb:045e:0040")
            dest = portable.import_bundle(bundle, d, filename="config.json")
            # the imported config must resolve identically on the same env...
            again = cfgmod.load(dest, plat="linux", host="x")
            self.assertEqual(
                {k: v.describe() for k, v in again.mappings["kp"].items()},
                {k: v.describe() for k, v in cfg.mappings["kp"].items()})
            # ...and still carry the per-OS layers for the *other* machine
            onmac = cfgmod.load(dest, plat="darwin", host="x")
            self.assertEqual(onmac.mappings["kp"]["esc"].press[0][1], "end")

    def test_rejects_foreign_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            with open(p, "w") as f:
                json.dump({"hello": "world"}, f)
            with self.assertRaises(ValueError):
                portable.read_bundle(p)

    def test_import_backs_up_existing_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfgmod.load(write_cfg(BASE_DOC), plat="linux", host="x")
            bundle = portable.export_bundle(cfg, os.path.join(d, "b.keyremap"))
            existing = os.path.join(d, "config.json")
            with open(existing, "w") as f:
                f.write("{}")
            portable.import_bundle(bundle, d, filename="config.json")
            backups = [f for f in os.listdir(d) if ".bak-" in f]
            self.assertEqual(len(backups), 1)


class TestDeviceIdentity(unittest.TestCase):
    def test_fingerprint_is_stable_and_portable(self):
        cfg = cfgmod.load(write_cfg(BASE_DOC), plat="linux", host="x")
        self.assertEqual(cfg.device_fingerprints(), {"kp": "usb:045e:0040"})

    def test_match_by_vid_pid_and_by_name(self):
        dm = cfgmod.DeviceMatch(vendor_id=0x045E, product_id=0x0040,
                                name_contains="Keypad")
        self.assertTrue(dm.matches(0x045E, 0x0040, "anything"))
        self.assertTrue(dm.matches(None, None, "Bluetooth Keypad"))
        self.assertFalse(dm.matches(0x1234, 0x5678, "Mouse"))


class TestTerminalLayer(unittest.TestCase):
    def test_visible_len_ignores_ansi(self):
        self.assertEqual(term.visible_len(f"{term.ACCENT}abc{term.S.RESET}"), 3)

    def test_pad_and_truncate_are_ansi_safe(self):
        s = f"{term.ACCENT}abcdef{term.S.RESET}"
        self.assertEqual(term.visible_len(term.pad(s, 10)), 10)
        self.assertLessEqual(term.visible_len(term.truncate(s, 4)), 4)

    def test_truncate_leaves_short_strings_alone(self):
        self.assertEqual(term.truncate("abc", 10), "abc")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestViewModel(unittest.TestCase):
    """The GUI/TUI share these pure helpers, so views can't disagree."""

    def setUp(self):
        self.cfg = cfgmod.load(write_cfg(BASE_DOC), plat="linux", host="x")

    def test_mapping_rows_shape_and_scancodes(self):
        from keyremap.gui import mapping_rows
        rows = mapping_rows(self.cfg)
        self.assertTrue(all(len(r) == 4 for r in rows))
        by_key = {r[0]: r for r in rows}
        self.assertEqual(by_key["esc"][3], "0x1")       # not extended
        self.assertEqual(by_key["tab"][2], "base")      # layer shown

    def test_capture_line_classified_by_device(self):
        from keyremap.gui import classify_capture_line
        hit = ("KEYDOWN vk=0x24 sc=0x47 | device=\\\\?\\HID#"
               "{0000}_Dev_VID&02045e_PID&0040_REV&0300_x&Col01#9")
        miss = "KEYDOWN vk=0x24 sc=0x47 | device=\\\\?\\ACPI#DLLK0CC7#4&10c"
        self.assertEqual(classify_capture_line(self.cfg, hit)[1], "hit")
        self.assertEqual(classify_capture_line(self.cfg, miss)[1], "other")

    def test_status_line_mentions_layers(self):
        from keyremap.gui import status_line
        from keyremap.state import Deployment, Status
        st = Status(deployment=Deployment())
        self.assertIn("base", status_line(self.cfg, st))
        self.assertIn("never", status_line(self.cfg, st))

    def test_gui_imports_without_tkinter(self):
        import keyremap.gui as g
        self.assertTrue(hasattr(g, "main"))


class TestState(unittest.TestCase):
    def test_deployment_ago_formats(self):
        from keyremap.state import Deployment
        self.assertEqual(Deployment().applied_ago, "never")
        self.assertTrue(Deployment(applied_at=time.time() - 5)
                        .applied_ago.endswith("s ago"))
        self.assertTrue(Deployment(applied_at=time.time() - 7200)
                        .applied_ago.endswith("h ago"))

    def test_config_sha_changes_with_content(self):
        from keyremap.state import config_sha
        a = write_cfg(BASE_DOC)
        doc2 = json.loads(json.dumps(BASE_DOC))
        doc2["profiles"]["base"]["kp"]["esc"] = "end"
        b = write_cfg(doc2)
        self.assertNotEqual(config_sha(a), config_sha(b))
        self.assertEqual(config_sha(a), config_sha(a))


class TestEngine(unittest.TestCase):
    """The semantics all three platforms must implement."""

    def setUp(self):
        from keyremap.config import Action
        from keyremap.engine import Engine
        self.Action, self.Engine = Action, Engine

    def r(self, outs):
        return [repr(o) for o in outs]

    def test_simple_remap_fires_on_press(self):
        e = self.Engine({1: self.Action(press=[([], "end")])})
        self.assertEqual(self.r(e.feed(1, 1, 0)), ["tap:end"])
        self.assertEqual(self.r(e.feed(1, 0, 1)), [])

    def test_modifier_is_held_and_ignores_repeat(self):
        e = self.Engine({2: self.Action(press=[([], "lctrl")])})
        self.assertEqual(self.r(e.feed(2, 1, 0)), ["down:lctrl"])
        self.assertEqual(self.r(e.feed(2, 2, 5)), [])      # auto-repeat
        self.assertEqual(self.r(e.feed(2, 0, 9)), ["up:lctrl"])

    def test_hold_fires_at_threshold_and_suppresses_tap(self):
        act = self.Action(tap=[(["accel"], "a")],
                          hold=[(["accel"], "a"), (["accel"], "c")], hold_ms=400)
        e = self.Engine({3: act})
        self.assertEqual(self.r(e.feed(3, 1, 0)), [])       # nothing on press
        self.assertEqual(self.r(e.due(399)), [])
        self.assertEqual(self.r(e.due(400)), ["tap:accel+a", "tap:accel+c"])
        self.assertEqual(self.r(e.feed(3, 0, 500)), [])     # tap suppressed

    def test_quick_release_fires_tap_only(self):
        act = self.Action(tap=[(["accel"], "a")], hold=[([], "home")], hold_ms=400)
        e = self.Engine({3: act})
        e.feed(3, 1, 0)
        self.assertEqual(self.r(e.feed(3, 0, 100)), ["tap:accel+a"])
        self.assertEqual(self.r(e.due(9999)), [])           # hold was cancelled

    def test_autorepeat_never_restarts_the_hold(self):
        act = self.Action(tap=[([], "a")], hold=[([], "home")], hold_ms=100)
        e = self.Engine({3: act})
        e.feed(3, 1, 0)
        for t in range(10, 100, 10):
            self.assertEqual(self.r(e.feed(3, 1, t)), [])   # repeats ignored
        self.assertEqual(self.r(e.due(100)), ["tap:home"])  # still fires on time

    def test_unmapped_key_passthrough_toggle(self):
        e = self.Engine({}, passthrough=True)
        self.assertEqual(self.r(e.feed(99, 1, 0, raw="x")), ["PASS"])
        self.assertEqual(self.r(self.Engine({}, passthrough=False).feed(99, 1, 0)), [])

    def test_swallowed_key_emits_nothing(self):
        self.assertEqual(self.r(self.Engine({5: None}).feed(5, 1, 0)), [])

    def test_release_all_frees_held_modifiers(self):
        e = self.Engine({2: self.Action(press=[([], "lshift")])})
        e.feed(2, 1, 0)
        self.assertEqual(self.r(e.release_all()), ["up:lshift"])

    def test_next_deadline_drives_the_caller_timeout(self):
        act = self.Action(tap=[([], "a")], hold=[([], "home")], hold_ms=400)
        e = self.Engine({3: act})
        self.assertIsNone(e.next_deadline())
        e.feed(3, 1, 1000)
        self.assertEqual(e.next_deadline(), 1400)


class TestValidatorCatchesRealBugs(unittest.TestCase):
    """A validator that never fails proves nothing — these prove it fails."""

    def setUp(self):
        from keyremap import validate
        self.v = validate

    def test_karabiner_rejects_invented_key_code(self):
        doc = json.dumps({"title": "t", "rules": [{"description": "d",
            "manipulators": [{"type": "basic", "from": {"key_code": "banana"},
                "to": [{"key_code": "a"}],
                "conditions": [{"type": "device_if",
                                "identifiers": [{"vendor_id": 1}]}]}]}]})
        self.assertTrue(any("banana" in p for p in self.v.validate_karabiner(doc)))

    def test_karabiner_rejects_missing_device_condition(self):
        doc = json.dumps({"title": "t", "rules": [{"description": "d",
            "manipulators": [{"type": "basic", "from": {"key_code": "a"},
                              "to": [{"key_code": "b"}]}]}]})
        self.assertTrue(any("device_if" in p
                            for p in self.v.validate_karabiner(doc)))

    def test_karabiner_rejects_manipulator_that_emits_nothing(self):
        doc = json.dumps({"title": "t", "rules": [{"description": "d",
            "manipulators": [{"type": "basic", "from": {"key_code": "a"},
                "conditions": [{"type": "device_if",
                                "identifiers": [{"vendor_id": 1}]}]}]}]})
        self.assertTrue(any("produces nothing" in p
                            for p in self.v.validate_karabiner(doc)))

    def test_karabiner_rejects_bad_modifier_and_bad_json(self):
        doc = json.dumps({"title": "t", "rules": [{"description": "d",
            "manipulators": [{"type": "basic", "from": {"key_code": "a"},
                "to": [{"key_code": "b", "modifiers": ["left_banana"]}],
                "conditions": [{"type": "device_if",
                                "identifiers": [{"vendor_id": 1}]}]}]}]})
        self.assertTrue(any("left_banana" in p
                            for p in self.v.validate_karabiner(doc)))
        self.assertTrue(self.v.validate_karabiner("{not json"))

    def test_ahk_rejects_the_map_delete_bug(self):
        bad = ('#Requires AutoHotkey v2.0\n'
               'AHI.SubscribeKey(id, 0x147, true, H)\n'
               'H(state) {\n  holdFired.Delete("home")\n}\n')
        self.assertTrue(any("Map.Delete" in p for p in self.v.validate_ahk(bad)))

    def test_ahk_rejects_getkeyboardid_on_ble(self):
        bad = ('#Requires AutoHotkey v2.0\n'
               'id := AHI.GetKeyboardId(0x045E, 0x0040)\n'
               'AHI.SubscribeKey(id, 0x147, true, H)\nH(state) {\n}\n')
        self.assertTrue(any("GetKeyboardId" in p for p in self.v.validate_ahk(bad)))

    def test_ahk_rejects_missing_handler_and_unbalanced_braces(self):
        bad = ('#Requires AutoHotkey v2.0\n'
               'AHI.SubscribeKey(id, 0x147, true, Missing)\n{\n')
        problems = self.v.validate_ahk(bad)
        self.assertTrue(any("never defined" in p for p in problems))
        self.assertTrue(any("unbalanced" in p for p in problems))

    def test_real_generated_artifacts_are_clean(self):
        cfg = cfgmod.load(write_cfg(BASE_DOC), plat="darwin", host="x")
        self.assertEqual(self.v.validate_karabiner(mac.generate(cfg)), [])
        cfgw = cfgmod.load(write_cfg(BASE_DOC), plat="windows", host="x")
        self.assertEqual(self.v.validate_ahk(win.generate_interception(cfgw)), [])
