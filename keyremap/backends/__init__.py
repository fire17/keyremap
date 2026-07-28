"""Backend selection. Imports are lazy so startup stays instant."""


def get_backend(env: str):
    if env in ("windows", "wsl"):
        from . import windows as be
    elif env == "linux":
        from . import linux_evdev as be
    elif env == "macos":
        from . import macos as be
    else:
        raise RuntimeError(f"no backend for environment {env!r}")
    return be
