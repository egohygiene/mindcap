"""Tests for stable-Chrome discovery and authentication design."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mindcap.core.errors import ProfileLockedError, StableChromeNotFoundError
from mindcap.plugins.chatgpt.strategies.browser import (
    AuthenticationCheck,
    AuthenticationState,
    BrowserCaptureStrategy,
    _check_profile_lock,
    _chrome_cdp_args,
    _find_stable_chrome,
)

# ---------------------------------------------------------------------------
# Stable Chrome discovery — macOS
# ---------------------------------------------------------------------------


def test_find_stable_chrome_macos_system_path(tmp_path: Path) -> None:
    """Discovers Chrome at the standard macOS application path."""
    fake_chrome = (
        tmp_path / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    )
    fake_chrome.parent.mkdir(parents=True)
    fake_chrome.touch()

    with (
        patch("sys.platform", "darwin"),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._CHROME_CANDIDATES_MACOS",
            [fake_chrome],
        ),
        patch.dict("os.environ", {}, clear=True),
    ):
        found = _find_stable_chrome()
    assert found == fake_chrome


def test_find_stable_chrome_macos_missing_raises(tmp_path: Path) -> None:
    """Raises StableChromeNotFoundError when Chrome is absent on macOS."""
    with (
        patch("sys.platform", "darwin"),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._CHROME_CANDIDATES_MACOS",
            [tmp_path / "nonexistent" / "Google Chrome"],
        ),
        patch("shutil.which", return_value=None),
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(StableChromeNotFoundError),
    ):
        _find_stable_chrome()


# ---------------------------------------------------------------------------
# Stable Chrome discovery — Linux
# ---------------------------------------------------------------------------


def test_find_stable_chrome_linux_system_path(tmp_path: Path) -> None:
    """Discovers Chrome at a standard Linux binary path."""
    fake_chrome = tmp_path / "google-chrome"
    fake_chrome.touch()

    with (
        patch("sys.platform", "linux"),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._CHROME_CANDIDATES_LINUX",
            [fake_chrome],
        ),
        patch.dict("os.environ", {}, clear=True),
    ):
        found = _find_stable_chrome()
    assert found == fake_chrome


def test_find_stable_chrome_linux_path_fallback(tmp_path: Path) -> None:
    """Falls back to shutil.which on Linux when candidate paths are absent."""
    fake_chrome = tmp_path / "google-chrome-stable"
    fake_chrome.touch()

    with (
        patch("sys.platform", "linux"),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._CHROME_CANDIDATES_LINUX",
            [],
        ),
        patch("shutil.which", return_value=str(fake_chrome)),
        patch.dict("os.environ", {}, clear=True),
    ):
        found = _find_stable_chrome()
    assert found == fake_chrome


def test_find_stable_chrome_linux_missing_raises(tmp_path: Path) -> None:
    """Raises StableChromeNotFoundError when Chrome is absent on Linux."""
    with (
        patch("sys.platform", "linux"),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._CHROME_CANDIDATES_LINUX",
            [],
        ),
        patch("shutil.which", return_value=None),
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(StableChromeNotFoundError),
    ):
        _find_stable_chrome()


# ---------------------------------------------------------------------------
# Stable Chrome discovery — Windows
# ---------------------------------------------------------------------------


def test_find_stable_chrome_windows_system_path(tmp_path: Path) -> None:
    """Discovers Chrome at a standard Windows path."""
    fake_chrome = tmp_path / "chrome.exe"
    fake_chrome.touch()

    with (
        patch("sys.platform", "win32"),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._CHROME_CANDIDATES_WINDOWS",
            [fake_chrome],
        ),
        patch.dict("os.environ", {}, clear=True),
    ):
        found = _find_stable_chrome()
    assert found == fake_chrome


def test_find_stable_chrome_windows_missing_raises(tmp_path: Path) -> None:
    """Raises StableChromeNotFoundError when Chrome is absent on Windows."""
    with (
        patch("sys.platform", "win32"),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._CHROME_CANDIDATES_WINDOWS",
            [Path("C:/nonexistent/chrome.exe")],
        ),
        patch("shutil.which", return_value=None),
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(StableChromeNotFoundError),
    ):
        _find_stable_chrome()


# ---------------------------------------------------------------------------
# Explicit MINDCAP_CHROME_EXECUTABLE override
# ---------------------------------------------------------------------------


def test_find_stable_chrome_env_override_valid(tmp_path: Path) -> None:
    """Respects a valid MINDCAP_CHROME_EXECUTABLE environment variable."""
    fake_chrome = tmp_path / "my-chrome"
    fake_chrome.touch()

    with patch.dict("os.environ", {"MINDCAP_CHROME_EXECUTABLE": str(fake_chrome)}):
        found = _find_stable_chrome()
    assert found == fake_chrome


def test_find_stable_chrome_env_override_invalid_raises(tmp_path: Path) -> None:
    """Raises StableChromeNotFoundError for a missing MINDCAP_CHROME_EXECUTABLE path."""
    missing = tmp_path / "does-not-exist"
    env = {"MINDCAP_CHROME_EXECUTABLE": str(missing)}

    with (
        patch.dict("os.environ", env),
        pytest.raises(StableChromeNotFoundError, match="MINDCAP_CHROME_EXECUTABLE"),
    ):
        _find_stable_chrome()


# ---------------------------------------------------------------------------
# Profile lock detection
# ---------------------------------------------------------------------------


def test_check_profile_lock_no_lock_file(tmp_path: Path) -> None:
    """Does not raise when no lock file is present."""
    _check_profile_lock(tmp_path)  # Should not raise.


def test_check_profile_lock_raises_when_locked(tmp_path: Path) -> None:
    """Raises ProfileLockedError when a SingletonLock file exists."""
    lock_file = tmp_path / "SingletonLock"
    lock_file.touch()

    with pytest.raises(ProfileLockedError, match="SingletonLock"):
        _check_profile_lock(tmp_path)


def test_check_profile_lock_error_message_includes_path(tmp_path: Path) -> None:
    """Error message includes the lock file path for actionability."""
    lock_file = tmp_path / "SingletonLock"
    lock_file.touch()

    with pytest.raises(ProfileLockedError) as exc_info:
        _check_profile_lock(tmp_path)

    assert str(lock_file) in str(exc_info.value)


# ---------------------------------------------------------------------------
# authenticate_chatgpt — uses subprocess, not Playwright
# ---------------------------------------------------------------------------


def test_authenticate_chatgpt_uses_subprocess_not_playwright(
    tmp_path: Path,
) -> None:
    """Authentication must launch Chrome via subprocess, not Playwright."""
    fake_chrome = tmp_path / "google-chrome"
    fake_chrome.touch()

    mock_popen = MagicMock()
    mock_sync_playwright = MagicMock()

    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=fake_chrome,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
        patch("subprocess.Popen", mock_popen),
        patch("builtins.input", return_value=""),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                AuthenticationState.VERIFIED, "authenticated"
            ),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.sync_playwright",
            mock_sync_playwright,
        ),
    ):
        from mindcap.plugins.chatgpt.strategies.browser import authenticate_chatgpt

        authenticate_chatgpt()

    mock_popen.assert_called_once()
    # Playwright must NOT be invoked during authentication.
    mock_sync_playwright.assert_not_called()


def test_authenticate_chatgpt_command_includes_profile_dir(
    tmp_path: Path,
) -> None:
    """Chrome launch command includes the dedicated profile directory."""
    fake_chrome = tmp_path / "google-chrome"
    fake_chrome.touch()

    captured_args: list[list[str]] = []

    def capture_popen(args: list[str], **_kwargs: object) -> MagicMock:
        captured_args.append(args)
        return MagicMock()

    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=fake_chrome,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
        patch("subprocess.Popen", side_effect=capture_popen),
        patch("builtins.input", return_value=""),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                AuthenticationState.VERIFIED, "authenticated"
            ),
        ),
    ):
        from mindcap.plugins.chatgpt.strategies.browser import authenticate_chatgpt

        authenticate_chatgpt()

    assert captured_args, "Popen was not called"
    args = captured_args[0]
    assert str(fake_chrome) == args[0]
    # Profile directory must appear in the arguments.
    profile_arg = next((a for a in args if "--user-data-dir" in a), None)
    assert profile_arg is not None
    assert str(tmp_path) in profile_arg


def test_authenticate_chatgpt_opens_chatgpt_url(tmp_path: Path) -> None:
    """Chrome is launched with https://chatgpt.com/ as a target."""
    fake_chrome = tmp_path / "google-chrome"
    fake_chrome.touch()

    captured_args: list[list[str]] = []

    def capture_popen(args: list[str], **_kwargs: object) -> MagicMock:
        captured_args.append(args)
        return MagicMock()

    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=fake_chrome,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
        patch("subprocess.Popen", side_effect=capture_popen),
        patch("builtins.input", return_value=""),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                AuthenticationState.VERIFIED, "authenticated"
            ),
        ),
    ):
        from mindcap.plugins.chatgpt.strategies.browser import authenticate_chatgpt

        authenticate_chatgpt()

    assert captured_args
    assert captured_args[0][-1] == "https://chatgpt.com/"


def test_authenticate_chatgpt_no_remote_debugging_flags(tmp_path: Path) -> None:
    """Chrome must not be launched with remote-debugging or automation flags."""
    fake_chrome = tmp_path / "google-chrome"
    fake_chrome.touch()

    captured_args: list[list[str]] = []

    def capture_popen(args: list[str], **_kwargs: object) -> MagicMock:
        captured_args.append(args)
        return MagicMock()

    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=fake_chrome,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
        patch("subprocess.Popen", side_effect=capture_popen),
        patch("builtins.input", return_value=""),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                AuthenticationState.VERIFIED, "authenticated"
            ),
        ),
    ):
        from mindcap.plugins.chatgpt.strategies.browser import authenticate_chatgpt

        authenticate_chatgpt()

    assert captured_args
    args_str = " ".join(captured_args[0])
    # None of the flags that disguise automation should appear.
    forbidden = [
        "--remote-debugging-port",
        "--disable-blink-features",
        "--no-sandbox",
        "--disable-web-security",
        "--enable-automation",
    ]
    for flag in forbidden:
        assert flag not in args_str, f"Forbidden flag present: {flag}"


def test_authenticate_chatgpt_raises_when_chrome_missing(tmp_path: Path) -> None:
    """Raises StableChromeNotFoundError when Chrome cannot be located."""
    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            side_effect=StableChromeNotFoundError("Chrome not found"),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
    ):
        from mindcap.plugins.chatgpt.strategies.browser import authenticate_chatgpt

        with pytest.raises(StableChromeNotFoundError):
            authenticate_chatgpt()


def test_authenticate_chatgpt_raises_when_profile_locked(tmp_path: Path) -> None:
    """Raises ProfileLockedError when the profile is locked before auth starts."""
    lock_file = tmp_path / "SingletonLock"
    lock_file.touch()

    fake_chrome = tmp_path / "google-chrome"
    fake_chrome.touch()

    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=fake_chrome,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
    ):
        from mindcap.plugins.chatgpt.strategies.browser import authenticate_chatgpt

        with pytest.raises(ProfileLockedError):
            authenticate_chatgpt()


# ---------------------------------------------------------------------------
# BrowserCaptureStrategy — external stable Chrome + CDP
# ---------------------------------------------------------------------------


def test_browser_capture_strategy_connects_over_cdp() -> None:
    """Capture strategy must connect to externally launched Chrome over CDP."""
    mock_page = MagicMock()
    mock_page.url = "https://chatgpt.com/c/test-id"

    def response_handler(_name: str, callback: object) -> None:
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        response.url = "https://chatgpt.com/backend-api/conversation/test-id"
        response.body.return_value = b'{"mapping": {"a": {}}, "current_node": "a"}'
        if callable(callback):
            callback(response)

    mock_page.on.side_effect = response_handler

    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = MagicMock()
    mock_browser.contexts = [mock_context]

    mock_chromium = MagicMock()
    mock_chromium.connect_over_cdp.return_value = mock_browser

    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.__enter__ = lambda s: mock_playwright
    mock_playwright.__exit__ = MagicMock(return_value=False)

    from mindcap.core.models import CaptureRequest

    request = CaptureRequest(
        source_type="chatgpt",
        source="https://chatgpt.com/c/test-id",
        provider="chatgpt",
        canonical_identifier="test-id",
        canonical_url="https://chatgpt.com/c/test-id",
        strategy="browser",
        artifact_root=Path("/tmp/mindcap-test"),
    )

    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.sync_playwright",
            return_value=mock_playwright,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=Path("/tmp/chrome"),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.chatgpt_profile_dir",
            return_value=Path("/tmp/fake-profile"),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=Path("/tmp/fake-profile"),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_dedicated_chrome_running",
            return_value=False,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_profile_locked",
            return_value=False,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._wait_for_fresh_cdp_port",
            return_value="http://127.0.0.1:9222",
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.subprocess.Popen",
            return_value=MagicMock(),
        ) as mock_popen,
    ):
        strategy = BrowserCaptureStrategy()
        envelope = strategy.capture(request)

    expected_args = _chrome_cdp_args(Path("/tmp/chrome"), Path("/tmp/fake-profile"))
    mock_popen.assert_called_once()
    assert mock_popen.call_args.args[0] == expected_args
    assert mock_popen.call_args.kwargs.get("stdout") == subprocess.DEVNULL
    assert mock_popen.call_args.kwargs.get("stderr") == subprocess.DEVNULL
    mock_chromium.connect_over_cdp.assert_called_once_with(
        "http://127.0.0.1:9222", timeout=60_000
    )
    assert envelope.canonical_identifier == "test-id"


# ---------------------------------------------------------------------------
# Archive bundles do not contain authentication state
# ---------------------------------------------------------------------------


def test_bundle_does_not_contain_auth_state(tmp_path: Path) -> None:
    """Saved-JSON pipeline bundles must not include cookies or browser storage."""
    from mindcap.core.models import CaptureRequest
    from mindcap.plugins.chatgpt.plugin import ChatGPTPlugin
    from mindcap.storage.filesystem import FilesystemStorageStrategy

    fixture = (
        Path(__file__).parent / "fixtures" / "chatgpt" / "branching-conversation.json"
    )
    identifier = "6a14b69f-7834-83ea-8257-0eceadb41691"
    request = CaptureRequest(
        source_type="chatgpt",
        source=str(fixture),
        provider="chatgpt",
        canonical_identifier=identifier,
        canonical_url=f"https://chatgpt.com/c/{identifier}",
        strategy="saved-json",
        artifact_root=tmp_path,
    )

    plugin = ChatGPTPlugin()
    envelope = plugin.strategy("saved-json").capture(request)
    normalized = plugin.normalize(envelope, identifier)
    transcript = plugin.render(normalized)
    storage = FilesystemStorageStrategy()
    stored = storage.persist(request, envelope, normalized, transcript)

    bundle_path = stored.path
    all_files = list(bundle_path.rglob("*"))

    forbidden_names = {"cookies.json", "Cookies", "Local Storage", "storage.json"}
    for f in all_files:
        assert f.name not in forbidden_names, (
            f"Bundle contains potential auth-state file: {f}"
        )
