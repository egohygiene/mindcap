from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mindcap.plugins.suno.auth import (
    authenticate_suno_cookie_stdin,
    load_suno_auth_state,
    normalize_cookie_input,
)


def test_normalize_cookie_input_accepts_raw_client_cookie() -> None:
    normalized = normalize_cookie_input("client-cookie-value")
    assert normalized.clerk_client_cookie == "client-cookie-value"
    assert normalized.cookie_header == "__client=client-cookie-value"


def test_normalize_cookie_input_accepts_cookie_header() -> None:
    normalized = normalize_cookie_input(
        "Cookie: foo=bar; __client=client-cookie-value; "
        "ajs_anonymous_id=%22device-123%22"
    )
    assert normalized.clerk_client_cookie == "client-cookie-value"
    assert normalized.device_id == "device-123"


def test_authenticate_suno_cookie_stdin_persists_private_state(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth" / "suno.json"
    with patch("mindcap.plugins.suno.auth.suno_auth_file", return_value=auth_path):
        state = authenticate_suno_cookie_stdin("client-cookie-value")
        loaded = load_suno_auth_state(required=True)

    assert auth_path.is_file()
    payload = json.loads(auth_path.read_text(encoding="utf-8"))
    assert payload["clerk_client_cookie"] == "client-cookie-value"
    assert state.device_id
    assert loaded and loaded.device_id == state.device_id
