from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.contract_native_validation import (
    GATES,
    RUNNERS,
    TOOLS,
    Commands,
    ValidationError,
    binding_labels,
    contained,
    execute,
    fixture_hashes,
    inspect_sif,
    load_contract,
    main,
    make_plan,
    plan_hash,
    reconcile,
    runtime_argv,
    select_entry,
    smoke_result,
    validate_entry,
    validate_executable,
    validate_plan,
    write_json,
)
from scripts.sif_identity import identity_metadata
from scripts.validation_contracts import contract_hash, validate_contract

COMMIT = "c" * 40
MACHINE = {"amd64": "x86_64", "arm64": "aarch64"}
ELF = {"amd64": ("62", "0"), "arm64": ("183", "0")}


def _dockerfile_text(tool: str, base: str) -> str:
    return f"FROM {base}\nRUN echo {tool}\n"


def build_contract(tool: str, repo: Path, **overrides) -> dict:
    """Build a minimal but schema-valid CONTRACT_READY contract for ``tool``.

    Mirrors the structure produced by scripts.validation_contracts.generate_contract
    but is hand-assembled so every tool can be forced CONTRACT_READY without a
    real Dockerfile corpus.
    """
    base = "mambaorg/micromamba:1.5.8"
    dockerfile_path = f"dockerfiles/Dockerfile.{tool}"
    dockerfile = repo / dockerfile_path
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    text = _dockerfile_text(tool, base)
    dockerfile.write_text(text, encoding="utf-8")
    dockerfile_sha256 = hashlib.sha256(text.encode()).hexdigest()

    if tool == "aicsimageio_extra":
        smoke_inputs = ["in-memory deterministic 2x3 uint8 array"]
    else:
        fixtures_dir = repo / "validation-contracts" / "fixtures"
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = fixtures_dir / f"{tool}.fixture"
        fixture_path.write_text(f"{tool} fixture\n", encoding="utf-8")
        smoke_inputs = [f"validation-contracts/fixtures/{tool}.fixture"]

    smoke_expected_outputs = ["SMOKE_OK"] if tool in ("3ddna_extra", "abricate") else []

    contract = {
        "validation_contract_schema_version": 1,
        "tool_id": tool,
        "display_name": tool,
        "dockerfile_path": dockerfile_path,
        "dockerfile_sha256": dockerfile_sha256,
        "classification": "MULTIARCH_READY",
        "eligibility": {"targeted": True, "package_mapping_confirmed": True, "mapping_status": "EXACT"},
        "package": f"ghcr.io/omnibioai/omnibioai-sif/{tool}",
        "registry_namespace": "ghcr.io/omnibioai/omnibioai-sif",
        "scientific_version": "1.0.0",
        "scientific_version_source": "pinned_package_version",
        "scientific_version_evidence": f"{tool}=1.0.0",
        "expected_executable": tool,
        "version_command": [tool, "--version"],
        "version_parser": f"regex:^{tool} (\\d+\\.\\d+\\.\\d+)$",
        "version_expected_value": "1.0.0",
        "version_output_source": "stdout",
        "version_observation_parser": f"{tool} (\\d+\\.\\d+\\.\\d+)",
        "smoke_command": [tool, "--smoke"],
        "smoke_inputs": smoke_inputs,
        "smoke_expected_outputs": smoke_expected_outputs,
        "smoke_success_condition": "deterministic smoke marker present",
        "expected_architectures": ["amd64", "arm64"],
        "native_amd64_expected": True,
        "native_arm64_expected": True,
        "base_image_reference": base,
        "base_image_architecture_evidence": {"quality": "INFERRED", "statement": "test evidence"},
        "source_type": "pinned_package_version",
        "source_reference": f"{tool}=1.0.0",
        "source_immutable_identity": f"{tool}=1.0.0",
        "runtime_network_required": False,
        "runtime_gpu_required": False,
        "runtime_database_required": False,
        "runtime_reference_data_required": False,
        "runtime_license_required": False,
        "contract_status": "CONTRACT_READY",
        "blocking_reasons": [],
        "evidence": [],
        "contract_confidence": "HIGH",
    }
    contract.update(overrides)
    contract["validation_contract_sha256"] = contract_hash(contract)
    path = repo / "validation-contracts" / "schema-v1" / f"{tool}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, sort_keys=True), encoding="utf-8")
    return contract


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with schema-valid CONTRACT_READY contracts for every TOOLS entry."""
    for tool in TOOLS:
        build_contract(tool, tmp_path)
    return tmp_path


@pytest.fixture
def plan(repo: Path) -> dict:
    return make_plan(repo, COMMIT)


def test_repo_fixture_produces_a_valid_fourteen_entry_plan(repo: Path, plan: dict) -> None:
    assert plan["candidate_count"] == 14
    assert len(plan["entries"]) == 14
    validate_plan(plan, repo, plan["plan_sha256"])


# ---------------------------------------------------------------------------
# contained()
# ---------------------------------------------------------------------------


def test_contained_resolves_relative_path_inside_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.txt").write_text("hi")
    assert contained(tmp_path, "sub/file.txt") == (tmp_path / "sub" / "file.txt").resolve()


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "../escape", "sub/../../escape"])
def test_contained_rejects_invalid_or_escaping_paths(tmp_path: Path, bad: str) -> None:
    (tmp_path / "sub").mkdir()
    with pytest.raises(ValidationError):
        contained(tmp_path, bad)


def test_contained_rejects_non_string_input(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="invalid relative input path"):
        contained(tmp_path, None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# plan_hash() / write_json()
# ---------------------------------------------------------------------------


def test_plan_hash_ignores_existing_plan_sha256_field() -> None:
    base = {"a": 1, "b": 2}
    with_hash = {**base, "plan_sha256": "irrelevant"}
    assert plan_hash(base) == plan_hash(with_hash)


def test_plan_hash_changes_when_content_changes() -> None:
    assert plan_hash({"a": 1}) != plan_hash({"a": 2})


def test_write_json_round_trips_sorted_and_newline_terminated(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    write_json(path, {"b": 1, "a": 2})
    text = path.read_text()
    assert text.endswith("\n")
    assert json.loads(text) == {"a": 2, "b": 1}
    assert list(json.loads(text)) == ["a", "b"]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_commands_records_successful_invocation(tmp_path: Path) -> None:
    run = Commands(tmp_path)
    result = run(["python3", "-c", "import sys; print('out'); print('err', file=sys.stderr)"])
    assert result["returncode"] == 0
    assert result["timed_out"] is False
    assert "out" in result["stdout"]
    assert "err" in result["stderr"]
    log = json.loads((tmp_path / "commands" / "001.json").read_text())
    assert log["returncode"] == 0
    assert (tmp_path / "commands" / "001.stdout").exists()


def test_commands_raises_on_nonzero_exit(tmp_path: Path) -> None:
    run = Commands(tmp_path)
    with pytest.raises(ValidationError, match="command failed"):
        run(["python3", "-c", "import sys; sys.exit(3)"])


def test_commands_raises_and_kills_on_timeout(tmp_path: Path) -> None:
    run = Commands(tmp_path)
    with pytest.raises(ValidationError, match="command failed"):
        run(["python3", "-c", "import time; time.sleep(30)"], timeout=1)
    log = json.loads((tmp_path / "commands" / "001.json").read_text())
    assert log["timed_out"] is True


def test_commands_rejects_oversized_output(tmp_path: Path) -> None:
    run = Commands(tmp_path)
    with pytest.raises(ValidationError, match="exceeds 1 MiB"):
        run(["python3", "-c", "print('x' * (1024 * 1024 + 10))"])


def test_commands_increments_count_and_uses_new_session(tmp_path: Path) -> None:
    run = Commands(tmp_path)
    run(["true"])
    run(["true"])
    assert run.count == 2
    assert (tmp_path / "commands" / "002.json").exists()


# ---------------------------------------------------------------------------
# validate_executable()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_validate_executable_accepts_matching_architecture(arch: str) -> None:
    m1, m2 = ELF[arch]
    text = f"executable=/usr/bin/tool\nmachine={m1} {m2}\n"
    result = validate_executable({"stdout": text}, arch)
    assert result == {"status": "PASS", "architecture": arch, "output": text}


def test_validate_executable_rejects_missing_executable_line() -> None:
    with pytest.raises(ValidationError, match="missing executable path"):
        validate_executable({"stdout": "machine=62 0\n"}, "amd64")


def test_validate_executable_rejects_architecture_mismatch() -> None:
    text = "executable=/usr/bin/tool\nmachine=183 0\n"
    with pytest.raises(ValidationError, match="ELF architecture mismatch"):
        validate_executable({"stdout": text}, "amd64")


def test_validate_executable_rejects_multiple_machine_lines() -> None:
    text = "executable=/usr/bin/tool\nmachine=62 0\nmachine=62 0\n"
    with pytest.raises(ValidationError, match="ELF architecture mismatch"):
        validate_executable({"stdout": text}, "amd64")


# ---------------------------------------------------------------------------
# runtime_argv()
# ---------------------------------------------------------------------------


def test_runtime_argv_oci_phase_shape(tmp_path: Path) -> None:
    argv = runtime_argv("oci", "sha256:" + "a" * 64, "amd64", tmp_path / "work", tmp_path / "fixtures", ["uname", "-m"])
    assert argv[0:2] == ["docker", "run"]
    assert "--network" in argv and "none" in argv
    assert argv[-2:] == ["uname", "-m"]
    assert argv[-6:-2] == ["run", "--no-capture-output", "--prefix", "/opt/conda"]


def test_runtime_argv_sif_phase_shape(tmp_path: Path) -> None:
    argv = runtime_argv("sif", str(tmp_path / "x.sif"), "arm64", tmp_path / "work", tmp_path / "fixtures", ["uname", "-m"])
    assert argv[0:3] == ["sudo", "apptainer", "exec"]
    assert argv[-2:] == ["uname", "-m"]


def test_runtime_argv_rejects_unknown_phase(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unknown artifact phase"):
        runtime_argv("bogus", "artifact", "amd64", tmp_path, tmp_path, ["uname", "-m"])


# ---------------------------------------------------------------------------
# inspect_sif()
# ---------------------------------------------------------------------------


def _labels(arch: str) -> dict:
    return {
        "org.label-schema.build-arch": arch,
        "org.omnibioai.tool": "abricate",
    }


def test_inspect_sif_accepts_matching_labels() -> None:
    metadata = {"data": {"attributes": {"labels": _labels("amd64")}}}
    inspect_sif(metadata, {"org.omnibioai.tool": "abricate"}, "amd64")


def test_inspect_sif_rejects_arch_mismatch() -> None:
    metadata = {"data": {"attributes": {"labels": _labels("amd64")}}}
    with pytest.raises(ValidationError, match="architecture or inherited binding"):
        inspect_sif(metadata, {}, "arm64")


def test_inspect_sif_rejects_mismatched_expected_label() -> None:
    metadata = {"data": {"attributes": {"labels": _labels("amd64")}}}
    with pytest.raises(ValidationError, match="architecture or inherited binding"):
        inspect_sif(metadata, {"org.omnibioai.tool": "other"}, "amd64")


def test_inspect_sif_rejects_missing_structure() -> None:
    with pytest.raises(ValidationError, match="missing structured SIF label metadata"):
        inspect_sif({}, {}, "amd64")


def test_inspect_sif_rejects_non_mapping_labels() -> None:
    metadata = {"data": {"attributes": {"labels": None}}}
    with pytest.raises(ValidationError, match="missing structured SIF label metadata"):
        inspect_sif(metadata, {}, "amd64")


# ---------------------------------------------------------------------------
# binding_labels()
# ---------------------------------------------------------------------------


def test_binding_labels_shape(repo: Path, plan: dict) -> None:
    entry = next(e for e in plan["entries"] if e["tool_id"] == "abricate" and e["arch"] == "amd64")
    contract = load_contract(repo, "abricate")
    labels = binding_labels(contract, plan, entry, "mambaorg/micromamba:1.5.8@sha256:" + "a" * 64)
    assert labels["org.omnibioai.tool"] == "abricate"
    assert labels["org.omnibioai.source-commit"] == plan["source_commit"]
    assert labels["org.omnibioai.target-platform"] == "linux/amd64"
    assert labels["org.omnibioai.base-identity"].startswith("mambaorg/micromamba:1.5.8@sha256:")


# ---------------------------------------------------------------------------
# smoke_result()
# ---------------------------------------------------------------------------


def _base_smoke_raw(**overrides) -> dict:
    result = {"returncode": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": ""}
    result.update(overrides)
    return result


def test_smoke_result_rejects_failed_command(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "abricate")
    with pytest.raises(ValidationError, match="failed or unbounded smoke"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw(returncode=1))


def test_smoke_result_rejects_unbounded_duration(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "abricate")
    with pytest.raises(ValidationError, match="failed or unbounded smoke"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw(elapsed_seconds=61))


def test_smoke_result_marker_tool_passes_when_markers_present(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "abricate")
    result = smoke_result(contract, repo, tmp_path, _base_smoke_raw(stdout="SMOKE_OK\n"))
    assert result == {"status": "PASS", "scope": contract["smoke_success_condition"]}


def test_smoke_result_marker_tool_fails_when_markers_missing(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "abricate")
    with pytest.raises(ValidationError, match="smoke output markers missing"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw(stdout="nope\n"))


def test_smoke_result_marker_tool_checks_stderr_too(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "3ddna_extra")
    result = smoke_result(contract, repo, tmp_path, _base_smoke_raw(stderr="SMOKE_OK\n"))
    assert result["status"] == "PASS"


def test_smoke_result_trivial_pass_tools(repo: Path, tmp_path: Path) -> None:
    for tool in ("accelerate", "aicsimageio_extra", "airr_extra"):
        contract = load_contract(repo, tool)
        result = smoke_result(contract, repo, tmp_path, _base_smoke_raw())
        assert result == {"status": "PASS", "scope": contract["smoke_success_condition"]}


def _gff3_rows(ids: list[str]) -> str:
    lines = ["##gff-version 3"]
    for identifier in ids:
        lines.append(f"chr1\tsrc\tfeat\t1\t10\t.\t+\t.\tID={identifier}")
    return "\n".join(lines) + "\n"


def test_smoke_result_agat_extra_passes_with_valid_gff3(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "agat_extra")
    (tmp_path / "smoke.gff3").write_text(_gff3_rows(["gene1", "transcript1", "exon1", "exon2"]))
    result = smoke_result(contract, repo, tmp_path, _base_smoke_raw())
    assert result == {"status": "PASS", "scope": contract["smoke_success_condition"]}


def test_smoke_result_agat_extra_rejects_missing_output(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "agat_extra")
    with pytest.raises(ValidationError, match="missing or unsafe AGAT output"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw())


def test_smoke_result_agat_extra_rejects_oversized_output(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "agat_extra")
    (tmp_path / "smoke.gff3").write_text("##gff-version 3\n" + "x" * (1024 * 1024 + 1))
    with pytest.raises(ValidationError, match="missing or unsafe AGAT output"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw())


def test_smoke_result_agat_extra_rejects_bad_header(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "agat_extra")
    (tmp_path / "smoke.gff3").write_text("not-gff3\n")
    with pytest.raises(ValidationError, match="invalid AGAT GFF3 output"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw())


def test_smoke_result_agat_extra_rejects_wrong_column_count(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "agat_extra")
    (tmp_path / "smoke.gff3").write_text("##gff-version 3\nchr1\tsrc\tfeat\n")
    with pytest.raises(ValidationError, match="invalid AGAT GFF3 output"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw())


def test_smoke_result_agat_extra_rejects_missing_feature_ids(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "agat_extra")
    (tmp_path / "smoke.gff3").write_text(_gff3_rows(["gene1"]))
    with pytest.raises(ValidationError, match="missing expected feature IDs"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw())


def test_smoke_result_alevin_fry_delegates_to_validate_smoke_outputs(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = load_contract(repo, "alevin_fry")
    sentinel = {"status": "PASS", "smoke_outputs_verified": True, "contract_sha256": "x"}
    calls = []

    def fake(contract_arg, repo_arg, work_arg, *, returncode, elapsed_seconds):
        calls.append((contract_arg["tool_id"], repo_arg, work_arg, returncode, elapsed_seconds))
        return sentinel

    monkeypatch.setattr("scripts.contract_native_validation.validate_smoke_outputs", fake)
    raw = _base_smoke_raw(returncode=0, elapsed_seconds=5.0)
    result = smoke_result(contract, repo, tmp_path, raw)
    assert result is sentinel
    assert calls == [("alevin_fry", repo, tmp_path, 0, 5.0)]


def test_smoke_result_rejects_unsupported_tool_outside_known_semantics(repo: Path, tmp_path: Path) -> None:
    contract = load_contract(repo, "abricate")
    contract = dict(contract, tool_id="not_a_real_tool")
    with pytest.raises(ValidationError, match="unsupported smoke semantics"):
        smoke_result(contract, repo, tmp_path, _base_smoke_raw())


# ---------------------------------------------------------------------------
# load_contract() / fixture_hashes()
# ---------------------------------------------------------------------------


def test_load_contract_rejects_tool_outside_canary(repo: Path) -> None:
    with pytest.raises(ValidationError, match="tool outside exact approved canary"):
        load_contract(repo, "samtools")


def test_load_contract_returns_valid_contract(repo: Path) -> None:
    contract = load_contract(repo, "abricate")
    assert contract["tool_id"] == "abricate"
    validate_contract(contract, repo)


def test_load_contract_rejects_tool_id_mismatch(repo: Path) -> None:
    build_contract("abricate", repo, tool_id="accelerate")
    with pytest.raises(ValidationError, match="wrong or blocked contract"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_blocked_status(repo: Path) -> None:
    contract = build_contract(
        "abricate", repo, contract_status="CONTRACT_BLOCKED_SMOKE",
        blocking_reasons=["no smoke"],
    )
    with pytest.raises(ValidationError, match="wrong or blocked contract"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_non_true_native_architecture_flag(repo: Path) -> None:
    # A truthy-but-not-exactly-True value passes validation_contracts' own
    # "ready contract lacks native architecture expectations" falsy check
    # but still fails contract_native_validation's stricter identity check.
    build_contract("abricate", repo, native_arm64_expected=1)
    with pytest.raises(ValidationError, match="both native architectures must be expected"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_network_required(repo: Path) -> None:
    build_contract("abricate", repo, runtime_network_required=True)
    with pytest.raises(ValidationError, match="only network-free contracts"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_command_with_empty_argument(repo: Path) -> None:
    build_contract("abricate", repo, version_command=["abricate", ""])
    with pytest.raises(ValidationError, match="commands must be nonempty argument arrays"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_command_with_nul_byte(repo: Path) -> None:
    build_contract("abricate", repo, version_command=["abricate", "\x00bad"])
    with pytest.raises(ValidationError, match="commands must be nonempty argument arrays"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_non_list_command(repo: Path) -> None:
    build_contract("abricate", repo, smoke_command="abricate --smoke")
    with pytest.raises(ValidationError, match="commands must be nonempty argument arrays"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_bad_version_output_source(repo: Path) -> None:
    build_contract("abricate", repo, version_output_source="elsewhere")
    with pytest.raises(ValidationError, match="missing version stream contract"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_missing_version_observation_parser(repo: Path) -> None:
    build_contract("abricate", repo, version_observation_parser="")
    with pytest.raises(ValidationError, match="missing version stream contract"):
        load_contract(repo, "abricate")


def test_load_contract_rejects_fixture_hash_mismatch(repo: Path) -> None:
    build_contract("abricate", repo, fixture_sha256={"validation-contracts/fixtures/abricate.fixture": "0" * 64})
    with pytest.raises(ValidationError, match="declared fixture hashes differ"):
        load_contract(repo, "abricate")


def test_fixture_hashes_skips_aicsimageio_sentinel(repo: Path) -> None:
    contract = load_contract(repo, "aicsimageio_extra")
    assert fixture_hashes(contract, repo) == {}


def test_fixture_hashes_computes_sha256_for_real_fixture(repo: Path) -> None:
    contract = load_contract(repo, "abricate")
    hashes = fixture_hashes(contract, repo)
    expected = hashlib.sha256((repo / "validation-contracts/fixtures/abricate.fixture").read_bytes()).hexdigest()
    assert hashes == {"validation-contracts/fixtures/abricate.fixture": expected}


def test_fixture_hashes_accepts_matching_declared_hash(repo: Path) -> None:
    real = hashlib.sha256((repo / "validation-contracts/fixtures/abricate.fixture").read_bytes()).hexdigest()
    build_contract("abricate", repo, fixture_sha256={"validation-contracts/fixtures/abricate.fixture": real})
    contract = load_contract(repo, "abricate")
    assert fixture_hashes(contract, repo) == {"validation-contracts/fixtures/abricate.fixture": real}


# ---------------------------------------------------------------------------
# make_plan() / validate_plan() / select_entry()
# ---------------------------------------------------------------------------


def test_make_plan_rejects_non_full_sha_commit(repo: Path) -> None:
    with pytest.raises(ValidationError, match="full Git SHA"):
        make_plan(repo, "not-a-sha")


def test_make_plan_rejects_short_commit(repo: Path) -> None:
    with pytest.raises(ValidationError, match="full Git SHA"):
        make_plan(repo, "a" * 39)


def test_make_plan_rejects_package_collision(repo: Path) -> None:
    build_contract("accelerate", repo, package="ghcr.io/omnibioai/omnibioai-sif/abricate")
    with pytest.raises(ValidationError, match="package collision"):
        make_plan(repo, COMMIT)


def test_make_plan_is_deterministic(repo: Path) -> None:
    first = make_plan(repo, COMMIT)
    second = make_plan(repo, COMMIT)
    assert first == second


def test_validate_plan_rejects_hash_mismatch(repo: Path, plan: dict) -> None:
    with pytest.raises(ValidationError, match="plan hash mismatch"):
        validate_plan(plan, repo, "0" * 64)


def test_validate_plan_rejects_tampered_plan_with_matching_claimed_hash(repo: Path, plan: dict) -> None:
    tampered = dict(plan)
    tampered["candidate_count"] = 99
    with pytest.raises(ValidationError, match="plan hash mismatch"):
        validate_plan(tampered, repo, plan["plan_sha256"])


def test_validate_plan_rejects_non_dict(repo: Path) -> None:
    with pytest.raises(ValidationError, match="plan hash mismatch"):
        validate_plan([], repo, "irrelevant")  # type: ignore[arg-type]


def test_validate_plan_rejects_authorized_publication(repo: Path, plan: dict) -> None:
    tampered = dict(plan, publication_authorized=True)
    tampered["plan_sha256"] = plan_hash(tampered)
    with pytest.raises(ValidationError, match="invalid authorization or schema type"):
        validate_plan(tampered, repo, tampered["plan_sha256"])


def test_validate_plan_rejects_non_int_schema_version(repo: Path, plan: dict) -> None:
    tampered = dict(plan, validation_plan_schema_version="1")
    tampered["plan_sha256"] = plan_hash(tampered)
    with pytest.raises(ValidationError, match="invalid authorization or schema type"):
        validate_plan(tampered, repo, tampered["plan_sha256"])


def test_validate_plan_rejects_source_drift(repo: Path, plan: dict) -> None:
    # Mutate a fixture referenced by one contract after the plan was produced.
    # The contract itself still validates (it has no declared fixture_sha256
    # to contradict), but make_plan() now recomputes a different fixture hash
    # than the one frozen into the plan's entries.
    (repo / "validation-contracts" / "fixtures" / "abricate.fixture").write_text("drifted\n")
    with pytest.raises(ValidationError, match="plan membership or current source evidence mismatch"):
        validate_plan(plan, repo, plan["plan_sha256"])


def test_select_entry_returns_matching_entry_and_contract(repo: Path, plan: dict) -> None:
    entry, contract = select_entry(plan, repo, plan["plan_sha256"], "abricate", "amd64")
    assert entry["tool_id"] == "abricate"
    assert entry["arch"] == "amd64"
    assert contract["tool_id"] == "abricate"


def test_select_entry_rejects_missing_candidate(repo: Path, plan: dict) -> None:
    # The plan itself stays fully valid; only the requested (tool, arch) pair
    # fails to resolve to exactly one entry, isolating select_entry's own check.
    with pytest.raises(ValidationError, match="candidate missing or duplicated"):
        select_entry(plan, repo, plan["plan_sha256"], "abricate", "unknown-arch")


# ---------------------------------------------------------------------------
# Golden record builder shared by validate_entry() and reconcile() tests.
# ---------------------------------------------------------------------------


def golden_record(tool: str, arch: str, plan: dict, repo: Path, work_root: Path, *, monkeypatch=None) -> tuple[dict, dict, dict]:
    """Hand-build a self-consistent PASS record for (tool, arch) without any subprocess.

    Mirrors the structure execute() produces, using the real helper functions
    (validate_executable, validate_version_evidence, smoke_result, identity_metadata,
    binding_labels, inspect_sif-compatible metadata) so the record is a faithful,
    non-vacuous instance of what native execution evidence looks like.
    """
    from scripts.validation_smoke import validate_version_evidence

    entry, contract = select_entry(plan, repo, plan["plan_sha256"], tool, arch)
    base_identity = contract["base_image_reference"] + "@sha256:" + "b" * 64
    labels = binding_labels(contract, plan, entry, base_identity)
    machine = MACHINE[arch]
    m1, m2 = ELF[arch]

    oci_archive_sha256 = hashlib.sha256(f"{tool}-{arch}-archive".encode()).hexdigest()
    oci_identity = "sha256:" + hashlib.sha256(f"{tool}-{arch}-config".encode()).hexdigest()
    sif_sha256 = hashlib.sha256(f"{tool}-{arch}-sif".encode()).hexdigest()

    runner_arch = {"amd64": "X64", "arm64": "ARM64"}[arch]
    runner_evidence = {
        "runner": {"os": "Linux", "arch": runner_arch},
        "uname_m": machine,
        "target_architecture": arch,
        "execution_mode": "NATIVE",
    }

    exe_output = f"executable=/usr/bin/{contract['expected_executable']}\nmachine={m1} {m2}\n"
    executable = validate_executable({"stdout": exe_output}, arch)

    version_stdout = f"{tool} 1.0.0\n"
    version_raw = {
        "argv": ["wrapped", *contract["version_command"]],
        "returncode": 0, "elapsed_seconds": 0.5, "timed_out": False,
        "stdout": version_stdout, "stderr": "",
    }
    version = validate_version_evidence(contract, repo, stdout=version_stdout, stderr="", returncode=0)

    smoke_stdout = "SMOKE_OK\n" if tool in ("3ddna_extra", "abricate") else ""
    smoke_raw = {
        "argv": ["wrapped", *contract["smoke_command"]],
        "returncode": 0, "elapsed_seconds": 0.5, "timed_out": False,
        "stdout": smoke_stdout, "stderr": "",
    }

    phases = {}
    for phase in ("oci", "sif"):
        work = work_root / f"work-{phase}"
        work.mkdir(parents=True, exist_ok=True)
        if tool == "agat_extra":
            (work / "smoke.gff3").write_text(_gff3_rows(["gene1", "transcript1", "exon1", "exon2"]))
        smoke = smoke_result(contract, repo, work, smoke_raw)
        phases[phase] = {
            "runtime_architecture": machine, "executable": executable,
            "version": version, "version_raw": version_raw,
            "smoke": smoke, "smoke_raw": smoke_raw,
        }

    candidate = {
        "schema_version": "1", "tool_id": tool, "scientific_version": contract["scientific_version"],
        "target_architecture": arch, "source_commit": plan["source_commit"],
        "dockerfile_path": contract["dockerfile_path"], "dockerfile_sha256": entry["dockerfile_sha256"],
        "base_image_identity": base_identity, "oci_source_identity": oci_identity, "sif_sha256": sif_sha256,
        "expected_executable": contract["expected_executable"],
        "version_evidence": phases["oci"]["version"]["observed_version"],
        "smoke_status": "PASS", "provenance_status": "PASS",
        "build_inputs": {
            "validation_contract_sha256": entry["contract_sha256"],
            "fixture_sha256": entry["fixture_sha256"],
            "source_immutable_identity": contract["source_immutable_identity"],
        },
    }

    record = {
        "tool_id": tool, "arch": arch, "source_commit": plan["source_commit"], "plan_sha256": plan["plan_sha256"],
        "contract_sha256": entry["contract_sha256"], "dockerfile_sha256": entry["dockerfile_sha256"],
        "fixture_sha256": entry["fixture_sha256"], "publication_authorized": False, "status": "PASS",
        "gates": {gate: "PASS" for gate in GATES},
        "runner_evidence": runner_evidence,
        "oci_identity": oci_identity, "oci_archive_sha256": oci_archive_sha256,
        "oci_config_architecture": arch, "oci_config_os": "linux", "docker_archive_manifest_count": 1,
        "sif_inspect": {"data": {"attributes": {"labels": {**labels, "org.label-schema.build-arch": arch}}}},
        "oci": phases["oci"], "sif": phases["sif"],
        "sif_sha256": sif_sha256,
        "conversion": {"archive_sha256": oci_archive_sha256, "sif_sha256": sif_sha256, "transport": "docker-archive"},
        "sbom_status": "SBOM_OPTIONAL_NOT_GENERATED",
        "candidate": identity_metadata(candidate),
    }
    return record, entry, contract


@pytest.fixture
def fake_alevin_fry_smoke(monkeypatch: pytest.MonkeyPatch):
    """alevin_fry's real smoke validation is exhaustively covered by
    test_validation_smoke.py; here it's a dependency of contract_native_validation's
    own dispatch and evidence-binding logic, so a deterministic stand-in keeps these
    tests focused on this module without re-deriving MatrixMarket fixtures."""

    def fake(contract, repo_root, work, *, returncode, elapsed_seconds):
        return {
            "status": "PASS",
            "smoke_outputs_verified": True,
            "contract_sha256": contract["validation_contract_sha256"],
            "elapsed_echo": elapsed_seconds,
        }

    monkeypatch.setattr("scripts.contract_native_validation.validate_smoke_outputs", fake)
    return fake


# ---------------------------------------------------------------------------
# validate_entry()
# ---------------------------------------------------------------------------


def test_golden_record_satisfies_validate_entry(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    validate_entry(record, entry, plan, contract, repo)


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_golden_record_satisfies_validate_entry_for_both_architectures(repo: Path, plan: dict, tmp_path: Path, arch: str) -> None:
    record, entry, contract = golden_record("abricate", arch, plan, repo, tmp_path / "evidence")
    validate_entry(record, entry, plan, contract, repo)


def test_golden_record_satisfies_validate_entry_for_agat_extra(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("agat_extra", "amd64", plan, repo, tmp_path / "evidence")
    validate_entry(record, entry, plan, contract, repo)


def test_golden_record_satisfies_validate_entry_for_alevin_fry(
    repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke
) -> None:
    record, entry, contract = golden_record("alevin_fry", "amd64", plan, repo, tmp_path / "evidence")
    validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_publication_authorized(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["publication_authorized"] = True
    with pytest.raises(ValidationError, match="publication is not authorized"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_identity_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["arch"] = "arm64"
    with pytest.raises(ValidationError, match="result identity mismatch"):
        validate_entry(record, entry, plan, contract, repo)


@pytest.mark.parametrize(
    "key", ["source_commit", "plan_sha256", "contract_sha256", "dockerfile_sha256", "fixture_sha256"]
)
def test_validate_entry_rejects_binding_mismatch(repo: Path, plan: dict, tmp_path: Path, key: str) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record[key] = "tampered" if key != "fixture_sha256" else {"tampered": "x"}
    with pytest.raises(ValidationError, match=f"result binding mismatch: {key}"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_missing_binding_key(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    del record["plan_sha256"]
    with pytest.raises(ValidationError, match="result binding mismatch: plan_sha256"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_non_pass_status(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["status"] = "FAIL"
    with pytest.raises(ValidationError, match="missing or failed terminal gates"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_incomplete_gates(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    del record["gates"]["provenance"]
    with pytest.raises(ValidationError, match="missing or failed terminal gates"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_failed_gate(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["gates"]["identity"] = "FAIL"
    with pytest.raises(ValidationError, match="failed gate"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_runner_target_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["runner_evidence"]["target_architecture"] = "arm64"
    record["runner_evidence"]["uname_m"] = "aarch64"
    record["runner_evidence"]["runner"]["arch"] = "ARM64"
    with pytest.raises(ValidationError, match="runner evidence target mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_candidate_identity_tamper(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    # identity_metadata() always re-derives operational_provenance (defaulting to {}
    # when absent); deleting it from the stored candidate makes the recomputed
    # identity structurally disagree with the stored dict without touching any
    # hash-relevant field (so it fails validate_entry's own equality check rather
    # than identity_metadata's internal supplied-hash check).
    del record["candidate"]["operational_provenance"]
    with pytest.raises(ValidationError, match="candidate canonical identity mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_candidate_content_identity_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    tampered_sif = hashlib.sha256(b"other-sif").hexdigest()
    candidate = dict(record["candidate"])
    candidate["sif_sha256"] = tampered_sif
    candidate.pop("build_identity_sha256")
    record["candidate"] = identity_metadata(candidate)
    with pytest.raises(ValidationError, match="candidate content identity mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_identity_contract_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    candidate = dict(record["candidate"])
    candidate["build_inputs"] = dict(candidate["build_inputs"], validation_contract_sha256="0" * 64)
    candidate.pop("build_identity_sha256")
    record["candidate"] = identity_metadata(candidate)
    with pytest.raises(ValidationError, match="identity contract mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_identity_fixture_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    candidate = dict(record["candidate"])
    candidate["build_inputs"] = dict(candidate["build_inputs"], fixture_sha256={"other": "x"})
    candidate.pop("build_identity_sha256")
    record["candidate"] = identity_metadata(candidate)
    with pytest.raises(ValidationError, match="identity fixture or source mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_scientific_contract_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    candidate = dict(record["candidate"])
    candidate["expected_executable"] = "wrong-executable"
    candidate.pop("build_identity_sha256")
    record["candidate"] = identity_metadata(candidate)
    with pytest.raises(ValidationError, match="candidate scientific contract mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_unbound_base_image_identity(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    candidate = dict(record["candidate"])
    candidate["base_image_identity"] = "other/image@sha256:" + "b" * 64
    candidate.pop("build_identity_sha256")
    record["candidate"] = identity_metadata(candidate)
    with pytest.raises(ValidationError, match="unbound base image identity"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_malformed_oci_identity(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    # Keep candidate.oci_source_identity equal to record.oci_identity (so the
    # earlier content-identity binding check passes) while making both malformed,
    # to isolate the later explicit OCI-identity format check.
    candidate = dict(record["candidate"])
    candidate["oci_source_identity"] = "not-a-digest"
    candidate.pop("build_identity_sha256")
    record["candidate"] = identity_metadata(candidate)
    record["oci_identity"] = "not-a-digest"
    with pytest.raises(ValidationError, match="malformed OCI identity"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_missing_archive_identity(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci_archive_sha256"] = "not-hex"
    with pytest.raises(ValidationError, match="missing archive identity"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_conversion_binding_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["conversion"]["transport"] = "other"
    with pytest.raises(ValidationError, match="OCI/SIF conversion binding mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_sif_structure_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["sif_inspect"]["data"]["attributes"]["labels"]["org.label-schema.build-arch"] = "arm64"
    with pytest.raises(ValidationError, match="architecture or inherited binding"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_phase_runtime_architecture_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["runtime_architecture"] = "aarch64"
    with pytest.raises(ValidationError, match="phase runtime architecture mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_phase_executable_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    # Change a field other than "output": recomputing validate_executable() from
    # the (unchanged) output now disagrees with the stored dict.
    record["oci"]["executable"] = dict(record["oci"]["executable"], architecture="arm64")
    with pytest.raises(ValidationError, match="phase executable mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_version_command_drift(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["version_raw"]["argv"] = ["other", "--version"]
    with pytest.raises(ValidationError, match="version command drift"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_version_timed_out(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["version_raw"]["timed_out"] = True
    with pytest.raises(ValidationError, match="version duration mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_version_elapsed_out_of_range(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["version_raw"]["elapsed_seconds"] = 61
    with pytest.raises(ValidationError, match="version duration mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_phase_version_evidence_mismatch(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["version"] = dict(record["oci"]["version"], observed_version="9.9.9")
    with pytest.raises(ValidationError, match="phase version evidence mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_smoke_command_drift(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["smoke_raw"]["argv"] = ["other", "--smoke"]
    with pytest.raises(ValidationError, match="phase smoke command, status or duration mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_smoke_nonzero_returncode(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["smoke_raw"]["returncode"] = 1
    with pytest.raises(ValidationError, match="phase smoke command, status or duration mismatch"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_smoke_result_missing(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["smoke"] = {"status": "FAIL", "scope": contract["smoke_success_condition"]}
    with pytest.raises(ValidationError, match="missing smoke result"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_missing_bound_alevin_fry_smoke_evidence(
    repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke
) -> None:
    record, entry, contract = golden_record("alevin_fry", "amd64", plan, repo, tmp_path / "evidence")
    record["oci"]["smoke"] = dict(record["oci"]["smoke"], smoke_outputs_verified=False)
    with pytest.raises(ValidationError, match="missing bound numerical smoke evidence"):
        validate_entry(record, entry, plan, contract, repo)


def test_validate_entry_rejects_unexpected_sbom_status(repo: Path, plan: dict, tmp_path: Path) -> None:
    record, entry, contract = golden_record("abricate", "amd64", plan, repo, tmp_path / "evidence")
    record["sbom_status"] = "SBOM_GENERATED"
    with pytest.raises(ValidationError, match="unexpected SBOM semantics"):
        validate_entry(record, entry, plan, contract, repo)


# ---------------------------------------------------------------------------
# reconcile()
# ---------------------------------------------------------------------------


def _build_all_records(plan: dict, repo: Path, tmp_path: Path) -> tuple[list[dict], dict]:
    records = []
    roots = {}
    for tool in TOOLS:
        for arch in RUNNERS:
            root = tmp_path / f"evidence-{tool}-{arch}"
            record, _, _ = golden_record(tool, arch, plan, repo, root)
            records.append(record)
            roots[(tool, arch)] = root
    return records, roots


def test_reconcile_happy_path_with_all_fourteen_real_records(
    repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke
) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    result = reconcile(plan, repo, plan["plan_sha256"], records, roots)
    assert result == {
        "status": "PASS", "native_validated_entries": 14, "plan_sha256": plan["plan_sha256"],
        "publication_authorized": False, "release_complete": False,
    }


def test_reconcile_rejects_wrong_record_count(repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    with pytest.raises(ValidationError, match="terminal matrix missing, duplicate or unexpected entries"):
        reconcile(plan, repo, plan["plan_sha256"], records[:-1], roots)


def test_reconcile_rejects_duplicate_keys(repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    records[-1] = dict(records[0])
    with pytest.raises(ValidationError, match="terminal matrix missing, duplicate or unexpected entries"):
        reconcile(plan, repo, plan["plan_sha256"], records, roots)


def test_reconcile_rejects_unexpected_key(repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    records[0] = dict(records[0], tool_id="abricate-typo")
    with pytest.raises(ValidationError, match="terminal matrix missing, duplicate or unexpected entries"):
        reconcile(plan, repo, plan["plan_sha256"], records, roots)


def test_reconcile_rejects_missing_evidence_root(repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    key = (records[0]["tool_id"], records[0]["arch"])
    del roots[key]
    with pytest.raises(ValidationError, match="missing or unexpected evidence directories"):
        reconcile(plan, repo, plan["plan_sha256"], records, roots)


def test_reconcile_rejects_extra_evidence_root(repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    roots[("abricate", "unknown-arch")] = tmp_path / "extra"
    with pytest.raises(ValidationError, match="missing or unexpected evidence directories"):
        reconcile(plan, repo, plan["plan_sha256"], records, roots)


def test_reconcile_propagates_validate_entry_failure(repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    for record in records:
        if record["tool_id"] == "abricate" and record["arch"] == "amd64":
            record["status"] = "FAIL"
    with pytest.raises(ValidationError, match="missing or failed terminal gates"):
        reconcile(plan, repo, plan["plan_sha256"], records, roots)


def test_reconcile_rejects_retained_smoke_evidence_drift(
    repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke
) -> None:
    # alevin_fry's mocked smoke validator echoes elapsed_seconds, so changing
    # smoke_raw's elapsed_seconds between record construction and reconcile's
    # recheck makes the recomputed smoke result disagree with the stored one,
    # without failing any earlier returncode/duration/argv check.
    records, roots = _build_all_records(plan, repo, tmp_path)
    for record in records:
        if record["tool_id"] == "alevin_fry" and record["arch"] == "amd64":
            record["oci"]["smoke_raw"] = dict(record["oci"]["smoke_raw"], elapsed_seconds=0.9)
    with pytest.raises(ValidationError, match="retained smoke evidence differs from result"):
        reconcile(plan, repo, plan["plan_sha256"], records, roots)


def test_reconcile_rejects_invalid_plan(repo: Path, plan: dict, tmp_path: Path, fake_alevin_fry_smoke) -> None:
    records, roots = _build_all_records(plan, repo, tmp_path)
    with pytest.raises(ValidationError, match="plan hash mismatch"):
        reconcile(plan, repo, "0" * 64, records, roots)


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------


def make_fake_run(output: Path, repo: Path, tool: str, arch: str, commit: str, contract: dict, plan: dict, entry: dict):
    """Simulate git/docker/apptainer subprocess calls for execute()'s happy path.

    No real git/docker/apptainer is invoked. File artifacts that execute() later
    reads back directly (the OCI archive and the SIF file) are written as real
    bytes on disk so sha256_file() and tarfile-free identity hashing behave
    naturally; archive_identity() itself is monkeypatched separately since it
    normally parses a real docker-produced tar archive.
    """
    machine = MACHINE[arch]
    m1, m2 = ELF[arch]
    base = contract["base_image_reference"]
    base_identity = base + "@sha256:" + "a" * 64
    labels = binding_labels(contract, plan, entry, base_identity)

    def resp(stdout="", stderr="", returncode=0, elapsed=0.1, timed_out=False, argv=None):
        return {"argv": argv or [], "returncode": returncode, "elapsed_seconds": elapsed,
                "timed_out": timed_out, "stdout": stdout, "stderr": stderr}

    def run(argv, *, timeout=60):
        if argv == ["uname", "-m"]:
            return resp(stdout=machine + "\n", argv=argv)
        if argv[:3] == ["git", "-C", str(repo)] and argv[3:5] == ["rev-parse", "HEAD"]:
            return resp(stdout=commit + "\n", argv=argv)
        if argv[:3] == ["git", "-C", str(repo)] and "status" in argv:
            return resp(stdout="", argv=argv)
        if argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            return resp(stdout=f"Digest: sha256:{'a' * 64}\n", argv=argv)
        if argv[:3] == ["docker", "buildx", "build"]:
            return resp(argv=argv)
        if argv[:2] == ["docker", "save"]:
            archive_path = Path(argv[3])
            archive_path.write_bytes(f"FAKE-ARCHIVE-{tool}-{arch}".encode())
            return resp(argv=argv)
        if argv[:3] == ["sudo", "apptainer", "build"]:
            sif_path = Path(argv[-2])
            sif_path.write_bytes(f"FAKE-SIF-{tool}-{arch}".encode())
            return resp(argv=argv)
        if argv[:3] == ["sudo", "apptainer", "inspect"]:
            payload = {"data": {"attributes": {"labels": {**labels, "org.label-schema.build-arch": arch}}}}
            return resp(stdout=json.dumps(payload), argv=argv)
        if len(argv) > 2 and argv[-2:] == ["uname", "-m"]:
            return resp(stdout=machine + "\n", argv=argv)
        if len(argv) > 2 and argv[-2] == "probe":
            exe = argv[-1]
            return resp(stdout=f"executable=/usr/bin/{exe}\nmachine={m1} {m2}\n", argv=argv)
        vcmd = contract["version_command"]
        if argv[-len(vcmd):] == vcmd:
            return resp(stdout=f"{tool} 1.0.0\n", argv=argv)
        scmd = contract["smoke_command"]
        if argv[-len(scmd):] == scmd:
            stdout = "SMOKE_OK\n" if tool in ("3ddna_extra", "abricate") else ""
            if tool == "agat_extra":
                phase = "oci" if argv[0] == "docker" else "sif"
                work = output / f"work-{phase}"
                (work / "smoke.gff3").write_text(_gff3_rows(["gene1", "transcript1", "exon1", "exon2"]))
            return resp(stdout=stdout, argv=argv)
        raise AssertionError(f"unexpected command in fake run: {argv}")

    return run


def _fake_archive_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(path: Path, tool: str, arch: str, commit: str, dockerfile_sha256: str) -> dict:
        data = path.read_bytes()
        return {
            "oci_identity": "sha256:" + hashlib.sha256(data + b"-config").hexdigest(),
            "oci_archive_sha256": hashlib.sha256(data).hexdigest(),
            "oci_config_architecture": arch,
            "oci_config_os": "linux",
            "docker_archive_manifest_count": 1,
        }

    monkeypatch.setattr("scripts.contract_native_validation.archive_identity", fake)


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_execute_happy_path(
    repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, arch: str
) -> None:
    _fake_archive_identity(monkeypatch)
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", {"amd64": "X64", "arm64": "ARM64"}[arch])
    entry, contract = select_entry(plan, repo, plan["plan_sha256"], "abricate", arch)
    output = tmp_path_factory.mktemp("exec_out") / "out"
    fake_run = make_fake_run(output, repo, "abricate", arch, COMMIT, contract, plan, entry)
    record = execute(plan, repo, plan["plan_sha256"], "abricate", arch, output, commands=fake_run)
    assert record["status"] == "PASS"
    assert record["arch"] == arch
    assert set(record["gates"]) == set(GATES)
    assert all(status == "PASS" for status in record["gates"].values())
    on_disk = json.loads((output / "entry.json").read_text())
    assert on_disk["status"] == "PASS"
    assert (output / "Dockerfile.bound").read_text().startswith(f"FROM {contract['base_image_reference']}@sha256:")


def test_execute_rejects_output_inside_repo(repo: Path, plan: dict) -> None:
    with pytest.raises(ValidationError, match="use a new isolated output directory"):
        execute(plan, repo, plan["plan_sha256"], "abricate", "amd64", repo / "inside")


def test_execute_rejects_existing_output(repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory) -> None:
    output = tmp_path_factory.mktemp("exists_out") / "exists"
    output.mkdir()
    with pytest.raises(ValidationError, match="use a new isolated output directory"):
        execute(plan, repo, plan["plan_sha256"], "abricate", "amd64", output)


def test_execute_rejects_checkout_sha_mismatch(repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory) -> None:
    def fake_run(argv, *, timeout=60):
        if argv[3:5] == ["rev-parse", "HEAD"]:
            return {"argv": argv, "returncode": 0, "elapsed_seconds": 0.1, "timed_out": False,
                    "stdout": "deadbeef\n", "stderr": ""}
        raise AssertionError("should not reach further commands")

    output = tmp_path_factory.mktemp("mismatch_out") / "out"
    with pytest.raises(ValidationError, match="checkout source SHA differs from plan"):
        execute(plan, repo, plan["plan_sha256"], "abricate", "amd64", output, commands=fake_run)
    assert json.loads((output / "entry.json").read_text())["status"] == "FAIL"


def test_execute_rejects_dirty_tree(repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory) -> None:
    def fake_run(argv, *, timeout=60):
        if argv[3:5] == ["rev-parse", "HEAD"]:
            return {"argv": argv, "returncode": 0, "elapsed_seconds": 0.1, "timed_out": False,
                    "stdout": COMMIT + "\n", "stderr": ""}
        if "status" in argv:
            return {"argv": argv, "returncode": 0, "elapsed_seconds": 0.1, "timed_out": False,
                    "stdout": " M dockerfiles/Dockerfile.abricate\n", "stderr": ""}
        raise AssertionError("should not reach further commands")

    output = tmp_path_factory.mktemp("dirty_out") / "out"
    with pytest.raises(ValidationError, match="tracked checkout changes must be reviewed"):
        execute(plan, repo, plan["plan_sha256"], "abricate", "amd64", output, commands=fake_run)


def test_execute_rejects_unreviewed_dockerfile_base_syntax(
    repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = build_contract("abricate", repo)
    base = contract["base_image_reference"]
    text = f"FROM {base} AS builder\nFROM {base}\nRUN echo abricate\n"
    (repo / "dockerfiles" / "Dockerfile.abricate").write_text(text)
    contract["dockerfile_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    contract["validation_contract_sha256"] = contract_hash(contract)
    (repo / "validation-contracts" / "schema-v1" / "abricate.json").write_text(json.dumps(contract, sort_keys=True))
    fresh_plan = make_plan(repo, COMMIT)
    entry, contract = select_entry(fresh_plan, repo, fresh_plan["plan_sha256"], "abricate", "amd64")
    output = tmp_path_factory.mktemp("bad_dockerfile") / "out"
    _fake_archive_identity(monkeypatch)
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    fake_run = make_fake_run(output, repo, "abricate", "amd64", COMMIT, contract, fresh_plan, entry)
    with pytest.raises(ValidationError, match="unreviewed Dockerfile base syntax"):
        execute(fresh_plan, repo, fresh_plan["plan_sha256"], "abricate", "amd64", output, commands=fake_run)


def test_execute_rejects_archive_drift_before_conversion(
    repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def lying_archive_identity(path: Path, tool: str, arch: str, commit: str, dockerfile_sha256: str) -> dict:
        data = path.read_bytes()
        return {
            "oci_identity": "sha256:" + hashlib.sha256(data + b"-config").hexdigest(),
            "oci_archive_sha256": "0" * 64,  # deliberately stale/wrong
            "oci_config_architecture": arch, "oci_config_os": "linux", "docker_archive_manifest_count": 1,
        }

    monkeypatch.setattr("scripts.contract_native_validation.archive_identity", lying_archive_identity)
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    entry, contract = select_entry(plan, repo, plan["plan_sha256"], "abricate", "amd64")
    output = tmp_path_factory.mktemp("archive_drift") / "out"
    fake_run = make_fake_run(output, repo, "abricate", "amd64", COMMIT, contract, plan, entry)
    with pytest.raises(ValidationError, match="archive changed before conversion"):
        execute(plan, repo, plan["plan_sha256"], "abricate", "amd64", output, commands=fake_run)


def test_execute_rejects_artifact_runtime_architecture_mismatch(
    repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_archive_identity(monkeypatch)
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    entry, contract = select_entry(plan, repo, plan["plan_sha256"], "abricate", "amd64")
    output = tmp_path_factory.mktemp("arch_mismatch") / "out"
    base_run = make_fake_run(output, repo, "abricate", "amd64", COMMIT, contract, plan, entry)

    def fake_run(argv, *, timeout=60):
        if argv != ["uname", "-m"] and len(argv) > 2 and argv[-2:] == ["uname", "-m"]:
            return {"argv": argv, "returncode": 0, "elapsed_seconds": 0.1, "timed_out": False,
                    "stdout": "wrong-machine\n", "stderr": ""}
        return base_run(argv, timeout=timeout)

    with pytest.raises(ValidationError, match="artifact runtime architecture mismatch"):
        execute(plan, repo, plan["plan_sha256"], "abricate", "amd64", output, commands=fake_run)


def test_execute_rejects_oci_sif_binding_archive_drift(
    repo: Path, plan: dict, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_archive_identity(monkeypatch)
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    entry, contract = select_entry(plan, repo, plan["plan_sha256"], "abricate", "amd64")
    output = tmp_path_factory.mktemp("binding_drift") / "out"
    base_run = make_fake_run(output, repo, "abricate", "amd64", COMMIT, contract, plan, entry)

    def fake_run(argv, *, timeout=60):
        result = base_run(argv, timeout=timeout)
        if argv[:3] == ["sudo", "apptainer", "build"]:
            # Simulate the archive changing after the pre-conversion integrity
            # check already passed but before the final OCI/SIF binding check.
            (output / "oci-image.tar").write_bytes(b"corrupted-after-sif-build")
        return result

    with pytest.raises(ValidationError, match="OCI/SIF binding mismatch"):
        execute(plan, repo, plan["plan_sha256"], "abricate", "amd64", output, commands=fake_run)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_plan_writes_plan_file_and_github_output(repo: Path, tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    github_output = tmp_path / "github_output.txt"
    rc = main([
        "plan", "--repo", str(repo), "--source-commit", COMMIT,
        "--plan", str(plan_path), "--github-output", str(github_output),
    ])
    assert rc == 0
    written = json.loads(plan_path.read_text())
    assert written["candidate_count"] == 14
    output_text = github_output.read_text()
    assert "matrix=" in output_text
    assert f"plan_sha256={written['plan_sha256']}" in output_text


def test_main_plan_without_github_output(repo: Path, tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    rc = main(["plan", "--repo", str(repo), "--source-commit", COMMIT, "--plan", str(plan_path)])
    assert rc == 0
    assert json.loads(plan_path.read_text())["source_commit"] == COMMIT


def test_main_execute_requires_authorization_output_tool_and_arch(repo: Path, plan: dict, tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    rc = main(["execute", "--repo", str(repo), "--plan", str(plan_path), "--plan-sha256", plan["plan_sha256"]])
    assert rc == 1


def test_main_execute_dispatches_to_execute_with_parsed_arguments(
    repo: Path, plan: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    captured = {}

    def fake_execute(plan_arg, repo_arg, digest_arg, tool_arg, arch_arg, output_arg, commands=None):
        captured.update(plan=plan_arg, repo=repo_arg, digest=digest_arg, tool=tool_arg, arch=arch_arg, output=output_arg)
        return {"status": "PASS"}

    monkeypatch.setattr("scripts.contract_native_validation.execute", fake_execute)
    output = tmp_path / "execout"
    rc = main([
        "execute", "--repo", str(repo), "--plan", str(plan_path), "--plan-sha256", plan["plan_sha256"],
        "--tool", "abricate", "--arch", "amd64", "--output", str(output), "--authorize-native-execution",
    ])
    assert rc == 0
    assert captured["tool"] == "abricate"
    assert captured["arch"] == "amd64"
    assert captured["digest"] == plan["plan_sha256"]
    assert captured["output"] == output


def test_main_execute_rejects_unknown_tool_choice(repo: Path, plan: dict, tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    with pytest.raises(SystemExit):
        main([
            "execute", "--repo", str(repo), "--plan", str(plan_path), "--plan-sha256", plan["plan_sha256"],
            "--tool", "not-a-tool", "--arch", "amd64", "--output", str(tmp_path / "out"),
            "--authorize-native-execution",
        ])


def test_main_reconcile_requires_records_and_output(repo: Path, plan: dict, tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    rc = main(["reconcile", "--repo", str(repo), "--plan", str(plan_path), "--plan-sha256", plan["plan_sha256"]])
    assert rc == 1


def test_main_reconcile_dispatches_with_loaded_records_and_roots(
    repo: Path, plan: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    records_dir = tmp_path / "records"
    for tool in ("abricate", "accelerate"):
        entry_dir = records_dir / tool / "amd64"
        entry_dir.mkdir(parents=True)
        write_json(entry_dir / "entry.json", {"tool_id": tool, "arch": "amd64"})

    captured = {}

    def fake_reconcile(plan_arg, repo_arg, digest_arg, records_arg, roots_arg):
        captured.update(plan=plan_arg, repo=repo_arg, digest=digest_arg, records=records_arg, roots=roots_arg)
        return {"status": "PASS", "native_validated_entries": 2}

    monkeypatch.setattr("scripts.contract_native_validation.reconcile", fake_reconcile)
    output = tmp_path / "summary.json"
    rc = main([
        "reconcile", "--repo", str(repo), "--plan", str(plan_path), "--plan-sha256", plan["plan_sha256"],
        "--records", str(records_dir), "--output", str(output),
    ])
    assert rc == 0
    assert json.loads(output.read_text())["native_validated_entries"] == 2
    assert {rec["tool_id"] for rec in captured["records"]} == {"abricate", "accelerate"}
    assert set(captured["roots"]) == {("abricate", "amd64"), ("accelerate", "amd64")}


def test_main_prints_fail_and_returns_one_on_value_error(repo: Path, plan: dict, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, dict(plan, candidate_count=999))  # tampered -> plan hash mismatch
    rc = main(["reconcile", "--repo", str(repo), "--plan", str(plan_path), "--plan-sha256", plan["plan_sha256"],
               "--records", str(tmp_path), "--output", str(tmp_path / "out.json")])
    assert rc == 1
    assert "FAIL:" in capsys.readouterr().out


def test_main_execute_propagates_output_already_exists(repo: Path, plan: dict, tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    output = tmp_path / "exists"
    output.mkdir()
    rc = main([
        "execute", "--repo", str(repo), "--plan", str(plan_path), "--plan-sha256", plan["plan_sha256"],
        "--tool", "abricate", "--arch", "amd64", "--output", str(output), "--authorize-native-execution",
    ])
    assert rc == 1
