from __future__ import annotations

import hashlib
import os
from pathlib import Path

_AUTO_SENTINELS = frozenset({"", "auto", "local"})


def canonicalize_chrome_bookmarks_identifier(value: str | None) -> tuple[str, None]:
    raw = (value or "").strip()
    if raw.lower() in _AUTO_SENTINELS:
        return "local", None
    expanded = Path(os.path.expandvars(raw)).expanduser()
    digest = hashlib.sha256(
        str(expanded.resolve(strict=False)).encode("utf-8")
    ).hexdigest()
    return f"path-{digest[:12]}", None


def supports_chrome_bookmarks_source(value: str | None) -> bool:
    canonicalize_chrome_bookmarks_identifier(value)
    return True
