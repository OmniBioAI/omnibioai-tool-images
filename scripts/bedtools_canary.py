#!/usr/bin/env python3
"""Structurally contained immutable Bedtools GHCR publication canary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

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


TOOL = "bedtools"
SCIENTIFIC_VERSION = "2.30.0"
REPOSITORY = "ghcr.io/omnibioai/omnibioai-sif/bedtools"
ALLOWED_ARCHITECTURES = ("amd64", "arm64")
MOVING_ALIASES = frozenset(("arm64", "amd64", "latest", "stable", SCIENTIFIC_VERSION))
ARTIFACT_TYPE = "application/vnd.omnibioai.sif.v1"
RUNTIME_COMMIT = "a4f3da56c2e6e9fd85e54ba009f6f3456de3b5b5"
LEGACY_ARM64_SHA256 = "50d024aacfcd5caa1da804792dcc5cba6f002295d0ba4802ff8bf3fd67756fde"


class CanaryError(RuntimeError):
    pass


# Ordered most-specific-first; the first matching classification wins.
_FAILURE_CLASSIFIERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ORAS_AUTH_FAILURE", ("unauthorized", "authentication required")),
    ("ORAS_PERMISSION_FAILURE", ("denied", "permission_denied", "insufficient_scope", "forbidden")),
    ("ORAS_REFERENCE_FAILURE", ("manifest unknown", "name unknown", "not found", "invalid reference")),
    ("ORAS_MANIFEST_OR_MEDIA_FAILURE", ("unsupported media type", "manifest invalid", "manifest_invalid", "blob unknown")),
    ("ORAS_NETWORK_FAILURE", ("dial tcp", "connection refused", "i/o timeout", "no such host", "tls handshake", "temporary failure in name resolution")),
)

# Defense-in-depth redaction of credential-bearing material that should never
# appear in ORAS stdout/stderr under normal operation, but must never reach a
# log if it somehow does.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED]"),
    (re.compile(r'(?i)("auth"\s*:\s*")[A-Za-z0-9+/=]+(")'), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)\b(password|token|secret)\b\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
)


def _sanitize(text: str) -> str:
    """Redact recognizable credential-bearing material from captured output.

    Returns a fixed safe placeholder instead of raising if sanitization
    itself fails for any reason, so a defect here can never leak raw output.
    """
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


class OrasInvocationFailed(CanaryError):
    """An ORAS subprocess exited non-zero.

    Carries the sanitized diagnostics as attributes (for tests/tools that
    want structured access) in addition to a human-readable message (so the
    existing generic `except Exception` handler in main() surfaces them
    without any further changes).
    """

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
        list(argv),
        check=False,
        capture_output=True,
        text=text,
        errors="replace" if text else None,
    )
    if completed.returncode != 0:
        if text:
            stdout = _sanitize(completed.stdout or "")
            stderr = _sanitize(completed.stderr or "")
        else:
            stdout = "<binary output not captured>"
            stderr = "<binary output not captured>"
        classification = _classify_oras_failure(stdout, stderr)
        raise OrasInvocationFailed(argv, completed.returncode, stdout, stderr, classification)
    return completed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_candidate(provenance_path: Path, sif: Path, output: Path) -> dict[str, Any]:
    provenance = json.loads(provenance_path.read_text())
    validate_evidence(provenance)
    if provenance["tool"] != TOOL or provenance["target_architecture"] not in ALLOWED_ARCHITECTURES:
        raise CanaryError("canary accepts only bedtools amd64/arm64 provenance")
    if provenance["sif_sha256"] != sha256_file(sif):
        raise CanaryError("candidate SIF differs from validated provenance")
    if provenance["dockerfile"] != "dockerfiles/Dockerfile.bedtools":
        raise CanaryError("unexpected Bedtools Dockerfile")
    if not provenance.get("base_image_identity") or not provenance.get("rendered_dockerfile_sha256"):
        raise CanaryError("digest-bound base image evidence is missing")
    if not provenance.get("package_identity", "").startswith("bedtools=2.30.0"):
        raise CanaryError("resolved Bedtools package does not preserve version 2.30.0")
    version = provenance["sif_version"]["output"]
    if "bedtools v2.30.0" not in version:
        raise CanaryError("SIF version evidence is not Bedtools 2.30.0")
    provenance_sha = sha256_file(provenance_path)
    candidate = {
        "schema_version": "1",
        "tool_id": TOOL,
        "scientific_version": SCIENTIFIC_VERSION,
        "target_architecture": provenance["target_architecture"],
        "source_commit": provenance["source_commit_sha"],
        "dockerfile_path": provenance["dockerfile"],
        "dockerfile_sha256": provenance["dockerfile_sha256"],
        "base_image_identity": provenance["base_image_identity"],
        "oci_source_identity": provenance["oci_identity"],
        "sif_sha256": provenance["sif_sha256"],
        "expected_executable": "bedtools",
        "version_evidence": version,
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


def _not_found(completed: subprocess.CompletedProcess) -> bool:
    error = (completed.stderr or "").lower()
    return any(marker in error for marker in ("not found", "404", "manifest_unknown", "name_unknown"))


def inspect_reference(repository: str, tag: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        ["oras", "manifest", "fetch", f"{repository}:{tag}"],
        check=False, capture_output=True, text=True,
    )
    if completed.returncode:
        if _not_found(completed):
            return None
        raise CanaryError(f"registry inspection failed for {tag}: {completed.stderr.strip()}")
    manifest = json.loads(completed.stdout)
    descriptor = run(["oras", "manifest", "fetch", "--descriptor", f"{repository}:{tag}"])
    manifest_descriptor = json.loads(descriptor.stdout)
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
            blob = run(["oras", "blob", "fetch", "--output", "-", f"{repository}@{digest}"])
            result["identity_metadata"] = json.loads(blob.stdout)
            result["identity_metadata_digest"] = digest
        if media_type == SIF_MEDIA_TYPE or title.endswith(".sif"):
            result["sif_sha256"] = digest.removeprefix("sha256:")
            result["sif_layer_digest"] = digest
    return result


def registry_snapshot(repository: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
    if repository != REPOSITORY:
        raise CanaryError("canary repository is not the authoritative Bedtools package")
    completed = subprocess.run(
        ["oras", "repo", "tags", repository], check=False, capture_output=True, text=True
    )
    if completed.returncode:
        if _not_found(completed):
            tags: list[str] = []
        else:
            raise CanaryError(f"registry tag listing failed: {completed.stderr.strip()}")
    else:
        tags = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    candidate_tag = immutable_tag(candidate)
    relevant = sorted(set(tags) | {candidate_tag})
    artifacts = []
    for tag in relevant:
        artifact = inspect_reference(repository, tag)
        if artifact is not None:
            artifacts.append(artifact)
    return {"repository": repository, "tags": tags, "artifacts": artifacts}


def evaluate_live(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], Any]:
    state = registry_snapshot(REPOSITORY, candidate)
    result = evaluate_publication(candidate, state)
    return state, result


def publish(candidate_path: Path, sif: Path, output: Path) -> dict[str, Any]:
    candidate = json.loads(candidate_path.read_text())
    metadata = identity_metadata(candidate)
    if metadata["tool_id"] != TOOL or metadata["target_architecture"] not in ALLOWED_ARCHITECTURES:
        raise CanaryError("publication escaped Bedtools architecture containment")
    tag = immutable_tag(metadata)
    if tag in MOVING_ALIASES or not tag.startswith("sif-v1-"):
        raise CanaryError("refusing moving, version-only, or non-immutable tag")
    if sha256_file(sif) != metadata["sif_sha256"]:
        raise CanaryError("SIF content changed after identity creation")
    state, result = evaluate_live(metadata)
    print(json.dumps({"candidate": metadata, "tag": tag, "decision": result.as_dict()}, sort_keys=True))
    if result.decision is Decision.SKIP_EXACT_MATCH:
        existing = inspect_reference(REPOSITORY, result.existing_reference or tag)
        if existing is None:
            raise CanaryError("exact-match reference disappeared")
        output.write_text(json.dumps({"write_performed": False, "reference": result.existing_reference,
                                      "registry": existing}, indent=2, sort_keys=True) + "\n")
        return {"write_performed": False, "reference": result.existing_reference, "registry": existing}
    if result.decision is not Decision.PUBLISH_NEW:
        raise CanaryError(f"publication blocked: {result.decision.value}: {result.reason}")

    annotations = identity_annotations(metadata)
    with tempfile.TemporaryDirectory(prefix="omnibioai-bedtools-canary-") as directory:
        staging = Path(directory)
        staged_sif = staging / f"bedtools-{metadata['target_architecture']}.sif"
        staged_identity = staging / "identity.json"
        staged_sif.write_bytes(sif.read_bytes())
        staged_identity.write_bytes(canonical_json_bytes(metadata) + b"\n")
        manifest_path = staging / "published-manifest.json"
        command = [
            "oras", "push", "--artifact-type", ARTIFACT_TYPE,
            "--artifact-platform", f"linux/{metadata['target_architecture']}",
            "--export-manifest", str(manifest_path),
        ]
        for key, value in sorted(annotations.items()):
            command.extend(("--annotation", f"{key}={value}"))
        command.extend((
            f"{REPOSITORY}:{tag}",
            f"{staged_sif}:{SIF_MEDIA_TYPE}",
            f"{staged_identity}:{IDENTITY_MEDIA_TYPE}",
        ))
        run(command)
        digest = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    remote = inspect_reference(REPOSITORY, tag)
    if remote is None or remote["manifest_digest"] != digest:
        raise CanaryError("published manifest cannot be independently resolved by digest")
    result_record = {"write_performed": True, "reference": f"{REPOSITORY}:{tag}", "registry": remote}
    output.write_text(json.dumps(result_record, indent=2, sort_keys=True) + "\n")
    return result_record


def retrieve_and_verify(candidate_path: Path, destination: Path, output: Path) -> dict[str, Any]:
    candidate = identity_metadata(json.loads(candidate_path.read_text()))
    tag = immutable_tag(candidate)
    remote = inspect_reference(REPOSITORY, tag)
    if remote is None:
        raise CanaryError("published immutable reference is absent")
    manifest = remote["manifest"]
    annotations = manifest.get("annotations") or {}
    if any(annotations.get(key) != value for key, value in identity_annotations(candidate).items()):
        raise CanaryError("retrieved manifest annotations differ from candidate identity")
    if remote.get("identity_metadata") != candidate:
        raise CanaryError("retrieved identity JSON differs from candidate")
    if remote.get("sif_sha256") != candidate["sif_sha256"]:
        raise CanaryError("retrieved SIF descriptor differs from candidate content identity")
    run(["oras", "blob", "fetch", "--output", str(destination),
         f"{REPOSITORY}@{remote['sif_layer_digest']}"])
    if sha256_file(destination) != candidate["sif_sha256"]:
        raise CanaryError("retrieved SIF bytes differ from candidate")
    state, decision = evaluate_live(candidate)
    if decision.decision is not Decision.SKIP_EXACT_MATCH:
        raise CanaryError(f"post-publication idempotency failed: {decision.decision.value}")
    legacy = next((entry for entry in state["artifacts"] if entry["tag"] == "arm64"), None)
    if legacy is None or legacy.get("sif_sha256") != LEGACY_ARM64_SHA256:
        raise CanaryError("legacy Bedtools ARM64 content identity changed")
    record = {
        "reference": f"{REPOSITORY}:{tag}",
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


def verify_retrieved_runtime(candidate_path: Path, sif: Path, workspace: Path, output: Path) -> dict[str, Any]:
    candidate = identity_metadata(json.loads(candidate_path.read_text()))
    if sha256_file(sif) != candidate["sif_sha256"]:
        raise CanaryError("retrieved SIF changed before runtime verification")
    tool = get_tool(TOOL)
    workspace.mkdir(parents=True, exist_ok=True)
    fixtures(workspace)
    entry = workspace / "entry.json"
    entry.write_text(json.dumps(tool, sort_keys=True))
    inspect = run(["sudo", "apptainer", "inspect", "--json", str(sif)])
    if not json.loads(inspect.stdout):
        raise CanaryError("retrieved SIF inspect metadata is empty")
    probe_output = workspace / "retrieved-sif-probe.json"
    run([
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
    if "bedtools v2.30.0" not in probe["version"]["output"]:
        raise CanaryError("retrieved SIF scientific version drifted")
    result = {"runtime_architecture": probe["architecture"], "executable": probe["executable"],
              "version": probe["version"], "smoke": probe["smoke"], "status": "PASS"}
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--provenance", required=True, type=Path)
    prepare.add_argument("--sif", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    decide = sub.add_parser("decide")
    decide.add_argument("--candidate", required=True, type=Path)
    decide.add_argument("--output", required=True, type=Path)
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--candidate", required=True, type=Path)
    publish_parser.add_argument("--sif", required=True, type=Path)
    publish_parser.add_argument("--output", required=True, type=Path)
    verify = sub.add_parser("retrieve")
    verify.add_argument("--candidate", required=True, type=Path)
    verify.add_argument("--destination", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    runtime = sub.add_parser("runtime")
    runtime.add_argument("--candidate", required=True, type=Path)
    runtime.add_argument("--sif", required=True, type=Path)
    runtime.add_argument("--workspace", required=True, type=Path)
    runtime.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            value = prepare_candidate(args.provenance, args.sif, args.output)
            print(json.dumps({"candidate": value, "immutable_tag": immutable_tag(value)}, sort_keys=True))
        elif args.command == "decide":
            candidate = identity_metadata(json.loads(args.candidate.read_text()))
            state, result = evaluate_live(candidate)
            args.output.write_text(json.dumps({"state": state, "decision": result.as_dict()}, indent=2,
                                              sort_keys=True) + "\n")
            print(json.dumps(result.as_dict(), sort_keys=True))
            if result.decision not in {Decision.PUBLISH_NEW, Decision.SKIP_EXACT_MATCH}:
                return 2
        elif args.command == "publish":
            publish(args.candidate, args.sif, args.output)
        elif args.command == "retrieve":
            retrieve_and_verify(args.candidate, args.destination, args.output)
        else:
            verify_retrieved_runtime(args.candidate, args.sif, args.workspace, args.output)
        return 0
    except Exception as error:
        print(f"CANARY_FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
