#!/usr/bin/env python3
"""Inspect local Docker archives and fail-closed pilot evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path
from datetime import datetime

from scripts.pilot_manifest import get_tool
from scripts.pilot_probe import validate_probe_result


def archive_identity(path: Path, tool: str, arch: str, commit: str, dockerfile_sha256: str) -> dict:
    archive_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with tarfile.open(path, "r:*") as archive:
        names = archive.getnames()
        if names.count("manifest.json") != 1:
            raise ValueError("Docker archive must contain exactly one manifest")
        member = archive.extractfile("manifest.json")
        if member is None:
            raise ValueError("Docker archive has no readable manifest")
        manifest = json.load(member)
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError("Docker archive must contain exactly one image")
        config_name = manifest[0].get("Config")
        if not isinstance(config_name, str) or config_name not in names:
            raise ValueError("Docker archive config is missing")
        config_member = archive.extractfile(config_name)
        if config_member is None:
            raise ValueError("Docker archive config cannot be read")
        config_bytes = config_member.read()
        config = json.loads(config_bytes)
    expected_arch = {"amd64": "amd64", "arm64": "arm64"}[arch]
    if config.get("os") != "linux" or config.get("architecture") != expected_arch:
        raise ValueError(f"archive platform mismatch: {config.get('os')}/{config.get('architecture')}")
    config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    if manifest[0].get("Config") != config_name:
        raise ValueError("archive manifest/config identity mismatch")
    labels = config.get("config", {}).get("Labels") or {}
    expected_labels = {
        "org.omnibioai.tool": tool,
        "org.omnibioai.source-commit": commit,
        "org.omnibioai.dockerfile-sha256": dockerfile_sha256,
        "org.omnibioai.target-platform": f"linux/{arch}",
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        raise ValueError("archive labels do not bind tool, source, Dockerfile, and architecture")
    return {
        "oci_identity": config_digest,
        "oci_archive_sha256": archive_sha,
        "oci_config_architecture": config["architecture"],
        "oci_config_os": config["os"],
        "docker_archive_manifest_count": 1,
    }


def validate_runner_evidence(record: dict) -> None:
    arch = record.get("target_architecture")
    if arch not in ("amd64", "arm64"):
        raise ValueError("invalid target architecture")
    runner = record.get("runner")
    if not isinstance(runner, dict):
        raise ValueError("missing actual GitHub runner evidence")
    expected = {"amd64": ("X64", ("x86_64",)), "arm64": ("ARM64", ("aarch64", "arm64"))}[arch]
    if runner.get("os") != "Linux" or runner.get("arch") != expected[0]:
        raise ValueError("actual runner.os/runner.arch do not match the native Linux target")
    if record.get("uname_m") not in expected[1] or record.get("execution_mode") != "NATIVE":
        raise ValueError("uname -m/execution mode do not match native target")


def validate_evidence(record: dict) -> None:
    if not isinstance(record, dict):
        raise ValueError("provenance must be an object")
    required_strings = (
        "tool", "source_commit_sha", "dockerfile", "dockerfile_sha256",
        "target_architecture", "oci_identity", "oci_archive_sha256",
        "sif_sha256", "tool_version_output", "uname_m", "execution_mode",
        "executable_type", "build_timestamp", "verification_timestamp",
    )
    if any(not isinstance(record.get(key), str) or not record[key].strip() for key in required_strings):
        raise ValueError("missing/empty mandatory provenance identity or version evidence")
    if record.get("verification_result") != "PASS":
        raise ValueError("input gate status is not PASS")
    if record["target_architecture"] not in ("amd64", "arm64"):
        raise ValueError("invalid target architecture")
    if (not re.fullmatch(r"[a-z0-9_]+", record["tool"])
            or record["dockerfile"] != f"dockerfiles/Dockerfile.{record['tool']}"):
        raise ValueError("tool/Dockerfile provenance identity mismatch")
    validate_runner_evidence(record)
    entry = get_tool(record["tool"])
    if record["executable_type"] != entry["executable_type"]:
        raise ValueError("provenance executable type does not match manifest")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", record["source_commit_sha"]):
        raise ValueError("malformed source commit SHA")
    for key in ("build_timestamp", "verification_timestamp"):
        try:
            datetime.fromisoformat(record[key].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"malformed {key}") from exc
    for phase in ("oci", "sif"):
        probe = {key: record.get(f"{phase}_{key}") for key in ("architecture", "executable", "version", "smoke")}
        validate_probe_result({**probe, "verification_status": "PASS", "phase": phase},
                              entry, record["target_architecture"], phase)
    inspect = record.get("sif_inspect")
    if (not isinstance(inspect, dict) or inspect.get("status") != "PASS"
            or not isinstance(inspect.get("metadata"), (dict, list)) or not inspect["metadata"]):
        raise ValueError("missing/failed/empty structured SIF inspect evidence")
    if record["tool_version_output"] != record["oci_version"]["output"]:
        raise ValueError("tool version summary differs from OCI version evidence")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", record["oci_identity"]):
        raise ValueError("malformed local OCI config digest")
    for checksum in ("oci_archive_sha256", "sif_sha256", "dockerfile_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", record.get(checksum, "")):
            raise ValueError(f"malformed {checksum}")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    archive = sub.add_parser("archive")
    archive.add_argument("--archive", type=Path, required=True)
    archive.add_argument("--tool", required=True)
    archive.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    archive.add_argument("--commit", required=True)
    archive.add_argument("--dockerfile-sha256", required=True)
    check = sub.add_parser("evidence")
    check.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "archive":
        print(json.dumps(archive_identity(args.archive, args.tool, args.arch,
                                          args.commit, args.dockerfile_sha256), sort_keys=True))
    else:
        validate_evidence(json.loads(args.record.read_text()))
        print("PROVENANCE_EVIDENCE_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
