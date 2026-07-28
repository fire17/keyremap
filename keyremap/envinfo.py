"""Detect the runtime environment so the right backend is chosen."""

import os
import platform
import shutil


def detect() -> str:
    """Return one of: 'wsl', 'windows', 'linux', 'macos'."""
    sysname = platform.system().lower()
    if sysname == "windows":
        return "windows"
    if sysname == "darwin":
        return "macos"
    if sysname == "linux":
        # WSL: kernel release mentions microsoft, and Windows interop exists
        try:
            with open("/proc/sys/kernel/osrelease", encoding="utf-8") as f:
                if "microsoft" in f.read().lower() and shutil.which("powershell.exe"):
                    return "wsl"
        except OSError:
            pass
        return "linux"
    raise RuntimeError(f"unsupported platform: {sysname}")


def windows_temp_dir() -> str:
    """WSL-visible path of the Windows %TEMP% dir (WSL only)."""
    import subprocess
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "$env:TEMP"],
        capture_output=True, text=True, check=True, timeout=20,
    ).stdout.strip()
    # C:\Users\X\AppData\Local\Temp -> /mnt/c/Users/X/AppData/Local/Temp
    drive = out[0].lower()
    return f"/mnt/{drive}" + out[2:].replace("\\", "/")


def to_windows_path(wsl_path: str) -> str:
    """Convert /mnt/c/... to C:\\..."""
    if not wsl_path.startswith("/mnt/") or len(wsl_path) < 7:
        raise ValueError(f"not a /mnt path: {wsl_path}")
    drive = wsl_path[5].upper()
    return f"{drive}:" + wsl_path[6:].replace("/", "\\")


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0
