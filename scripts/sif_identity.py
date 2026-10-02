#!/usr/bin/env python3
"""Fail-closed, write-free identity evaluation for publishable SIF artifacts.

This module deliberately has no registry mutation implementation.  Its CLI reads a
candidate and a registry snapshot, evaluates them, and reports the action that a
separately authorized publisher would take.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1"
BUILD_ID_ALGORITHM = "sha256"
BUILD_ID_HEX_LENGTH = 64
TAG_HASH_PREFIX_LENGTH = 24
IMMUTABLE_TAG_PREFIX = "sif-v1"
MOVING_ALIAS_MUTATION_DEFAULT = False

IDENTITY_MEDIA_TYPE = "application/vnd.omnibioai.sif.identity.v1+json"
SIF_MEDIA_TYPE = "application/vnd.sylabs.sif.layer.v1+octet-stream"

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_OCI_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")

_ARCH_ALIASES = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "x64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
}
_MOVING_ALIASES = {"latest", "stable", "amd64", "arm64"}

REQUIRED_CANDIDATE_FIELDS = {
    "schema_version",
    "tool_id",
    "scientific_version",
    "target_architecture",
    "source_commit",
    "dockerfile_path",
    "dockerfile_sha256",
    "base_image_identity",
    "oci_source_identity",
    "sif_sha256",
    "expected_executable",
    "version_evidence",
    "smoke_status",
    "provenance_status",
}
ALLOWED_CANDIDATE_FIELDS = REQUIRED_CANDIDATE_FIELDS | {
    "build_inputs",
    "operational_provenance",
    "build_identity_sha256",
}


class IdentityError(ValueError):
    """Candidate or registry identity evidence is invalid."""


class Decision(str, Enum):
    PUBLISH_NEW = "PUBLISH_NEW"
    SKIP_EXACT_MATCH = "SKIP_EXACT_MATCH"
    BLOCK_TAG_COLLISION = "BLOCK_TAG_COLLISION"
    BLOCK_IDENTITY_MISMATCH = "BLOCK_IDENTITY_MISMATCH"
    BLOCK_METADATA_MISSING = "BLOCK_METADATA_MISSING"
    BLOCK_LEGACY_CONFLICT = "BLOCK_LEGACY_CONFLICT"
    ERROR = "ERROR"


class LegacyStatus(str, Enum):
    ABSENT = "ABSENT"
    LEGACY_ARTIFACT = "LEGACY_ARTIFACT"
    LEGACY_ARTIFACT_EXACT_CONTENT = "LEGACY_ARTIFACT_EXACT_CONTENT"
    LEGACY_ARTIFACT_DIFFERENT_CONTENT = "LEGACY_ARTIFACT_DIFFERENT_CONTENT"


@dataclass(frozen=True)
class Evaluation:
    decision: Decision
    reason: str
    immutable_tag: str | None
    build_identity_sha256: str | None
    legacy_status: LegacyStatus = LegacyStatus.ABSENT
    legacy_tag_present: bool = False
    legacy_content_sha256: str | None = None
    existing_reference: str | None = None
    registry_write_performed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "immutable_tag": self.immutable_tag,
            "build_identity_sha256": self.build_identity_sha256,
            "legacy_status": self.legacy_status.value,
            "legacy_tag_present": self.legacy_tag_present,
            "legacy_content_sha256": self.legacy_content_sha256,
            "existing_reference": self.existing_reference,
            "registry_write_performed": self.registry_write_performed,
        }


def normalize_architecture(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityError("target architecture is missing")
    normalized = _ARCH_ALIASES.get(value.strip().lower())
    if normalized is None:
        raise IdentityError(f"unsupported target architecture: {value!r}")
    return normalized


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityError(f"{field} must be non-empty text")
    if any(ord(character) < 32 for character in value):
        raise IdentityError(f"{field} contains control characters")
    return value.strip()


def _require_sha256(value: Any, field: str) -> str:
    text = _require_text(value, field).lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if not _HEX_64.fullmatch(text):
        raise IdentityError(f"{field} must be a full SHA256 digest")
    return text


def _json_value(value: Any, field: str) -> Any:
    """Validate and normalize a value to the supported canonical JSON subset."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise IdentityError(f"{field} may not contain floating-point values")
    if isinstance(value, list):
        return [_json_value(item, field) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise IdentityError(f"{field} keys must be non-empty strings")
            result[key] = _json_value(item, field)
        return result
    raise IdentityError(f"{field} contains a non-JSON value")


def canonical_json_bytes(value: Any) -> bytes:
    normalized = _json_value(value, "canonical record")
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalize_dockerfile_path(value: Any) -> str:
    text = _require_text(value, "dockerfile_path").replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise IdentityError("dockerfile_path must be a repository-relative path")
    return str(path)


def normalize_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise IdentityError("candidate must be an object")
    missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(candidate))
    if missing:
        raise IdentityError("candidate is missing required fields: " + ", ".join(missing))
    unexpected = sorted(set(candidate) - ALLOWED_CANDIDATE_FIELDS)
    if unexpected:
        raise IdentityError("candidate has unknown fields: " + ", ".join(unexpected))

    schema = _require_text(candidate["schema_version"], "schema_version")
    if schema != SCHEMA_VERSION:
        raise IdentityError(f"unsupported schema_version: {schema}")
    tool_id = _require_text(candidate["tool_id"], "tool_id").lower()
    if not _TOOL_ID.fullmatch(tool_id):
        raise IdentityError("tool_id is not canonical")
    scientific_version = _require_text(candidate["scientific_version"], "scientific_version")
    architecture = normalize_architecture(candidate["target_architecture"])
    source_commit = _require_text(candidate["source_commit"], "source_commit").lower()
    if not _COMMIT.fullmatch(source_commit):
        raise IdentityError("source_commit must be a full 40-character Git commit")

    smoke_status = _require_text(candidate["smoke_status"], "smoke_status").upper()
    provenance_status = _require_text(candidate["provenance_status"], "provenance_status").upper()
    if smoke_status != "PASS" or provenance_status != "PASS":
        raise IdentityError("candidate smoke and provenance status must both be PASS")

    build_inputs = candidate.get("build_inputs", {})
    if not isinstance(build_inputs, Mapping):
        raise IdentityError("build_inputs must be an object")

    result = {
        "schema_version": schema,
        "tool_id": tool_id,
        "scientific_version": scientific_version,
        "target_architecture": architecture,
        "source_commit": source_commit,
        "dockerfile_path": _normalize_dockerfile_path(candidate["dockerfile_path"]),
        "dockerfile_sha256": _require_sha256(candidate["dockerfile_sha256"], "dockerfile_sha256"),
        "base_image_identity": _require_text(candidate["base_image_identity"], "base_image_identity"),
        "oci_source_identity": _require_text(candidate["oci_source_identity"], "oci_source_identity"),
        "sif_sha256": _require_sha256(candidate["sif_sha256"], "sif_sha256"),
        "expected_executable": _require_text(candidate["expected_executable"], "expected_executable"),
        "version_evidence": _require_text(candidate["version_evidence"], "version_evidence"),
        "smoke_status": smoke_status,
        "provenance_status": provenance_status,
        "build_inputs": _json_value(build_inputs, "build_inputs"),
        # Operational provenance is retained but never hashed into build identity.
        "operational_provenance": _json_value(
            candidate.get("operational_provenance", {}), "operational_provenance"
        ),
    }
    supplied_id = candidate.get("build_identity_sha256")
    if supplied_id is not None:
        result["build_identity_sha256"] = _require_sha256(
            supplied_id, "build_identity_sha256"
        )
    return result


def scientific_identity(candidate: Mapping[str, Any]) -> dict[str, str]:
    normalized = normalize_candidate(candidate)
    return {
        "tool_id": normalized["tool_id"],
        "scientific_version": normalized["scientific_version"],
    }


def build_identity_record(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_candidate(candidate)
    return {
        "schema_version": normalized["schema_version"],
        "tool_id": normalized["tool_id"],
        "scientific_version": normalized["scientific_version"],
        "target_architecture": normalized["target_architecture"],
        "source_commit": normalized["source_commit"],
        "dockerfile_path": normalized["dockerfile_path"],
        "dockerfile_sha256": normalized["dockerfile_sha256"],
        "base_image_identity": normalized["base_image_identity"],
        "oci_source_identity": normalized["oci_source_identity"],
        "build_inputs": normalized["build_inputs"],
    }


def build_identity_sha256(candidate: Mapping[str, Any]) -> str:
    record = build_identity_record(candidate)
    return hashlib.sha256(canonical_json_bytes(record)).hexdigest()


def content_identity(candidate: Mapping[str, Any]) -> str:
    return normalize_candidate(candidate)["sif_sha256"]


def identity_metadata(candidate: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_candidate(candidate)
    computed = build_identity_sha256(normalized)
    supplied = normalized.get("build_identity_sha256")
    if supplied is not None and supplied != computed:
        raise IdentityError("supplied build_identity_sha256 does not match canonical build inputs")
    normalized["build_identity_sha256"] = computed
    return normalized


def _tag_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    if not component:
        raise IdentityError("scientific_version cannot be represented in an OCI tag")
    return component


def immutable_tag(candidate: Mapping[str, Any]) -> str:
    metadata = identity_metadata(candidate)
    tag = "-".join(
        (
            IMMUTABLE_TAG_PREFIX,
            _tag_component(metadata["scientific_version"]),
            metadata["target_architecture"],
            metadata["build_identity_sha256"][:TAG_HASH_PREFIX_LENGTH],
        )
    )
    if not _OCI_TAG.fullmatch(tag):
        raise IdentityError("generated immutable tag is not a valid OCI tag")
    return tag


def identity_annotations(candidate: Mapping[str, Any]) -> dict[str, str]:
    metadata = identity_metadata(candidate)
    return {
        "org.opencontainers.image.version": metadata["scientific_version"],
        "org.opencontainers.image.revision": metadata["source_commit"],
        "org.opencontainers.image.architecture": metadata["target_architecture"],
        "org.omnibioai.sif.identity.schema": metadata["schema_version"],
        "org.omnibioai.sif.tool": metadata["tool_id"],
        "org.omnibioai.sif.dockerfile.sha256": metadata["dockerfile_sha256"],
        "org.omnibioai.sif.build.sha256": metadata["build_identity_sha256"],
        "org.omnibioai.sif.content.sha256": metadata["sif_sha256"],
    }


def _entries(registry_state: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    if not isinstance(registry_state, Mapping):
        raise IdentityError("registry state must be an object")
    entries = registry_state.get("artifacts", [])
    if not isinstance(entries, list):
        raise IdentityError("registry artifacts must be a list")
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("tag"), str):
            raise IdentityError("every registry artifact must have a text tag")
    return entries


def _entry_sif_sha256(entry: Mapping[str, Any]) -> str | None:
    value = entry.get("sif_sha256")
    if value is None:
        return None
    return _require_sha256(value, "registry sif_sha256")


def _looks_like_version_only(tag: str, scientific_version: str) -> bool:
    return tag == scientific_version or tag == _tag_component(scientific_version)


def _legacy_evidence(
    entries: Sequence[Mapping[str, Any]], architecture: str, candidate_sif: str
) -> tuple[LegacyStatus, bool, str | None]:
    legacy = next((entry for entry in entries if entry["tag"].lower() == architecture), None)
    if legacy is None:
        return LegacyStatus.ABSENT, False, None
    legacy_sha = _entry_sif_sha256(legacy)
    if legacy_sha is None:
        return LegacyStatus.LEGACY_ARTIFACT, True, None
    if legacy_sha == candidate_sif:
        return LegacyStatus.LEGACY_ARTIFACT_EXACT_CONTENT, True, legacy_sha
    return LegacyStatus.LEGACY_ARTIFACT_DIFFERENT_CONTENT, True, legacy_sha


def evaluate_publication(
    candidate: Mapping[str, Any], registry_state: Mapping[str, Any]
) -> Evaluation:
    """Evaluate publication without any registry-write capability."""
    try:
        metadata = identity_metadata(candidate)
        tag = immutable_tag(metadata)
        build_id = metadata["build_identity_sha256"]
        candidate_sif = metadata["sif_sha256"]
        entries = _entries(registry_state)
        legacy_status, legacy_present, legacy_sha = _legacy_evidence(
            entries, metadata["target_architecture"], candidate_sif
        )

        same_tag = [entry for entry in entries if entry["tag"] == tag]
        if len(same_tag) > 1:
            raise IdentityError("registry snapshot contains duplicate immutable tags")
        if same_tag:
            entry = same_tag[0]
            remote_raw = entry.get("identity_metadata")
            if remote_raw is None:
                return Evaluation(
                    Decision.BLOCK_METADATA_MISSING,
                    "immutable tag exists without retrievable identity metadata",
                    tag,
                    build_id,
                    legacy_status,
                    legacy_present,
                    legacy_sha,
                    tag,
                )
            if not isinstance(remote_raw, Mapping) or "build_identity_sha256" not in remote_raw:
                return Evaluation(
                    Decision.BLOCK_METADATA_MISSING,
                    "immutable tag metadata is missing the full build identity",
                    tag,
                    build_id,
                    legacy_status,
                    legacy_present,
                    legacy_sha,
                    tag,
                )
            try:
                remote = identity_metadata(remote_raw)
            except IdentityError as error:
                return Evaluation(
                    Decision.BLOCK_METADATA_MISSING,
                    f"immutable tag identity metadata is invalid: {error}",
                    tag,
                    build_id,
                    legacy_status,
                    legacy_present,
                    legacy_sha,
                    tag,
                )
            if remote["build_identity_sha256"] != build_id:
                return Evaluation(
                    Decision.BLOCK_TAG_COLLISION,
                    "immutable tag maps to a different full build identity",
                    tag,
                    build_id,
                    legacy_status,
                    legacy_present,
                    legacy_sha,
                    tag,
                )
            remote_sif = _entry_sif_sha256(entry) or remote["sif_sha256"]
            if remote_sif != candidate_sif or remote["sif_sha256"] != candidate_sif:
                return Evaluation(
                    Decision.BLOCK_IDENTITY_MISMATCH,
                    "matching build identity has different SIF content",
                    tag,
                    build_id,
                    legacy_status,
                    legacy_present,
                    legacy_sha,
                    tag,
                )
            return Evaluation(
                Decision.SKIP_EXACT_MATCH,
                "immutable tag, full build identity, and SIF content all match",
                tag,
                build_id,
                legacy_status,
                legacy_present,
                legacy_sha,
                tag,
            )

        # Search immutable references by full identity and exact content, not tag alone.
        for entry in entries:
            entry_tag = entry["tag"]
            if entry_tag.lower() in _MOVING_ALIASES or _looks_like_version_only(
                entry_tag, metadata["scientific_version"]
            ):
                continue
            remote_raw = entry.get("identity_metadata")
            if (
                not isinstance(remote_raw, Mapping)
                or "build_identity_sha256" not in remote_raw
            ):
                continue
            try:
                remote = identity_metadata(remote_raw)
            except IdentityError:
                continue
            if remote["build_identity_sha256"] != build_id:
                continue
            remote_sif = _entry_sif_sha256(entry) or remote["sif_sha256"]
            if remote_sif != candidate_sif or remote["sif_sha256"] != candidate_sif:
                return Evaluation(
                    Decision.BLOCK_IDENTITY_MISMATCH,
                    "full build identity exists under another tag with different SIF content",
                    tag,
                    build_id,
                    legacy_status,
                    legacy_present,
                    legacy_sha,
                    entry_tag,
                )
            return Evaluation(
                Decision.SKIP_EXACT_MATCH,
                "exact artifact already exists under another immutable reference",
                tag,
                build_id,
                legacy_status,
                legacy_present,
                legacy_sha,
                entry_tag,
            )

        return Evaluation(
            Decision.PUBLISH_NEW,
            "candidate immutable tag is absent and verified identity is new",
            tag,
            build_id,
            legacy_status,
            legacy_present,
            legacy_sha,
        )
    except (IdentityError, TypeError, KeyError) as error:
        return Evaluation(Decision.ERROR, str(error), None, None)


def _load_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IdentityError(f"cannot read {path}: {error}") from error
    if not isinstance(value, Mapping):
        raise IdentityError(f"{path} must contain a JSON object")
    return value


def _print_result(candidate: Mapping[str, Any], result: Evaluation) -> None:
    try:
        metadata = identity_metadata(candidate)
    except IdentityError:
        metadata = {}
    fields = {
        "TOOL": metadata.get("tool_id", "UNKNOWN"),
        "ARCH": metadata.get("target_architecture", "UNKNOWN"),
        "SCIENTIFIC_VERSION": metadata.get("scientific_version", "UNKNOWN"),
        "BUILD_ID": result.build_identity_sha256 or "UNKNOWN",
        "IMMUTABLE_TAG": result.immutable_tag or "UNKNOWN",
        "SIF_SHA256": metadata.get("sif_sha256", "UNKNOWN"),
        "LEGACY_TAG_PRESENT": "YES" if result.legacy_tag_present else "NO",
        "LEGACY_CONTENT_SHA256": result.legacy_content_sha256 or "UNKNOWN",
        "LEGACY_STATUS": result.legacy_status.value,
        "IMMUTABLE_TAG_PRESENT": "YES" if result.existing_reference == result.immutable_tag else "NO",
        "DECISION": result.decision.value,
        "REASON": result.reason,
        "REGISTRY_WRITE_PERFORMED": "NO",
    }
    for key, value in fields.items():
        print(f"{key}={value}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--registry-state", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        candidate = _load_object(args.candidate)
        state = _load_object(args.registry_state)
    except IdentityError as error:
        print(f"DECISION={Decision.ERROR.value}")
        print(f"REASON={error}")
        print("REGISTRY_WRITE_PERFORMED=NO")
        return 2
    result = evaluate_publication(candidate, state)
    _print_result(candidate, result)
    return 0 if result.decision in {Decision.PUBLISH_NEW, Decision.SKIP_EXACT_MATCH} else 2


if __name__ == "__main__":
    sys.exit(main())
