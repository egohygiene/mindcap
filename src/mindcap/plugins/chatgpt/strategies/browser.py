from __future__ import annotations

import contextlib
import json
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Browser, Error, Response, sync_playwright

from mindcap.config import chatgpt_profile_dir, ensure_private_directory
from mindcap.core.errors import (
    AuthenticationRequiredError,
    CaptureFailedError,
    ProfileLockedError,
    StableChromeNotFoundError,
)
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit

# Known authentication redirect patterns.  ChatGPT may vary the exact path
# but any redirect matching one of these patterns signals that the dedicated
# browser profile is not authenticated.
_AUTH_HOSTNAMES = frozenset(["accounts.google.com"])
_AUTH_PATH_PREFIXES = ("/auth/", "/login", "/sso/")
_AUTH_EXACT_PATHS = frozenset(["/", "/auth/login", "/auth/verify"])
_CDP_CONNECT_TIMEOUT_SECONDS = 15.0
_AUTH_CHECK_WAIT_MS = 4_000
_CAPTURE_ARCHITECTURE = "external_stable_chrome_cdp"
_CHROME_TERMINATE_TIMEOUT_SECONDS = 15
_CHROME_KILL_TIMEOUT_SECONDS = 10
_CHATGPT_HOSTNAMES = frozenset(["chatgpt.com", "www.chatgpt.com"])
_DEVTOOLS_PORT_POLL_INTERVAL = 0.1
_ENDPOINT_REACHABLE_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class _Candidate:
    score: int
    url: str
    body: bytes


class AuthenticationState(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    INDETERMINATE = "indeterminate"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class AuthenticationCheck:
    state: AuthenticationState
    detail: str


def _payload_score(payload: Any, url: str, identifier: str) -> int:
    """Score how likely a JSON response body is to be the target conversation.

    A score ≥ 100 qualifies the response as a candidate.  Higher scores are
    preferred when multiple candidates are observed.
    """
    score = 0
    if identifier in url:
        score += 50

    if not isinstance(payload, dict):
        return score

    # Direct mapping at the top level (classic ChatGPT conversation payload).
    if isinstance(payload.get("mapping"), dict):
        score += 100
    if payload.get("current_node"):
        score += 20
    if payload.get("title"):
        score += 10

    # Wrapped under a "conversation" key.
    nested_conv = payload.get("conversation")
    if isinstance(nested_conv, dict):
        if isinstance(nested_conv.get("mapping"), dict):
            score += 100
        if nested_conv.get("current_node"):
            score += 20

    # Wrapped under a "data" key (alternate API envelope).
    nested_data = payload.get("data")
    if isinstance(nested_data, dict):
        if isinstance(nested_data.get("mapping"), dict):
            score += 100
        if nested_data.get("current_node"):
            score += 20

    return score


def _safe_response_url(value: str) -> str:
    """Remove query parameters and fragments that may contain access values."""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _is_auth_redirect(url: str, expected_identifier: str | None = None) -> bool:
    """Return True when the final page URL indicates an authentication wall.

    Checks are path-prefix and hostname-based to avoid false positives from
    incidental substring matches (e.g., ``/uploading`` containing ``login``).
    """
    try:
        parsed = urlsplit(url)
    except Exception:
        return False
    hostname = parsed.hostname or ""
    if any(ah in hostname for ah in _AUTH_HOSTNAMES):
        return True
    path = parsed.path.lower()
    if any(
        path == prefix.rstrip("/") or path.startswith(prefix)
        for prefix in _AUTH_PATH_PREFIXES
    ):
        return True
    if path in _AUTH_EXACT_PATHS:
        return True
    if expected_identifier and hostname in _CHATGPT_HOSTNAMES:
        return f"/c/{expected_identifier}" not in path
    return False


def _chrome_auth_args(chrome: Path, profile: Path) -> list[str]:
    return [
        str(chrome),
        "--profile-directory=Default",
        f"--user-data-dir={profile}",
        "https://chatgpt.com/",
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


def _is_profile_locked(profile: Path) -> bool:
    return (profile / "SingletonLock").exists()


def _is_dedicated_chrome_running(profile: Path) -> bool:
    """Best-effort check for a running dedicated Chrome process.

    Uses `ps` on Unix-like platforms and `wmic` on Windows.
    """
    profile_flag = f"--user-data-dir={profile}"
    try:
        command = (
            ["wmic", "process", "get", "CommandLine"]
            if sys.platform == "win32"
            else ["ps", "ax", "-o", "command="]
        )
        listing = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError:
        return False
    for line in listing.stdout.splitlines():
        if profile_flag not in line:
            continue
        lower = line.lower()
        if "chrome" in lower or "chromium" in lower:
            return True
    return False


def _chrome_popen_kwargs() -> dict[str, Any]:
    """Return Popen kwargs that suppress Chrome subprocess noise.

    Redirects stdout and stderr to DEVNULL during normal operation,
    suppressing Chrome, TensorFlow Lite, Google Updater, GCM, Crashpad,
    and FIDO subprocess messages that are not actionable by the user.
    """
    return {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def _read_devtools_port_content(profile: Path) -> str | None:
    """Read the current DevToolsActivePort content, or None if absent/unreadable."""
    devtools_file = profile / "DevToolsActivePort"
    try:
        if not devtools_file.is_file():
            return None
        return devtools_file.read_text(encoding="utf-8")
    except OSError:
        return None


def _parse_devtools_port(content: str) -> tuple[str, str] | None:
    """Parse DevToolsActivePort content into (port, browser_path) or None.

    Returns None when the content is incomplete or malformed.
    """
    lines = content.splitlines()
    if len(lines) < 2:
        return None
    port = lines[0].strip()
    browser_path = lines[1].strip()
    if not port or not browser_path.startswith("/devtools/browser/"):
        return None
    return port, browser_path


def _is_endpoint_reachable(port: str) -> bool:
    """Return True when the loopback TCP port is accepting connections."""
    try:
        port_int = int(port)
        with socket.create_connection(
            ("127.0.0.1", port_int),
            timeout=_ENDPOINT_REACHABLE_TIMEOUT_SECONDS,
        ):
            return True
    except (OSError, ValueError, OverflowError):
        return False


def _wait_for_fresh_cdp_port(
    profile: Path,
    process: subprocess.Popen[Any],
    launch_time: float,
    previous_content: str | None,
    timeout_seconds: float,
) -> str:
    """Wait for Chrome to publish a fresh DevToolsActivePort file.

    Polls the ``DevToolsActivePort`` file in the dedicated profile directory
    until all of the following conditions are satisfied:

    * The file exists and has been modified at or after *launch_time*.
    * Its content differs from *previous_content* (a stale endpoint is rejected).
    * The content can be parsed into a valid port and browser path.
    * The advertised loopback TCP port is accepting connections.

    Returns the HTTP CDP endpoint ``http://127.0.0.1:<port>`` so that
    Playwright can discover the current browser WebSocket automatically,
    rather than hard-coding a specific WebSocket path from a prior run.

    Raises :class:`~mindcap.core.errors.CaptureFailedError` if *process*
    exits before publishing an endpoint, or if the timeout expires without a
    valid reachable endpoint appearing.  The error message includes the
    previous (stale) endpoint port for safe diagnostic output.
    """
    devtools_file = profile / "DevToolsActivePort"
    _prev_parsed = _parse_devtools_port(previous_content) if previous_content else None
    previous_port = _prev_parsed[0] if _prev_parsed else "none"
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CaptureFailedError(
                f"Chrome exited (code {process.returncode}) before publishing "
                "a DevTools endpoint. "
                f"Previous endpoint port: {previous_port}."
            )

        if not devtools_file.is_file():
            time.sleep(_DEVTOOLS_PORT_POLL_INTERVAL)
            continue

        try:
            mtime = devtools_file.stat().st_mtime
        except OSError:
            time.sleep(_DEVTOOLS_PORT_POLL_INTERVAL)
            continue
        if mtime < launch_time:
            time.sleep(_DEVTOOLS_PORT_POLL_INTERVAL)
            continue

        try:
            content = devtools_file.read_text(encoding="utf-8")
        except OSError:
            time.sleep(_DEVTOOLS_PORT_POLL_INTERVAL)
            continue

        if content == previous_content:
            time.sleep(_DEVTOOLS_PORT_POLL_INTERVAL)
            continue

        parsed = _parse_devtools_port(content)
        if parsed is None:
            time.sleep(_DEVTOOLS_PORT_POLL_INTERVAL)
            continue

        port, _browser_path = parsed
        if not _is_endpoint_reachable(port):
            time.sleep(_DEVTOOLS_PORT_POLL_INTERVAL)
            continue

        return f"http://127.0.0.1:{port}"

    raise CaptureFailedError(
        f"Chrome did not expose a fresh DevTools endpoint within "
        f"{timeout_seconds:.0f}s. "
        f"Previous endpoint port: {previous_port}. "
        "Try re-running after fully quitting the dedicated Chrome process."
    )


def _cleanup_devtools_port(profile: Path) -> None:
    """Remove the DevToolsActivePort metadata file when it is safe to do so.

    Only removes the ephemeral port-advertisement file — never touches
    cookies, profile databases, lock files, or any other authentication state.
    Skips removal when a dedicated Chrome process still owns the profile.
    """
    if _is_dedicated_chrome_running(profile):
        return
    devtools_file = profile / "DevToolsActivePort"
    with contextlib.suppress(OSError):
        devtools_file.unlink(missing_ok=True)


def _shutdown_chrome(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_CHROME_TERMINATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=_CHROME_KILL_TIMEOUT_SECONDS)


def verify_chatgpt_authentication() -> AuthenticationCheck:
    profile = ensure_private_directory(chatgpt_profile_dir())
    chrome = _find_stable_chrome()
    # SingletonLock detects Chrome holding a POSIX advisory lock; the process
    # check catches cases where the lock file has been stale-deleted but Chrome
    # is still running.  Both guards are needed for reliable conflict detection.
    if _is_profile_locked(profile) or _is_dedicated_chrome_running(profile):
        return AuthenticationCheck(
            state=AuthenticationState.INDETERMINATE,
            detail="Dedicated profile is locked by a running browser process.",
        )

    # Capture stale endpoint content and record launch timestamp before
    # starting Chrome so the freshness check works correctly.
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
            return AuthenticationCheck(
                state=AuthenticationState.UNREACHABLE,
                detail=(
                    "Authentication may have been saved, but verification could "
                    "not connect to the current Chrome debugging endpoint. "
                    f"{error}"
                ),
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(
                http_endpoint, timeout=60_000
            )
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(
                "https://chatgpt.com/", wait_until="domcontentloaded", timeout=60_000
            )
            page.wait_for_timeout(_AUTH_CHECK_WAIT_MS)
            final_url = page.url
            page.close()
            browser.close()
            browser = None
        if _is_auth_redirect(final_url):
            if "verify" in final_url or "expired" in final_url:
                return AuthenticationCheck(
                    state=AuthenticationState.EXPIRED,
                    detail=(
                        "Authentication appears expired or requires account "
                        "verification."
                    ),
                )
            return AuthenticationCheck(
                state=AuthenticationState.UNVERIFIED,
                detail='Profile is not authenticated. Run "mindcap auth chatgpt".',
            )
        parsed = urlsplit(final_url)
        if parsed.hostname in _CHATGPT_HOSTNAMES:
            if parsed.path.startswith("/c/"):
                return AuthenticationCheck(
                    state=AuthenticationState.VERIFIED,
                    detail="ChatGPT session appears authenticated.",
                )
            return AuthenticationCheck(
                state=AuthenticationState.INDETERMINATE,
                detail=(
                    f"ChatGPT opened at {parsed.path or '/'}; login could not "
                    "be proven safely."
                ),
            )
        return AuthenticationCheck(
            state=AuthenticationState.INDETERMINATE,
            detail="Unexpected destination while checking authentication status.",
        )
    except (Error, OSError) as error:
        return AuthenticationCheck(
            state=AuthenticationState.UNREACHABLE,
            detail=f"Authentication check could not complete: {error}",
        )
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        _shutdown_chrome(process)
        _cleanup_devtools_port(profile)


class BrowserCaptureStrategy:
    name = "browser"

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        if not request.canonical_url:
            raise CaptureFailedError("Browser capture requires a canonical URL.")

        profile = ensure_private_directory(chatgpt_profile_dir())
        candidates: list[_Candidate] = []
        observed_json = 0
        final_url = request.canonical_url or ""

        chrome = _find_stable_chrome()
        if _is_profile_locked(profile) or _is_dedicated_chrome_running(profile):
            raise ProfileLockedError(
                "The dedicated Chrome profile is already in use. Fully quit the "
                "dedicated Chrome process and retry capture."
            )

        # Capture stale endpoint content and record launch timestamp before
        # starting Chrome so the freshness check works correctly.
        previous_content = _read_devtools_port_content(profile)
        launch_time = time.time()

        process = subprocess.Popen(
            _chrome_cdp_args(chrome, profile), **_chrome_popen_kwargs()
        )
        browser: Browser | None = None
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
                page.bring_to_front()

                def observe(response: Response) -> None:
                    nonlocal observed_json
                    try:
                        content_type = response.headers.get("content-type", "")
                        if "json" not in content_type.lower():
                            return
                        body = response.body()
                        payload = json.loads(body)
                        observed_json += 1
                        score = _payload_score(
                            payload, response.url, request.canonical_identifier
                        )
                        if (
                            score >= 100
                            and request.canonical_identifier in response.url.lower()
                        ):
                            candidates.append(_Candidate(score, response.url, body))
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

        if _is_auth_redirect(
            final_url, expected_identifier=request.canonical_identifier
        ):
            raise AuthenticationRequiredError(
                'The dedicated profile is not authenticated. Run "mindcap auth '
                'chatgpt" first.'
            )

        if not candidates:
            raise CaptureFailedError(
                "No recognizable ChatGPT conversation payload was observed "
                f"after inspecting {observed_json} JSON responses. ChatGPT may "
                "have changed its frontend payload shape."
            )

        selected = max(candidates, key=lambda candidate: candidate.score)
        return CaptureEnvelope(
            provider="chatgpt",
            source_type="conversation",
            canonical_identifier=request.canonical_identifier,
            canonical_url=request.canonical_url,
            captured_at=datetime.now(UTC),
            strategy=self.name,
            response_units=[
                RawResponseUnit(
                    unit_id="response-000",
                    sequence=0,
                    media_type="application/json",
                    body=selected.body,
                    source_url=_safe_response_url(selected.url),
                )
            ],
            safe_metadata={
                "candidate_score": selected.score,
                "candidate_count": len(candidates),
                "observed_json_responses": observed_json,
                "capture_architecture": _CAPTURE_ARCHITECTURE,
            },
        )


# ---------------------------------------------------------------------------
# Stable-Chrome discovery
# ---------------------------------------------------------------------------

#: Candidate stable Chrome executable paths, checked in priority order.
_CHROME_CANDIDATES_MACOS = [
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path.home()
    / "Applications"
    / "Google Chrome.app"
    / "Contents"
    / "MacOS"
    / "Google Chrome",
]

_CHROME_CANDIDATES_LINUX = [
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/chromium-browser"),
    Path("/usr/bin/chromium"),
    Path("/snap/bin/chromium"),
]

_CHROME_CANDIDATES_WINDOWS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def _find_stable_chrome() -> Path:
    """Return the path to the user's normally installed stable Google Chrome.

    Raises :class:`~mindcap.core.errors.StableChromeNotFoundError` when Chrome
    cannot be located on the current platform.  Never falls back to Playwright's
    bundled Chrome for Testing.
    """
    # 1. Respect an explicit environment override.
    import os

    env_override = os.environ.get("MINDCAP_CHROME_EXECUTABLE")
    if env_override:
        candidate = Path(env_override).expanduser()
        if candidate.is_file():
            return candidate
        raise StableChromeNotFoundError(
            f"MINDCAP_CHROME_EXECUTABLE is set but the path does not exist: {candidate}"
        )

    platform = sys.platform

    if platform == "darwin":
        candidates = _CHROME_CANDIDATES_MACOS
    elif platform == "win32":
        candidates = _CHROME_CANDIDATES_WINDOWS
    else:
        candidates = _CHROME_CANDIDATES_LINUX

    for path in candidates:
        if path.is_file():
            return path

    # Fall back to PATH resolution for Linux/Windows distributions that install
    # Chrome under an unexpected prefix.
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
    ):
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved)

    platform_hints: dict[str, str] = {
        "darwin": "Install Google Chrome from https://www.google.com/chrome/",
        "win32": (
            "Install Google Chrome from https://www.google.com/chrome/ "
            "or set MINDCAP_CHROME_EXECUTABLE to the chrome.exe path."
        ),
    }
    hint = platform_hints.get(
        platform,
        "Install Google Chrome (e.g. `sudo apt install google-chrome-stable`) "
        "or set MINDCAP_CHROME_EXECUTABLE to the chrome binary path.",
    )
    raise StableChromeNotFoundError(
        f"Stable Google Chrome could not be found on this system. {hint}"
    )


def _check_profile_lock(profile: Path) -> None:
    """Raise :class:`~mindcap.core.errors.ProfileLockedError` if the Chrome
    profile directory contains a lock file that indicates another process is
    currently using it.
    """
    lock_file = profile / "SingletonLock"
    if lock_file.exists():
        raise ProfileLockedError(
            f"The dedicated Chrome profile appears to be locked by another "
            f"process: {lock_file}\n"
            "Fully quit the Chrome window that was opened for authentication "
            "and try again.  If no Chrome window is open, delete the lock file "
            f"manually:\n  rm {lock_file}"
        )


def authenticate_chatgpt() -> None:
    """Authenticate the dedicated ChatGPT profile using stable Google Chrome.

    This function locates the user's normally installed stable Google Chrome,
    launches it directly via :mod:`subprocess` without any Playwright automation
    or remote-debugging flags, and opens the dedicated Mindcap profile at
    ``https://chatgpt.com/``.

    Google rejects OAuth from Playwright's bundled *Chrome for Testing* with
    "This browser or app may not be secure."  Using the user's everyday stable
    Chrome avoids that rejection without disabling any security protections.

    The function does **not** inspect, copy, print, or archive cookies, tokens,
    passwords, or browser storage.
    """
    profile = ensure_private_directory(chatgpt_profile_dir())
    _check_profile_lock(profile)

    chrome = _find_stable_chrome()

    print(
        "\nMindcap is opening stable Google Chrome for authentication.\n"
        f"  Chrome:  {chrome}\n"
        f"  Profile: {profile}\n"
        "\nThis is a separate dedicated profile — your everyday Chrome profile "
        "is not touched.\n"
        "\nSteps:\n"
        "  1. Sign into ChatGPT in the Chrome window that opens.\n"
        "  2. Confirm that your conversation history is visible.\n"
        "  3. Fully QUIT Chrome (Cmd+Q on macOS, File → Exit on Windows/Linux).\n"
        "  4. Return here and press Enter.\n"
    )

    subprocess.Popen(_chrome_auth_args(chrome, profile))

    input("Press Enter after you have fully quit Chrome to continue...")

    _check_profile_lock(profile)
    check = verify_chatgpt_authentication()
    if check.state is AuthenticationState.UNREACHABLE:
        raise CaptureFailedError(check.detail)
    if check.state is not AuthenticationState.VERIFIED:
        raise AuthenticationRequiredError(
            "Authentication could not be verified safely after Chrome exited. "
            f"{check.detail}"
        )


def browser_capture_architecture() -> str:
    return _CAPTURE_ARCHITECTURE
