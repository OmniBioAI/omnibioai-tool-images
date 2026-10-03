#!/usr/bin/env python3
"""Manifest-bound immutable SIF release engine.

The engine preserves the Bedtools canary semantics while taking every
tool-specific value from the reviewed pilot manifest.  Registry writes are
possible only through ``release_candidate(..., publish_enabled=True)``; the
default and the CLI default are read-only dry-run mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.pilot_integrity import validate_evidence
from scripts.pilot_manifest import get_tool
from scripts.pilot_probe import fixtures, validate_probe_result
from scripts.sif_identity import (
    IDENTITY_MEDIA_TYPE,
    SIF_MEDIA_TYPE,
    Decision,
    canonical_json_bytes,
    evaluate_publication,
    identity_annotations,
    identity_metadata,
    immutable_tag,
)


ALLOWED_ARCHITECTURES = ("amd64", "arm64")
ARTIFACT_TYPE = "application/vnd.omnibioai.sif.v1"
RUNTIME_COMMIT = "a4f3da56c2e6e9fd85e54ba009f6f3456de3b5b5"
SBOM_STATUSES = {"SBOM_OPTIONAL_NOT_GENERATED", "SBOM_PASS"}


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseSpec:
    tool_id: str
    dockerfile: str
    expected_executable: str
    repository: str
    scientific_version_pattern: str
    package_identity_kind: str
    package_identity_name: str

    @property
    def moving_aliases(self) -> frozenset[str]:
        # The version-only alias is checked after resolving the candidate.
        return frozenset(("arm64", "amd64", "latest", "stable"))


def release_spec(tool_id: str) -> ReleaseSpec:
    entry = get_tool(tool_id)
    package_identity = entry["package_identity"]
    return ReleaseSpec(
        tool_id=entry["tool_id"],
        dockerfile=entry["dockerfile"],
        expected_executable=entry["expected_executable"],
        repository=entry["ghcr_repository"],
        scientific_version_pattern=entry["scientific_version_pattern"],
        package_identity_kind=package_identity["kind"],
        package_identity_name=package_identity["name"],
    )


def resolve_scientific_version(entry: Mapping[str, Any], version_output: str) -> str:
    if not isinstance(version_output, str) or not version_output.strip():
        raise ReleaseError("scientific version output is missing or empty")
    pattern_text = entry.get("scientific_version_pattern")
    if not isinstance(pattern_text, str) or not pattern_text:
        raise ReleaseError("scientific version pattern is missing")
    try:
        pattern = re.compile(pattern_text)
    except re.error as error:
        raise ReleaseError("scientific version pattern is malformed") from error
    if "version" not in pattern.groupindex:
        raise ReleaseError("scientific version pattern lacks named version group")
    versions = {match.group("version") for match in pattern.finditer(version_output)}
    if len(versions) != 1:
        raise ReleaseError("scientific version cannot be resolved unambiguously")
    version = versions.pop()
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+_~-]*", version) or "/" in version:
        raise ReleaseError("resolved scientific version is not canonical")
    return version


# Ordered most-specific-first; the first matching classification wins.
_FAILURE_CLASSIFIERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ORAS_AUTH_FAILURE", ("unauthorized", "authentication required")),
    ("ORAS_PERMISSION_FAILURE", ("denied", "permission_denied", "insufficient_scope", "forbidden")),
    ("ORAS_REFERENCE_FAILURE", ("manifest unknown", "name unknown", "not found", "invalid reference")),
    ("ORAS_MANIFEST_OR_MEDIA_FAILURE", ("unsupported media type", "manifest invalid", "manifest_invalid", "blob unknown")),
    ("ORAS_NETWORK_FAILURE", ("dial tcp", "connection refused", "i/o timeout", "no such host", "tls handshake", "temporary failure in name resolution")),
)

_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED]"),
    (re.compile(r'(?i)("auth"\s*:\s*")[A-Za-z0-9+/=]+(")'), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)\b(password|token|secret)\b\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
)


def _sanitize(text: str) -> str:
    try:
        sanitized = text
        for pattern, replacement in _REDACTION_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized
    except Exception:
        return "[OUTPUT UNAVAILABLE: sanitization failed closed]"


def _classify_oras_failure(stdout: str, stderr: str) -> str:
    combined = f" {stdout}\n{stderr} ".lower()
    for classification, markers in _FAILURE_CLASSIFIERS:
        if any(marker in combined for marker in markers):
            return classification
    return "ORAS_UNKNOWN_FAILURE"


class OrasInvocationFailed(ReleaseError):
    def __init__(
        self, argv: Sequence[str], returncode: int, stdout: str, stderr: str, classification: str
    ) -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.classification = classification
        super().__init__(
            f"ORAS_INVOCATION_FAILED classification={classification} "
            f"exit_code={returncode} command={' '.join(self.argv)}\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )


def run(argv: Sequence[str], *, text: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        list(argv), check=False, capture_output=True, text=text,
        errors="replace" if text else None,
    )
    if completed.returncode != 0:
        if text:
            stdout = _sanitize(completed.stdout or "")
            stderr = _sanitize(completed.stderr or "")
        else:
            stdout = "<binary output not captured>"
            stderr = "<binary output not captured>"
        raise OrasInvocationFailed(
            argv, completed.returncode, stdout, stderr,
            _classify_oras_failure(stdout, stderr),
        )
    return completed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_contained_staging_file(path: Path, staging_root: Path) -> Path:
    resolved_root = staging_root.resolve(strict=True)
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise ReleaseError(f"staging path does not resolve to an existing file: {path}") from error
    if not resolved_path.is_file():
        raise ReleaseError(f"staging path is not a regular file: {path}")
    if not resolved_path.is_relative_to(resolved_root):
        raise ReleaseError(
            f"staging path escapes the canary staging root: {path} -> {resolved_path} "
            f"(root {resolved_root})"
        )
    return resolved_path


def validate_release_provenance(record: Mapping[str, Any], spec: ReleaseSpec) -> str:
    validate_evidence(dict(record))
    if record.get("tool") != spec.tool_id or record.get("dockerfile") != spec.dockerfile:
        raise ReleaseError("provenance tool/Dockerfile differs from release specification")
    if record.get("target_architecture") not in ALLOWED_ARCHITECTURES:
        raise ReleaseError("provenance architecture is not supported")
    base_identity = record.get("base_image_identity")
    if (not isinstance(base_identity, str) or not base_identity.startswith("docker.io/")
            or not re.search(r"@sha256:[0-9a-f]{64}$", base_identity)):
        raise ReleaseError("digest-bound base image evidence is missing or malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("rendered_dockerfile_sha256", ""))):
        raise ReleaseError("rendered Dockerfile identity is missing or malformed")
    if record.get("oci_child_digest") != record.get("oci_identity"):
        raise ReleaseError("architecture-specific OCI child identity is missing or mismatched")
    package_identity = record.get("package_identity")
    if not isinstance(package_identity, str) or not package_identity.startswith(spec.package_identity_name + "="):
        raise ReleaseError("package identity is missing or does not match the manifest")
    version_outputs = (
        record.get("oci_version", {}).get("meaningful_output"),
        record.get("sif_version", {}).get("meaningful_output"),
    )
    versions = [resolve_scientific_version(get_tool(spec.tool_id), output) for output in version_outputs]
    if len(set(versions)) != 1 or record.get("scientific_version") != versions[0]:
        raise ReleaseError("OCI/SIF scientific version evidence differs or is missing")
    sbom_status = record.get("sbom_status")
    if sbom_status not in SBOM_STATUSES:
        raise ReleaseError("SBOM status is missing, empty, or failed")
    if sbom_status == "SBOM_PASS" and not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sbom_sha256", ""))):
        raise ReleaseError("SBOM_PASS requires a non-empty valid SBOM SHA256")
    return versions[0]


def prepare_candidate_for_spec(
    spec: ReleaseSpec, provenance_path: Path, sif: Path, output: Path
) -> dict[str, Any]:
    provenance = json.loads(provenance_path.read_text())
    scientific_version = validate_release_provenance(provenance, spec)
    if provenance["sif_sha256"] != sha256_file(sif):
        raise ReleaseError("candidate SIF differs from validated provenance")
    provenance_sha = sha256_file(provenance_path)
    candidate = {
        "schema_version": "1",
        "tool_id": spec.tool_id,
        "scientific_version": scientific_version,
        "target_architecture": provenance["target_architecture"],
        "source_commit": provenance["source_commit_sha"],
        "dockerfile_path": provenance["dockerfile"],
        "dockerfile_sha256": provenance["dockerfile_sha256"],
        "base_image_identity": provenance["base_image_identity"],
        "oci_source_identity": provenance["oci_child_digest"],
        "sif_sha256": provenance["sif_sha256"],
        "expected_executable": spec.expected_executable,
        "version_evidence": provenance["sif_version"]["output"],
        "smoke_status": provenance["sif_smoke"]["status"],
        "provenance_status": provenance["verification_result"],
        "build_inputs": {
            "package_identity": provenance["package_identity"],
            "rendered_dockerfile_sha256": provenance["rendered_dockerfile_sha256"],
            "tool_runtime_commit": RUNTIME_COMMIT,
        },
        "operational_provenance": {
            "github_workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
            "github_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "workflow_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
            "runner_os": provenance["runner"]["os"],
            "runner_architecture": provenance["runner"]["arch"],
            "uname_m": provenance["uname_m"],
            "provenance_sha256": provenance_sha,
        },
    }
    metadata = identity_metadata(candidate)
    output.write_bytes(canonical_json_bytes(metadata) + b"\n")
    return metadata


def prepare_candidate(tool_id: str, provenance_path: Path, sif: Path, output: Path) -> dict[str, Any]:
    return prepare_candidate_for_spec(release_spec(tool_id), provenance_path, sif, output)


def _not_found(completed: subprocess.CompletedProcess) -> bool:
    error = (completed.stderr or "").lower()
    return any(marker in error for marker in ("not found", "404", "manifest_unknown", "name_unknown"))


def inspect_reference(
    repository: str, tag: str, *, run_fn: Callable[..., subprocess.CompletedProcess] = run
) -> dict[str, Any] | None:
    completed = subprocess.run(
        ["oras", "manifest", "fetch", f"{repository}:{tag}"],
        check=False, capture_output=True, text=True, errors="replace",
    )
    if completed.returncode:
        if _not_found(completed):
            return None
        stderr = _sanitize(completed.stderr or "")
        raise ReleaseError(f"registry inspection failed for {tag}: {stderr}")
    try:
        manifest = json.loads(completed.stdout)
        descriptor = run_fn(["oras", "manifest", "fetch", "--descriptor", f"{repository}:{tag}"])
        manifest_descriptor = json.loads(descriptor.stdout)
    except (json.JSONDecodeError, KeyError) as error:
        raise ReleaseError("registry manifest/descriptor metadata is malformed") from error
    result: dict[str, Any] = {
        "tag": tag,
        "manifest_digest": manifest_descriptor["digest"],
        "manifest": manifest,
    }
    for layer in manifest.get("layers", []):
        digest = layer.get("digest", "")
        media_type = layer.get("mediaType")
        title = (layer.get("annotations") or {}).get("org.opencontainers.image.title", "")
        if media_type == IDENTITY_MEDIA_TYPE:
            blob = run_fn(["oras", "blob", "fetch", "--output", "-", f"{repository}@{digest}"])
            try:
                result["identity_metadata"] = json.loads(blob.stdout)
            except json.JSONDecodeError as error:
                raise ReleaseError("registry identity metadata is malformed") from error
            result["identity_metadata_digest"] = digest
        if media_type == SIF_MEDIA_TYPE or title.endswith(".sif"):
            result["sif_sha256"] = digest.removeprefix("sha256:")
            result["sif_layer_digest"] = digest
    return result


def registry_snapshot(
    spec: ReleaseSpec,
    candidate: Mapping[str, Any] | None = None,
    *,
    inspect_fn: Callable[[str, str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    inspect_fn = inspect_fn or inspect_reference
    completed = subprocess.run(
        ["oras", "repo", "tags", spec.repository],
        check=False, capture_output=True, text=True, errors="replace",
    )
    if completed.returncode:
        if _not_found(completed):
            tags: list[str] = []
        else:
            raise ReleaseError(f"registry tag listing failed: {_sanitize(completed.stderr or '')}")
    else:
        tags = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    relevant = set(tags)
    if candidate is not None:
        relevant.add(immutable_tag(candidate))
    artifacts = []
    for tag in sorted(relevant):
        artifact = inspect_fn(spec.repository, tag)
        if artifact is not None:
            artifacts.append(artifact)
    return {"repository": spec.repository, "tags": sorted(tags), "artifacts": artifacts}


def evaluate_live_for_spec(
    spec: ReleaseSpec, candidate: Mapping[str, Any]
) -> tuple[dict[str, Any], Any]:
    if identity_metadata(candidate)["tool_id"] != spec.tool_id:
        raise ReleaseError("candidate tool does not match exact package specification")
    state = registry_snapshot(spec, candidate)
    return state, evaluate_publication(candidate, state)


def capture_baseline(tool_id: str, output: Path) -> dict[str, Any]:
    state = registry_snapshot(release_spec(tool_id))
    output.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return state


def _would(decision: Decision) -> str:
    return "WOULD_" + decision.value


def release_candidate_for_spec(
    spec: ReleaseSpec,
    candidate_path: Path,
    sif: Path,
    output: Path,
    *,
    publish_enabled: bool = False,
    evaluate_live_fn: Callable[[Mapping[str, Any]], tuple[dict[str, Any], Any]] | None = None,
    inspect_reference_fn: Callable[[str, str], dict[str, Any] | None] = inspect_reference,
    run_fn: Callable[..., subprocess.CompletedProcess] = run,
    contain_fn: Callable[[Path, Path], Path] = require_contained_staging_file,
    immutable_tag_fn: Callable[[Mapping[str, Any]], str] = immutable_tag,
    staging_prefix: str = "omnibioai-sif-release-",
) -> dict[str, Any]:
    candidate = json.loads(candidate_path.read_text())
    metadata = identity_metadata(candidate)
    if metadata["tool_id"] != spec.tool_id or metadata["target_architecture"] not in ALLOWED_ARCHITECTURES:
        raise ReleaseError("publication escaped manifest tool/architecture containment")
    if spec.repository != get_tool(spec.tool_id)["ghcr_repository"]:
        raise ReleaseError("publication repository differs from the canonical manifest mapping")
    tag = immutable_tag_fn(metadata)
    moving_aliases = spec.moving_aliases | {metadata["scientific_version"]}
    if tag in moving_aliases or not tag.startswith("sif-v1-"):
        raise ReleaseError("refusing moving, version-only, or non-immutable tag")
    if sha256_file(sif) != metadata["sif_sha256"]:
        raise ReleaseError("SIF content changed after identity creation")
    evaluate_live_fn = evaluate_live_fn or (lambda value: evaluate_live_for_spec(spec, value))
    state, result = evaluate_live_fn(metadata)
    print(json.dumps({"candidate": metadata, "tag": tag, "decision": result.as_dict()}, sort_keys=True))

    if not publish_enabled:
        record = {
            "dry_run": True,
            "write_performed": False,
            "repository": spec.repository,
            "reference": f"{spec.repository}:{tag}",
            "decision": _would(result.decision),
            "decision_detail": result.as_dict(),
            "registry_state": state,
        }
        output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return record

    if result.decision is Decision.SKIP_EXACT_MATCH:
        existing = inspect_reference_fn(spec.repository, result.existing_reference or tag)
        if existing is None:
            raise ReleaseError("exact-match reference disappeared")
        record = {"dry_run": False, "write_performed": False,
                  "reference": result.existing_reference, "registry": existing}
        output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return record
    if result.decision is not Decision.PUBLISH_NEW:
        raise ReleaseError(f"publication blocked: {result.decision.value}: {result.reason}")

    annotations = identity_annotations(metadata)
    with tempfile.TemporaryDirectory(prefix=staging_prefix) as directory:
        staging = Path(directory)
        staged_sif = staging / f"{spec.tool_id}-{metadata['target_architecture']}.sif"
        staged_identity = staging / "identity.json"
        staged_sif.write_bytes(sif.read_bytes())
        staged_identity.write_bytes(canonical_json_bytes(metadata) + b"\n")
        contained_sif = contain_fn(staged_sif, staging)
        contained_identity = contain_fn(staged_identity, staging)
        manifest_path = staging / "published-manifest.json"
        command = [
            "oras", "push", "--artifact-type", ARTIFACT_TYPE,
            "--artifact-platform", f"linux/{metadata['target_architecture']}",
            "--export-manifest", str(manifest_path),
            "--disable-path-validation",
        ]
        for key, value in sorted(annotations.items()):
            command.extend(("--annotation", f"{key}={value}"))
        command.extend((
            f"{spec.repository}:{tag}",
            f"{contained_sif}:{SIF_MEDIA_TYPE}",
            f"{contained_identity}:{IDENTITY_MEDIA_TYPE}",
        ))
        run_fn(command)
        digest = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    remote = inspect_reference_fn(spec.repository, tag)
    if remote is None or remote.get("manifest_digest") != digest:
        raise ReleaseError("published manifest cannot be independently resolved by digest")
    record = {"dry_run": False, "write_performed": True,
              "reference": f"{spec.repository}:{tag}", "registry": remote}
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def release_candidate(
    tool_id: str, candidate_path: Path, sif: Path, output: Path, *, publish_enabled: bool = False
) -> dict[str, Any]:
    return release_candidate_for_spec(
        release_spec(tool_id), candidate_path, sif, output, publish_enabled=publish_enabled
    )


def verify_legacy_preserved(
    baseline: Mapping[str, Any], current: Mapping[str, Any], candidate_tag: str
) -> None:
    if baseline.get("repository") != current.get("repository"):
        raise ReleaseError("registry repository changed during legacy comparison")
    before = {entry["tag"]: entry.get("manifest_digest") for entry in baseline.get("artifacts", [])}
    after = {entry["tag"]: entry.get("manifest_digest") for entry in current.get("artifacts", [])}
    if any(tag not in after or after[tag] != digest for tag, digest in before.items()):
        raise ReleaseError("pre-existing registry reference was deleted or mutated")
    unexpected = set(after) - set(before) - {candidate_tag}
    if unexpected:
        raise ReleaseError("unexpected registry references appeared during publication")


def retrieve_and_verify_for_spec(
    spec: ReleaseSpec,
    candidate_path: Path,
    destination: Path,
    output: Path,
    *,
    baseline_path: Path | None = None,
    required_legacy_tags: Mapping[str, str] | None = None,
    inspect_reference_fn: Callable[[str, str], dict[str, Any] | None] = inspect_reference,
    evaluate_live_fn: Callable[[Mapping[str, Any]], tuple[dict[str, Any], Any]] | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess] = run,
) -> dict[str, Any]:
    candidate = identity_metadata(json.loads(candidate_path.read_text()))
    if candidate["tool_id"] != spec.tool_id:
        raise ReleaseError("retrieval candidate differs from release specification")
    tag = immutable_tag(candidate)
    remote = inspect_reference_fn(spec.repository, tag)
    if remote is None:
        raise ReleaseError("published immutable reference is absent")
    annotations = (remote.get("manifest") or {}).get("annotations") or {}
    if any(annotations.get(key) != value for key, value in identity_annotations(candidate).items()):
        raise ReleaseError("retrieved manifest annotations differ from candidate identity")
    if remote.get("identity_metadata") != candidate:
        raise ReleaseError("retrieved identity JSON differs from candidate")
    if remote.get("sif_sha256") != candidate["sif_sha256"]:
        raise ReleaseError("retrieved SIF descriptor differs from candidate content identity")
    layer_digest = remote.get("sif_layer_digest")
    if not isinstance(layer_digest, str) or not layer_digest.startswith("sha256:"):
        raise ReleaseError("retrieved SIF layer digest is missing")
    run_fn(["oras", "blob", "fetch", "--output", str(destination),
            f"{spec.repository}@{layer_digest}"])
    if sha256_file(destination) != candidate["sif_sha256"]:
        raise ReleaseError("retrieved SIF bytes differ from candidate")
    evaluate_live_fn = evaluate_live_fn or (lambda value: evaluate_live_for_spec(spec, value))
    state, decision = evaluate_live_fn(candidate)
    if decision.decision is not Decision.SKIP_EXACT_MATCH:
        raise ReleaseError(f"post-publication idempotency failed: {decision.decision.value}")
    if baseline_path is not None:
        verify_legacy_preserved(json.loads(baseline_path.read_text()), state, tag)
    for legacy_tag, expected_sha in (required_legacy_tags or {}).items():
        legacy = next((entry for entry in state.get("artifacts", []) if entry.get("tag") == legacy_tag), None)
        if legacy is None or legacy.get("sif_sha256") != expected_sha:
            raise ReleaseError(f"required legacy {legacy_tag} content identity changed")
    record = {
        "reference": f"{spec.repository}:{tag}",
        "manifest_digest": remote["manifest_digest"],
        "identity_metadata_digest": remote["identity_metadata_digest"],
        "sif_sha256": sha256_file(destination),
        "identity_metadata_match": True,
        "provenance_match": True,
        "post_publication_decision": decision.decision.value,
        "registry_state": state,
    }
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def retrieve_and_verify(
    tool_id: str, candidate_path: Path, destination: Path, output: Path,
    *, baseline_path: Path | None = None,
) -> dict[str, Any]:
    return retrieve_and_verify_for_spec(
        release_spec(tool_id), candidate_path, destination, output, baseline_path=baseline_path
    )


def verify_retrieved_runtime_for_spec(
    spec: ReleaseSpec, candidate_path: Path, sif: Path, workspace: Path, output: Path,
    *, run_fn: Callable[..., subprocess.CompletedProcess] = run,
) -> dict[str, Any]:
    candidate = identity_metadata(json.loads(candidate_path.read_text()))
    if candidate["tool_id"] != spec.tool_id or sha256_file(sif) != candidate["sif_sha256"]:
        raise ReleaseError("retrieved SIF identity differs before runtime verification")
    tool = get_tool(spec.tool_id)
    workspace.mkdir(parents=True, exist_ok=True)
    fixtures(workspace)
    entry = workspace / "entry.json"
    entry.write_text(json.dumps(tool, sort_keys=True))
    inspect = run_fn(["sudo", "apptainer", "inspect", "--json", str(sif)])
    try:
        inspect_metadata = json.loads(inspect.stdout)
    except json.JSONDecodeError as error:
        raise ReleaseError("retrieved SIF inspect metadata is malformed") from error
    if not inspect_metadata:
        raise ReleaseError("retrieved SIF inspect metadata is empty")
    probe_output = workspace / "retrieved-sif-probe.json"
    run_fn([
        "sudo", "apptainer", "exec", "--cleanenv",
        "--bind", f"{workspace}:/pilot/work",
        "--bind", f"{Path.cwd() / 'scripts/pilot_probe.py'}:/pilot/pilot_probe.py:ro",
        "--bind", f"{entry}:/pilot/entry.json:ro",
        str(sif), "python3", "/pilot/pilot_probe.py", "--entry", "/pilot/entry.json",
        "--arch", candidate["target_architecture"], "--phase", "sif",
        "--workspace", "/pilot/work", "--output", "/pilot/work/retrieved-sif-probe.json",
    ])
    probe = json.loads(probe_output.read_text())
    validate_probe_result(probe, tool, candidate["target_architecture"], "sif")
    version = resolve_scientific_version(tool, probe["version"]["meaningful_output"])
    if version != candidate["scientific_version"]:
        raise ReleaseError("retrieved SIF scientific version differs from candidate")
    result = {
        "runtime_architecture": probe["architecture"],
        "executable": probe["executable"],
        "version": probe["version"],
        "smoke": probe["smoke"],
        "scientific_version": version,
        "status": "PASS",
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def verify_retrieved_runtime(
    tool_id: str, candidate_path: Path, sif: Path, workspace: Path, output: Path
) -> dict[str, Any]:
    return verify_retrieved_runtime_for_spec(
        release_spec(tool_id), candidate_path, sif, workspace, output
    )


def _bool(value: str) -> bool:
    if value not in ("true", "false"):
        raise argparse.ArgumentTypeError("expected true or false")
    return value == "true"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--tool", required=True)
    prepare.add_argument("--provenance", required=True, type=Path)
    prepare.add_argument("--sif", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    baseline = sub.add_parser("baseline")
    baseline.add_argument("--tool", required=True)
    baseline.add_argument("--output", required=True, type=Path)
    release = sub.add_parser("release")
    release.add_argument("--tool", required=True)
    release.add_argument("--candidate", required=True, type=Path)
    release.add_argument("--sif", required=True, type=Path)
    release.add_argument("--output", required=True, type=Path)
    release.add_argument("--publish", type=_bool, default=False)
    retrieve = sub.add_parser("retrieve")
    retrieve.add_argument("--tool", required=True)
    retrieve.add_argument("--candidate", required=True, type=Path)
    retrieve.add_argument("--destination", required=True, type=Path)
    retrieve.add_argument("--output", required=True, type=Path)
    retrieve.add_argument("--baseline", type=Path)
    runtime = sub.add_parser("runtime")
    runtime.add_argument("--tool", required=True)
    runtime.add_argument("--candidate", required=True, type=Path)
    runtime.add_argument("--sif", required=True, type=Path)
    runtime.add_argument("--workspace", required=True, type=Path)
    runtime.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            value = prepare_candidate(args.tool, args.provenance, args.sif, args.output)
            print(json.dumps({"candidate": value, "immutable_tag": immutable_tag(value)}, sort_keys=True))
        elif args.command == "baseline":
            capture_baseline(args.tool, args.output)
        elif args.command == "release":
            release_candidate(
                args.tool, args.candidate, args.sif, args.output, publish_enabled=args.publish
            )
        elif args.command == "retrieve":
            retrieve_and_verify(
                args.tool, args.candidate, args.destination, args.output,
                baseline_path=args.baseline,
            )
        else:
            verify_retrieved_runtime(
                args.tool, args.candidate, args.sif, args.workspace, args.output
            )
        return 0
    except Exception as error:
        print(f"RELEASE_FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
