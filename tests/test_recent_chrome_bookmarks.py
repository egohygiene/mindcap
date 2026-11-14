"""Offline recent-view boundaries and preservation of canonical Chrome evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from mindcap.core.models import CaptureRequest
from mindcap.core.protocols import SourcePlugin
from mindcap.plugins.chrome_bookmarks.plugin import ChromeBookmarksPlugin
from mindcap.plugins.chrome_bookmarks.recent import project_recent_bookmark_urls

AS_OF = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
CUTOFF = AS_OF - timedelta(days=7)
SCHEMA = "mindcap.chrome-bookmarks.normalized/v0.1"


def record(
    url: object = "https://example.invalid/recent",
    added: datetime | str | None = AS_OF,
    *,
    status: str = "parsed",
    **metadata: object,
) -> dict[str, object]:
    return {
        "record_type": "bookmark",
        "url": url,
        "date_added": added.isoformat() if isinstance(added, datetime) else added,
        "date_added_status": status,
        **metadata,
    }


def document(records: Sequence[object]) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "provider": "chrome-bookmarks",
        "bookmark_records": list(records),
    }


def test_window_is_inclusive_to_microsecond_precision() -> None:
    view = project_recent_bookmark_urls(
        document(
            [
                record("https://example.invalid/cutoff", CUTOFF),
                record("https://example.invalid/now", AS_OF),
                record(
                    "https://example.invalid/old", CUTOFF - timedelta(microseconds=1)
                ),
                record(
                    "https://example.invalid/future", AS_OF + timedelta(microseconds=1)
                ),
            ]
        ),
        as_of=AS_OF,
    )

    assert view.urls == (
        "https://example.invalid/cutoff",
        "https://example.invalid/now",
    )
    assert view.older_count == 1
    assert view.future_count == 1
    assert view.cutoff == CUTOFF


def test_offsets_are_compared_as_instants_in_a_168_hour_utc_window() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    view = project_recent_bookmark_urls(
        document([record(added=CUTOFF.astimezone(offset))]),
        as_of=AS_OF.astimezone(offset),
    )

    assert view.urls == ("https://example.invalid/recent",)
    assert view.as_of == AS_OF
    assert view.as_of.tzinfo is UTC
    assert view.cutoff.tzinfo is UTC
    assert view.as_of - view.cutoff == timedelta(hours=168)


@pytest.mark.parametrize(
    "clock",
    [datetime(2026, 11, 14, 12), "private-reference-clock", None],
)
def test_reference_clock_requires_an_explicit_timezone(clock: object) -> None:
    with pytest.raises(ValueError, match="timezone-aware") as caught:
        project_recent_bookmark_urls(document([]), as_of=cast(datetime, clock))

    assert "private-reference-clock" not in str(caught.value)


def test_reference_clock_must_allow_the_complete_window() -> None:
    with pytest.raises(ValueError, match="complete seven-day window"):
        project_recent_bookmark_urls(
            document([]), as_of=datetime.min.replace(tzinfo=UTC)
        )


def test_unknown_dates_are_counted_without_reinterpreting_raw_chrome_values() -> None:
    records = [
        record(status="missing", added=None),
        record(status="zero", added=None),
        record(status="invalid", added="not-a-date"),
        record(added="2026-11-14T12:00:00"),
        record(added="not-a-date"),
        record(added=None),
        record(added="2026-11-14T12:00:00+25:00"),
        record(status="missing", date_added_raw="13439131200000000"),
    ]
    view = project_recent_bookmark_urls(document(records), as_of=AS_OF)

    assert view.urls == ()
    assert view.unknown_date_count == len(records)
    assert view.older_count == view.future_count == view.invalid_url_count == 0


def test_filtering_precedes_deduplication_of_the_same_url() -> None:
    url = "https://example.invalid/shared"
    view = project_recent_bookmark_urls(
        document(
            [
                record(url, CUTOFF - timedelta(days=1)),
                record(url, AS_OF + timedelta(days=1)),
                record(url, status="missing"),
                record(url),
                record(url, CUTOFF),
            ]
        ),
        as_of=AS_OF,
    )

    assert view.urls == (url,)
    assert (view.older_count, view.future_count, view.unknown_date_count) == (1, 1, 1)
    assert view.duplicate_count == 1


def test_all_captured_profiles_and_roots_keep_first_eligible_source_order() -> None:
    records = [
        record("https://example.invalid/z", profile_id="one", root_kind="bookmark_bar"),
        record("https://example.invalid/a", profile_id="one", root_kind="other"),
        record("chrome://bookmarks", profile_id="one", root_kind="synced"),
        record("https://example.invalid/z", profile_id="two", root_kind="other"),
        record("https://example.invalid/b", profile_id="two", root_kind="synced"),
    ]
    view = project_recent_bookmark_urls(document(records), as_of=AS_OF)

    assert view.urls == (
        "https://example.invalid/z",
        "https://example.invalid/a",
        "chrome://bookmarks",
        "https://example.invalid/b",
    )
    assert view.duplicate_count == 1


def test_deduplication_does_not_normalize_or_screen_url_contents() -> None:
    urls = [
        "https://example.invalid/A?utm_source=synthetic#fragment",
        "https://example.invalid/a?utm_source=synthetic#fragment",
        "https://example.invalid/A?utm_source=synthetic#fragment/",
        "https://example.invalid/naïve",
        "https://example.invalid/literal%0Aencoding",
        "chrome://bookmarks",
    ]
    view = project_recent_bookmark_urls(
        document([record(url) for url in [*urls, urls[0]]]), as_of=AS_OF
    )

    assert view.urls == tuple(urls)
    assert view.duplicate_count == 1
    assert view.text() == "".join(url + "\n" for url in urls)


@pytest.mark.parametrize(
    "url",
    [
        "",
        " ",
        "\u00a0",
        None,
        23,
        "https://example.invalid/\x00",
        "https://example.invalid/\rsecond",
        "https://example.invalid/\nsecond",
        "https://example.invalid/\x85second",
        "https://example.invalid/\u2028second",
        "https://example.invalid/\u2029second",
        "https://example.invalid/\ud800",
    ],
)
def test_non_line_safe_urls_are_excluded_without_rewriting_them(url: object) -> None:
    source = document([record(url)])
    before = copy.deepcopy(source)
    view = project_recent_bookmark_urls(source, as_of=AS_OF)

    assert view.urls == ()
    assert view.text() == ""
    assert view.invalid_url_count == 1
    assert source == before


def test_exclusion_counts_are_disjoint_and_explain_every_source_record() -> None:
    view = project_recent_bookmark_urls(
        document(
            [
                record(),
                record(),
                record(None, CUTOFF - timedelta(days=1)),
                record(None, AS_OF + timedelta(days=1)),
                record(None, status="zero"),
                record(None),
            ]
        ),
        as_of=AS_OF,
    )

    assert view.source_count == 6
    assert (
        view.duplicate_count,
        view.older_count,
        view.future_count,
        view.unknown_date_count,
        view.invalid_url_count,
    ) == (1, 1, 1, 1, 1)
    assert view.source_count == len(view.urls) + 5


@pytest.mark.parametrize(
    "invalid",
    [
        {"schema": "private-schema-marker"},
        {"provider": "private-provider-marker"},
        {"bookmark_records": None},
        {"bookmark_records": ["private-record-marker"]},
        {"bookmark_records": [{"record_type": "private-record-marker"}]},
    ],
)
def test_unsupported_capture_shapes_fail_without_source_content_in_errors(
    invalid: dict[str, object],
) -> None:
    source = {**document([]), **invalid}
    with pytest.raises(ValueError) as caught:
        project_recent_bookmark_urls(source, as_of=AS_OF)

    assert "private-" not in str(caught.value)


@pytest.mark.parametrize("invalid", [None, [], "private-capture-marker"])
def test_non_mapping_input_reports_a_generic_validation_error(invalid: object) -> None:
    with pytest.raises(ValueError) as caught:
        project_recent_bookmark_urls(cast(dict[str, object], invalid), as_of=AS_OF)

    assert "private-capture-marker" not in str(caught.value)


def test_projection_is_immutable_and_does_not_expose_payload_in_repr() -> None:
    source = document(
        [
            record(
                "https://example.invalid/private-view",
                title="private-title-marker",
                profile_name="private-profile-marker",
                folder_path=["private-folder-marker"],
            )
        ]
    )
    before = copy.deepcopy(source)
    view = project_recent_bookmark_urls(source, as_of=AS_OF)

    assert source == before
    assert isinstance(view.urls, tuple)
    assert "example.invalid" not in repr(view)
    assert "private-" not in repr(view)
    with pytest.raises(FrozenInstanceError):
        view.source_count = 0  # type: ignore[misc]  # Intentional frozen mutation probe.


def test_empty_projection_has_no_phantom_line_or_source_record() -> None:
    view = project_recent_bookmark_urls(document([]), as_of=AS_OF)

    assert view.text() == ""
    assert view.urls == ()
    assert view.source_count == 0


def test_projection_does_not_open_network_connections_or_emit_source_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("socket.socket", side_effect=AssertionError("Network attempted")):
        view = project_recent_bookmark_urls(document([record()]), as_of=AS_OF)

    assert view.urls == ("https://example.invalid/recent",)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def chrome_node(url: str, added: datetime, identity: str) -> dict[str, object]:
    epoch = datetime(1601, 1, 1, tzinfo=UTC)
    return {
        "type": "url",
        "id": identity,
        "guid": "synthetic-" + identity,
        "name": "Synthetic bookmark",
        "url": url,
        "date_added": str((added - epoch) // timedelta(microseconds=1)),
    }


def inventory(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_existing_capture_pipeline_preserves_raw_and_normalized_evidence(
    tmp_path: Path,
) -> None:
    user_data = tmp_path / "Synthetic User Data"
    shared = "https://example.invalid/shared"
    payloads: dict[str, dict[str, list[dict[str, object]]]] = {
        "Default": {
            "bookmark_bar": [
                chrome_node(shared, AS_OF, "one"),
                {
                    "type": "folder",
                    "name": "Synthetic folder",
                    "children": [
                        chrome_node("https://example.invalid/nested", CUTOFF, "two")
                    ],
                },
            ],
            "other": [
                chrome_node(
                    "https://example.invalid/old", CUTOFF - timedelta(days=1), "three"
                )
            ],
            "synced": [chrome_node("chrome://bookmarks", AS_OF, "four")],
        },
        "Profile 1": {
            "bookmark_bar": [chrome_node(shared, CUTOFF, "five")],
            "other": [
                chrome_node("https://example.invalid/second-profile", AS_OF, "six")
            ],
            "synced": [],
        },
    }
    original_bytes = []
    for name, roots in payloads.items():
        path = user_data / name / "Bookmarks"
        path.parent.mkdir(parents=True)
        payload = {
            "version": 1,
            "roots": {
                key: {"type": "folder", "children": nodes}
                for key, nodes in roots.items()
            },
        }
        content = json.dumps(payload).encode("utf-8")
        path.write_bytes(content)
        original_bytes.append(content)
    source_before = inventory(user_data)
    plugin: SourcePlugin = ChromeBookmarksPlugin()
    request = CaptureRequest(
        source_type="chrome-bookmarks",
        source="",
        provider="chrome-bookmarks",
        canonical_identifier="local",
        strategy="filesystem",
        artifact_root=tmp_path / "artifacts",
        options={"user_data_dirs": [str(user_data)], "channel": "stable"},
    )

    with (
        patch("socket.socket", side_effect=AssertionError("Network attempted")),
        patch(
            "mindcap.plugins.chrome_bookmarks.discovery.automatic_user_data_roots",
            side_effect=AssertionError("Automatic profile discovery attempted"),
        ),
    ):
        envelope = plugin.strategy("filesystem").capture(request)
        normalized = plugin.normalize(envelope, "local")
        stored = plugin.storage().persist(
            request, envelope, normalized, plugin.render(normalized)
        )
        plugin.storage().verify(stored.path)
        capture_before = inventory(stored.path)
        normalized_before = copy.deepcopy(normalized)
        view = project_recent_bookmark_urls(normalized, as_of=AS_OF)
        plugin.storage().verify(stored.path)

    assert [unit.body for unit in envelope.response_units] == original_bytes
    assert view.urls == (
        shared,
        "https://example.invalid/nested",
        "chrome://bookmarks",
        "https://example.invalid/second-profile",
    )
    assert (view.source_count, view.older_count, view.duplicate_count) == (6, 1, 1)
    assert inventory(user_data) == source_before
    assert inventory(stored.path) == capture_before
    assert normalized == normalized_before
