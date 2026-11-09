"""Synthetic producer wire, raw fidelity, portability and unsupported capabilities."""

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from mindcap.contracts.source_bundle import (
    MANIFEST,
    CapturedSourceBundle,
    HandoffUnit,
    Producer,
    build_source_bundle,
    handoff_schema,
)
from mindcap.core.hashing import sha256_bytes
from mindcap.core.models import CaptureRequest
from mindcap.plugins.chrome_bookmarks.handoff import bookmark_source_bundle
from mindcap.plugins.chrome_bookmarks.plugin import ChromeBookmarksPlugin

FIXTURE = (
    Path(__file__).parent / "fixtures/chrome_bookmarks/multiple_profiles/User Data"
)
REVISION = "a" * 40


def basic(**overrides):
    arguments = {
        "source_id": "synthetic-capture",
        "source_type": "synthetic-reference",
        "producer": Producer(version="0.1.0", revision=REVISION),
        "captured_at": datetime(2026, 11, 4, tzinfo=UTC),
        "status": "complete",
        "files": {
            "raw/source.txt": b"Synthetic original.\r\n",
            "units/one.txt": b"Synthetic original.\n",
        },
        "normalized_members": {"units/one.txt"},
        "units": (
            HandoffUnit(
                identity="one",
                member="units/one.txt",
                media_type="text/plain",
                locators=("artifact",),
                identity_basis="observed",
                original_members=("raw/source.txt",),
            ),
        ),
    }
    return build_source_bundle(**{**arguments, **overrides})


def members(content):
    with ZipFile(BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def capture(tmp_path):
    plugin = ChromeBookmarksPlugin()
    request = CaptureRequest(
        source_type="chrome-bookmarks",
        source="",
        provider="chrome-bookmarks",
        canonical_identifier="local",
        strategy="filesystem",
        artifact_root=tmp_path,
        options={"user_data_dirs": [str(FIXTURE)], "channel": "stable"},
    )
    envelope = plugin.strategy("filesystem").capture(request)
    document = plugin.normalize(envelope, "local")
    stored = plugin.storage().persist(
        request, envelope, document, plugin.render(document)
    )
    identities = {
        item["profile_id"]: "synthetic-"
        + item["profile_directory_name"].replace(" ", "-").lower()
        for item in document["profiles"]
    }
    return stored, identities


def test_neutral_wire_is_deterministic_exact_and_private():
    with patch("socket.socket", side_effect=AssertionError("Network attempted")):
        content = basic()
        assert content == basic()
    payloads = members(content)
    manifest = CapturedSourceBundle.model_validate_json(payloads.pop(MANIFEST))
    assert manifest.sensitivity == "private"
    assert (
        not manifest.capabilities.delta and not manifest.capabilities.knowledge_review
    )
    assert {item.path for item in manifest.artifacts} == set(payloads)
    for item in manifest.artifacts:
        assert item.sha256 == sha256_bytes(payloads[item.path])
        assert item.size_bytes == len(payloads[item.path])
    assert payloads["raw/source.txt"] == b"Synthetic original.\r\n"
    assert handoff_schema()["$id"] == "urn:mindcap:captured-source-bundle:v1"


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "a/../b", "x\\y", ".git/config", "x\nsecret"]
)
def test_unsafe_member_never_encodes(path):
    with pytest.raises((ValueError, ValidationError)):
        basic(files={path: b"Synthetic"}, normalized_members=set(), units=())


def test_duplicates_missing_lineage_unknown_caps_and_incomplete_state_are_explicit():
    payloads = members(basic())
    manifest = json.loads(payloads[MANIFEST])
    manifest["units"].append(manifest["units"][0])
    with pytest.raises(ValidationError):
        CapturedSourceBundle.model_validate(manifest)
    manifest["units"].pop()
    manifest["units"][0]["original_members"] = ["missing.txt"]
    with pytest.raises(ValidationError):
        CapturedSourceBundle.model_validate(manifest)
    manifest = json.loads(payloads[MANIFEST])
    manifest["capabilities"]["knowledge_review"] = True
    with pytest.raises(ValidationError):
        CapturedSourceBundle.model_validate(manifest)
    partial = members(basic(status="partial", warnings=("CAPTURE_WARNINGS_PRESENT",)))
    assert json.loads(partial[MANIFEST])["status"] == "partial"


def test_real_bookmark_capture_handoff_preserves_every_original_without_network(
    tmp_path,
):
    with patch("socket.socket", side_effect=AssertionError("Network attempted")):
        stored, identities = capture(tmp_path)
        before = {
            p.relative_to(stored.path).as_posix(): p.read_bytes()
            for p in stored.path.rglob("*")
            if p.is_file()
        }
        arguments = {
            "source_id": "synthetic-browser",
            "producer_revision": REVISION,
            "profile_identities": identities,
        }
        first = bookmark_source_bundle(stored.path, **arguments)
        assert bookmark_source_bundle(stored.path, **arguments) == first
    payloads = members(first)
    manifest = CapturedSourceBundle.model_validate_json(payloads[MANIFEST])
    assert manifest.units and all(
        unit.identity_basis == "observed" for unit in manifest.units
    )
    assert all(payloads["capture/" + name] == value for name, value in before.items())
    assert all(
        (stored.path / name).read_bytes() == value for name, value in before.items()
    )
    for unit in manifest.units:
        normalized = json.loads(payloads[unit.member])
        assert "captured_at" not in normalized and "profile_directory" not in normalized
        assert normalized["profile_id"] in identities.values()
    assert manifest.capabilities.remote_fetch is False


def test_bookmark_handoff_requires_explicit_logical_profiles(tmp_path):
    stored, _ = capture(tmp_path)
    with pytest.raises(ValueError, match="HANDOFF_IDENTITY"):
        bookmark_source_bundle(
            stored.path,
            source_id="synthetic",
            producer_revision=REVISION,
            profile_identities={},
        )


def test_bookmark_corruption_and_symlinks_fail_without_touching_capture(tmp_path):
    stored, identities = capture(tmp_path)
    link = stored.path / "unexpected"
    link.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="HANDOFF_PATH"):
        bookmark_source_bundle(
            stored.path,
            source_id="synthetic",
            producer_revision=REVISION,
            profile_identities=identities,
        )
