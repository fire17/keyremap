# keyremap

**Remap one keyboard without touching the others — and take that mapping to any computer.**

Plug a keypad into a Mac, a PC and a Linux box, run one command on each, and all three
behave identically. Devices are identified by their *hardware id*, so the config is
portable by construction; per-OS and per-machine layers let you deviate where you must
without forking the base.

```
keyremap            # the control room (TUI)
keyremap gui        # the desktop app (native window, or browser UI)
keyremap doctor     # what this machine still needs, and the exact fix
keyremap selftest   # prove the install is sound here, before you trust it
keyremap apply      # build + deploy for this OS
```

---

## Install

**macOS / Linux / WSL**

```sh
curl -fsSL https://raw.githubusercontent.com/OWNER/keyremap/main/install.sh | sh
```

**Windows** — clone, then run `windows-tools\install.ps1` (or use WSL, which drives the
Windows side automatically).

No pip, no venv, no dependencies — everything is Python standard library, **including the
YAML reader**. (A clean Windows Python with no `pyyaml` was exactly how that gap was
found: the config failed to load. It now parses either way, and the built-in reader is
tested to agree with pyyaml byte-for-byte on the shipped config.)

### Taking your setup to another computer

```sh
keyremap export                 # writes keyremap-<host>.keyremap
# …copy that one file across…
keyremap import keyremap-*.keyremap
keyremap doctor && keyremap apply
```

That is the whole migration. Your existing config is backed up, never overwritten.

---

## The idea

Two keyboards can emit *byte-identical* keystrokes — same virtual key, same scancode. The
only thing that separates them is which device the event came from, so remapping "just the
keypad" requires device-aware interception, not a registry tweak or a global hotkey script.
keyremap owns that plumbing on three platforms and hides it behind one config file.

| Platform | Mechanism | Reboot? |
|---|---|---|
| **macOS** | generated Karabiner-Elements rules (`device_if` on vendor/product id) | no |
| **Windows / WSL** | AutoHotkey v2 + AutoHotInterception (Interception driver) | no — see below |
| **Linux** | evdev grab + uinput re-emit (live daemon) | no |

WSL2 has no `/dev/input`; keyremap detects that and drives the Windows side over
`powershell.exe` interop.

---

## Config

One file. Layers merge **base → os:*platform* → host:*hostname*** (later wins per key,
`null` removes an inherited mapping).

```yaml
version: 2

devices:
  keypad:
    match: { vendor_id: 0x045E, product_id: 0x0040, name_contains: "Keypad" }

profiles:
  base:                      # everywhere — this is what travels
    keypad:
      esc: home
      pagedown: lctrl        # a key can BE a modifier (held, not tapped)
      end: accel+v           # accel = Ctrl on Win/Linux, Command on macOS
      home:
        tap: accel+a         # on release, if released quickly
        hold: [accel+a, accel+c]   # at the threshold, still held
        hold_ms: 400

  os:
    darwin:                  # only on macOS
      keypad: { esc: escape }
  host:
    tamis-mac:               # only on that machine
      keypad: { tab: null }  # remove an inherited mapping here
```

### Actions

| Field | Fires |
|---|---|
| `press` | immediately on key-down (the default for a plain `key: target` line) |
| `tap` | on release — only if released before `hold_ms` |
| `hold` | at the `hold_ms` mark while still held; suppresses `tap` |

Any of them may be a **list**, which runs as a sequence. That is how "tap = select all,
hold = select all **and** copy" works: seeing the selection appear late is your proof the
copy ran too.

`accel` is the portable shortcut modifier — write `accel+a` once and get Ctrl+A on
Windows/Linux and Cmd+A on macOS. Aliases: `mod`, `cmdctrl`, `super`.

---

## The control room (TUI)

`keyremap` with no arguments. Five panes, one keystroke apart, ~0% CPU when idle
(the loop blocks in `select()`), and it hot-reloads the config the moment you save it.

```
 1 Dashboard  2 Mappings  3 Capture  4 Doctor  5 Apply ──────────────────────────

  Device
  configured        keypad
  fingerprint       keypad → usb:045e:0040
  present           HID Keyboard Device

  Engine
  backend           wsl
  last applied      2m ago  (10 mappings)

  Profile layers
    ● base
    ○ os:darwin  (not this machine)

  effective         10 mappings
```

- **Mappings** — every effective mapping *and the layer it came from*, so you always know
  why a key does what it does.
- **Capture** — live keystrokes tagged with their source device. Use it to learn a key's
  real scancode before mapping it; guessing silently fails (see below).
- **Doctor** — per-platform checks with the exact fix command for anything missing.
- **Apply** — builds and deploys, and records what was deployed.

`keyremap gui` is the same thing as a desktop window (tkinter, which ships with Python).

---

## Finding a key's real code

Never guess a scancode — capture it. In the TUI press **3** then **s**, or run
`keyremap listen 30`, then press the key. Real numbers from a Bluetooth keypad:

| Key | Code | Note |
|---|---|---|
| Home / End | `0x147` / `0x14F` | E0-extended |
| PgUp / PgDn | `0x149` / `0x151` | E0-extended |
| Ins / Del | `0x152` / `0x153` | E0-extended |

AutoHotInterception marks E0-extended with `0x100` — not `0x200`.

---

## Gotchas that cost real debugging time

- **Bluetooth-LE devices report VID/PID as `0x0000` to the Interception driver.**
  `GetKeyboardId(vid, pid)` therefore fails *and pops its own dialog before your `try` can
  catch it*. Match the device **handle** substring via `GetDeviceList()` instead.
- **No reboot is needed after installing the Interception driver.** Restart only the target
  device — `pnputil /restart-device "<instance-id>"` — and the class filter attaches to it
  alone. Your built-in keyboard never receives the filter at all.
- **`Map.Delete()` throws in AutoHotkey v2 when the key is absent**, and a throwing callback
  dies silently: the key simply stops working while every other key behaves normally.
  Generated scripts only ever assign, and wrap callbacks in a logging `try`.
- **Keypads send auto-repeat while held** (27 events in one press, observed), which spams
  press actions and restarts hold timers forever. Generated scripts ignore repeats.
- **Driver-free "heuristic" mode is a fallback, not an equal.** One keypad's firmware
  watches NumLock LED feedback and stops emitting its NumLock churn if you swallow it —
  destroying the fingerprint the heuristic depends on.
- **Never hold a `/mnt/c` log open from WSL while Windows appends to it.** The sharing
  violation makes `FileAppend` throw *inside* a hotkey, swallowing that key behind a modal
  dialog.

---

## How it's verified

The part that can actually be *wrong* is the decision logic — what a press, release,
repeat or hold should emit. That lives in `engine.py` as a pure function of events, with
no OS in the way, so it is tested exhaustively on any machine; the Linux backend is a thin
shell around it, and Windows/macOS delegate the same contract to AutoHotkey timers and
Karabiner's `to_if_alone` / `to_if_held_down`.

Everything else is checked by **strict artifact validation**. `keyremap selftest`
generates the real output for all three platforms and validates it against each one's
actual vocabulary — so a mistake is caught on the machine you're standing at, not on the
machine you're flying to:

```
keyremap selftest

  ✓ engine semantics                  tap/hold/repeat/modifier contract (simulated)
  ✓ artifact: macOS (Karabiner)       generated + validated
  ✓ artifact: Windows (AutoHotkey)    generated + validated
  ✓ artifact: Linux (evdev)           generated + validated
  ✓ export/import round-trip          same behaviour after moving machines
  ✓ …environment checks…
  ALL GREEN — safe to apply on this machine.
```

The validators have negative tests proving they *fail* on the bugs that actually bit this
project: an invented Karabiner `key_code`, a manipulator with **no `device_if`** (which
would silently remap every keyboard on the Mac), an AHK `Map.Delete` on a state map, and
`GetKeyboardId()` on a Bluetooth device.

```sh
python3 -m unittest discover -s tests -v     # 59 tests, ~0.07s
```

Layout: `keys.py` (one canonical key table → Windows/evdev/Karabiner names), `config.py`
(layering + validation), `engine.py` (the semantics), `backends/` (one per platform),
`validate.py`, `tui/` and `gui.py` + `webgui.py` (thin views over shared pure helpers).

## Status — honestly

| Path | State |
|---|---|
| Windows (Interception) | **used daily, verified end-to-end** on real hardware |
| Native GUI (tkinter) | **verified running** on Windows with live device detection |
| Browser GUI | **verified running** (served and loaded from another OS's browser) |
| CLI on native Windows Python | **verified** — 64 tests + selftest green, no pyyaml installed |
| macOS (Karabiner) | artifact generated and strictly validated; **not yet loaded by a real Karabiner** |
| Linux (evdev) | engine tested exhaustively; **kernel grab not yet run on real hardware** |

Run `keyremap selftest` on a new machine and it will tell you which of these apply to you.

## License

MIT — see [LICENSE](LICENSE).
