#!/usr/bin/env python3
"""Render an animated SVG of the TUI — no asciinema, no JS, no CDN.

GitHub's CSP strips scripts and external references from SVG, so the animation
is pure SMIL/CSS with the frames baked in. Frames come from the real renderer,
so the demo cannot drift from what the app actually draws.

Run: python3 tools/make_demo.py > assets/demo.svg
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keyremap.tui.app import App

ANSI = re.compile(r"\x1b\[[0-9;]*m")
COLS, ROWS = 96, 26
CW, CH = 8.2, 17.0          # character cell
PAD = 16

# palette matching the TUI's own
FG, MUTED, ACCENT, GOOD, LILAC, WARN = (
    "#e6e9f0", "#8b93a7", "#5cc8ff", "#5ddba4", "#c3a6ff", "#ffc95c")


def colour_for(line: str, raw: str) -> str:
    """Approximate the TUI's colours from the escape codes it emitted."""
    if "38;5;39" in raw:
        return ACCENT
    if "38;5;78" in raw:
        return GOOD
    if "38;5;183" in raw:
        return LILAC
    if "38;5;214" in raw:
        return WARN
    if "38;5;245" in raw:
        return MUTED
    return FG


def frames(cfg_path: str):
    """Real renderer, neutral data: the demo must never carry the author's
    hostname, device names or paths into a public repo."""
    from keyremap import doctor, state

    app = App(cfg_path)
    app.refresh_status(quick=True)
    if app.status is not None:                    # neutralise identifying bits
        app.status.host = "your-mac"
        app.status.env = "darwin"
        app.status.device_label = "Bluetooth Keypad"
        app.status.device_present = True
        app.status.engine_detail = "Karabiner-Elements active"
        app.status.engine_running = True
        app.status.deployment = state.Deployment(
            applied_at=__import__("time").time() - 120, mappings=10)
    app.env = "darwin"
    app.doctor_rows = [                            # a populated Doctor pane
        (doctor.OK, "Python >= 3.10", "3.12.7", ""),
        (doctor.OK, "Karabiner-Elements", "installed", ""),
        (doctor.OK, "Karabiner driver extension", "activated", ""),
        (doctor.OK, "karabiner_cli lint", "ok", ""),
        (doctor.OK, "configured device", "Bluetooth Keypad", ""),
    ]
    app.apply_output = [                           # a populated Apply pane
        "\x1b[38;5;78m✓\x1b[0m wrote ~/.config/karabiner/assets/"
        "complex_modifications/keyremap.json",
        "\x1b[38;5;245m10 mappings · enabled in profile 'Default'\x1b[0m",
    ]
    out = []
    for tab, dwell in ((0, 3), (1, 4), (3, 3), (4, 2)):
        app.tab = tab
        rendered = app.render()
        rows = []
        for raw in rendered[:ROWS]:
            rows.append((ANSI.sub("", raw)[:COLS], raw))
        out.append((rows, dwell))
    return out


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fs = frames(os.path.join(here, "config.yaml"))
    total = sum(d for _, d in fs)

    w = int(COLS * CW + PAD * 2)
    h = int(ROWS * CH + PAD * 2 + 10)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" '
        f'aria-label="keyremap terminal UI: dashboard, mappings, doctor, apply">',
        '<defs><style>'
        'text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
        f'font-size:13px;white-space:pre}}'
        '</style></defs>',
        f'<rect width="{w}" height="{h}" rx="10" fill="#0e1016"/>',
        # window chrome
        '<circle cx="26" cy="20" r="5" fill="#ff5f57"/>'
        '<circle cx="44" cy="20" r="5" fill="#febc2e"/>'
        '<circle cx="62" cy="20" r="5" fill="#28c840"/>',
    ]

    at = 0.0
    for i, (rows, dwell) in enumerate(fs):
        begin = at
        parts.append(f'<g opacity="0">')
        parts.append(
            f'<animate attributeName="opacity" values="0;1;1;0" '
            f'keyTimes="0;0.04;{1 - dwell / total * 0.15:.3f};1" '
            f'dur="{total}s" begin="{begin - at:.2f}s" '
            f'repeatCount="indefinite" '
            f'keySplines="" calcMode="linear"/>')
        # simple approach: each frame visible for its slice of the loop
        parts[-1] = (
            f'<animate attributeName="opacity" '
            f'values="{";".join("1" if j == i else "0" for j in range(len(fs)))};'
            f'{"1" if i == 0 else "0"}" '
            f'keyTimes="{";".join(f"{k / len(fs):.3f}" for k in range(len(fs)))};1" '
            f'dur="{total}s" repeatCount="indefinite" calcMode="discrete"/>')
        for r, (plain, raw) in enumerate(rows):
            if not plain.strip():
                continue
            y = PAD + 26 + r * CH
            parts.append(
                f'<text x="{PAD}" y="{y:.0f}" fill="{colour_for(plain, raw)}">'
                f'{esc(plain)}</text>')
        parts.append('</g>')
        at += dwell

    parts.append('</svg>')
    sys.stdout.write("\n".join(parts) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
