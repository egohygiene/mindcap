"""Regression tests for the Chrome/CDP session lifecycle.

Covers:
- Stale DevToolsActivePort race conditions.
- Process-exit detection while waiting for the endpoint.
- Partial / malformed endpoint file content.
- Unreachable loopback endpoints.
- Safe cleanup (never removes authentication state).
- Correct auth-state distinctions (not present, unreachable, indeterminate, verified).
- Consecutive runs each use their own fresh endpoint.
- Failed verification does not falsely report that authentication itself failed.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mindcap.core.errors import AuthenticationRequiredError, CaptureFailedError
from mindcap.plugins.chatgpt.strategies.browser import (
    AuthenticationCheck,
    AuthenticationState,
    _cleanup_devtools_port,
    _is_endpoint_reachable,
    _parse_devtools_port,
    _read_devtools_port_content,
    _wait_for_fresh_cdp_port,
    authenticate_chatgpt,
    verify_chatgpt_authentication,
)

# ---------------------------------------------------------------------------
# _read_devtools_port_content
# ---------------------------------------------------------------------------


def test_read_devtools_port_content_absent(tmp_path: Path) -> None:
    """Returns None when the DevToolsActivePort file does not exist."""
    assert _read_devtools_port_content(tmp_path) is None


def test_read_devtools_port_content_present(tmp_path: Path) -> None:
    """Returns the raw file content when the file exists."""
    content = "52850\n/devtools/browser/abc-123\n"
    (tmp_path / "DevToolsActivePort").write_text(content, encoding="utf-8")
    assert _read_devtools_port_content(tmp_path) == content


def test_read_devtools_port_content_unreadable(tmp_path: Path) -> None:
    """Returns None instead of raising when the file cannot be read."""
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text("52850\n/devtools/browser/abc\n", encoding="utf-8")
    devtools_file.chmod(0o000)
    try:
        result = _read_devtools_port_content(tmp_path)
        assert result is None
    finally:
        devtools_file.chmod(0o644)


# ---------------------------------------------------------------------------
# _parse_devtools_port
# ---------------------------------------------------------------------------


def test_parse_devtools_port_valid() -> None:
    """Parses a well-formed DevToolsActivePort file."""
    content = "52850\n/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3\n"
    result = _parse_devtools_port(content)
    assert result == ("52850", "/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3")


def test_parse_devtools_port_empty_string() -> None:
    """Returns None for an empty string."""
    assert _parse_devtools_port("") is None


def test_parse_devtools_port_single_line() -> None:
    """Returns None when only one line is present (partial write)."""
    assert _parse_devtools_port("52850\n") is None


def test_parse_devtools_port_missing_port() -> None:
    """Returns None when the port line is blank."""
    assert _parse_devtools_port("\n/devtools/browser/abc\n") is None


def test_parse_devtools_port_wrong_path_prefix() -> None:
    """Returns None when the browser path does not start with /devtools/browser/."""
    assert _parse_devtools_port("52850\n/wrong/path\n") is None


def test_parse_devtools_port_malformed_path() -> None:
    """Returns None for a completely malformed file."""
    assert _parse_devtools_port("not-a-port\nnot-a-path\n") is None


# ---------------------------------------------------------------------------
# _is_endpoint_reachable
# ---------------------------------------------------------------------------


def test_is_endpoint_reachable_open_port() -> None:
    """Returns True when a listening TCP server is on the port."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = str(server.getsockname()[1])
    try:
        assert _is_endpoint_reachable(port) is True
    finally:
        server.close()


def test_is_endpoint_reachable_closed_port() -> None:
    """Returns False when nothing is listening on the given port."""
    # Find a port that's definitely not listening by binding and immediately closing.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = str(sock.getsockname()[1])
    sock.close()
    assert _is_endpoint_reachable(port) is False


def test_is_endpoint_reachable_invalid_port() -> None:
    """Returns False for a non-numeric port string."""
    assert _is_endpoint_reachable("not-a-port") is False


def test_is_endpoint_reachable_out_of_range_port() -> None:
    """Returns False for an out-of-range port number."""
    assert _is_endpoint_reachable("99999") is False


# ---------------------------------------------------------------------------
# _wait_for_fresh_cdp_port — helper
# ---------------------------------------------------------------------------


def _make_alive_process() -> MagicMock:
    """Return a mock Popen whose poll() always returns None (process running)."""
    process = MagicMock(spec=subprocess.Popen)
    process.poll.return_value = None
    return process


def _make_exited_process(returncode: int = 1) -> MagicMock:
    """Return a mock Popen whose poll() immediately reports exit."""
    process = MagicMock(spec=subprocess.Popen)
    process.poll.return_value = returncode
    process.returncode = returncode
    return process


def _fresh_devtools_content(port: str = "52850") -> str:
    return f"{port}\n/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3\n"


# ---------------------------------------------------------------------------
# Stale file exists before launch — Chrome replaces it after launch
# ---------------------------------------------------------------------------


def test_stale_devtools_port_replaced_after_launch(tmp_path: Path) -> None:
    """Chrome replaces a stale DevToolsActivePort file.

    The verifier must connect to the new endpoint, not the stale one.
    """
    stale_content = "52508\n/devtools/browser/df93a470-d9b1-44d8-b327-c0874aca1784\n"
    fresh_content = "52850\n/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3\n"

    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(stale_content, encoding="utf-8")

    # Stale mtime — before the recorded launch time.
    stale_mtime = time.time() - 60.0
    os.utime(devtools_file, (stale_mtime, stale_mtime))

    launch_time = time.time()

    # Simulate Chrome replacing the file after a short delay using a thread.
    def write_fresh() -> None:
        time.sleep(0.05)
        devtools_file.write_text(fresh_content, encoding="utf-8")
        # mtime will be ≥ launch_time because we write after launch_time.

    thread = threading.Thread(target=write_fresh, daemon=True)
    thread.start()

    process = _make_alive_process()

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        return_value=True,
    ):
        endpoint = _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, stale_content, timeout_seconds=5.0
        )

    thread.join(timeout=2.0)

    assert endpoint == "http://127.0.0.1:52850"


def test_verifier_uses_new_endpoint_not_stale(tmp_path: Path) -> None:
    """After replacement the returned HTTP endpoint encodes the new port."""
    stale_content = "52508\n/devtools/browser/old-id\n"
    fresh_content = "52850\n/devtools/browser/new-id\n"

    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(fresh_content, encoding="utf-8")
    # mtime in the future relative to launch ensures freshness.
    launch_time = time.time() - 1.0

    process = _make_alive_process()

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        return_value=True,
    ):
        endpoint = _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, stale_content, timeout_seconds=2.0
        )

    assert "52850" in endpoint
    assert "52508" not in endpoint
    assert endpoint.startswith("http://127.0.0.1:")


# ---------------------------------------------------------------------------
# Stale endpoint remains unchanged — times out safely
# ---------------------------------------------------------------------------


def test_stale_endpoint_unchanged_times_out(tmp_path: Path) -> None:
    """When DevToolsActivePort never changes, a CaptureFailedError is raised."""
    stale_content = "52508\n/devtools/browser/df93a470-d9b1-44d8-b327-c0874aca1784\n"
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(stale_content, encoding="utf-8")

    stale_mtime = time.time() - 60.0
    os.utime(devtools_file, (stale_mtime, stale_mtime))

    launch_time = time.time()
    process = _make_alive_process()

    with pytest.raises(CaptureFailedError, match="fresh DevTools endpoint"):
        _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, stale_content, timeout_seconds=0.3
        )


def test_stale_endpoint_timeout_includes_previous_port(tmp_path: Path) -> None:
    """Timeout error message includes the stale port for diagnostic clarity."""
    stale_content = "52508\n/devtools/browser/df93a470-d9b1-44d8-b327-c0874aca1784\n"
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(stale_content, encoding="utf-8")

    os.utime(devtools_file, (time.time() - 60.0, time.time() - 60.0))

    launch_time = time.time()
    process = _make_alive_process()

    with pytest.raises(CaptureFailedError) as exc_info:
        _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, stale_content, timeout_seconds=0.3
        )

    assert "52508" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Chrome exits before publishing an endpoint
# ---------------------------------------------------------------------------


def test_chrome_exits_before_publishing_endpoint(tmp_path: Path) -> None:
    """CaptureFailedError is raised immediately when Chrome exits with no endpoint."""
    launch_time = time.time()
    process = _make_exited_process(returncode=1)

    with pytest.raises(CaptureFailedError, match="Chrome exited"):
        _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, None, timeout_seconds=5.0
        )


def test_chrome_exits_error_includes_return_code(tmp_path: Path) -> None:
    """Chrome exit error message includes the process return code."""
    launch_time = time.time()
    process = _make_exited_process(returncode=127)

    with pytest.raises(CaptureFailedError) as exc_info:
        _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, None, timeout_seconds=5.0
        )

    assert "127" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Endpoint file is partially written
# ---------------------------------------------------------------------------


def test_endpoint_file_partially_written_polls_until_complete(tmp_path: Path) -> None:
    """Polling continues past a partial file until all required lines appear."""
    devtools_file = tmp_path / "DevToolsActivePort"
    launch_time = time.time() - 1.0  # file will be "fresh"
    process = _make_alive_process()

    def write_gradually() -> None:
        # Write partial content first.
        time.sleep(0.02)
        devtools_file.write_text("52850\n", encoding="utf-8")
        # Then complete it.
        time.sleep(0.05)
        devtools_file.write_text(
            "52850\n/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3\n",
            encoding="utf-8",
        )

    thread = threading.Thread(target=write_gradually, daemon=True)
    thread.start()

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        return_value=True,
    ):
        endpoint = _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, None, timeout_seconds=5.0
        )

    thread.join(timeout=2.0)
    assert endpoint == "http://127.0.0.1:52850"


# ---------------------------------------------------------------------------
# Endpoint contains malformed data
# ---------------------------------------------------------------------------


def test_endpoint_contains_malformed_data_times_out(tmp_path: Path) -> None:
    """A malformed DevToolsActivePort file causes a safe timeout."""
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text("not-a-port\nnot-a-path\n", encoding="utf-8")

    launch_time = time.time() - 1.0
    process = _make_alive_process()

    with pytest.raises(CaptureFailedError, match="fresh DevTools endpoint"):
        _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, None, timeout_seconds=0.3
        )


# ---------------------------------------------------------------------------
# Reported endpoint is not reachable
# ---------------------------------------------------------------------------


def test_endpoint_not_reachable_continues_polling(tmp_path: Path) -> None:
    """A valid but unreachable endpoint keeps the poll loop going until timeout."""
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(
        "52850\n/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3\n",
        encoding="utf-8",
    )
    launch_time = time.time() - 1.0
    process = _make_alive_process()

    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
            return_value=False,
        ),
        pytest.raises(CaptureFailedError),
    ):
        _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, None, timeout_seconds=0.3
        )


def test_endpoint_becomes_reachable_after_delay(tmp_path: Path) -> None:
    """Polling succeeds as soon as the endpoint becomes reachable."""
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(
        "52850\n/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3\n",
        encoding="utf-8",
    )
    launch_time = time.time() - 1.0
    process = _make_alive_process()

    reachable_calls = [False, False, True]
    reachable_iter = iter(reachable_calls)

    def reachable_side_effect(_port: str) -> bool:
        try:
            return next(reachable_iter)
        except StopIteration:
            return True

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        side_effect=reachable_side_effect,
    ):
        endpoint = _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, None, timeout_seconds=5.0
        )

    assert endpoint == "http://127.0.0.1:52850"


# ---------------------------------------------------------------------------
# Consecutive runs each use their own fresh endpoint
# ---------------------------------------------------------------------------


def test_consecutive_runs_use_own_endpoint(tmp_path: Path) -> None:
    """Each run detects a new endpoint independently of the previous run's port."""
    first_content = "52850\n/devtools/browser/first-id\n"
    second_content = "53100\n/devtools/browser/second-id\n"

    devtools_file = tmp_path / "DevToolsActivePort"

    # First run.
    devtools_file.write_text(first_content, encoding="utf-8")
    launch_time_1 = time.time() - 1.0
    process_1 = _make_alive_process()

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        return_value=True,
    ):
        endpoint_1 = _wait_for_fresh_cdp_port(
            tmp_path, process_1, launch_time_1, None, timeout_seconds=2.0
        )

    # Second run: simulate Chrome replacing the file.
    devtools_file.write_text(second_content, encoding="utf-8")
    launch_time_2 = time.time() - 0.5
    process_2 = _make_alive_process()

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        return_value=True,
    ):
        endpoint_2 = _wait_for_fresh_cdp_port(
            tmp_path, process_2, launch_time_2, first_content, timeout_seconds=2.0
        )

    assert "52850" in endpoint_1
    assert "53100" in endpoint_2
    assert endpoint_1 != endpoint_2


# ---------------------------------------------------------------------------
# _cleanup_devtools_port — never removes authentication state
# ---------------------------------------------------------------------------


def test_cleanup_removes_only_devtools_port_file(tmp_path: Path) -> None:
    """Cleanup removes DevToolsActivePort but leaves all other profile files."""
    # Create profile files that must never be removed.
    preserved = {
        "Cookies": b"sqlite-cookie-data",
        "Local State": b'{"profile": {}}',
        "Default/Preferences": b'{"session": {}}',
        "SingletonLock": b"",
    }
    for relative, data in preserved.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text("52850\n/devtools/browser/abc\n", encoding="utf-8")

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_dedicated_chrome_running",
        return_value=False,
    ):
        _cleanup_devtools_port(tmp_path)

    assert not devtools_file.exists(), "DevToolsActivePort should have been removed"
    for relative in preserved:
        assert (tmp_path / relative).exists(), (
            f"Auth/profile file must not be removed: {relative}"
        )


def test_cleanup_skips_when_chrome_still_running(tmp_path: Path) -> None:
    """Cleanup leaves DevToolsActivePort intact when Chrome still owns the profile."""
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text("52850\n/devtools/browser/abc\n", encoding="utf-8")

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_dedicated_chrome_running",
        return_value=True,
    ):
        _cleanup_devtools_port(tmp_path)

    assert devtools_file.exists()


def test_cleanup_tolerates_absent_file(tmp_path: Path) -> None:
    """Cleanup does not raise when DevToolsActivePort does not exist."""
    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_dedicated_chrome_running",
        return_value=False,
    ):
        _cleanup_devtools_port(tmp_path)  # Must not raise.


# ---------------------------------------------------------------------------
# Authentication state distinctions
# ---------------------------------------------------------------------------


def _make_verify_mock(final_url: str) -> tuple[MagicMock, MagicMock]:
    """Build Playwright mocks whose page.url returns final_url."""
    mock_page = MagicMock()
    mock_page.url = final_url
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
    return mock_playwright, mock_browser


def _patch_verify_infra(
    final_url: str,
    tmp_path: Path,
) -> Any:
    """Context manager patching all infrastructure for verify_chatgpt_authentication."""
    from contextlib import ExitStack

    mock_playwright, _ = _make_verify_mock(final_url)
    stack = ExitStack()
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=tmp_path / "chrome",
        )
    )
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        )
    )
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_profile_locked",
            return_value=False,
        )
    )
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_dedicated_chrome_running",
            return_value=False,
        )
    )
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._wait_for_fresh_cdp_port",
            return_value="http://127.0.0.1:52850",
        )
    )
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.subprocess.Popen",
            return_value=MagicMock(),
        )
    )
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.sync_playwright",
            return_value=mock_playwright,
        )
    )
    stack.enter_context(
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._cleanup_devtools_port",
        )
    )
    return stack


def test_auth_state_verified(tmp_path: Path) -> None:
    """Returns VERIFIED when ChatGPT opens at a conversation path."""
    with _patch_verify_infra("https://chatgpt.com/c/some-id", tmp_path):
        check = verify_chatgpt_authentication()

    assert check.state is AuthenticationState.VERIFIED


def test_auth_state_not_present(tmp_path: Path) -> None:
    """Returns UNVERIFIED when the browser is redirected to a login page."""
    with _patch_verify_infra("https://chatgpt.com/auth/login", tmp_path):
        check = verify_chatgpt_authentication()

    assert check.state is AuthenticationState.UNVERIFIED


def test_auth_state_expired(tmp_path: Path) -> None:
    """Returns EXPIRED when the final URL contains an expiry signal."""
    with _patch_verify_infra("https://chatgpt.com/auth/verify", tmp_path):
        check = verify_chatgpt_authentication()

    assert check.state is AuthenticationState.EXPIRED


def test_auth_state_indeterminate_on_unexpected_page(tmp_path: Path) -> None:
    """Returns INDETERMINATE when ChatGPT opens at an unrecognized non-auth path."""
    # Use a path that is not in _AUTH_EXACT_PATHS and does not start with
    # any auth prefix, but is also not a conversation path (/c/<id>).
    with _patch_verify_infra("https://chatgpt.com/explore", tmp_path):
        check = verify_chatgpt_authentication()

    assert check.state is AuthenticationState.INDETERMINATE


def test_auth_state_unreachable_when_cdp_unavailable(tmp_path: Path) -> None:
    """Returns UNREACHABLE when _wait_for_fresh_cdp_port raises CaptureFailedError."""
    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=tmp_path / "chrome",
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_profile_locked",
            return_value=False,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_dedicated_chrome_running",
            return_value=False,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._wait_for_fresh_cdp_port",
            side_effect=CaptureFailedError("Chrome exited (code 1) before publishing."),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.subprocess.Popen",
            return_value=MagicMock(),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._cleanup_devtools_port",
        ),
    ):
        check = verify_chatgpt_authentication()

    assert check.state is AuthenticationState.UNREACHABLE


def test_auth_state_unreachable_message_mentions_endpoint(tmp_path: Path) -> None:
    """UNREACHABLE detail message mentions the Chrome debugging endpoint."""
    with (
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._find_stable_chrome",
            return_value=tmp_path / "chrome",
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.ensure_private_directory",
            return_value=tmp_path,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_profile_locked",
            return_value=False,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._is_dedicated_chrome_running",
            return_value=False,
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._wait_for_fresh_cdp_port",
            side_effect=CaptureFailedError("Chrome did not expose a fresh endpoint."),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.subprocess.Popen",
            return_value=MagicMock(),
        ),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser._cleanup_devtools_port",
        ),
    ):
        check = verify_chatgpt_authentication()

    assert "endpoint" in check.detail.lower()
    assert "verification" in check.detail.lower() or "connect" in check.detail.lower()


# ---------------------------------------------------------------------------
# Failed verification does not falsely report authentication failure
# ---------------------------------------------------------------------------


def test_failed_verification_does_not_raise_auth_required_error(
    tmp_path: Path,
) -> None:
    """When verification is UNREACHABLE, authenticate_chatgpt raises CaptureFailedError
    instead of AuthenticationRequiredError, not prompting a needless re-login.
    """
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
        patch("subprocess.Popen", return_value=MagicMock()),
        patch("builtins.input", return_value=""),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                state=AuthenticationState.UNREACHABLE,
                detail=(
                    "Authentication may have been saved, but verification could "
                    "not connect to the current Chrome debugging endpoint."
                ),
            ),
        ),
        pytest.raises(CaptureFailedError) as exc_info,
    ):
        authenticate_chatgpt()

    # Must NOT be AuthenticationRequiredError (that would tell the user to re-login).
    assert not isinstance(exc_info.value, AuthenticationRequiredError)


def test_failed_verification_message_matches_expected(tmp_path: Path) -> None:
    """UNREACHABLE after auth produces the canonical 'may have been saved' message."""
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
        patch("subprocess.Popen", return_value=MagicMock()),
        patch("builtins.input", return_value=""),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                state=AuthenticationState.UNREACHABLE,
                detail=(
                    "Authentication may have been saved, but verification could "
                    "not connect to the current Chrome debugging endpoint."
                ),
            ),
        ),
        pytest.raises(CaptureFailedError) as exc_info,
    ):
        authenticate_chatgpt()

    message = str(exc_info.value)
    assert "may have been saved" in message
    assert "verification" in message.lower()
    assert "endpoint" in message.lower()


def test_genuine_auth_failure_still_raises_auth_required_error(
    tmp_path: Path,
) -> None:
    """UNVERIFIED state still raises AuthenticationRequiredError as before."""
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
        patch("subprocess.Popen", return_value=MagicMock()),
        patch("builtins.input", return_value=""),
        patch(
            "mindcap.plugins.chatgpt.strategies.browser.verify_chatgpt_authentication",
            return_value=AuthenticationCheck(
                state=AuthenticationState.UNVERIFIED,
                detail='Profile is not authenticated. Run "mindcap auth chatgpt".',
            ),
        ),
        pytest.raises(AuthenticationRequiredError),
    ):
        authenticate_chatgpt()


# ---------------------------------------------------------------------------
# HTTP CDP endpoint — never reuses a WebSocket path from a prior run
# ---------------------------------------------------------------------------


def test_cdp_endpoint_is_http_not_websocket(tmp_path: Path) -> None:
    """_wait_for_fresh_cdp_port returns an HTTP URL, not a WebSocket URL."""
    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(
        "52850\n/devtools/browser/e447a5b6-222e-4bb9-8444-6dd975a0b7d3\n",
        encoding="utf-8",
    )
    launch_time = time.time() - 1.0
    process = _make_alive_process()

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        return_value=True,
    ):
        endpoint = _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, None, timeout_seconds=2.0
        )

    assert endpoint.startswith("http://"), f"Expected HTTP URL, got: {endpoint}"
    assert not endpoint.startswith("ws://"), "Must not return a WebSocket URL"
    # Must not contain a hard-coded WebSocket browser path.
    assert "/devtools/browser/" not in endpoint


def test_cdp_endpoint_never_reuses_prior_ws_path(tmp_path: Path) -> None:
    """Each run produces an HTTP endpoint without the prior run's browser GUID."""
    old_guid = "df93a470-d9b1-44d8-b327-c0874aca1784"
    new_guid = "e447a5b6-222e-4bb9-8444-6dd975a0b7d3"
    old_content = f"52508\n/devtools/browser/{old_guid}\n"
    new_content = f"52850\n/devtools/browser/{new_guid}\n"

    devtools_file = tmp_path / "DevToolsActivePort"
    devtools_file.write_text(new_content, encoding="utf-8")
    launch_time = time.time() - 1.0
    process = _make_alive_process()

    with patch(
        "mindcap.plugins.chatgpt.strategies.browser._is_endpoint_reachable",
        return_value=True,
    ):
        endpoint = _wait_for_fresh_cdp_port(
            tmp_path, process, launch_time, old_content, timeout_seconds=2.0
        )

    assert old_guid not in endpoint
    assert "52508" not in endpoint
