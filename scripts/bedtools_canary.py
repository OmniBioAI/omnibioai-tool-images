#!/usr/bin/env python3
"""Frozen Bedtools compatibility wrapper over the generic SIF release engine."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts import sif_release as engine
from scripts.sif_identity import Decision, identity_metadata, immutable_tag


TOOL = "bedtools"
SCIENTIFIC_VERSION = "2.30.0"
REPOSITORY = "ghcr.io/omnibioai/omnibioai-sif/bedtools"
ALLOWED_ARCHITECTURES = ("amd64", "arm64")
MOVING_ALIASES = frozenset(("arm64", "amd64", "latest", "stable", SCIENTIFIC_VERSION))
ARTIFACT_TYPE = engine.ARTIFACT_TYPE
IDENTITY_MEDIA_TYPE = engine.IDENTITY_MEDIA_TYPE
SIF_MEDIA_TYPE = engine.SIF_MEDIA_TYPE
RUNTIME_COMMIT = engine.RUNTIME_COMMIT
LEGACY_ARM64_SHA256 = "50d024aacfcd5caa1da804792dcc5cba6f002295d0ba4802ff8bf3fd67756fde"

CanaryError = engine.ReleaseError
OrasInvocationFailed = engine.OrasInvocationFailed

_FAILURE_CLASSIFIERS = engine._FAILURE_CLASSIFIERS
_REDACTION_PATTERNS = engine._REDACTION_PATTERNS


def _sanitize(text: str) -> str:
    """Keep the historical monkeypatchable Bedtools redaction boundary."""
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


sha256_file = engine.sha256_file
require_contained_staging_file = engine.require_contained_staging_file


def _spec() -> engine.ReleaseSpec:
    spec = engine.release_spec(TOOL)
    if spec.repository != REPOSITORY or spec.dockerfile != "dockerfiles/Dockerfile.bedtools":
        raise CanaryError("Bedtools manifest mapping differs from the frozen reference")
    return spec


def prepare_candidate(provenance_path: Path, sif: Path, output: Path) -> dict[str, Any]:
    candidate = engine.prepare_candidate_for_spec(_spec(), provenance_path, sif, output)
    if candidate["scientific_version"] != SCIENTIFIC_VERSION:
        raise CanaryError("SIF version evidence is not Bedtools 2.30.0")
    return candidate


def inspect_reference(repository: str, tag: str) -> dict[str, Any] | None:
    if repository != REPOSITORY:
        raise CanaryError("canary repository is not the authoritative Bedtools package")
    return engine.inspect_reference(repository, tag, run_fn=run)


def registry_snapshot(repository: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
    if repository != REPOSITORY:
        raise CanaryError("canary repository is not the authoritative Bedtools package")
    return engine.registry_snapshot(_spec(), candidate, inspect_fn=inspect_reference)


def evaluate_live(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], Any]:
    state = registry_snapshot(REPOSITORY, candidate)
    from scripts.sif_identity import evaluate_publication
    return state, evaluate_publication(candidate, state)


def publish(candidate_path: Path, sif: Path, output: Path) -> dict[str, Any]:
    return engine.release_candidate_for_spec(
        _spec(), candidate_path, sif, output,
        publish_enabled=True,
        evaluate_live_fn=evaluate_live,
        inspect_reference_fn=inspect_reference,
        run_fn=run,
        contain_fn=require_contained_staging_file,
        immutable_tag_fn=immutable_tag,
        staging_prefix="omnibioai-bedtools-canary-",
    )


def retrieve_and_verify(candidate_path: Path, destination: Path, output: Path) -> dict[str, Any]:
    return engine.retrieve_and_verify_for_spec(
        _spec(), candidate_path, destination, output,
        required_legacy_tags={"arm64": LEGACY_ARM64_SHA256},
        inspect_reference_fn=inspect_reference,
        evaluate_live_fn=evaluate_live,
        run_fn=run,
    )


def verify_retrieved_runtime(
    candidate_path: Path, sif: Path, workspace: Path, output: Path
) -> dict[str, Any]:
    return engine.verify_retrieved_runtime_for_spec(
        _spec(), candidate_path, sif, workspace, output, run_fn=run
    )


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
