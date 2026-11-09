"""Proposed captured-byte handoff, separate from acquisition and review."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated, Literal
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from mindcap.core.hashing import canonical_json_bytes, sha256_bytes

MANIFEST = "CAPTURED-SOURCE.json"
MAX_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 4096
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identity = Annotated[
    str, Field(min_length=1, max_length=512, pattern=r"^[a-zA-Z0-9:._-]+$")
]


def portable_member(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or str(path) != value
        or unicodedata.normalize("NFC", value) != value
        or any(part in {"..", ".git", ".garden"} for part in path.parts)
        or any(ord(char) < 32 or char in '\\:?*<>"|' for char in value)
    ):
        raise ValueError("HANDOFF_PATH: portable relative members required")
    return value


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Producer(ClosedModel):
    name: Literal["mindcap"] = "mindcap"
    version: str = Field(min_length=1, max_length=80)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class Artifact(ClosedModel):
    path: str = Field(min_length=1, max_length=1024)
    sha256: Digest
    size_bytes: int = Field(ge=0, le=MAX_BYTES)
    role: Literal["raw", "normalized"]


class HandoffUnit(ClosedModel):
    identity: Identity
    member: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(pattern=r"^[\w.+-]+/[\w.+-]+$")
    locators: tuple[Annotated[str, Field(min_length=1, max_length=1024)], ...] = Field(
        min_length=1, max_length=16
    )
    identity_basis: Literal["observed", "snapshot-local"]
    original_members: tuple[str, ...] = Field(min_length=1, max_length=32)


class Capabilities(ClosedModel):
    normalized_units: Literal[True] = True
    raw_artifacts: Literal[True] = True
    delta: Literal[False] = False
    remote_fetch: Literal[False] = False
    knowledge_review: Literal[False] = False


class CapturedSourceBundle(ClosedModel):
    schema_: Literal["mindcap.captured-source-bundle/v1"] = Field(alias="schema")
    source_id: Identity
    source_type: Identity
    producer: Producer
    captured_at: AwareDatetime
    status: Literal["complete", "partial"]
    sensitivity: Literal["private"] = "private"
    previous_bundle_sha256: Digest | None = None
    warnings: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")], ...] = ()
    capabilities: Capabilities
    artifacts: tuple[Artifact, ...] = Field(min_length=1, max_length=MAX_MEMBERS - 1)
    units: tuple[HandoffUnit, ...] = Field(max_length=20000)

    @model_validator(mode="after")
    def exact_members(self) -> CapturedSourceBundle:
        artifacts = {item.path: item for item in self.artifacts}
        if len(artifacts) != len(self.artifacts) or MANIFEST in artifacts:
            raise ValueError("HANDOFF_MEMBERS: distinct payload members required")
        folded = {portable_member(name).casefold() for name in artifacts}
        if len(folded) != len(artifacts):
            raise ValueError("HANDOFF_MEMBERS: ambiguous member spellings")
        if sum(item.size_bytes for item in self.artifacts) > MAX_BYTES:
            raise ValueError("HANDOFF_BUDGET: payload exceeds the bounded handoff")
        if len({unit.identity for unit in self.units}) != len(self.units):
            raise ValueError("HANDOFF_IDENTITY: normalized identities must be unique")
        for unit in self.units:
            artifact = artifacts.get(unit.member)
            if artifact is None or artifact.role != "normalized":
                raise ValueError("HANDOFF_REFERENCE: normalized payload is unavailable")
            if any(
                name not in artifacts or artifacts[name].role != "raw"
                for name in unit.original_members
            ):
                raise ValueError("HANDOFF_REFERENCE: exact raw member lineage required")
        return self


def handoff_schema() -> dict:
    schema = CapturedSourceBundle.model_json_schema(by_alias=True)
    schema.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:mindcap:captured-source-bundle:v1",
        }
    )
    return schema


def build_source_bundle(
    *,
    source_id: str,
    source_type: str,
    producer: Producer,
    captured_at: datetime | str,
    status: Literal["complete", "partial"],
    files: dict[str, bytes],
    normalized_members: set[str],
    units: tuple[HandoffUnit, ...],
    previous_bundle_sha256: str | None = None,
    warnings: tuple[str, ...] = (),
) -> bytes:
    """Package explicit captured bytes; never infer provenance or open a URL."""
    if not normalized_members <= files.keys() or any(
        not isinstance(value, bytes) for value in files.values()
    ):
        raise ValueError(
            "HANDOFF_INPUT: explicit normalized members and byte payloads required"
        )
    manifest = CapturedSourceBundle(
        schema_="mindcap.captured-source-bundle/v1",
        capabilities=Capabilities(),
        source_id=source_id,
        source_type=source_type,
        producer=producer,
        captured_at=captured_at,
        status=status,
        previous_bundle_sha256=previous_bundle_sha256,
        warnings=warnings,
        units=units,
        artifacts=tuple(
            Artifact(
                path=name,
                sha256=sha256_bytes(content),
                size_bytes=len(content),
                role="normalized" if name in normalized_members else "raw",
            )
            for name, content in sorted(files.items())
        ),
    )
    if not any(item.role == "raw" for item in manifest.artifacts):
        raise ValueError("HANDOFF_INPUT: immutable raw evidence is required")
    payloads = {
        **files,
        MANIFEST: canonical_json_bytes(manifest.model_dump(mode="json", by_alias=True)),
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, content in sorted(payloads.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, content)
    result = output.getvalue()
    if len(result) > MAX_BYTES:
        raise ValueError("HANDOFF_BUDGET: encoded package exceeds the byte limit")
    return result
