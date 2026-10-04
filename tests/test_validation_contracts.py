from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.validation_contracts import (
    ContractError,
    canonical_json,
    contract_hash,
    generate_all,
    generate_contract,
    load_contract_files,
    select_targets,
    validate_contract,
    validate_contract_set,
    validate_output,
)


def catalog_row(**overrides: str) -> dict[str, str]:
    row = {
        "tool_id": "example",
        "dockerfile": "dockerfiles/Dockerfile.example",
        "repository_classification": "MULTIARCH_READY",
        "ghcr_package": "ghcr.io/omnibioai/omnibioai-sif/example",
        "package_exists": "YES",
        "mapping_status": "EXACT",
    }
    row.update(overrides)
    return row


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "dockerfiles").mkdir()
    (tmp_path / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\n"
        "RUN pip install example==1.2.3\n"
        'CMD ["example", "--version"]\n',
        encoding="utf-8",
    )
    return tmp_path


def test_canonical_serialization_and_hash_are_deterministic(repo: Path) -> None:
    first = generate_contract(catalog_row(), repo)
    second = generate_contract(catalog_row(), repo)
    assert canonical_json(first) == canonical_json(second)
    assert contract_hash(first) == contract_hash(second)


def test_reviewed_canary_override_is_used_only_for_matching_dockerfile(repo: Path) -> None:
    baseline = generate_contract(catalog_row(), repo)
    baseline["contract_status"] = "CONTRACT_READY"
    baseline["blocking_reasons"] = []
    baseline["smoke_command"] = ["example", "--smoke"]
    baseline["smoke_success_condition"] = "exit 0"
    baseline["source_type"] = "upstream_archive_sha256"
    baseline["source_immutable_identity"] = "sha256:" + "a" * 64
    baseline["pinning"] = {
        "pinning_canary_schema_version": 1,
        "static_contract_status": "STATIC_CONTRACT_READY",
    }
    baseline["validation_contract_sha256"] = contract_hash(baseline)
    path = repo / "validation-contracts" / "schema-v1"
    path.mkdir(parents=True)
    (path / "example.json").write_text(
        json.dumps(baseline, sort_keys=True), encoding="utf-8"
    )

    reviewed = generate_contract(catalog_row(), repo)
    assert reviewed["contract_status"] == "CONTRACT_READY"
    assert reviewed["pinning"]["static_contract_status"] == "STATIC_CONTRACT_READY"

    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\nRUN pip install example==9.9.9\n",
        encoding="utf-8",
    )
    stale = generate_contract(catalog_row(), repo)
    assert stale["contract_status"] == "CONTRACT_BLOCKED_MULTIPLE_REASONS"
    assert "pinning" not in stale


def test_generated_contract_is_fail_closed_without_smoke(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    assert contract["expected_executable"] == "example"
    assert contract["scientific_version"] == "1.2.3"
    assert contract["version_command"] == ["example", "--version"]
    assert contract["contract_status"] == "CONTRACT_BLOCKED_SMOKE"
    validate_contract(contract, repo)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("tool_id", "missing contract fields"),
        ("dockerfile_path", "missing contract fields"),
        ("dockerfile_sha256", "missing contract fields"),
        ("package", "missing contract fields"),
        ("scientific_version", "missing contract fields"),
        ("expected_executable", "missing contract fields"),
        ("version_command", "missing contract fields"),
        ("smoke_command", "missing contract fields"),
        ("source_immutable_identity", "missing contract fields"),
    ],
)
def test_missing_schema_fields_are_rejected(repo: Path, field: str, message: str) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract.pop(field)
    with pytest.raises(ContractError, match=message):
        validate_contract(contract, repo)


def test_ready_contract_requires_executable_version_smoke_and_source(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["contract_status"] = "CONTRACT_READY"
    contract["blocking_reasons"] = []
    contract["validation_contract_sha256"] = contract_hash(contract)
    with pytest.raises(ContractError, match="ready contract missing mandatory"):
        validate_contract(contract, repo)


def test_unknown_architecture_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["expected_architectures"] = ["amd64", "sparc64"]
    contract["validation_contract_sha256"] = contract_hash(contract)
    with pytest.raises(ContractError, match="unknown or unordered architecture"):
        validate_contract(contract, repo)


def test_malformed_sha_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["dockerfile_sha256"] = "not-a-sha"
    with pytest.raises(ContractError, match="malformed Dockerfile SHA256"):
        validate_contract(contract, repo)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("tool_id", "duplicate tool ID"),
        ("dockerfile_path", "duplicate Dockerfile mapping"),
        ("package", "package mapping collision"),
    ],
)
def test_duplicate_and_collision_detection(repo: Path, field: str, message: str) -> None:
    first = generate_contract(catalog_row(), repo)
    second = deepcopy(first)
    second.update(
        {
            "tool_id": "other",
            "dockerfile_path": "dockerfiles/Dockerfile.other",
            "package": "ghcr.io/omnibioai/omnibioai-sif/other",
        }
    )
    (repo / "dockerfiles" / "Dockerfile.other").write_bytes(
        (repo / "dockerfiles" / "Dockerfile.example").read_bytes()
    )
    second["validation_contract_sha256"] = contract_hash(second)
    if field == "tool_id":
        second["tool_id"] = first["tool_id"]
    elif field == "dockerfile_path":
        second["dockerfile_path"] = first["dockerfile_path"]
    else:
        second["package"] = first["package"]
    second["validation_contract_sha256"] = contract_hash(second)
    with pytest.raises(ContractError, match=message):
        validate_contract_set([first, second], repo)


def test_reference_tools_are_excluded() -> None:
    rows = [catalog_row(tool_id="bedtools"), catalog_row(tool_id="candidate")]
    assert [row["tool_id"] for row in select_targets(rows)] == ["candidate"]


@pytest.mark.parametrize(
    "classification",
    [
        "STUB_OR_PLACEHOLDER",
        "ARCHITECTURE_DEPENDENT",
        "AMD64_ONLY",
        "ARM64_ONLY",
        "ARM64_PINNED_BUT_PORTABLE",
    ],
)
def test_ineligible_classifications_are_excluded(classification: str) -> None:
    assert select_targets([catalog_row(repository_classification=classification)]) == []


def test_missing_package_is_excluded() -> None:
    assert select_targets([catalog_row(package_exists="NO")]) == []


def test_architecture_sensitive_dockerfile_is_blocked(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM --platform=linux/amd64 ubuntu:24.04\nRUN pip install example==1.2.3\n",
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["native_amd64_expected"] is False
    assert contract["native_arm64_expected"] is False
    assert "hard-coded linux/amd64 platform" in contract["blocking_reasons"]


def test_unpinned_source_does_not_invent_a_version(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\nRUN pip install example\n",
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["scientific_version"] is None
    assert contract["source_immutable_identity"] is None
    assert contract["contract_status"] == "CONTRACT_BLOCKED_MULTIPLE_REASONS"


def test_multiline_package_pin_is_discovered(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\n"
        "RUN micromamba install -y \\\n"
        "    example=1.2.3 && micromamba clean --all --yes\n"
        'CMD ["example", "--version"]\n',
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["scientific_version"] == "1.2.3"
    assert contract["source_immutable_identity"] == "example=1.2.3"


def test_gpu_requirement_is_recorded_and_blocks_standard_contract(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\n# Requires GPU runtime\nRUN pip install example==1.2.3\n",
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["runtime_gpu_required"] is True
    assert "special runtime dependency requires a separate validation gate" in contract["blocking_reasons"]


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "contracts"
    directory.mkdir()
    (directory / "bad.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(ContractError, match="malformed contract JSON"):
        load_contract_files(directory)


def test_generate_twice_is_byte_identical(repo: Path, tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
        writer.writerow(catalog_row())
    first = tmp_path / "first"
    second = tmp_path / "second"
    source_commit = "a" * 40
    generate_all(catalog, repo, first, source_commit)
    generate_all(catalog, repo, second, source_commit)
    first_files = {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    second_files = {path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()}
    assert first_files == second_files
    validate_output(first, repo)


def test_contract_hash_detects_tampering(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["blocking_reasons"].append("tampered")
    with pytest.raises(ContractError, match="contract SHA256 mismatch"):
        validate_contract(contract, repo)


def test_scientific_version_conflict_is_rejected(repo: Path) -> None:
    first = generate_contract(catalog_row(), repo)
    second = deepcopy(first)
    second["scientific_version"] = "9.9.9"
    second["version_expected_value"] = "9.9.9"
    second["validation_contract_sha256"] = contract_hash(second)
    with pytest.raises(ContractError, match="scientific version conflict"):
        validate_contract_set([first, second], repo)


def test_inventory_json_is_valid_after_generation(repo: Path, tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
        writer.writerow(catalog_row())
    output = tmp_path / "output"
    generate_all(catalog, repo, output, "b" * 40)
    assert json.loads((output / "inventory.json").read_text())[0]["tool_id"] == "example"
