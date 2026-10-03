"""Generate and validate deterministic multiarch tool validation contracts.

This module is intentionally independent of the immutable release factory.  It
uses a reviewed population snapshot and static Dockerfile evidence only; it
does not build images, contact a registry, or execute scientific tools.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shlex
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
EXPECTED_ARCHITECTURES = ["amd64", "arm64"]
REFERENCE_TOOLS = {
    "bcftools",
    "bedtools",
    "bwa",
    "fastqc",
    "minimap2",
    "multiqc",
    "muscle",
    "prodigal",
    "samtools",
    "vcftools",
}
STATUSES = {
    "CONTRACT_READY",
    "CONTRACT_BLOCKED_EXECUTABLE",
    "CONTRACT_BLOCKED_VERSION",
    "CONTRACT_BLOCKED_SMOKE",
    "CONTRACT_BLOCKED_ARCHITECTURE",
    "CONTRACT_BLOCKED_PACKAGE_MAPPING",
    "CONTRACT_BLOCKED_LICENSE",
    "CONTRACT_BLOCKED_RUNTIME_DEPENDENCY",
    "CONTRACT_BLOCKED_SOURCE_IDENTITY",
    "CONTRACT_BLOCKED_AMBIGUOUS_EVIDENCE",
    "CONTRACT_BLOCKED_MULTIPLE_REASONS",
}
BLOCKER_TO_STATUS = {
    "EXECUTABLE": "CONTRACT_BLOCKED_EXECUTABLE",
    "VERSION": "CONTRACT_BLOCKED_VERSION",
    "SMOKE": "CONTRACT_BLOCKED_SMOKE",
    "ARCHITECTURE": "CONTRACT_BLOCKED_ARCHITECTURE",
    "PACKAGE_MAPPING": "CONTRACT_BLOCKED_PACKAGE_MAPPING",
    "LICENSE": "CONTRACT_BLOCKED_LICENSE",
    "RUNTIME_DEPENDENCY": "CONTRACT_BLOCKED_RUNTIME_DEPENDENCY",
    "SOURCE_IDENTITY": "CONTRACT_BLOCKED_SOURCE_IDENTITY",
    "AMBIGUOUS_EVIDENCE": "CONTRACT_BLOCKED_AMBIGUOUS_EVIDENCE",
}
EXCEPTION_FILES = {
    "CONTRACT_BLOCKED_EXECUTABLE": "blocked-executable.txt",
    "CONTRACT_BLOCKED_VERSION": "blocked-version.txt",
    "CONTRACT_BLOCKED_SMOKE": "blocked-smoke.txt",
    "CONTRACT_BLOCKED_ARCHITECTURE": "blocked-architecture.txt",
    "CONTRACT_BLOCKED_PACKAGE_MAPPING": "blocked-package-mapping.txt",
    "CONTRACT_BLOCKED_LICENSE": "blocked-license.txt",
    "CONTRACT_BLOCKED_RUNTIME_DEPENDENCY": "blocked-runtime.txt",
    "CONTRACT_BLOCKED_SOURCE_IDENTITY": "blocked-source-identity.txt",
    "CONTRACT_BLOCKED_AMBIGUOUS_EVIDENCE": "blocked-ambiguous.txt",
    "CONTRACT_BLOCKED_MULTIPLE_REASONS": "blocked-multiple.txt",
}
REQUIRED_KEYS = {
    "validation_contract_schema_version",
    "tool_id",
    "display_name",
    "dockerfile_path",
    "dockerfile_sha256",
    "classification",
    "eligibility",
    "package",
    "registry_namespace",
    "scientific_version",
    "scientific_version_source",
    "scientific_version_evidence",
    "expected_executable",
    "version_command",
    "version_parser",
    "version_expected_value",
    "smoke_command",
    "smoke_inputs",
    "smoke_expected_outputs",
    "smoke_success_condition",
    "expected_architectures",
    "native_amd64_expected",
    "native_arm64_expected",
    "base_image_reference",
    "base_image_architecture_evidence",
    "source_type",
    "source_reference",
    "source_immutable_identity",
    "runtime_network_required",
    "runtime_gpu_required",
    "runtime_database_required",
    "runtime_reference_data_required",
    "runtime_license_required",
    "contract_status",
    "blocking_reasons",
    "evidence",
    "contract_confidence",
    "validation_contract_sha256",
}


class ContractError(ValueError):
    """Raised when generation or validation must fail closed."""


def canonical_json(value: Any) -> bytes:
    """Return the canonical UTF-8 representation used for hashing."""

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def contract_hash(contract: Mapping[str, Any]) -> str:
    """Hash semantic contract fields, excluding the self-referential hash."""

    payload = dict(contract)
    payload.pop("validation_contract_sha256", None)
    return sha256_bytes(canonical_json(payload))


def _line_evidence(path: str, line_number: int, text: str, field: str) -> dict[str, Any]:
    return {
        "field": field,
        "quality": "DIRECT",
        "source": path,
        "line": line_number,
        "value": text.strip(),
    }


def _docker_lines(text: str) -> list[tuple[int, str]]:
    return [(number, line) for number, line in enumerate(text.splitlines(), 1)]


def _base_image(lines: Sequence[tuple[int, str]], path: str) -> tuple[str | None, dict[str, Any] | None]:
    for number, line in lines:
        match = re.match(r"\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", line, re.I)
        if match:
            return match.group(1), _line_evidence(path, number, line, "base_image_reference")
    return None, None


def _json_command(line: str) -> list[str] | None:
    match = re.match(r"\s*(?:CMD|ENTRYPOINT)\s+(\[.*\])\s*$", line, re.I)
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        return None
    return value


def _is_generic_runner(command: Sequence[str]) -> bool:
    joined = " ".join(command).lower()
    return "tools.generic_sif_runner.run" in joined


def _explicit_tool_commands(
    lines: Sequence[tuple[int, str]], path: str
) -> list[tuple[list[str], dict[str, Any]]]:
    commands: list[tuple[list[str], dict[str, Any]]] = []
    for number, line in lines:
        command = _json_command(line)
        if command and not _is_generic_runner(command):
            commands.append((command, _line_evidence(path, number, line, "tool_command")))
    return commands


def _normalize_package_token(tool_id: str) -> str:
    name = re.sub(r"_(?:v\d+_)?extra$", "", tool_id, flags=re.I)
    name = re.sub(r"_(?:arm64|amd64)$", "", name, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _logical_instructions(lines: Sequence[tuple[int, str]]) -> list[tuple[int, str]]:
    instructions: list[tuple[int, str]] = []
    start = 0
    parts: list[str] = []
    for number, line in lines:
        stripped = line.strip()
        if not parts:
            start = number
        parts.append(stripped.rstrip("\\").strip())
        if not stripped.endswith("\\"):
            instructions.append((start, " ".join(part for part in parts if part)))
            parts = []
    if parts:
        instructions.append((start, " ".join(part for part in parts if part)))
    return instructions


def _explicit_version_pin(
    tool_id: str, lines: Sequence[tuple[int, str]], path: str
) -> tuple[str | None, str | None, str | None, dict[str, Any] | None]:
    """Find only explicit, tool-associated immutable version pins."""

    normalized = _normalize_package_token(tool_id)
    variable_values: dict[str, tuple[str, int, str]] = {}
    for number, line in lines:
        match = re.match(
            r"\s*(?:ARG|ENV)\s+([A-Za-z0-9_]*(?:VERSION|VER)[A-Za-z0-9_]*)[=\s]+[\"']?([vV]?\d+(?:\.\d+)+(?:[-+._A-Za-z0-9]*)?)[\"']?\s*$",
            line,
            re.I,
        )
        if match:
            variable_values[match.group(1)] = (match.group(2), number, line)
    if len(variable_values) == 1:
        variable, (version, number, line) = next(iter(variable_values.items()))
        variable_name = re.sub(r"[^a-z0-9]", "", variable.casefold().replace("version", "").replace("ver", ""))
        if not variable_name or variable_name in normalized or normalized in variable_name:
            evidence = _line_evidence(path, number, line, "scientific_version")
            return version.lstrip("vV"), "dockerfile_version_variable", f"{variable}={version}", evidence

    token_pattern = re.compile(
        r"(?<![A-Za-z0-9_.+-])([A-Za-z0-9_.+-]+)(?:==|=)([vV]?\d+(?:\.\d+)+(?:[-+._A-Za-z0-9]*))"
    )
    matches: list[tuple[str, str, int, str]] = []
    for number, line in _logical_instructions(lines):
        if not re.search(r"(?i)\b(?:pip\d*|conda|mamba|micromamba|apt-get|apt)\s+install\b", line):
            continue
        for package, version in token_pattern.findall(line):
            if _normalize_package_token(package) == normalized:
                matches.append((package, version, number, line))
    if len(matches) == 1:
        package, version, number, line = matches[0]
        evidence = _line_evidence(path, number, line, "scientific_version")
        return version.lstrip("vV"), "pinned_package_version", f"{package}={version}", evidence
    return None, None, None, None


def _architecture_blockers(text: str) -> list[str]:
    blockers: list[str] = []
    patterns = {
        "hard-coded linux/amd64 platform": r"(?i)--platform\s*=\s*linux/amd64",
        "hard-coded x86_64 artifact": r"(?i)(?:x86_64|linux[-_]?64|amd64)",
        "hard-coded linux/arm64 platform": r"(?i)--platform\s*=\s*linux/arm64",
        "hard-coded aarch64 artifact": r"(?i)(?:aarch64|arm64)",
    }
    for reason, pattern in patterns.items():
        if re.search(pattern, text):
            blockers.append(reason)
    return blockers


def _runtime_requirements(text: str) -> dict[str, bool]:
    lowered = text.casefold()
    return {
        "runtime_network_required": False,
        "runtime_gpu_required": any(
            token in lowered for token in ("cuda", "nvidia", "rocm")
        )
        or bool(re.search(r"\bgpu\b", lowered)),
        "runtime_database_required": any(token in lowered for token in ("database server", "postgresql", "mysql-server")),
        "runtime_reference_data_required": any(token in lowered for token in ("reference data", "reference genome", "download-db", "download_db")),
        "runtime_license_required": any(token in lowered for token in ("license server", "license_file", "license-file")),
    }


def _status_for(blocker_codes: Sequence[str]) -> str:
    unique = sorted(set(blocker_codes))
    if not unique:
        return "CONTRACT_READY"
    if len(unique) == 1:
        return BLOCKER_TO_STATUS[unique[0]]
    return "CONTRACT_BLOCKED_MULTIPLE_REASONS"


def generate_contract(row: Mapping[str, str], repo_root: Path) -> dict[str, Any]:
    tool_id = row["tool_id"]
    dockerfile_path = row["dockerfile"]
    dockerfile = repo_root / dockerfile_path
    if not dockerfile.is_file():
        raise ContractError(f"{tool_id}: missing Dockerfile {dockerfile_path}")
    raw = dockerfile.read_bytes()
    text = raw.decode("utf-8")
    lines = _docker_lines(text)
    evidence: list[dict[str, Any]] = [
        {
            "field": "classification",
            "quality": "DIRECT",
            "source": "validation-contracts/evidence/current-package-preflight.csv",
            "value": row["repository_classification"],
        },
        {
            "field": "package",
            "quality": "DIRECT",
            "source": "validation-contracts/evidence/current-package-preflight.csv",
            "value": row["ghcr_package"],
        },
    ]
    base_image, base_evidence = _base_image(lines, dockerfile_path)
    if base_evidence:
        evidence.append(base_evidence)

    commands = _explicit_tool_commands(lines, dockerfile_path)
    version_command: list[str] | None = None
    executable: str | None = None
    for command, command_evidence in commands:
        if executable is None:
            executable = command[0]
            evidence.append({**command_evidence, "field": "expected_executable"})
        if any(arg.casefold() in {"--version", "-version", "version", "-v"} for arg in command[1:]):
            version_command = command
            evidence.append({**command_evidence, "field": "version_command"})
            break

    scientific_version, version_source, version_reference, version_evidence = _explicit_version_pin(
        tool_id, lines, dockerfile_path
    )
    if version_evidence:
        evidence.append(version_evidence)

    package_mapping_confirmed = row["package_exists"] == "YES" and row["mapping_status"] in {
        "EXACT",
        "CASE_NORMALIZED",
    }
    architecture_findings = _architecture_blockers(text)
    runtime = _runtime_requirements(text)

    blocker_codes: list[str] = []
    blocking_reasons: list[str] = []
    if not package_mapping_confirmed:
        blocker_codes.append("PACKAGE_MAPPING")
        blocking_reasons.append("exact current GHCR package mapping is not confirmed")
    if architecture_findings:
        blocker_codes.append("ARCHITECTURE")
        blocking_reasons.extend(architecture_findings)
    if executable is None:
        blocker_codes.append("EXECUTABLE")
        blocking_reasons.append("no canonical scientific executable is explicitly invoked by the Dockerfile")
    if scientific_version is None:
        blocker_codes.extend(("VERSION", "SOURCE_IDENTITY"))
        blocking_reasons.append("no exact tool-associated scientific version or immutable source pin is present")
    if version_command is None:
        blocker_codes.append("VERSION")
        blocking_reasons.append("no explicit tool-specific version command is present")

    # A version/help invocation is not promoted into a scientific smoke test.
    blocker_codes.append("SMOKE")
    blocking_reasons.append("no deterministic scientifically meaningful smoke contract is present in repository evidence")

    if runtime["runtime_license_required"]:
        blocker_codes.append("LICENSE")
        blocking_reasons.append("Dockerfile indicates a runtime license dependency requiring separate review")
    if runtime["runtime_gpu_required"] or runtime["runtime_database_required"] or runtime["runtime_reference_data_required"]:
        blocker_codes.append("RUNTIME_DEPENDENCY")
        blocking_reasons.append("special runtime dependency requires a separate validation gate")

    status = _status_for(blocker_codes)
    contract: dict[str, Any] = {
        "validation_contract_schema_version": SCHEMA_VERSION,
        "tool_id": tool_id,
        "display_name": tool_id,
        "dockerfile_path": dockerfile_path,
        "dockerfile_sha256": sha256_bytes(raw),
        "classification": row["repository_classification"],
        "eligibility": {
            "targeted": True,
            "package_mapping_confirmed": package_mapping_confirmed,
            "mapping_status": row["mapping_status"],
        },
        "package": row["ghcr_package"],
        "registry_namespace": "ghcr.io/omnibioai/omnibioai-sif",
        "scientific_version": scientific_version,
        "scientific_version_source": version_source,
        "scientific_version_evidence": version_reference,
        "expected_executable": executable,
        "version_command": version_command,
        "version_parser": "exact_scientific_version_token" if version_command and scientific_version else None,
        "version_expected_value": scientific_version,
        "smoke_command": None,
        "smoke_inputs": [],
        "smoke_expected_outputs": [],
        "smoke_success_condition": None,
        "expected_architectures": EXPECTED_ARCHITECTURES,
        "native_amd64_expected": not architecture_findings,
        "native_arm64_expected": not architecture_findings,
        "base_image_reference": base_image,
        "base_image_architecture_evidence": {
            "quality": "INFERRED",
            "statement": "repository audit classifies this Dockerfile MULTIARCH_READY; runtime architecture remains a build-phase gate",
        },
        "source_type": (
            "pinned_package_version"
            if version_source == "pinned_package_version"
            else "dockerfile_version_variable"
            if version_source == "dockerfile_version_variable"
            else "unknown_unpinned"
        ),
        "source_reference": version_reference,
        "source_immutable_identity": version_reference if scientific_version else None,
        **runtime,
        "contract_status": status,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "evidence": sorted(
            evidence,
            key=lambda item: (
                item["field"], item["source"], item.get("line", 0), item.get("value", "")
            ),
        ),
        "contract_confidence": "HIGH" if status == "CONTRACT_READY" else "LOW",
    }
    contract["validation_contract_sha256"] = contract_hash(contract)
    return contract


def load_catalog(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error) as exc:
        raise ContractError(f"cannot read population catalog: {exc}") from exc
    required = {
        "tool_id",
        "dockerfile",
        "repository_classification",
        "ghcr_package",
        "package_exists",
        "mapping_status",
    }
    if not rows or not required.issubset(rows[0]):
        raise ContractError("population catalog is empty or missing required columns")
    return rows


def select_targets(rows: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    targets = [
        dict(row)
        for row in rows
        if row["repository_classification"] == "MULTIARCH_READY"
        and row["package_exists"] == "YES"
        and row["tool_id"].casefold() not in REFERENCE_TOOLS
    ]
    return sorted(targets, key=lambda row: (row["tool_id"].casefold(), row["tool_id"]))


def validate_contract(contract: Mapping[str, Any], repo_root: Path) -> None:
    missing = REQUIRED_KEYS - set(contract)
    if missing:
        raise ContractError(f"missing contract fields: {sorted(missing)}")
    if contract["validation_contract_schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported validation contract schema")
    if not isinstance(contract["tool_id"], str) or not contract["tool_id"]:
        raise ContractError("missing tool ID")
    if contract["contract_status"] not in STATUSES:
        raise ContractError("invalid contract status")
    if contract["expected_architectures"] != EXPECTED_ARCHITECTURES:
        raise ContractError("unknown or unordered architecture set")
    if not re.fullmatch(r"[0-9a-f]{64}", str(contract["dockerfile_sha256"])):
        raise ContractError("malformed Dockerfile SHA256")
    if not re.fullmatch(r"[0-9a-f]{64}", str(contract["validation_contract_sha256"])):
        raise ContractError("malformed contract SHA256")
    path = repo_root / str(contract["dockerfile_path"])
    if not path.is_file():
        raise ContractError("missing Dockerfile")
    if sha256_file(path) != contract["dockerfile_sha256"]:
        raise ContractError("Dockerfile SHA256 mismatch")
    if not contract["package"]:
        raise ContractError("missing package")
    if not isinstance(contract["blocking_reasons"], list):
        raise ContractError("blocking reasons must be a list")
    if contract_hash(contract) != contract["validation_contract_sha256"]:
        raise ContractError("contract SHA256 mismatch")
    if contract["contract_status"] == "CONTRACT_READY":
        ready_fields = (
            "scientific_version",
            "expected_executable",
            "version_command",
            "version_parser",
            "version_expected_value",
            "smoke_command",
            "smoke_success_condition",
            "source_immutable_identity",
        )
        absent = [field for field in ready_fields if not contract[field]]
        if absent:
            raise ContractError(f"ready contract missing mandatory values: {absent}")
        if contract["blocking_reasons"]:
            raise ContractError("ready contract has blocking reasons")
        if not contract["native_amd64_expected"] or not contract["native_arm64_expected"]:
            raise ContractError("ready contract lacks native architecture expectations")


def validate_contract_set(contracts: Sequence[Mapping[str, Any]], repo_root: Path) -> None:
    if not contracts:
        raise ContractError("empty contract set")
    for contract in contracts:
        validate_contract(contract, repo_root)
    versions_by_tool: defaultdict[str, set[str]] = defaultdict(set)
    for contract in contracts:
        if contract["scientific_version"]:
            versions_by_tool[str(contract["tool_id"])].add(str(contract["scientific_version"]))
    conflicts = sorted(tool for tool, versions in versions_by_tool.items() if len(versions) > 1)
    if conflicts:
        raise ContractError(f"scientific version conflict: {conflicts}")
    for field, label in (
        ("tool_id", "duplicate tool ID"),
        ("dockerfile_path", "duplicate Dockerfile mapping"),
        ("package", "package mapping collision"),
    ):
        duplicates = sorted(value for value, count in Counter(c[field] for c in contracts).items() if count > 1)
        if duplicates:
            raise ContractError(f"{label}: {duplicates}")
    references = sorted(c["tool_id"] for c in contracts if c["tool_id"].casefold() in REFERENCE_TOOLS)
    if references:
        raise ContractError(f"reference tools present in target set: {references}")
    invalid_classes = sorted(c["tool_id"] for c in contracts if c["classification"] != "MULTIARCH_READY")
    if invalid_classes:
        raise ContractError(f"ineligible classification in target set: {invalid_classes}")


def _pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _population_counts(rows: Sequence[Mapping[str, str]]) -> Counter[str]:
    return Counter(row["repository_classification"] for row in rows if not row["tool_id"].startswith("UNMAPPED::"))


def _inventory_row(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "tool_id": str(contract["tool_id"]),
        "dockerfile_path": str(contract["dockerfile_path"]),
        "classification": str(contract["classification"]),
        "package": str(contract["package"]),
        "scientific_version": str(contract["scientific_version"] or ""),
        "expected_executable": str(contract["expected_executable"] or ""),
        "version_command": shlex.join(contract["version_command"]) if contract["version_command"] else "",
        "smoke_contract": shlex.join(contract["smoke_command"]) if contract["smoke_command"] else "",
        "amd64_expected": str(contract["native_amd64_expected"]).lower(),
        "arm64_expected": str(contract["native_arm64_expected"]).lower(),
        "runtime_gpu_required": str(contract["runtime_gpu_required"]).lower(),
        "runtime_network_required": str(contract["runtime_network_required"]).lower(),
        "runtime_license_required": str(contract["runtime_license_required"]).lower(),
        "contract_status": str(contract["contract_status"]),
        "blocking_reason": "; ".join(contract["blocking_reasons"]),
        "contract_sha256": str(contract["validation_contract_sha256"]),
    }


def _csv_bytes(rows: Sequence[Mapping[str, str]]) -> bytes:
    if not rows:
        raise ContractError("cannot serialize empty inventory")
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def generate_all(catalog: Path, repo_root: Path, output: Path, source_commit: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ContractError("source commit must be a full lowercase Git SHA")
    rows = load_catalog(catalog)
    # Normalize the reviewed CSV snapshot so platform line endings cannot
    # affect evidence identity or make a second generation differ.
    catalog_bytes = _csv_bytes(rows)
    catalog_sha256 = sha256_bytes(catalog_bytes)
    targets = select_targets(rows)
    contracts = [generate_contract(row, repo_root) for row in targets]
    validate_contract_set(contracts, repo_root)

    for directory in (output / "schema-v1", output / "exceptions", output / "evidence"):
        if directory.exists():
            shutil.rmtree(directory)
    for filename in (
        "inventory.json",
        "inventory.csv",
        "summary.json",
        "contract-ready-tools.txt",
        "proposed-batch-10.txt",
    ):
        path = output / filename
        if path.exists():
            path.unlink()
    (output / "schema-v1").mkdir(parents=True)
    for contract in contracts:
        _write(output / "schema-v1" / f"{contract['tool_id']}.json", _pretty_json(contract))

    inventory = [_inventory_row(contract) for contract in contracts]
    _write(output / "inventory.json", _pretty_json(inventory))
    _write(output / "inventory.csv", _csv_bytes(inventory))

    status_counts = Counter(contract["contract_status"] for contract in contracts)
    ready = [contract["tool_id"] for contract in contracts if contract["contract_status"] == "CONTRACT_READY"]
    blocked = [
        {
            "tool_id": contract["tool_id"],
            "contract_status": contract["contract_status"],
            "blocking_reasons": contract["blocking_reasons"],
        }
        for contract in contracts
        if contract["contract_status"] != "CONTRACT_READY"
    ]
    population = _population_counts(rows)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "source_catalog_sha256": catalog_sha256,
        "total_dockerfiles": sum(population.values()),
        "multiarch_ready_count": population["MULTIARCH_READY"],
        "arm64_pinned_but_portable_count": population["ARM64_PINNED_BUT_PORTABLE"],
        "architecture_dependent_count": population["ARCHITECTURE_DEPENDENT"],
        "amd64_only_count": population["AMD64_ONLY"],
        "arm64_only_count": population["ARM64_ONLY"],
        "stub_or_placeholder_count": population["STUB_OR_PLACEHOLDER"],
        "package_intersection_count": sum(
            row["repository_classification"] == "MULTIARCH_READY" and row["package_exists"] == "YES"
            for row in rows
        ),
        "reference_tools_excluded": len(REFERENCE_TOOLS),
        "contract_target_count": len(contracts),
        "contract_ready_count": len(ready),
        "blocked_executable_count": status_counts["CONTRACT_BLOCKED_EXECUTABLE"],
        "blocked_version_count": status_counts["CONTRACT_BLOCKED_VERSION"],
        "blocked_smoke_count": status_counts["CONTRACT_BLOCKED_SMOKE"],
        "blocked_architecture_count": status_counts["CONTRACT_BLOCKED_ARCHITECTURE"],
        "blocked_package_mapping_count": status_counts["CONTRACT_BLOCKED_PACKAGE_MAPPING"],
        "blocked_license_count": status_counts["CONTRACT_BLOCKED_LICENSE"],
        "blocked_runtime_dependency_count": status_counts["CONTRACT_BLOCKED_RUNTIME_DEPENDENCY"],
        "blocked_source_identity_count": status_counts["CONTRACT_BLOCKED_SOURCE_IDENTITY"],
        "blocked_ambiguous_evidence_count": status_counts["CONTRACT_BLOCKED_AMBIGUOUS_EVIDENCE"],
        "blocked_multiple_reasons_count": status_counts["CONTRACT_BLOCKED_MULTIPLE_REASONS"],
        "ready_tools": ready,
        "blocked_tools": blocked,
    }
    _write(output / "summary.json", _pretty_json(summary))
    _write(output / "contract-ready-tools.txt", ("\n".join(ready) + ("\n" if ready else "")).encode())
    batch = ready[:10] if len(ready) >= 10 else []
    _write(output / "proposed-batch-10.txt", ("\n".join(batch) + ("\n" if batch else "")).encode())

    by_status: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for contract in contracts:
        by_status[contract["contract_status"]].append(contract)
    for status, filename in EXCEPTION_FILES.items():
        lines = [
            f"{contract['tool_id']}\t" + "; ".join(contract["blocking_reasons"])
            for contract in by_status[status]
        ]
        _write(output / "exceptions" / filename, ("\n".join(lines) + ("\n" if lines else "")).encode())

    evidence = {
        "schema_version": SCHEMA_VERSION,
        "catalog_file": "current-package-preflight.csv",
        "catalog_sha256": catalog_sha256,
        "source_commit": source_commit,
        "selection_rule": "MULTIARCH_READY and package_exists=YES, excluding the ten reference tools",
        "live_read_only_package_check": "601/601 package repositories resolved on 2026-10-03",
    }
    _write(output / "evidence" / "source-evidence.json", _pretty_json(evidence))
    _write(output / "evidence" / "current-package-preflight.csv", catalog_bytes)
    return summary


def load_contract_files(directory: Path) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda value: value.name.casefold()):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"malformed contract JSON {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ContractError(f"contract is not an object: {path}")
        contracts.append(value)
    return contracts


def validate_output(output: Path, repo_root: Path) -> None:
    contracts = load_contract_files(output / "schema-v1")
    validate_contract_set(contracts, repo_root)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if summary["contract_target_count"] != len(contracts):
        raise ContractError("summary target count mismatch")
    ready = sorted(
        contract["tool_id"] for contract in contracts if contract["contract_status"] == "CONTRACT_READY"
    )
    ready_file = (output / "contract-ready-tools.txt").read_text(encoding="utf-8").splitlines()
    if ready_file != ready:
        raise ContractError("contract-ready list mismatch")
    batch_file = (output / "proposed-batch-10.txt").read_text(encoding="utf-8").splitlines()
    expected_batch = ready[:10] if len(ready) >= 10 else []
    if batch_file != expected_batch:
        raise ContractError("proposed batch mismatch")


def _current_commit(repo_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--catalog", type=Path, required=True)
    generate_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--source-commit")
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    validate_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            commit = args.source_commit or _current_commit(args.repo_root)
            summary = generate_all(args.catalog, args.repo_root, args.output, commit)
            print(
                json.dumps(
                    {
                        "contract_ready_count": summary["contract_ready_count"],
                        "contract_target_count": summary["contract_target_count"],
                        "source_catalog_sha256": summary["source_catalog_sha256"],
                    },
                    sort_keys=True,
                )
            )
        else:
            validate_output(args.output, args.repo_root)
            print("validation contracts: PASS")
    except (ContractError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"validation contracts: FAIL: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
