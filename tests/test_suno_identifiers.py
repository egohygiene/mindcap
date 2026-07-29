from __future__ import annotations

import pytest

from mindcap.core.errors import InvalidSourceError
from mindcap.plugins.suno.identifiers import canonicalize_suno_identifier

WORKSPACE_ID = "8f8fd77f-c5bf-467a-8cb5-558fdbf86386"


def test_suno_bare_identifier_is_canonicalized() -> None:
    identifier, url = canonicalize_suno_identifier(WORKSPACE_ID)
    assert identifier == WORKSPACE_ID
    assert url == f"https://suno.com/create?wid={WORKSPACE_ID}"


def test_suno_workspace_url_is_canonicalized() -> None:
    identifier, url = canonicalize_suno_identifier(
        f"https://suno.com/create?wid={WORKSPACE_ID}"
    )
    assert identifier == WORKSPACE_ID
    assert url == f"https://suno.com/create?wid={WORKSPACE_ID}"


def test_suno_clip_url_is_rejected() -> None:
    with pytest.raises(InvalidSourceError):
        canonicalize_suno_identifier(f"https://suno.com/song/{WORKSPACE_ID}")
