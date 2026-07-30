"""DistroKid My Music release discovery for the Mindcap sync subsystem.

Discovers every release available in the authenticated DistroKid My Music
library and yields canonical :class:`~mindcap.sync.models.SourceDescriptor`
objects.

Discovery strategy
------------------
Navigates to ``https://distrokid.com/mymusic/`` using the dedicated DistroKid
browser profile and collects release entries from the DOM.  Handles both
traditional pagination and infinite-scroll load-more patterns.

Authentication
--------------
Uses the dedicated DistroKid browser profile (Playwright / stable Chrome CDP),
the same profile used by the existing browser capture strategy.  No cookies or
tokens are written to descriptors.

Provider notes
--------------
The DistroKid My Music page renders releases via client-side JavaScript.  The
exact DOM structure is unstable and may change without notice.  This adapter
uses safe schema diagnostics and falls back gracefully when the expected
structure is not found.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from mindcap.core.progress import CaptureProgressReporter
from mindcap.sync.models import (
    CollectionRequest,
    DiscoveryResult,
    SourceDescriptor,
)

_MYMUSIC_URL = "https://distrokid.com/mymusic/"
_MAX_SCROLL_ROUNDS = 200
_MAX_ITEMS = 2000

# Safe DOM selectors (best-effort; updated as the contract evolves).
_RELEASE_ROW_SELECTOR = (
    "tr[data-album], .albumsRow, .release-row, [data-albumuuid], tr[data-albumuuid]"
)
_LOAD_MORE_SELECTOR = "a#loadmore, button#loadmore, .load-more, #load-more"


class DistroKidCollectionDiscovery:
    """Discover all DistroKid My Music releases.

    Implements the :class:`~mindcap.sync.protocols.CollectionDiscoveryStrategy`
    protocol using the Playwright browser strategy.

    Parameters
    ----------
    mymusic_url:
        Override the default My Music URL (useful for testing).
    """

    def __init__(self, mymusic_url: str = _MYMUSIC_URL) -> None:
        self._mymusic_url = mymusic_url
        self.discovery_result: DiscoveryResult | None = None

    def discover(
        self,
        request: CollectionRequest,
        reporter: CaptureProgressReporter | None,
    ) -> Iterable[SourceDescriptor]:
        """Yield :class:`~mindcap.sync.models.SourceDescriptor` objects.

        The discovery result is stored in :attr:`discovery_result` after the
        iterable is exhausted.
        """
        return list(self._discover_iter(request, reporter))

    def _discover_iter(
        self,
        request: CollectionRequest,
        reporter: CaptureProgressReporter | None,
    ) -> Iterable[SourceDescriptor]:
        warnings: list[str] = []
        descriptors: list[SourceDescriptor] = []

        try:
            descriptors, warnings = _browser_discover(
                mymusic_url=request.collection_url or self._mymusic_url,
                reporter=reporter,
                max_items=request.max_items or _MAX_ITEMS,
            )
        except Exception as exc:
            warnings.append(f"Browser discovery failed: {exc}")

        seen_ids: set[str] = set()
        duplicate_count = 0
        for descriptor in descriptors:
            cid = descriptor.canonical_identifier
            if cid in seen_ids:
                duplicate_count += 1
                continue
            seen_ids.add(cid)
            yield descriptor

        self.discovery_result = DiscoveryResult(
            provider="distrokid",
            collection_identifier=request.collection_identifier,
            collection_url=request.collection_url or self._mymusic_url,
            expected_item_count=None,
            unique_items_discovered=len(seen_ids),
            pages_observed=1,
            duplicate_identifiers_observed=duplicate_count,
            terminal_signal="dom-scrape-complete" if descriptors else None,
            discovery_complete=bool(descriptors),
            warnings=warnings,
            discovered_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Browser-backed DOM scraping
# ---------------------------------------------------------------------------


def _browser_discover(
    mymusic_url: str,
    reporter: CaptureProgressReporter | None,
    max_items: int,
) -> tuple[list[SourceDescriptor], list[str]]:
    """Navigate to My Music and scrape visible releases.

    Returns (descriptors, warnings).
    """
    import subprocess

    from playwright.sync_api import sync_playwright

    from mindcap.config import distrokid_profile_dir, ensure_private_directory
    from mindcap.plugins.chatgpt.strategies.browser import (
        _find_stable_chrome,
        _is_profile_locked,
        _wait_for_fresh_cdp_port,
    )
    from mindcap.plugins.distrokid.strategies.browser import (
        _chrome_cdp_args,
        _chrome_popen_kwargs,
        _cleanup_devtools_port,
        _read_devtools_port_content,
        _shutdown_chrome,
    )

    profile = ensure_private_directory(distrokid_profile_dir())
    warnings: list[str] = []

    try:
        chrome = _find_stable_chrome()
    except Exception as exc:
        return [], [f"Could not locate stable Chrome: {exc}"]

    if _is_profile_locked(profile):
        return [], ["DistroKid browser profile is locked by another process."]

    cdp_port = _wait_for_fresh_cdp_port()
    chrome_args = _chrome_cdp_args(chrome, profile)
    chrome_args.append(f"--remote-debugging-port={cdp_port}")

    proc = subprocess.Popen(chrome_args, **_chrome_popen_kwargs())  # type: ignore[arg-type]
    descriptors: list[SourceDescriptor] = []
    try:
        import time

        time.sleep(2.0)  # Allow Chrome to start.

        with sync_playwright() as pw:
            devtools_content = _read_devtools_port_content(cdp_port)
            ws_endpoint = devtools_content.get("webSocketDebuggerUrl", "")
            browser = pw.chromium.connect_over_cdp(ws_endpoint, timeout=15_000)
            try:
                descriptors, warnings = _scrape_mymusic(
                    browser=browser,
                    mymusic_url=mymusic_url,
                    reporter=reporter,
                    max_items=max_items,
                )
            finally:
                browser.close()
    except Exception as exc:
        warnings.append(f"Browser session failed: {exc}")
    finally:
        _cleanup_devtools_port(cdp_port)
        _shutdown_chrome(proc)

    return descriptors, warnings


def _scrape_mymusic(
    browser: Any,
    mymusic_url: str,
    reporter: CaptureProgressReporter | None,
    max_items: int,
) -> tuple[list[SourceDescriptor], list[str]]:
    """Scrape the My Music page and return (descriptors, warnings)."""
    warnings: list[str] = []
    descriptors: list[SourceDescriptor] = []

    contexts = browser.contexts
    context = browser.new_context() if not contexts else contexts[0]

    page = context.new_page()
    import contextlib

    try:
        page.goto(mymusic_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=15_000)

        # Handle potential load-more pattern.
        for _round in range(_MAX_SCROLL_ROUNDS):
            rows = page.query_selector_all(_RELEASE_ROW_SELECTOR)
            if len(rows) >= max_items:
                break

            load_more = page.query_selector(_LOAD_MORE_SELECTOR)
            if load_more and load_more.is_visible():
                load_more.click()
                page.wait_for_load_state("networkidle", timeout=10_000)
                import time

                time.sleep(0.5)
            else:
                break

        rows = page.query_selector_all(_RELEASE_ROW_SELECTOR)
        for position, row in enumerate(rows[:max_items]):
            try:
                descriptor = _row_to_descriptor(row, position)
                if descriptor is not None:
                    descriptors.append(descriptor)
            except Exception as exc:
                warnings.append(f"Could not parse release row {position}: {exc}")
    except Exception as exc:
        warnings.append(f"Page navigation or scraping failed: {exc}")
    finally:
        with contextlib.suppress(Exception):
            page.close()

    return descriptors, warnings


def _row_to_descriptor(row: Any, position: int) -> SourceDescriptor | None:
    """Convert one DOM row to a :class:`~mindcap.sync.models.SourceDescriptor`."""
    # Try known attribute selectors for the album UUID.
    album_uuid: str | None = (
        row.get_attribute("data-albumuuid")
        or row.get_attribute("data-album")
        or row.get_attribute("data-id")
    )
    if not album_uuid:
        return None

    title: str | None = None
    for sel in (".releaseTitle", ".album-title", "td.title", "h3", "h4"):
        el = row.query_selector(sel)
        if el:
            title = el.inner_text().strip() or None
            if title:
                break

    artist: str | None = None
    for sel in (".artistName", ".artist-name", "td.artist"):
        el = row.query_selector(sel)
        if el:
            artist = el.inner_text().strip() or None
            if artist:
                break

    safe_meta: dict[str, Any] = {}
    if artist:
        safe_meta["artist"] = artist

    for safe_key, attr in (
        ("release_type", "data-releasetype"),
        ("upc", "data-upc"),
        ("release_date", "data-releasedate"),
    ):
        val = row.get_attribute(attr)
        if val:
            safe_meta[safe_key] = val

    canonical_url = f"https://distrokid.com/dashboard/album/?albumuuid={album_uuid}"

    return SourceDescriptor(
        provider="distrokid",
        source_type="release",
        canonical_identifier=album_uuid,
        canonical_url=canonical_url,
        display_title=str(title) if title else album_uuid,
        collection_position=position,
        remote_revision=None,  # DistroKid does not expose stable revision tokens.
        safe_metadata=safe_meta,
    )
