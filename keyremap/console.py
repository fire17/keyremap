"""One place that makes stdout safe for the characters we actually print.

Windows consoles default to cp1252, where a plain '✓' raises
UnicodeEncodeError and takes the whole program with it. Every entry point
calls this first; `errors="replace"` means a console that still cannot encode
a glyph shows a placeholder instead of crashing a status report.
"""

import sys


def utf8(*streams) -> None:
    for stream in (streams or (sys.stdout, sys.stderr)):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # already wrapped, closed, or not reconfigurable — never fatal
