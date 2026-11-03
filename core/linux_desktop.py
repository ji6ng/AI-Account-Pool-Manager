"""Linux desktop integration without requiring a desktop session."""
from __future__ import annotations

import os


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def desktop_exec(executable: str) -> str:
    escaped = executable.replace("%", "%%")
    for character in ('\\', '"', '`', '$'):
        escaped = escaped.replace(character, '\\' + character)
    escaped = escaped.replace('\\', '\\\\')
    escaped = escaped.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return '"' + escaped + '"'
