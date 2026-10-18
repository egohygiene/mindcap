"""Credential and terminal-injection coverage for shared diagnostics."""

from __future__ import annotations

import pytest

from mindcap.core.diagnostics import redact_text


def test_named_session_cookies_are_redacted_outside_cookie_header() -> None:
    text = redact_text("__client=private-value __clerk_db_jwt=another-value")
    assert "private-value" not in text
    assert "another-value" not in text


@pytest.mark.parametrize(
    "value,secrets",
    [
        (
            "HTTP 403 https://alice:password@cdn.example/song.mp3"
            "?signature=signed-secret&expires=42#private-fragment",
            ["alice", "password", "signed-secret", "private-fragment", "expires"],
        ),
        (
            "Authorization: Bearer auth-secret\n"
            "Cookie: first=cookie-secret; __session=session-secret\n"
            "HTTP 403 at cdn.example",
            ["auth-secret", "cookie-secret", "session-secret"],
        ),
        (
            "headers={'Authorization': 'Bearer auth-secret', "
            "'Cookie': 'first=cookie-secret; second=another-cookie', "
            "'X-API-Key': 'key-secret'} HTTP 403",
            ["auth-secret", "cookie-secret", "another-cookie", "key-secret"],
        ),
        (
            'password="password with spaces" token=access-secret '
            "session_id=session-secret refresh_token=refresh-secret "
            "client_secret=client-secret HTTP 403",
            [
                "password with spaces",
                "access-secret",
                "session-secret",
                "refresh-secret",
                "client-secret",
            ],
        ),
        (
            "Bearer bearer-secret HTTP 403 Basic dXNlcjpwYXNz",
            ["bearer-secret", "dXNlcjpwYXNz"],
        ),
        (
            "HTTP 403 token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature_value",
            ["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxIn0", "signature_value"],
        ),
        (
            'HTTP 403 command --password "private password" --api-key private-key',
            ["private password", "private-key"],
        ),
    ],
)
def test_credentials_are_redacted_but_status_remains(
    value: str, secrets: list[str]
) -> None:
    safe = redact_text(value)
    for secret in secrets:
        assert secret not in safe
    assert "403" in safe
    assert "[REDACTED]" in safe
    assert redact_text(safe) == safe


def test_url_redaction_preserves_public_host_path_port_and_punctuation() -> None:
    safe = redact_text(
        "Fetch (https://user:secret@cdn.example:8443/song.mp3?t=secret)."
    )
    assert safe == "Fetch (https://cdn.example:8443/song.mp3?[REDACTED])."
    assert redact_text(safe) == safe


def test_ipv6_and_invalid_urls_are_safe() -> None:
    assert redact_text("https://user:secret@[::1]:80/audio?key=value") == (
        "https://[::1]:80/audio?[REDACTED]"
    )
    assert "private-secret" not in redact_text(
        "https://user:private-secret@[invalid/audio"
    )


def test_terminal_controls_and_hyperlinks_cannot_inject_events() -> None:
    safe = redact_text(
        "\x1b[31mHTTP 403\x1b[0m\r\nFORGED\x00\u202e"
        "\x1b]8;;https://private-secret\x07label\x1b]8;;\x07"
    )
    assert safe == "HTTP 403 FORGED label"
    assert "private-secret" not in safe


def test_useful_plain_messages_and_literal_markup_are_preserved() -> None:
    value = "Asset download failed (audio): HTTP 403 [red]provider text[/red]"
    assert redact_text(value) == value


@pytest.mark.parametrize(
    "value",
    [
        'HTTP 403 cookies={"name": "private", "value": "secret", "nested": [1, 2]}',
        "HTTP 403 'cookie': {'name': 'private', 'value': 'secret'}",
        'HTTP 403 storage_state={"cookies": [{"value": "secret"}]}',
        'HTTP 403 auth={"username": "private", "password": "secret"}',
        'HTTP 403 cookies=[{"value": "secret"}]',
        'HTTP 403 cookies={"value": "secret"',
    ],
)
def test_nested_or_malformed_auth_payloads_are_omitted(value: str) -> None:
    safe = redact_text(value)
    assert "secret" not in safe
    assert "private" not in safe
    assert "403" in safe
    assert redact_text(safe) == safe


def test_inline_digest_auth_omits_entire_header_payload() -> None:
    safe = redact_text(
        'HTTP 403 Authorization: Digest username="private", nonce=secret'
    )
    assert "private" not in safe
    assert "secret" not in safe
    assert "403" in safe
