from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_content_hash(normalized: dict[str, Any]) -> str:
    semantic = dict(normalized)
    semantic.pop("capture_version", None)
    semantic.pop("captured_at", None)
    return sha256_bytes(canonical_json_bytes(semantic))
