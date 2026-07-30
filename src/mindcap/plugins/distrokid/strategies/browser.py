from __future__ import annotations

import contextlib
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Browser, Error, Response, sync_playwright

from mindcap.config import distrokid_profile_dir, ensure_private_directory
from mindcap.core.errors import (
    AuthenticationRequiredError,
    CaptureFailedError,
    ProfileLockedError,
)
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.core.progress import CaptureProgressReporter
from mindcap.plugins.chatgpt.strategies.browser import (
    _CDP_CONNECT_TIMEOUT_SECONDS,
    _chrome_popen_kwargs,
    _cleanup_devtools_port,
    _find_stable_chrome,
    _is_dedicated_chrome_running,
    _is_profile_locked,
    _read_devtools_port_content,
    _shutdown_chrome,
    _wait_for_fresh_cdp_port,
)

_DISTROKID_HOSTS = frozenset({"distrokid.com", "www.distrokid.com"})
_CAPTURE_ARCHITECTURE = "external_stable_chrome_cdp"
_AUTH_CHECK_WAIT_MS = 4_000


class DistroKidAuthenticationState(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    INDETERMINATE = "indeterminate"
    UNREACHABLE = "unreachable"
    PROFILE_LOCKED = "profile-locked"


@dataclass(frozen=True)
class DistroKidAuthenticationCheck:
    state: DistroKidAuthenticationState
    detail: str


@dataclass(frozen=True)
class _ObservedResponse:
    url: str
    media_type: str
    body: bytes
    endpoint_category: str
    status_code: int | None
    safe_metadata: dict[str, Any]


def _chrome_auth_args(chrome: Path, profile: Path) -> list[str]:
    return [
        str(chrome),
        "--profile-directory=Default",
        f"--user-data-dir={profile}",
        "https://distrokid.com/mymusic/",
    ]


def _chrome_cdp_args(chrome: Path, profile: Path) -> list[str]:
    return [
        str(chrome),
        "--profile-directory=Default",
        f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "about:blank",
    ]


def _is_auth_redirect(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except Exception:
        return True
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if hostname and hostname not in _DISTROKID_HOSTS:
        return True
    if path.startswith(("/signin", "/login", "/register", "/account/login")):
        return True
    return path in {"/", "/login", "/signin"}


def _safe_response_url(url: str) -> str:
    parsed = urlsplit(url)
    safe_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in {"albumuuid", "page", "cursor"}:
            safe_pairs.append((key_lower, value))
    query = urlencode(safe_pairs, doseq=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _categorize_endpoint(url: str, media_type: str) -> str:
    path = urlsplit(url).path.lower()
    if path.startswith("/mymusic"):
        return "library-page"
    if path.startswith("/dashboard/album"):
        return "release-page"
    if "json" in media_type and "delivery" in path:
        return "delivery-status"
    if "json" in media_type and "credit" in path:
        return "credits"
    if "json" in media_type and "lyric" in path:
        return "lyrics"
    if "json" in media_type and "track" in path:
        return "track"
    if "json" in media_type:
        return "provider-json"
    if "html" in media_type:
        return "document"
    return "asset-metadata"


def _safe_schema_summary(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return {
            "top_level_keys": sorted(payload.keys())[:50],
            "type": "object",
        }
    if isinstance(payload, list):
        first = payload[0] if payload else None
        first_keys = sorted(first.keys())[:30] if isinstance(first, dict) else []
        return {
            "type": "array",
            "length": len(payload),
            "first_item_keys": first_keys,
        }
    return {"type": type(payload).__name__}


def verify_distrokid_authentication() -> DistroKidAuthenticationCheck:
    profile = ensure_private_directory(distrokid_profile_dir())
    if _is_profile_locked(profile) or _is_dedicated_chrome_running(profile):
        return DistroKidAuthenticationCheck(
            state=DistroKidAuthenticationState.PROFILE_LOCKED,
            detail=(
                "Dedicated DistroKid profile is locked "
                "by a running browser process."
            ),
        )

    chrome = _find_stable_chrome()
    previous_content = _read_devtools_port_content(profile)
    launch_time = time.time()
    process = subprocess.Popen(
        _chrome_cdp_args(chrome, profile), **_chrome_popen_kwargs()
    )
    browser: Browser | None = None
    try:
        try:
            http_endpoint = _wait_for_fresh_cdp_port(
                profile,
                process,
                launch_time,
                previous_content,
                timeout_seconds=_CDP_CONNECT_TIMEOUT_SECONDS,
            )
        except CaptureFailedError as error:
            return DistroKidAuthenticationCheck(
                state=DistroKidAuthenticationState.UNREACHABLE,
                detail=(
                    "Authentication may have been saved, but verification could "
                    f"not connect to Chrome debugging endpoint. {error}"
                ),
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(
                http_endpoint, timeout=60_000
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(
                "https://distrokid.com/mymusic/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            page.wait_for_timeout(_AUTH_CHECK_WAIT_MS)
            final_url = page.url
            page.close()
            browser.close()
            browser = None

        if _is_auth_redirect(final_url):
            if "expired" in final_url or "verify" in final_url:
                return DistroKidAuthenticationCheck(
                    state=DistroKidAuthenticationState.EXPIRED,
                    detail=(
                        "Authentication appears expired or "
                        "requires re-verification."
                    ),
                )
            return DistroKidAuthenticationCheck(
                state=DistroKidAuthenticationState.UNVERIFIED,
                detail='Profile is not authenticated. Run "mindcap auth distrokid".',
            )

        final_path = urlsplit(final_url).path.lower()
        if final_path.startswith("/mymusic") or final_path.startswith(
            "/dashboard/album"
        ):
            return DistroKidAuthenticationCheck(
                state=DistroKidAuthenticationState.VERIFIED,
                detail="DistroKid session appears authenticated.",
            )

        return DistroKidAuthenticationCheck(
            state=DistroKidAuthenticationState.INDETERMINATE,
            detail=(
                f"DistroKid opened at {final_path or '/'}; "
                "login could not be proven."
            ),
        )
    except (Error, OSError) as error:
        return DistroKidAuthenticationCheck(
            state=DistroKidAuthenticationState.UNREACHABLE,
            detail=f"Authentication check could not complete: {error}",
        )
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        _shutdown_chrome(process)
        _cleanup_devtools_port(profile)


def authenticate_distrokid() -> None:
    profile = ensure_private_directory(distrokid_profile_dir())
    if _is_profile_locked(profile):
        raise ProfileLockedError(
            "The dedicated DistroKid profile is locked. Fully quit Chrome and retry."
        )

    chrome = _find_stable_chrome()
    print(
        "\nMindcap is opening stable Google Chrome for DistroKid authentication.\n"
        f"  Chrome:  {chrome}\n"
        f"  Profile: {profile}\n"
        "\nSteps:\n"
        "  1. Sign into DistroKid in the opened Chrome window.\n"
        "  2. Confirm your My Music page is visible.\n"
        "  3. Fully QUIT Chrome (File → Exit / Cmd+Q).\n"
        "  4. Return here and press Enter.\n"
    )
    subprocess.Popen(_chrome_auth_args(chrome, profile))
    input("Press Enter after you have fully quit Chrome to continue...")

    check = verify_distrokid_authentication()
    if check.state is DistroKidAuthenticationState.UNREACHABLE:
        raise CaptureFailedError(check.detail)
    if check.state is not DistroKidAuthenticationState.VERIFIED:
        raise AuthenticationRequiredError(
            "Authentication could not be verified safely after Chrome exited. "
            f"{check.detail}"
        )


class DistroKidBrowserCaptureStrategy:
    name = "browser"

    def __init__(self, reporter: CaptureProgressReporter | None = None) -> None:
        self._reporter = reporter

    def _debug(self, message: str) -> None:
        if self._reporter is not None:
            self._reporter.debug_line(message)

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        if not request.canonical_url:
            raise CaptureFailedError("Browser capture requires a canonical URL.")

        profile = ensure_private_directory(distrokid_profile_dir())
        if _is_profile_locked(profile) or _is_dedicated_chrome_running(profile):
            raise ProfileLockedError(
                "The dedicated DistroKid profile is already in use. Fully quit "
                "the dedicated Chrome process and retry capture."
            )

        chrome = _find_stable_chrome()
        previous_content = _read_devtools_port_content(profile)
        launch_time = time.time()
        process = subprocess.Popen(
            _chrome_cdp_args(chrome, profile), **_chrome_popen_kwargs()
        )
        browser: Browser | None = None
        observed: list[_ObservedResponse] = []
        final_url = request.canonical_url

        try:
            http_endpoint = _wait_for_fresh_cdp_port(
                profile,
                process,
                launch_time,
                previous_content,
                timeout_seconds=_CDP_CONNECT_TIMEOUT_SECONDS,
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    http_endpoint, timeout=60_000
                )
                context = (
                    browser.contexts[0] if browser.contexts else browser.new_context()
                )
                page = context.new_page()

                def observe(response: Response) -> None:
                    try:
                        parsed = urlsplit(response.url)
                        hostname = (parsed.hostname or "").lower()
                        if hostname not in _DISTROKID_HOSTS:
                            return
                        content_type = response.headers.get("content-type", "")
                        lowered = content_type.lower()
                        if "json" not in lowered and "html" not in lowered:
                            return
                        body = response.body()
                        category = _categorize_endpoint(response.url, lowered)
                        safe_metadata: dict[str, Any] = {
                            "status_code": response.status,
                            "byte_count": len(body),
                        }
                        if "json" in lowered:
                            safe_metadata.update(_safe_schema_summary(body))
                        observed.append(
                            _ObservedResponse(
                                url=_safe_response_url(response.url),
                                media_type=content_type.split(";")[0]
                                or "application/octet-stream",
                                body=body,
                                endpoint_category=category,
                                status_code=response.status,
                                safe_metadata=safe_metadata,
                            )
                        )
                    except Exception:
                        return

                page.on("response", observe)
                page.goto(
                    request.canonical_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                page.wait_for_timeout(request.wait_seconds * 1_000)
                final_url = page.url
                page.close()
                browser.close()
                browser = None
        finally:
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
            _shutdown_chrome(process)
            _cleanup_devtools_port(profile)

        if _is_auth_redirect(final_url):
            raise AuthenticationRequiredError(
                "The dedicated profile is not authenticated. "
                'Run "mindcap auth distrokid" first.'
            )

        if not observed:
            raise CaptureFailedError(
                "No suitable DistroKid JSON/HTML response was observed. "
                "Provider contracts may have changed."
            )

        debug_discovery = bool(request.options.get("debug_discovery"))
        if debug_discovery:
            for item in observed:
                self._debug(
                    "discovery "
                    f"category={item.endpoint_category} status={item.status_code} "
                    f"type={item.media_type} bytes={len(item.body)} url={item.url}"
                )

        scope = (
            "library"
            if request.canonical_identifier == "account-library"
            else "release"
        )
        response_units: list[RawResponseUnit] = []
        for index, item in enumerate(observed):
            response_units.append(
                RawResponseUnit(
                    unit_id=f"response-{index:03d}",
                    sequence=index,
                    media_type=item.media_type,
                    body=item.body,
                    source_url=item.url,
                    endpoint_category=item.endpoint_category,
                    retrieved_at=datetime.now(UTC),
                    safe_metadata=item.safe_metadata,
                )
            )

        return CaptureEnvelope(
            provider="distrokid",
            source_type=scope,
            canonical_identifier=request.canonical_identifier,
            canonical_url=request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy=self.name,
            response_units=response_units,
            safe_metadata={
                "scope": scope,
                "capture_architecture": _CAPTURE_ARCHITECTURE,
                "observed_response_count": len(response_units),
                "capture_complete": True,
                "terminal_signal": "page-idle-timeout",
            },
        )


def browser_capture_architecture() -> str:
    return _CAPTURE_ARCHITECTURE
