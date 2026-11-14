"""Private recent-URL projection of an existing canonical bookmark capture.

This module neither acquires source data nor changes capture evidence. The
caller supplies the normalized document and an explicit reference clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unicodedata import category

_SCHEMA = "mindcap.chrome-bookmarks.normalized/v0.1"
_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class RecentBookmarkURLs:
    """One deterministic private view, with disjoint exclusion counts."""

    as_of: datetime
    cutoff: datetime
    urls: tuple[str, ...] = field(repr=False)
    source_count: int
    duplicate_count: int
    older_count: int
    future_count: int
    unknown_date_count: int
    invalid_url_count: int

    def text(self) -> str:
        """Render exact URLs as lines; callers own private output/storage policy."""
        return "".join(url + "\n" for url in self.urls)


def _added_at(record: Mapping[str, object]) -> datetime | None:
    if record.get("date_added_status") != "parsed":
        return None
    value = record.get("date_added")
    if not isinstance(value, str):
        return None
    try:
        added = datetime.fromisoformat(value)
        if added.tzinfo is None or added.utcoffset() is None:
            return None
        return added.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _line_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or value.isspace():
        return None
    if any(category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in value):
        return None
    return value


def project_recent_bookmark_urls(
    document: Mapping[str, object], *, as_of: datetime
) -> RecentBookmarkURLs:
    """Select exact URLs created in the inclusive rolling seven-day UTC window.

    The normalized capture already owns Chrome epoch conversion, source order,
    profile discovery and root traversal. Filter before exact-string deduplication
    so an older occurrence cannot suppress a qualifying one. Do not reinterpret,
    canonicalize or fetch URLs. The result is private, not a sharing approval.
    """
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("An explicit timezone-aware as_of clock is required.")
    if as_of.utcoffset() is None:
        raise ValueError("An explicit timezone-aware as_of clock is required.")
    try:
        as_of = as_of.astimezone(UTC)
        cutoff = as_of - _WINDOW
    except (ValueError, OverflowError) as error:
        raise ValueError(
            "The clock must support a complete seven-day window."
        ) from error
    if (
        not isinstance(document, Mapping)
        or document.get("schema") != _SCHEMA
        or document.get("provider") != "chrome-bookmarks"
    ):
        raise ValueError("A canonical normalized Chrome bookmark capture is required.")
    records = document.get("bookmark_records")
    if not isinstance(records, list) or any(
        not isinstance(record, Mapping) or record.get("record_type") != "bookmark"
        for record in records
    ):
        raise ValueError("Canonical bookmark records must be an array of bookmarks.")

    urls: list[str] = []
    seen: set[str] = set()
    duplicate_count = older_count = future_count = 0
    unknown_date_count = invalid_url_count = 0
    for record in records:
        added = _added_at(record)
        if added is None:
            unknown_date_count += 1
        elif added < cutoff:
            older_count += 1
        elif added > as_of:
            future_count += 1
        else:
            url = _line_url(record.get("url"))
            if url is None:
                invalid_url_count += 1
            elif url in seen:
                duplicate_count += 1
            else:
                seen.add(url)
                urls.append(url)
    return RecentBookmarkURLs(
        as_of=as_of,
        cutoff=cutoff,
        urls=tuple(urls),
        source_count=len(records),
        duplicate_count=duplicate_count,
        older_count=older_count,
        future_count=future_count,
        unknown_date_count=unknown_date_count,
        invalid_url_count=invalid_url_count,
    )
