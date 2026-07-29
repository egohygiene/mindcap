import pytest

from mindcap.core.errors import InvalidSourceError
from mindcap.plugins.chatgpt.identifiers import canonicalize_chatgpt_identifier
from mindcap.plugins.chatgpt.strategies.browser import (
    _is_auth_redirect,
    _safe_response_url,
)

IDENTIFIER = "6a14b69f-7834-83ea-8257-0eceadb41691"


def test_bare_identifier_is_canonicalized() -> None:
    identifier, url = canonicalize_chatgpt_identifier(IDENTIFIER)
    assert identifier == IDENTIFIER
    assert url == f"https://chatgpt.com/c/{IDENTIFIER}"


def test_url_is_canonicalized() -> None:
    identifier, url = canonicalize_chatgpt_identifier(
        f"https://chatgpt.com/c/{IDENTIFIER}"
    )
    assert identifier == IDENTIFIER
    assert url == f"https://chatgpt.com/c/{IDENTIFIER}"


def test_invalid_source_is_rejected() -> None:
    with pytest.raises(InvalidSourceError):
        canonicalize_chatgpt_identifier("https://example.com/not-chatgpt")


def test_response_url_drops_query_and_fragment() -> None:
    assert (
        _safe_response_url(
            "https://chatgpt.com/internal/conversation?id=secret#fragment"
        )
        == "https://chatgpt.com/internal/conversation"
    )


# ---------------------------------------------------------------------------
# Auth redirect detection
# ---------------------------------------------------------------------------


def test_auth_path_prefix_detected() -> None:
    assert _is_auth_redirect("https://chatgpt.com/auth/login") is True


def test_auth_auth_subpath_detected() -> None:
    assert _is_auth_redirect("https://chatgpt.com/auth/callback") is True


def test_login_path_detected() -> None:
    assert _is_auth_redirect("https://chatgpt.com/login") is True


def test_login_subpath_detected() -> None:
    assert _is_auth_redirect("https://chatgpt.com/login/") is True


def test_sso_path_detected() -> None:
    assert _is_auth_redirect("https://chatgpt.com/sso/redirect") is True


def test_google_accounts_hostname_detected() -> None:
    assert _is_auth_redirect("https://accounts.google.com/o/oauth2/auth") is True


def test_normal_conversation_url_not_flagged() -> None:
    assert _is_auth_redirect(f"https://chatgpt.com/c/{IDENTIFIER}") is False


def test_uploading_path_not_flagged() -> None:
    """'/uploading' must not match the '/login' prefix check."""
    assert _is_auth_redirect("https://chatgpt.com/uploading") is False


def test_empty_url_not_flagged() -> None:
    assert _is_auth_redirect("") is False
