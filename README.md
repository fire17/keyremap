# keyremap — portable per-device key remapping

Remap keys on **one specific input device** (e.g. a Bluetooth keypad) without
touching any other keyboard. One OS-agnostic `config.yaml` drives every
platform backend.

```
config.yaml          what to remap (devices by VID/PID + mappings)
remap.py             CLI: detect | listen | check | apply
keyremap/keys.py     canonical key table (VK/scancode/AHK/evdev/Karabiner)
keyremap/backends/   windows.py  linux_evdev.py  macos.py
assets/              RawKbListen.ps1 (Windows raw-input listener)
out/<env>/           generated artifacts (AHK script / Karabiner JSON)
```

## Commands

```bash
python3 remap.py detect          # list devices, mark which match config
python3 remap.py listen [secs]   # stream keydowns tagged with source device
python3 remap.py check           # validate config against ALL backends
python3 remap.py apply           # build/run remapping for this OS
```

## How each platform works

| Env | Detect/listen | Remap mechanism | Activation |
|---|---|---|---|
| **Windows / WSL** | Raw Input API (per-device attribution) | AutoHotkey v2 + AutoHotInterception (Interception kernel driver) → true per-device blocking | run generated `.ahk`; add to Startup folder |
| Windows (no driver) | same | `apply --mode heuristic`: plain AHK, identifies the keypad by its NumLock-churn fingerprint | degraded — only for devices with that quirk |
| **Linux** | evdev | grab device + re-emit via uinput (live daemon) | `apply` runs it; systemd unit template in `linux_evdev.py` |
| **macOS** | `hidutil list` | generated Karabiner-Elements rule with `device_if` VID/PID condition | copy JSON to `~/.config/karabiner/assets/complex_modifications/`, enable rule |

WSL note: WSL2 has no `/dev/input`; the tool auto-detects WSL and drives the
Windows side through `powershell.exe` interop.

## Windows one-time setup (interception mode) — no reboot required

1. Install [AutoHotkey v2](https://www.autohotkey.com/).
2. Download [AutoHotInterception](https://github.com/evilC/AutoHotInterception/releases);
   put its `Lib\` (including `AutoHotInterception.dll` from `Common\Lib\`) next
   to the generated `.ahk`.
3. From AHI's release, run `install-interception.exe /install` **as admin**.
4. **Instead of rebooting**, restart just the target device so the newly
   registered class filter attaches to its stack (admin):

       pnputil /restart-device "<device instance id>"

   Get the id from `remap.py detect`. Only that device is disrupted (~2 s);
   the built-in keyboard is untouched and never gets the filter this way.
5. `python3 remap.py apply` → run `out/<env>/keyremap_interception.ahk`.
6. Auto-start: `keyremap-launch.cmd` in `shell:startup` (picks interception
   mode when the driver is present, else the heuristic fallback).

### Gotchas that cost real debugging time

- **Bluetooth-LE HID devices report VID/PID as `0x0000` to the Interception
  driver**, so `GetKeyboardId(vid, pid)` fails *and pops its own MsgBox before
  your `try` can catch it*. Match on the device **handle** substring instead
  (e.g. `VID&02045e_PID&0040`) via `AHI.GetInstance().GetDeviceList()`.
- **Heuristic mode is a genuine fallback, not an equal**: this keypad's
  firmware watches NumLock LED feedback and *stops emitting* its NumLock
  churn if you swallow it (or force `SetNumLockState "AlwaysOn"`), destroying
  the very fingerprint the mode relies on. Let the churn through; only record
  timestamps.
- **Never hold a log file open from WSL (`tail -f` on `/mnt/c/...`) while a
  Windows process appends to it** — the sharing violation makes AHK's
  `FileAppend` throw *inside the hotkey*, which swallows that key
  system-wide behind a modal dialog. Log after `Send`, wrapped in `try`.
- A LL-hook + Raw Input "fusion" approach (block in the hook, attribute via
  `WM_INPUT`) does **not** work: the raw event does not arrive while the hook
  is blocking, so attribution always times out. Kept in git history as a
  dead end; use the driver.

## Config

```yaml
devices:
  keypad:
    match: { vendor_id: 0x045E, product_id: 0x0040, name_contains: "Keypad" }

options:
  passthrough_unmapped: true
  swallow_numlock_quirk: true   # BLE keypads that churn NumLock around nav keys

mappings:
  keypad:
    # simple remap — key names and aliases live in keyremap/keys.py
    clear: end
    esc: home

    # a key can BE a modifier: held down while you press others
    pagedown: lctrl
    pageup: lshift

    # combos; "accel" is Ctrl on Windows/Linux and Command on macOS
    end: accel+v                # paste, on every platform

    # press / tap / hold — any of them may be a list (a sequence)
    home:
      tap: accel+a              # fires on RELEASE, if released quickly
      hold: [accel+a, accel+c]  # fires at the hold_ms mark, suppresses the tap
      hold_ms: 400
```

**Action semantics**

| Field | Fires |
|---|---|
| `press` | immediately on key-down (the default for a plain `key: target` line) |
| `tap` | on release, only if released before `hold_ms` |
| `hold` | at the `hold_ms` mark while still held — and then `tap` is suppressed |

The tap/hold split is useful when you want the effect to be *visible confirmation*:
tap = select all, hold = select all **and** copy, so seeing the selection appear late
tells you the copy ran too.

**Portability.** `accel` and the tap/hold model are implemented natively per platform —
AutoHotkey timers on Windows, Karabiner's `to_if_alone` / `to_if_held_down` on macOS,
and deadline handling in the evdev loop on Linux. One config, no per-OS edits.

## Finding your device's key codes

Never guess scancodes — capture them. On Windows, `windows-tools/list-devices.ahk`
enumerates what the driver sees, and a capture script (non-blocking `SubscribeKeyboard`)
logs the exact code for each key you press. `remap.py listen` does the same at the
Raw Input level, tagging every keystroke with its source device.

Real example: this keypad's nav keys are numpad keys — the firmware sends
NumLock↓ + fake-Shift + key + NumLock↑ around each one (hence `swallow_numlock_quirk`),
and its extended keys arrive as `0x147` (Home), `0x14F` (End), `0x149`/`0x151`
(PgUp/PgDn), `0x152`/`0x153` (Ins/Del) — AutoHotInterception marks E0-extended with
`0x100`, not `0x200`.

## Status

Windows (Interception mode) is **used daily and verified** — every mapping above was
observed firing. The Linux and macOS backends generate correct, schema-verified output
but have **not been run on real hardware yet**; treat them as untested.

## License

MIT — see [LICENSE](LICENSE).
