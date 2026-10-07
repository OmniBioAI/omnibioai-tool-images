"""Synthetic verifier tests, not scientific-tool runtime evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.validation_contracts import contract_hash, sha256_file
from scripts.validation_smoke import (
    SmokeValidationError, validate_smoke_outputs, validate_version_evidence,
)


REPO = Path(__file__).resolve().parents[1]
OUTPUT = "smoke-output/alevin-infer"
MATRIX = "%%MatrixMarket matrix coordinate real general\n2 2 4\n1 1 7\n1 2 7\n2 1 3\n2 2 3\n"


@pytest.fixture
def contract():
    return json.loads((REPO / "validation-contracts/schema-v1/alevin_fry.json").read_text())


@pytest.fixture
def workspace(tmp_path):
    output = tmp_path / OUTPUT
    output.mkdir(parents=True)
    (output / "quants_mat.mtx").write_text(MATRIX)
    (output / "quants_mat_rows.txt").write_text("AAAAAAAAAAAAAAAC\nAAAAAAAAAAAAAAAG\n")
    (output / "quants_mat_cols.txt").write_text("geneA\ngeneB\n")
    return tmp_path


def check(contract, workspace, **kwargs):
    return validate_smoke_outputs(
        contract, REPO, workspace,
        returncode=kwargs.get("returncode", 0), elapsed_seconds=kwargs.get("elapsed_seconds", 0.1),
    )


def test_valid_synthetic_output_returns_content_hashes_only(contract, workspace):
    result = check(contract, workspace)
    assert result["smoke_outputs_verified"] is True
    assert result["shape"] == [2, 2]
    assert len(result["output_sha256"]) == 3
    assert result["contract_sha256"] == contract["validation_contract_sha256"]
    assert "native_validated" not in result
    assert "release_complete" not in result


@pytest.mark.parametrize("header", ["real", "integer"])
def test_coordinate_order_does_not_affect_count_equality(contract, workspace, header):
    matrix = MATRIX.splitlines()
    matrix[0] = matrix[0].replace("real", header)
    (workspace / OUTPUT / "quants_mat.mtx").write_text("\n".join(matrix[:2] + matrix[:1:-1]) + "\n")
    assert check(contract, workspace)["smoke_outputs_verified"]


@pytest.mark.parametrize("matrix", [
    "", MATRIX.replace("real general", "real symmetric"),
    MATRIX.replace("2 2 4", "3 2 4"), MATRIX.replace("2 2 4", "2 2 3"),
    MATRIX.replace("1 1 7", "1 1 8"), MATRIX.replace("1 1 7", "1 1 -7"),
    MATRIX.replace("1 1 7", "1 1 nan"), MATRIX.replace("1 1 7", "1 1 inf"),
    MATRIX.replace("1 1 7", "0 1 7"), MATRIX.replace("1 1 7", "3 1 7"),
    MATRIX.replace("1 1 7", "1.5 1 7"), MATRIX.replace("1 1 7", "1 1"),
    MATRIX.replace("1 2 7", "1 1 7"), MATRIX + "2 2 3\n",
    MATRIX.replace("real general", "integer general").replace("1 1 7", "1 1 7.5"),
])
def test_corrupt_or_wrong_matrix_fails_closed(contract, workspace, matrix):
    (workspace / OUTPUT / "quants_mat.mtx").write_text(matrix)
    with pytest.raises(SmokeValidationError):
        check(contract, workspace)


@pytest.mark.parametrize("name,value", [
    ("quants_mat_rows.txt", "AAAAAAAAAAAAAAAG\nAAAAAAAAAAAAAAAC\n"),
    ("quants_mat_rows.txt", "AAAAAAAAAAAAAAAC\n"),
    ("quants_mat_cols.txt", "geneB\ngeneA\n"),
    ("quants_mat_cols.txt", "geneA\ngeneA\n"),
])
def test_wrong_labels_fail_even_when_matrix_counts_match(contract, workspace, name, value):
    (workspace / OUTPUT / name).write_text(value)
    with pytest.raises(SmokeValidationError, match="labels mismatch"):
        check(contract, workspace)


@pytest.mark.parametrize("name", ["quants_mat.mtx", "quants_mat_rows.txt", "quants_mat_cols.txt"])
def test_missing_output_is_not_accepted(contract, workspace, name):
    (workspace / OUTPUT / name).unlink()
    with pytest.raises(SmokeValidationError):
        check(contract, workspace)


@pytest.mark.parametrize("code,elapsed", [(1, 0.1), (-9, 0.1), (True, 0.1), (0, 61), (0, -1), (0, float("nan")), (0, float("inf")), (0, True)])
def test_failure_or_invalid_duration_is_not_hidden_by_valid_outputs(contract, workspace, code, elapsed):
    with pytest.raises(SmokeValidationError):
        check(contract, workspace, returncode=code, elapsed_seconds=elapsed)


def test_contract_hash_mismatch_fails_before_outputs(contract, workspace):
    contract["smoke_assertions"]["timeout_seconds"] = 1000
    with pytest.raises(SmokeValidationError, match="contract SHA256 mismatch"):
        check(contract, workspace)


@pytest.mark.parametrize("field,value", [
    ("kind", "ignore_counts"), ("output_directory", "../escape"),
    ("output_directory", "/absolute"), ("expected_fixture", "unbound.json"),
    ("timeout_seconds", 0), ("timeout_seconds", True),
])
def test_unsupported_or_unsafe_assertions_fail_closed(contract, workspace, field, value):
    changed = copy.deepcopy(contract)
    changed["smoke_assertions"][field] = value
    changed["validation_contract_sha256"] = contract_hash(changed)
    with pytest.raises(SmokeValidationError):
        check(changed, workspace)


def test_changed_fixture_hash_is_not_ignored(contract, workspace):
    name = next(iter(contract["fixture_sha256"]))
    contract["fixture_sha256"][name] = "0" * 64
    contract["validation_contract_sha256"] = contract_hash(contract)
    with pytest.raises(SmokeValidationError, match="fixture SHA256 mismatch"):
        check(contract, workspace)


def test_output_symlink_cannot_escape_workspace(contract, workspace, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.mtx")
    outside.write_text(MATRIX)
    path = workspace / OUTPUT / "quants_mat.mtx"
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(SmokeValidationError, match="escapes verification root"):
        check(contract, workspace)


@pytest.mark.parametrize("field,value", [
    ("tolerance", 1), ("tolerance", 0), ("tolerance", float("nan")),
    ("shape", [True, 2]), ("shape", [2, 0]), ("entries", [[1, 1, -7]]),
    ("entries", [[1, 1, 7], [1, 1, 7]]), ("rows", ["same", "same"]),
    ("row_totals", [15, 6]), ("columns", ["geneA"]),
])
def test_malformed_expected_results_fail_even_with_updated_hash(contract, workspace, tmp_path, field, value):
    repo = tmp_path / "isolated-repo"
    for relative in [contract["dockerfile_path"], *contract["fixture_sha256"]]:
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO / relative).read_bytes())
    relative = contract["smoke_assertions"]["expected_fixture"]
    expected_file = repo / relative
    expected = json.loads(expected_file.read_text())
    expected[field] = value
    expected_file.write_text(json.dumps(expected))
    contract["fixture_sha256"][relative] = sha256_file(expected_file)
    contract["validation_contract_sha256"] = contract_hash(contract)
    with pytest.raises(SmokeValidationError):
        validate_smoke_outputs(contract, repo, workspace, returncode=0, elapsed_seconds=0.1)


def test_row_total_tolerance_is_checked_separately(contract, workspace):
    # Each entry error is within tolerance, but their summed row error is not.
    matrix = MATRIX.replace("1 1 7", "1 1 7.00008").replace("1 2 7", "1 2 7.00008")
    (workspace / OUTPUT / "quants_mat.mtx").write_text(matrix)
    with pytest.raises(SmokeValidationError, match="row total mismatch"):
        check(contract, workspace)


def test_verifier_does_not_modify_output_files(contract, workspace):
    before = {str(path): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    check(contract, workspace)
    after = {str(path): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    assert before == after


# Synthetic proposed stream metadata only: these tests do not update production
# contracts or establish that a scientific tool emits the supplied text.
VERSION_CASES = [
    ("3ddna_extra", r"(?m)^version: ([^\r\n]+)$", "version: 190716\n"),
    ("abricate", r"(?m)^abricate[ \t]+([^\r\n]+)$", "abricate 1.4.0\n"),
    ("accelerate", r"(?m)^- `Accelerate` version: ([^\r\n]+)$", "- `Accelerate` version: 1.15.0\n"),
    ("agat_extra", r"(?m)^(v[^\r\n]+)$", "v1.7.0\n"),
    ("aicsimageio_extra", r"(?m)^([^\r\n]+)$", "4.14.0\n"),
    ("airr_extra", r"(?m)^([^\r\n]+)$", "2.0.0\n"),
    ("alevin_fry", r"(?m)^alevin-fry[ \t]+([^\r\n]+)$", "alevin-fry 0.18.3\n"),
]


@pytest.fixture(params=VERSION_CASES, ids=[case[0] for case in VERSION_CASES])
def version_case(request):
    tool, observation, output = request.param
    value = json.loads((REPO / f"validation-contracts/schema-v1/{tool}.json").read_text())
    value["version_observation_parser"] = observation
    value["version_output_source"] = "stdout" if tool in ("airr_extra", "aicsimageio_extra") else "either"
    value["validation_contract_sha256"] = contract_hash(value)
    return value, output


def version_check(value, output, **kwargs):
    return validate_version_evidence(value, REPO, stdout=output, stderr=kwargs.get("stderr", ""), returncode=kwargs.get("returncode", 0))


def test_version_expected_output(version_case):
    value, output = version_case
    before = copy.deepcopy(value)
    result = version_check(value, output)
    assert result["observed_version"] == value["version_expected_value"]
    assert result["contract_sha256"] == value["validation_contract_sha256"]
    assert value == before
    assert "native_validated" not in result


@pytest.mark.parametrize("tool,observation,output", VERSION_CASES)
def test_generated_contract_has_exact_version_configuration(tool, observation, output):
    value = json.loads((REPO / f"validation-contracts/schema-v1/{tool}.json").read_text())
    assert value["version_observation_parser"] == observation
    assert value["version_output_source"] == ("stdout" if tool in ("airr_extra", "aicsimageio_extra") else "either")
    # Displayed/internal versions may differ from package release identifiers:
    # 3D-DNA release 201008 declares 190716; AGAT displays a leading 'v'.
    assert version_check(value, output)["observed_version"] == value["version_expected_value"]


@pytest.mark.parametrize("mode", ["missing", "wrong", "duplicate", "conflicting"])
def test_version_missing_wrong_duplicate_conflicting_fail(version_case, mode):
    value, output = version_case
    wrong = output.replace(value["version_expected_value"], "v999.999" if value["tool_id"] == "agat_extra" else "999.999")
    text = {"missing": "", "wrong": wrong, "duplicate": output * 2, "conflicting": output + wrong}[mode]
    with pytest.raises(SmokeValidationError):
        version_check(value, text)


def test_version_stream_binding(version_case):
    value, output = version_case
    if value["version_output_source"] == "either":
        assert version_check(value, "", stderr=output)["evidence_stream"] == "stderr"
        with pytest.raises(SmokeValidationError):
            version_check(value, output, stderr=output)
    else:
        assert version_check(value, output, stderr="runtime warning\n")["evidence_stream"] == "stdout"
        with pytest.raises(SmokeValidationError):
            version_check(value, "", stderr=output)


@pytest.mark.parametrize("returncode", [True, False, 1, -1, "0"])
def test_version_failed_exit_rejected(version_case, returncode):
    value, output = version_case
    with pytest.raises(SmokeValidationError):
        version_check(value, output, returncode=returncode)


@pytest.mark.parametrize("field,replacement", [
    ("version_output_source", "unknown"),
    ("version_observation_parser", "["),
    ("version_observation_parser", "no-group"),
    ("version_observation_parser", "(one)(two)"),
    ("version_observation_parser", None),
])
def test_version_invalid_metadata_rejected(version_case, field, replacement):
    value, output = version_case
    value[field] = replacement
    value["validation_contract_sha256"] = contract_hash(value)
    with pytest.raises(SmokeValidationError):
        version_check(value, output)


def test_version_metadata_hash_mismatch(version_case):
    value, output = version_case
    value["version_output_source"] = "stderr"
    with pytest.raises(SmokeValidationError):
        version_check(value, output)


def test_unconfigured_contract_is_not_implicitly_accepted(version_case):
    value, output = version_case
    value.pop("version_observation_parser")
    value["validation_contract_sha256"] = contract_hash(value)
    with pytest.raises(SmokeValidationError):
        version_check(value, output)


@pytest.mark.parametrize("output", [None, b"version", "x" * (1024 * 1024 + 1)])
def test_invalid_or_oversized_version_stream(version_case, output):
    value, _ = version_case
    with pytest.raises(SmokeValidationError):
        version_check(value, output)
