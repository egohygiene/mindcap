from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mindcap.core.errors import InvalidSourceError
from mindcap.core.models import CaptureEnvelope, CaptureRequest, RawResponseUnit
from mindcap.core.progress import CaptureProgressReporter
from mindcap.core.protocols import CaptureStrategy
from mindcap.plugins.chrome_bookmarks.archive.storage import (
    ChromeBookmarksStorageStrategy,
)
from mindcap.plugins.chrome_bookmarks.discovery import discover_profiles
from mindcap.plugins.chrome_bookmarks.identifiers import (
    canonicalize_chrome_bookmarks_identifier,
    supports_chrome_bookmarks_source,
)
from mindcap.plugins.chrome_bookmarks.normalizer import normalize_chrome_bookmarks
from mindcap.plugins.chrome_bookmarks.renderer import render_chrome_bookmarks_markdown
from mindcap.plugins.chrome_bookmarks.snapshot import snapshot_bookmarks


class ChromeBookmarksCaptureStrategy:
    name = "filesystem"

    def __init__(self, reporter: CaptureProgressReporter | None = None) -> None:
        self._reporter = reporter

    def capture(self, request: CaptureRequest) -> CaptureEnvelope:
        user_data_dirs_option = request.options.get("user_data_dirs") or []
        user_data_dirs = [
            str(value) for value in user_data_dirs_option if str(value).strip()
        ]
        if (
            request.source
            and request.source.strip()
            and request.source.strip().lower() not in {"auto", "local"}
            and not user_data_dirs
        ):
            user_data_dirs = [request.source]
        requested_profiles_option = request.options.get("profiles") or []
        requested_profiles = [
            str(value) for value in requested_profiles_option if str(value).strip()
        ]
        channel = str(request.options.get("channel") or "stable")

        profiles = discover_profiles(
            channel=channel,
            user_data_dirs=user_data_dirs or None,
            requested_profiles=requested_profiles or None,
        )
        if not profiles:
            raise InvalidSourceError(
                "No readable Google Chrome profiles with Bookmarks files were found."
            )

        if self._reporter is not None:
            self._reporter.phase("Snapshotting Chrome bookmarks...")

        response_units: list[RawResponseUnit] = []
        safe_profiles: list[dict[str, Any]] = []
        warnings: list[str] = []
        sequence = 0
        captured_at = datetime.now(UTC)
        for profile in profiles:
            snapshot = snapshot_bookmarks(profile)
            primary_unit_id = f"profile-{sequence:03d}-primary"
            response_units.append(
                RawResponseUnit(
                    unit_id=primary_unit_id,
                    sequence=sequence,
                    media_type="application/json",
                    body=snapshot.primary_bytes,
                    safe_metadata={
                        "profile_id": profile.profile_id,
                        "profile_directory_name": profile.profile_directory_name,
                        "snapshot_role": "primary",
                        "selected": snapshot.selected_source == "primary",
                    },
                )
            )
            sequence += 1
            selected_unit_id = primary_unit_id
            if snapshot.backup_bytes is not None:
                backup_unit_id = f"profile-{sequence:03d}-backup"
                response_units.append(
                    RawResponseUnit(
                        unit_id=backup_unit_id,
                        sequence=sequence,
                        media_type="application/json",
                        body=snapshot.backup_bytes,
                        safe_metadata={
                            "profile_id": profile.profile_id,
                            "profile_directory_name": profile.profile_directory_name,
                            "snapshot_role": "backup",
                            "selected": snapshot.selected_source == "backup",
                        },
                    )
                )
                sequence += 1
                if snapshot.selected_source == "backup":
                    selected_unit_id = backup_unit_id
            warnings.extend(snapshot.warnings)
            safe_profiles.append(
                {
                    "channel": profile.channel,
                    "user_data_dir": str(profile.user_data_dir),
                    "profile_dir": str(profile.profile_dir),
                    "profile_directory_name": profile.profile_directory_name,
                    "profile_id": profile.profile_id,
                    "profile_name": profile.profile_name,
                    "bookmarks_path": str(profile.bookmarks_path),
                    "selected_source": snapshot.selected_source,
                    "selected_unit_id": selected_unit_id,
                    "warnings": list(snapshot.warnings),
                    "primary_before": {
                        "size": snapshot.primary_before.size,
                        "modified_ns": snapshot.primary_before.modified_ns,
                    },
                    "primary_after": {
                        "size": snapshot.primary_after.size,
                        "modified_ns": snapshot.primary_after.modified_ns,
                    },
                    "retries": snapshot.retries,
                }
            )

        return CaptureEnvelope(
            provider="chrome-bookmarks",
            source_type="bookmark-collection",
            canonical_identifier=request.canonical_identifier,
            canonical_url=None,
            captured_at=captured_at,
            strategy=self.name,
            response_units=response_units,
            safe_metadata={
                "profiles": safe_profiles,
                "profile_count": len(safe_profiles),
                "project_title": "Chrome bookmarks",
            },
            warnings=warnings,
        )


class ChromeBookmarksPlugin:
    source_type = "chrome-bookmarks"

    def supports(self, value: str) -> bool:
        return supports_chrome_bookmarks_source(value)

    def canonicalize(self, value: str) -> tuple[str, str | None]:
        return canonicalize_chrome_bookmarks_identifier(value)

    def default_strategy(self) -> str:
        return "filesystem"

    def strategies(self) -> tuple[str, ...]:
        return ("filesystem",)

    def strategy(
        self,
        name: str,
        reporter: CaptureProgressReporter | None = None,
    ) -> CaptureStrategy:
        strategies: dict[str, CaptureStrategy] = {
            "filesystem": ChromeBookmarksCaptureStrategy(reporter=reporter)
        }
        try:
            return strategies[name]
        except KeyError as error:
            available = ", ".join(sorted(strategies))
            raise InvalidSourceError(
                f'Unknown Chrome Bookmarks strategy "{name}". Available: {available}'
            ) from error

    def normalize(
        self, envelope: CaptureEnvelope, requested_identifier: str
    ) -> dict[str, Any]:
        return normalize_chrome_bookmarks(envelope, requested_identifier)

    def render(self, normalized: dict[str, Any]) -> str:
        return render_chrome_bookmarks_markdown(normalized)

    def storage(self) -> ChromeBookmarksStorageStrategy:
        return ChromeBookmarksStorageStrategy()
