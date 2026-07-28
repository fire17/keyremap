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
    with os.fdopen(fd, "w", encoding="utf-8") as f:
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
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"hello": "world"}, f)
            with self.assertRaises(ValueError):
                portable.read_bundle(p)

    def test_import_backs_up_existing_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfgmod.load(write_cfg(BASE_DOC), plat="linux", host="x")
            bundle = portable.export_bundle(cfg, os.path.join(d, "b.keyremap"))
            existing = os.path.join(d, "config.json")
            with open(existing, "w", encoding="utf-8") as f:
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


class TestMiniYaml(unittest.TestCase):
    """The built-in reader must agree with pyyaml on configs we ship."""

    def test_matches_pyyaml_on_the_real_config(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")
        from keyremap import miniyaml
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(here, "config.yaml"),
                    encoding="utf-8").read()
        self.assertEqual(miniyaml.safe_load(text), yaml.safe_load(text))

    def test_scalars_and_structures(self):
        from keyremap import miniyaml
        doc = miniyaml.safe_load("""
# a comment
version: 2
devices:
  kp:
    match: { vendor_id: 0x045E, product_id: 64 }
profiles:
  base:
    kp:
      esc: home
      home:
        tap: accel+a
        hold: [accel+a, accel+c]
        hold_ms: 400
      tab: null
  os:
    darwin: {}
flag: true
name: "quoted: string"
""")
        self.assertEqual(doc["version"], 2)
        self.assertEqual(doc["devices"]["kp"]["match"]["vendor_id"], 0x045E)
        self.assertEqual(doc["profiles"]["base"]["kp"]["home"]["hold"],
                         ["accel+a", "accel+c"])
        self.assertIsNone(doc["profiles"]["base"]["kp"]["tab"])
        self.assertEqual(doc["profiles"]["os"]["darwin"], {})
        self.assertIs(doc["flag"], True)
        self.assertEqual(doc["name"], "quoted: string")

    def test_config_loads_without_pyyaml(self):
        """Simulate a fresh Mac: no pyyaml installed."""
        import builtins
        from keyremap import config as c
        real_import = builtins.__import__

        def no_yaml(name, *a, **k):
            if name == "yaml":
                raise ImportError("simulated: pyyaml not installed")
            return real_import(name, *a, **k)

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        builtins.__import__ = no_yaml
        try:
            cfg = c.load(os.path.join(here, "config.yaml"),
                         plat="darwin", host="mac")
        finally:
            builtins.__import__ = real_import
        self.assertTrue(cfg.mappings["keypad"])
        self.assertEqual(cfg.devices["keypad"].vendor_id, 0x045E)

    def test_unsupported_features_raise_rather_than_guess(self):
        from keyremap.miniyaml import YamlError, safe_load
        with self.assertRaises(YamlError):
            safe_load("a: &anchor 1\n")


class TestImportFormat(unittest.TestCase):
    def test_json_destination_gets_json(self):
        cfg = cfgmod.load(write_cfg(BASE_DOC), plat="linux", host="x")
        with tempfile.TemporaryDirectory() as d:
            b = portable.export_bundle(cfg, os.path.join(d, "b.keyremap"))
            dest = portable.import_bundle(b, d, filename="config.json")
            self.assertTrue(dest.endswith(".json"))
            json.load(open(dest, encoding="utf-8"))  # must be valid JSON


class TestAgainstUpstreamKarabiner(unittest.TestCase):
    """Guard the Mac path against Karabiner's OWN key-code vocabulary."""

    def test_every_emittable_key_code_exists_upstream(self):
        from keyremap.validate import upstream_key_codes
        upstream = upstream_key_codes()
        self.assertGreater(len(upstream), 150, "vendored list looks truncated")
        emitted = {k.kb for k in KEYS.values()}
        self.assertEqual(sorted(emitted - upstream), [],
                         "we can emit a key_code Karabiner does not define")

    def test_generated_rule_uses_only_upstream_codes(self):
        from keyremap.validate import upstream_key_codes
        upstream = upstream_key_codes()
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = cfgmod.load(os.path.join(here, "config.yaml"),
                          plat="darwin", host="mac")
        doc = json.loads(mac.generate(cfg))
        used = set()
        for rule in doc["rules"]:
            for m in rule["manipulators"]:
                used.add(m["from"]["key_code"])
                for f in ("to", "to_if_alone", "to_if_held_down"):
                    for t in m.get(f, []):
                        used.add(t["key_code"])
        self.assertEqual(sorted(used - upstream), [])

    def test_device_if_uses_decimal_integers(self):
        """Karabiner requires decimal ints for vendor_id/product_id."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = cfgmod.load(os.path.join(here, "config.yaml"),
                          plat="darwin", host="mac")
        doc = json.loads(mac.generate(cfg))
        for rule in doc["rules"]:
            for m in rule["manipulators"]:
                ident = m["conditions"][0]["identifiers"][0]
                self.assertIsInstance(ident["vendor_id"], int)
                self.assertIsInstance(ident["product_id"], int)
                self.assertEqual(ident["vendor_id"], 0x045E)


class TestLinuxBackendEndToEnd(unittest.TestCase):
    """Drive the real Linux run-loop with a fake kernel.

    WSL has no /dev/input, so instead of leaving the backend untested we
    substitute evdev/uinput with stubs and assert the exact events it writes.
    This exercises device matching, grab, the select loop, hold deadlines and
    uinput output — everything except the kernel itself.
    """

    def _stub_evdev(self, events):
        import types

        ec = types.SimpleNamespace()
        names = ["KEY_ESC", "KEY_HOME", "KEY_END", "KEY_A", "KEY_C",
                 "KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_PAGEUP", "KEY_PAGEDOWN",
                 "KEY_TAB", "KEY_BACKSPACE", "KEY_DELETE", "KEY_INSERT",
                 "KEY_EQUAL", "KEY_NUMLOCK"]
        for i, n in enumerate(names, start=1):
            setattr(ec, n, 100 + i)
        ec.EV_KEY = 1
        ec.EV_SYN = 0

        class Ev:
            def __init__(self, code, value):
                self.type, self.code, self.value = ec.EV_KEY, code, value

        written = []

        class UInput:
            def write(self, typ, code, val):
                written.append(("write", code, val))

            def write_event(self, ev):
                written.append(("pass", ev.code, ev.value))

            def syn(self):
                pass

            def close(self):
                pass

        class InputDevice:
            def __init__(self, path):
                self.path, self.fd, self.name = path, 7, "Bluetooth Keypad"
                self.info = types.SimpleNamespace(vendor=0x045E, product=0x0040)
                self._queue = list(events)

            def capabilities(self):
                return {ec.EV_KEY: []}

            def grab(self):
                written.append(("grab", 0, 0))

            def ungrab(self):
                pass

            def read(self):
                if not self._queue:
                    raise KeyboardInterrupt   # ends the loop deterministically
                code, value = self._queue.pop(0)
                return [Ev(code, value)]

        mod = types.ModuleType("evdev")
        mod.ecodes = ec
        mod.InputDevice = InputDevice
        mod.UInput = UInput
        mod.list_devices = lambda: ["/dev/input/event0"]
        return mod, ec, written

    def _run(self, mapping, events):
        import select as real_select
        import sys as _sys
        doc = json.loads(json.dumps(BASE_DOC))
        doc["devices"] = {"kp": {"match": {"vendor_id": "0x045E",
                                           "product_id": "0x0040"}}}
        doc["profiles"] = {"base": {"kp": mapping}}
        cfg = cfgmod.load(write_cfg(doc), plat="linux", host="x")

        mod, ec, written = self._stub_evdev(events)
        from keyremap.backends import linux_evdev as be
        saved_mod = _sys.modules.get("evdev")
        saved_sel = real_select.select
        _sys.modules["evdev"] = mod
        real_select.select = lambda r, w, x, t=None: (list(r), [], [])
        try:
            try:
                be.run(cfg)
            except KeyboardInterrupt:
                pass
        finally:
            real_select.select = saved_sel
            if saved_mod is None:
                _sys.modules.pop("evdev", None)
            else:
                _sys.modules["evdev"] = saved_mod
        return written, ec

    def test_device_is_grabbed_and_simple_remap_emits(self):
        # 101 = KEY_ESC, 102 = KEY_HOME in the stub's numbering
        written, _ = self._run({"esc": "home"}, [(101, 1), (101, 0)])
        self.assertIn(("grab", 0, 0), written)
        writes = [(c, v) for k, c, v in written if k == "write"]
        self.assertIn((102, 1), writes)   # HOME pressed
        self.assertIn((102, 0), writes)   # HOME released

    def test_modifier_key_is_held_for_its_duration(self):
        written, _ = self._run({"pagedown": "lctrl"},
                               [(109, 1), (109, 0)])   # KEY_PAGEDOWN
        writes = [(c, v) for k, c, v in written if k == "write"]
        self.assertIn((106, 1), writes)   # KEY_LEFTCTRL down
        self.assertIn((106, 0), writes)   # KEY_LEFTCTRL up

    def test_unmapped_key_passes_through_untouched(self):
        written, _ = self._run({"esc": "home"}, [(110, 1)])  # KEY_TAB unmapped
        self.assertIn(("pass", 110, 1), written)


class TestKarabinerAutoEnable(unittest.TestCase):
    """Dropping a file in assets/ only makes a rule available; enabling it in
    the selected profile is what makes it actually work."""

    def _profile_doc(self, extra_rules=None):
        return {"global": {"show_in_menu_bar": True},
                "profiles": [
                    {"name": "Default", "selected": False,
                     "complex_modifications": {"rules": []}},
                    {"name": "Work", "selected": True,
                     "complex_modifications": {
                         "rules": list(extra_rules or [])}},
                ]}

    def _run_enable(self, doc):
        cfg = cfgmod.load(write_cfg(BASE_DOC), plat="darwin", host="mac")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "karabiner.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            ok, detail = mac.enable_in_profile(cfg, p)
            with open(p, encoding="utf-8") as f:
                after = json.load(f)
            backup = os.path.exists(p + ".keyremap-backup")
        return ok, detail, after, backup

    def test_rules_land_in_the_selected_profile(self):
        ok, detail, after, _ = self._run_enable(self._profile_doc())
        self.assertTrue(ok, detail)
        selected = [p for p in after["profiles"] if p.get("selected")][0]
        descs = [r["description"] for r in
                 selected["complex_modifications"]["rules"]]
        self.assertTrue(any(d.startswith("keyremap:") for d in descs))
        unselected = [p for p in after["profiles"] if not p.get("selected")][0]
        self.assertEqual(unselected["complex_modifications"]["rules"], [])

    def test_user_rules_are_preserved(self):
        mine = {"description": "my own rule", "manipulators": []}
        ok, _, after, _ = self._run_enable(self._profile_doc([mine]))
        self.assertTrue(ok)
        rules = [p for p in after["profiles"]
                 if p.get("selected")][0]["complex_modifications"]["rules"]
        self.assertIn(mine, rules)

    def test_reapplying_replaces_rather_than_duplicates(self):
        cfg = cfgmod.load(write_cfg(BASE_DOC), plat="darwin", host="mac")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "karabiner.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self._profile_doc(), f)
            for _ in range(3):
                self.assertTrue(mac.enable_in_profile(cfg, p)[0])
            with open(p, encoding="utf-8") as f:
                after = json.load(f)
        rules = [x for x in [pr for pr in after["profiles"]
                             if pr.get("selected")][0]
                 ["complex_modifications"]["rules"]
                 if x["description"].startswith("keyremap:")]
        self.assertEqual(len(rules), 1, "re-applying duplicated the rule")

    def test_a_backup_is_written_once(self):
        _, _, _, backup = self._run_enable(self._profile_doc())
        self.assertTrue(backup, "no pre-change backup of karabiner.json")

    def test_missing_file_is_reported_not_crashed(self):
        cfg = cfgmod.load(write_cfg(BASE_DOC), plat="darwin", host="mac")
        ok, detail = mac.enable_in_profile(cfg, "/nonexistent/karabiner.json")
        self.assertFalse(ok)
        self.assertIn("not found", detail)


class TestAdopt(unittest.TestCase):
    """A device's ids are read by a different stack on each OS. If they
    disagree, the rule matches nothing and the keypad silently does nothing."""

    SAMPLE = """# keyremap config
version: 2

devices:
  keypad:
    match:
      vendor_id: 0x045E     # keep this comment
      product_id: 0x0040
      name_contains: "Keypad"
  other:
    match:
      vendor_id: 0x1111
      product_id: 0x2222

profiles:
  base:
    keypad:
      esc: home
"""

    def test_rewrites_only_the_named_device(self):
        from keyremap.adopt import rewrite_ids
        out, ok = rewrite_ids(self.SAMPLE, "keypad", 0x1234, 0x5678)
        self.assertTrue(ok)
        self.assertIn("vendor_id: 0x1234", out)
        self.assertIn("product_id: 0x5678", out)
        self.assertIn("vendor_id: 0x1111", out)   # 'other' untouched
        self.assertIn("product_id: 0x2222", out)

    def test_preserves_comments_and_everything_else(self):
        from keyremap.adopt import rewrite_ids
        out, _ = rewrite_ids(self.SAMPLE, "keypad", 0x1234, 0x5678)
        self.assertIn("# keep this comment", out)
        self.assertIn("# keyremap config", out)
        self.assertIn('name_contains: "Keypad"', out)
        self.assertIn("esc: home", out)
        self.assertEqual(len(out.splitlines()), len(self.SAMPLE.splitlines()))

    def test_result_still_parses_and_resolves(self):
        from keyremap.adopt import rewrite_ids
        out, _ = rewrite_ids(self.SAMPLE, "keypad", 0x1234, 0x5678)
        fd, p = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(out)
        cfg = cfgmod.load(p, plat="darwin", host="mac")
        self.assertEqual(cfg.devices["keypad"].vendor_id, 0x1234)
        self.assertEqual(cfg.devices["keypad"].product_id, 0x5678)
        self.assertEqual(cfg.devices["other"].vendor_id, 0x1111)
        self.assertEqual(cfg.mappings["keypad"]["esc"].press[0][1], "home")

    def test_reports_failure_when_device_absent(self):
        from keyremap.adopt import rewrite_ids
        _, ok = rewrite_ids(self.SAMPLE, "nosuchdevice", 1, 2)
        self.assertFalse(ok)


class TestInstallManifest(unittest.TestCase):
    """A stale install list shipped a broken install once: every command died
    with 'cannot import name miniyaml'. This keeps it honest."""

    def setUp(self):
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(self.root, "install-manifest.txt"),
                  encoding="utf-8") as f:
            self.listed = {l.strip() for l in f
                           if l.strip() and not l.startswith("#")}

    def test_every_runtime_file_is_listed(self):
        needed = {"remap.py", "config.yaml"}
        for base, dirs, names in os.walk(os.path.join(self.root, "keyremap")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for n in names:
                if n.endswith((".py", ".json")):
                    rel = os.path.relpath(os.path.join(base, n), self.root)
                    needed.add(rel.replace(os.sep, "/"))
        missing = sorted(needed - self.listed)
        self.assertEqual(missing, [], f"install.sh would ship a broken app: {missing}")

    def test_nothing_listed_is_missing_from_disk(self):
        gone = sorted(f for f in self.listed
                      if not os.path.exists(os.path.join(self.root, f)))
        self.assertEqual(gone, [], f"manifest lists files that do not exist: {gone}")

    def test_install_script_reads_the_manifest_not_a_hardcoded_list(self):
        with open(os.path.join(self.root, "install.sh"),
                  encoding="utf-8") as f:
            sh = f.read()
        self.assertIn("install-manifest.txt", sh)
        self.assertNotIn("keyremap/tui/term.py\"", sh)   # the old inline list


class TestDocsMatchReality(unittest.TestCase):
    """The README's first command is the first thing a stranger runs; a wrong
    branch in that URL 404s for everyone (it did)."""

    def setUp(self):
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(self.root, "README.md"),
                  encoding="utf-8") as f:
            self.readme = f.read()

    def test_install_urls_are_branch_agnostic(self):
        import re
        bad = re.findall(
            r"raw\.githubusercontent\.com/[\w-]+/[\w-]+/(main|master)/", self.readme)
        self.assertEqual(bad, [], "pin install URLs to HEAD, not a branch name")

    def test_no_placeholder_owner_in_a_url(self):
        """Prose may mention the old bug; a live URL may not contain it."""
        for name in ("README.md", "install.sh"):
            with open(os.path.join(self.root, name),
                      encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("githubusercontent.com/OWNER", text,
                             f"{name} has a placeholder in a real URL")

    def test_tests_badge_matches_the_real_count(self):
        import re
        m = re.search(r"tests-(\d+)%20passing", self.readme)
        self.assertIsNotNone(m, "tests badge missing")
        claimed = int(m.group(1))
        loader = unittest.TestLoader()
        actual = loader.discover(os.path.join(self.root, "tests")).countTestCases()
        self.assertEqual(claimed, actual,
                         f"README claims {claimed} tests, suite has {actual}")


class TestEncodingSafety(unittest.TestCase):
    """Windows defaults to cp1252, so any text file with a non-Latin-1
    character explodes unless encoding is explicit. This bit CI once."""

    def test_no_text_open_without_explicit_encoding(self):
        """Parsed with ast, so multi-line calls and strings can't fool it."""
        import ast
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", ".git", "out", "assets")]
            for n in names:
                if not n.endswith(".py"):
                    continue
                p = os.path.join(base, n)
                with open(p, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=p)
                for node in ast.walk(tree):
                    if not (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "open"):
                        continue
                    kw = {k.arg for k in node.keywords}
                    mode = ""
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    if "b" in mode or "encoding" in kw:
                        continue
                    offenders.append(f"{os.path.relpath(p, root)}:{node.lineno}")
        self.assertEqual(sorted(offenders), [],
                         "text open() without encoding='utf-8' breaks on Windows")

    def test_config_with_non_latin1_characters_loads(self):
        doc = json.loads(json.dumps(BASE_DOC))
        fd, p = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# emoji ✓ and an em-dash — and ⌘\n")
            f.write("version: 2\n")
            f.write("devices:\n  kp:\n    match:\n      vendor_id: 0x045E\n"
                    "      product_id: 0x0040\n")
            f.write("profiles:\n  base:\n    kp:\n      esc: home\n")
        cfg = cfgmod.load(p, plat="darwin", host="mac")
        self.assertEqual(cfg.mappings["kp"]["esc"].press[0][1], "home")


class TestCrossPlatformEquivalence(unittest.TestCase):
    """'The same mapping on every machine' is the whole promise — but Windows
    and Linux run keyremap's engine while macOS hands the job to Karabiner.
    Two implementations of one contract can drift. This simulates Karabiner's
    documented semantics and asserts it agrees with the engine, event for event.
    """

    @staticmethod
    def _karabiner_sim(manip, events, hold_ms):
        """Karabiner's basic-manipulator semantics, per its documentation:
        `to` fires on key-down; `to_if_held_down` fires once the key has been
        held past the threshold; `to_if_alone` fires on release only if the key
        was released before then (and is suppressed once held fired)."""
        out, down_at, held_fired = [], None, False

        def emit(field):
            for t in manip.get(field, []):
                mods = t.get("modifiers", [])
                out.append("+".join(mods + [t["key_code"]]))

        for kind, t in events:
            if kind == "down":
                down_at, held_fired = t, False
                emit("to")
            elif kind == "tick":
                if (down_at is not None and not held_fired
                        and t - down_at >= hold_ms and "to_if_held_down" in manip):
                    held_fired = True
                    emit("to_if_held_down")
            elif kind == "up":
                if not held_fired:
                    emit("to_if_alone")
                down_at = None
        return out

    def _engine_run(self, act, events):
        from keyremap.engine import Engine
        e = Engine({1: act})
        out = []
        for kind, t in events:
            if kind == "down":
                out += e.feed(1, 1, t)
            elif kind == "up":
                out += e.feed(1, 0, t)
            else:
                out += e.due(t)
        # normalise engine output to the same "mods+key" strings
        norm = []
        for o in out:
            if o.target is None:
                continue
            mods, key = o.target
            mods = ["left_command" if m == "accel" else m for m in mods]
            norm.append("+".join(mods + [key]))
        return norm

    def _mac_manip(self, mapping, src_kb):
        doc = json.loads(json.dumps(BASE_DOC))
        doc["profiles"] = {"base": {"kp": mapping}}
        cfg = cfgmod.load(write_cfg(doc), plat="darwin", host="mac")
        rules = json.loads(mac.generate(cfg))["rules"][0]["manipulators"]
        return next(m for m in rules if m["from"]["key_code"] == src_kb), cfg

    def _compare(self, mapping, src, src_kb, events, hold_ms):
        manip, cfg = self._mac_manip(mapping, src_kb)
        act = cfg.mappings["kp"][src]
        mac_out = self._karabiner_sim(manip, events, hold_ms)
        eng_out = self._engine_run(act, events)
        # compare key names only (mac uses Karabiner spellings)
        strip = lambda seq: [s.split("+")[-1] for s in seq]
        self.assertEqual(strip(mac_out), strip(eng_out),
                         f"macOS and engine disagree: {mac_out} vs {eng_out}")
        return mac_out

    def test_tap_path_agrees(self):
        m = {"home": {"tap": "accel+a", "hold": ["accel+a", "accel+c"],
                      "hold_ms": 400}}
        ev = [("down", 0), ("tick", 100), ("up", 120)]      # quick tap
        out = self._compare(m, "home", "home", ev, 400)
        self.assertEqual([s.split("+")[-1] for s in out], ["a"])

    def test_hold_path_agrees(self):
        m = {"home": {"tap": "accel+a", "hold": ["accel+a", "accel+c"],
                      "hold_ms": 400}}
        ev = [("down", 0), ("tick", 400), ("up", 600)]      # held past threshold
        out = self._compare(m, "home", "home", ev, 400)
        self.assertEqual([s.split("+")[-1] for s in out], ["a", "c"])

    def test_hold_suppresses_tap_on_both(self):
        m = {"home": {"tap": "accel+a", "hold": "accel+c", "hold_ms": 300}}
        ev = [("down", 0), ("tick", 300), ("up", 900)]
        out = self._compare(m, "home", "home", ev, 300)
        self.assertNotIn("a", [s.split("+")[-1] for s in out])

    def test_simple_press_agrees(self):
        m = {"esc": "home"}
        ev = [("down", 0), ("up", 50)]
        self._compare(m, "esc", "escape", ev, 1000)

    def test_boundary_release_one_ms_early_is_a_tap_on_both(self):
        m = {"home": {"tap": "accel+a", "hold": "accel+c", "hold_ms": 400}}
        ev = [("down", 0), ("tick", 399), ("up", 399)]
        out = self._compare(m, "home", "home", ev, 400)
        self.assertEqual([s.split("+")[-1] for s in out], ["a"])


class TestConsoleGuard(unittest.TestCase):
    """Windows cp1252 killed a status report twice. One shared guard now, and
    every entry point that prints non-ASCII must use it."""

    def test_guard_never_raises_on_odd_streams(self):
        from keyremap.console import utf8
        class NoReconfigure:
            pass
        utf8(NoReconfigure())          # must not raise
        import io
        utf8(io.StringIO())            # nor on a StringIO

    def test_entry_points_that_print_glyphs_call_the_guard(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("remap.py", "tests/bench.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                src = f.read()
            has_glyphs = any(g in src for g in ("✓", "✗", "→", "·"))
            if has_glyphs:
                self.assertIn("from keyremap.console import utf8", src,
                              f"{rel} prints non-ASCII without the console guard")


class TestReport(unittest.TestCase):
    """The report gets pasted into chat — it must be useful and not leak.

    doctor/detect shell out (PowerShell on WSL), so they are stubbed: this
    tests the report's own logic, and building it once keeps the suite fast.
    """

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cls.cfg = cfgmod.load(os.path.join(here, "config.yaml"),
                              plat="darwin", host="mac")
        import types
        from keyremap import backends, doctor, report
        real_doctor, real_get = doctor.run, backends.get_backend
        doctor.run = lambda cfg: [
            ("ok", "Karabiner-Elements", "installed", ""),
            ("warn", "configured device", "not connected", "connect it")]
        fake = types.SimpleNamespace(detect=lambda cfg: [
            {"name": "Bluetooth Keypad", "instance": "x", "vid": 0x045E,
             "pid": 0x0040, "matches": ["keypad"]},
            {"name": "/Users/someone/thing C:\\Users\\Someone\\x",
             "instance": "y", "vid": None, "pid": None, "matches": []}])
        backends.get_backend = lambda env: fake
        try:
            cls.text = report.build(cls.cfg)
        finally:
            doctor.run, backends.get_backend = real_doctor, real_get

    def test_contains_what_a_helper_actually_needs(self):
        for needed in ("keyremap report", "layers applied",
                       "Devices this machine", "Doctor", "Effective mappings",
                       "Behaviour hash"):
            self.assertIn(needed, self.text, f"report is missing {needed!r}")

    def test_redacts_both_posix_and_windows_usernames(self):
        import getpass
        self.assertNotIn(os.path.expanduser("~"), self.text)
        user = getpass.getuser()
        if len(user) > 2:
            self.assertNotIn(user, self.text)
        self.assertNotIn("Someone", self.text)   # Windows-style path
        self.assertNotIn("someone", self.text)   # POSIX-style path
        self.assertIn("<user>", self.text)

    def test_lists_every_effective_mapping(self):
        for src in self.cfg.mappings["keypad"]:
            self.assertIn(f"`{src}`", self.text)

    def test_is_read_only(self):
        """Building a report must never touch the config it describes."""
        import types
        from keyremap import backends, doctor, report
        before = os.path.getmtime(self.cfg.path)
        real_doctor, real_get = doctor.run, backends.get_backend
        doctor.run = lambda cfg: []
        backends.get_backend = lambda env: types.SimpleNamespace(
            detect=lambda cfg: [])
        try:
            report.build(self.cfg)
        finally:
            doctor.run, backends.get_backend = real_doctor, real_get
        self.assertEqual(before, os.path.getmtime(self.cfg.path))

    def test_behaviour_hash_is_platform_independent(self):
        """The whole promise: the same config means the same mapping. Two
        platforms resolving the same layers must produce the same hash."""
        import hashlib
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def h(plat):
            c = cfgmod.load(os.path.join(here, "config.yaml"), plat=plat,
                            host="same-host")
            b = "\n".join(f"{d}.{s}={a.describe()}"
                          for d, t in sorted(c.mappings.items())
                          for s, a in sorted(t.items()))
            return hashlib.sha256(b.encode()).hexdigest()[:16]

        self.assertEqual(h("darwin"), h("windows"),
                         "macOS and Windows resolve to different behaviour")
        self.assertEqual(h("darwin"), h("linux"))

    def test_behaviour_hash_changes_when_a_mapping_changes(self):
        import hashlib
        doc = json.loads(json.dumps(BASE_DOC))
        doc["profiles"] = {"base": {"kp": {"esc": "home"}}}
        a = cfgmod.load(write_cfg(doc), plat="linux", host="x")
        doc["profiles"] = {"base": {"kp": {"esc": "end"}}}
        b = cfgmod.load(write_cfg(doc), plat="linux", host="x")
        f = lambda c: hashlib.sha256("\n".join(
            f"{d}.{s}={act.describe()}" for d, t in sorted(c.mappings.items())
            for s, act in sorted(t.items())).encode()).hexdigest()
        self.assertNotEqual(f(a), f(b))

    def test_redaction_helper_handles_both_conventions(self):
        from keyremap.report import _redact
        out = _redact("C:\\Users\\Alice\\x and /home/bob/y and /Users/carol/z")
        for name in ("Alice", "bob", "carol"):
            self.assertNotIn(name, out)
