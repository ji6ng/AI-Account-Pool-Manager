"""Shared Linux run mode and startup selection."""
from __future__ import annotations

import os
import sys
from typing import Callable

_mode = ""


def mode() -> str:
    if not sys.platform.startswith("linux"):
        return "native"
    return _mode or ("desktop" if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") else "server")


def is_server() -> bool:
    return mode() == "server"


def is_desktop() -> bool:
    return mode() == "desktop"


def set_mode(value: str | None = None, *, log: Callable[[str], None] = print) -> str:
    global _mode
    if not sys.platform.startswith("linux"):
        _mode = ""
        return "native"
    environment = os.environ.get("AIPOOL_MODE", "")
    if environment and environment not in ("server", "desktop"):
        log(f"警告：忽略无效 AIPOOL_MODE={environment!r}，仅支持 server|desktop")
    if value is not None:
        if value not in ("server", "desktop"):
            raise ValueError(f"Invalid Linux mode: {value}")
        _mode, source = value, "flag"
    elif environment in ("server", "desktop"):
        _mode, source = environment, "env"
    else:
        _mode = "desktop" if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") else "server"
        source = "auto"
    log(f"Linux mode={_mode} source={source}")
    return _mode
