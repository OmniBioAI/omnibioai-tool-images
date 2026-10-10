from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest

import scripts.validation_contracts as vc_module
from scripts.validation_contracts import (
    ContractError,
    _csv_bytes,
    _status_for,
    canonical_json,
    contract_hash,
    generate_all,
    generate_contract,
    load_catalog,
    load_contract_files,
    main,
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


def test_missing_from_line_leaves_base_image_unset(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        'RUN pip install example==1.2.3\nCMD ["example", "--version"]\n',
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["base_image_reference"] is None


def test_malformed_json_command_is_ignored_as_not_executable(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\nRUN pip install example==1.2.3\nCMD [bad]\n",
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["expected_executable"] is None
    assert "no canonical scientific executable is explicitly invoked by the Dockerfile" in contract["blocking_reasons"]


def test_non_string_list_json_command_is_ignored_as_not_executable(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\nRUN pip install example==1.2.3\nCMD [1, 2]\n",
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["expected_executable"] is None


def test_trailing_unterminated_continuation_does_not_crash_parsing(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\n"
        "RUN pip install example==1.2.3\n"
        'CMD ["example", "--version"]\n'
        "RUN apt-get install -y \\\n",
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["scientific_version"] == "1.2.3"


def test_single_version_variable_pin_is_discovered(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\nARG EXAMPLE_VERSION=1.2.3\nCMD [\"example\", \"--version\"]\n",
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["scientific_version"] == "1.2.3"
    assert contract["scientific_version_source"] == "dockerfile_version_variable"
    assert contract["scientific_version_evidence"] == "EXAMPLE_VERSION=1.2.3"
    assert contract["source_type"] == "dockerfile_version_variable"


def test_reviewed_canary_override_is_ignored_when_json_is_malformed(repo: Path) -> None:
    path = repo / "validation-contracts" / "schema-v1"
    path.mkdir(parents=True)
    (path / "example.json").write_text("{not-json", encoding="utf-8")
    contract = generate_contract(catalog_row(), repo)
    assert "pinning" not in contract


def test_reviewed_canary_override_is_ignored_without_pinning_key(repo: Path) -> None:
    path = repo / "validation-contracts" / "schema-v1"
    path.mkdir(parents=True)
    dockerfile_sha256 = vc_module.sha256_bytes(
        (repo / "dockerfiles" / "Dockerfile.example").read_bytes()
    )
    (path / "example.json").write_text(
        json.dumps({"dockerfile_sha256": dockerfile_sha256, "tool_id": "example"}),
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert "pinning" not in contract


def test_reviewed_canary_override_is_ignored_with_bad_pinning_schema(repo: Path) -> None:
    path = repo / "validation-contracts" / "schema-v1"
    path.mkdir(parents=True)
    dockerfile_sha256 = vc_module.sha256_bytes(
        (repo / "dockerfiles" / "Dockerfile.example").read_bytes()
    )
    (path / "example.json").write_text(
        json.dumps(
            {
                "dockerfile_sha256": dockerfile_sha256,
                "pinning": {"pinning_canary_schema_version": 2},
            }
        ),
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert "pinning" not in contract


def test_status_for_no_blockers_is_contract_ready() -> None:
    assert _status_for([]) == "CONTRACT_READY"


def test_generate_contract_requires_dockerfile_to_exist(repo: Path) -> None:
    with pytest.raises(ContractError, match="missing Dockerfile"):
        generate_contract(catalog_row(dockerfile="dockerfiles/Dockerfile.missing"), repo)


def test_unconfirmed_package_mapping_blocks_contract(repo: Path) -> None:
    contract = generate_contract(catalog_row(package_exists="NO"), repo)
    assert "PACKAGE_MAPPING" not in contract["contract_status"]  # sanity: status is a string, not a code list
    assert "exact current GHCR package mapping is not confirmed" in contract["blocking_reasons"]
    assert contract["eligibility"]["package_mapping_confirmed"] is False


def test_runtime_license_dependency_blocks_contract(repo: Path) -> None:
    (repo / "dockerfiles" / "Dockerfile.example").write_text(
        "FROM ubuntu:24.04\n"
        "# requires license_file for activation\n"
        "RUN pip install example==1.2.3\n"
        'CMD ["example", "--version"]\n',
        encoding="utf-8",
    )
    contract = generate_contract(catalog_row(), repo)
    assert contract["runtime_license_required"] is True
    assert "Dockerfile indicates a runtime license dependency requiring separate review" in contract["blocking_reasons"]


def test_load_catalog_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="cannot read population catalog"):
        load_catalog(tmp_path / "missing.csv")


def test_load_catalog_rejects_missing_required_columns(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["tool_id"])
        writer.writeheader()
        writer.writerow({"tool_id": "example"})
    with pytest.raises(ContractError, match="population catalog is empty or missing required columns"):
        load_catalog(catalog)


def test_load_catalog_rejects_empty_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
    with pytest.raises(ContractError, match="population catalog is empty or missing required columns"):
        load_catalog(catalog)


def test_unsupported_schema_version_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["validation_contract_schema_version"] = 2
    with pytest.raises(ContractError, match="unsupported validation contract schema"):
        validate_contract(contract, repo)


def test_empty_tool_id_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["tool_id"] = ""
    with pytest.raises(ContractError, match="missing tool ID"):
        validate_contract(contract, repo)


def test_invalid_contract_status_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["contract_status"] = "NOT_A_REAL_STATUS"
    with pytest.raises(ContractError, match="invalid contract status"):
        validate_contract(contract, repo)


def test_malformed_contract_sha_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["validation_contract_sha256"] = "not-a-sha"
    with pytest.raises(ContractError, match="malformed contract SHA256"):
        validate_contract(contract, repo)


def test_missing_dockerfile_on_disk_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["dockerfile_path"] = "dockerfiles/Dockerfile.does-not-exist"
    with pytest.raises(ContractError, match="missing Dockerfile"):
        validate_contract(contract, repo)


def test_dockerfile_sha_mismatch_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["dockerfile_sha256"] = "a" * 64
    with pytest.raises(ContractError, match="Dockerfile SHA256 mismatch"):
        validate_contract(contract, repo)


def test_missing_package_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["package"] = ""
    with pytest.raises(ContractError, match="missing package"):
        validate_contract(contract, repo)


def test_non_list_blocking_reasons_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(), repo)
    contract["blocking_reasons"] = "not-a-list"
    with pytest.raises(ContractError, match="blocking reasons must be a list"):
        validate_contract(contract, repo)


def _fully_ready_contract(repo: Path) -> dict:
    contract = generate_contract(catalog_row(), repo)
    contract["contract_status"] = "CONTRACT_READY"
    contract["smoke_command"] = ["example", "--smoke"]
    contract["smoke_success_condition"] = "exit 0"
    contract["blocking_reasons"] = []
    contract["validation_contract_sha256"] = contract_hash(contract)
    return contract


def test_ready_contract_with_blocking_reasons_is_rejected(repo: Path) -> None:
    contract = _fully_ready_contract(repo)
    contract["blocking_reasons"] = ["should not be here"]
    contract["validation_contract_sha256"] = contract_hash(contract)
    with pytest.raises(ContractError, match="ready contract has blocking reasons"):
        validate_contract(contract, repo)


def test_ready_contract_without_native_architecture_is_rejected(repo: Path) -> None:
    contract = _fully_ready_contract(repo)
    contract["native_amd64_expected"] = False
    contract["validation_contract_sha256"] = contract_hash(contract)
    with pytest.raises(ContractError, match="ready contract lacks native architecture expectations"):
        validate_contract(contract, repo)


def test_validate_contract_set_rejects_empty_set(repo: Path) -> None:
    with pytest.raises(ContractError, match="empty contract set"):
        validate_contract_set([], repo)


def test_reference_tool_in_target_set_is_rejected(repo: Path) -> None:
    contract = generate_contract(catalog_row(tool_id="bedtools"), repo)
    with pytest.raises(ContractError, match="reference tools present in target set"):
        validate_contract_set([contract], repo)


def test_ineligible_classification_in_target_set_is_rejected(repo: Path) -> None:
    contract = generate_contract(
        catalog_row(repository_classification="ARCHITECTURE_DEPENDENT"), repo
    )
    with pytest.raises(ContractError, match="ineligible classification in target set"):
        validate_contract_set([contract], repo)


def test_csv_bytes_rejects_empty_rows() -> None:
    with pytest.raises(ContractError, match="cannot serialize empty inventory"):
        _csv_bytes([])


def test_generate_all_rejects_non_git_sha_source_commit(repo: Path, tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
        writer.writerow(catalog_row())
    with pytest.raises(ContractError, match="source commit must be a full lowercase Git SHA"):
        generate_all(catalog, repo, tmp_path / "output", "not-a-sha")


def test_generate_all_cleans_up_stale_output_on_rerun(repo: Path, tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
        writer.writerow(catalog_row())
    output = tmp_path / "output"
    generate_all(catalog, repo, output, "a" * 40)
    stray = output / "schema-v1" / "stray-leftover.json"
    stray.write_text("{}", encoding="utf-8")
    assert stray.exists()
    generate_all(catalog, repo, output, "b" * 40)
    assert not stray.exists()
    validate_output(output, repo)


def test_load_contract_files_rejects_non_object_json(tmp_path: Path) -> None:
    directory = tmp_path / "contracts"
    directory.mkdir()
    (directory / "list.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ContractError, match="contract is not an object"):
        load_contract_files(directory)


def _generated_output(repo: Path, tmp_path: Path) -> Path:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
        writer.writerow(catalog_row())
    output = tmp_path / "output"
    generate_all(catalog, repo, output, "c" * 40)
    return output


def test_validate_output_rejects_summary_target_count_mismatch(repo: Path, tmp_path: Path) -> None:
    output = _generated_output(repo, tmp_path)
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["contract_target_count"] = summary["contract_target_count"] + 1
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ContractError, match="summary target count mismatch"):
        validate_output(output, repo)


def test_validate_output_rejects_ready_list_mismatch(repo: Path, tmp_path: Path) -> None:
    output = _generated_output(repo, tmp_path)
    (output / "contract-ready-tools.txt").write_text("bogus-tool\n", encoding="utf-8")
    with pytest.raises(ContractError, match="contract-ready list mismatch"):
        validate_output(output, repo)


def test_validate_output_rejects_batch_mismatch(repo: Path, tmp_path: Path) -> None:
    output = _generated_output(repo, tmp_path)
    (output / "proposed-batch-10.txt").write_text("bogus-tool\n", encoding="utf-8")
    with pytest.raises(ContractError, match="proposed batch mismatch"):
        validate_output(output, repo)


def test_main_generate_uses_current_commit_when_not_supplied(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
        writer.writerow(catalog_row())
    output = tmp_path / "output"
    fixed_commit = "c" * 40
    monkeypatch.setattr(vc_module.subprocess, "check_output", lambda *a, **k: fixed_commit + "\n")
    rc = main(["generate", "--catalog", str(catalog), "--repo-root", str(repo), "--output", str(output)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["contract_target_count"] == 1
    assert json.loads((output / "summary.json").read_text())["source_commit"] == fixed_commit


def test_main_generate_with_explicit_source_commit(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_row()))
        writer.writeheader()
        writer.writerow(catalog_row())
    output = tmp_path / "output"
    rc = main(
        [
            "generate", "--catalog", str(catalog), "--repo-root", str(repo),
            "--output", str(output), "--source-commit", "d" * 40,
        ]
    )
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["contract_target_count"] == 1
    assert printed["contract_ready_count"] == 0


def test_main_validate_command_success_prints_pass(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = _generated_output(repo, tmp_path)
    rc = main(["validate", "--repo-root", str(repo), "--output", str(output)])
    assert rc == 0
    assert "validation contracts: PASS" in capsys.readouterr().out


def test_main_reports_contract_error_and_exits_nonzero(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "output"
    (output / "schema-v1").mkdir(parents=True)
    with pytest.raises(SystemExit) as excinfo:
        main(["validate", "--repo-root", str(repo), "--output", str(output)])
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "validation contracts: FAIL" in captured.err
    assert "empty contract set" in captured.err
