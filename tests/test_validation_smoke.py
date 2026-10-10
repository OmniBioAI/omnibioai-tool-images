"""Coverage for scripts.validation_smoke's read-only evidence assertions.

This module never executes tools; it only validates already-captured
command output and already-produced output files against a validation
contract. ``validate_contract`` itself (contract-shape / Dockerfile-hash
checks) belongs to scripts.validation_contracts and is covered elsewhere,
so here it is stubbed to a no-op for most tests -- these tests exercise
validation_smoke's own logic: version-observation parsing, smoke output
shape/content checks, and the fail-closed wrapping of every error path.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts import validation_smoke as vs
from scripts.validation_contracts import ContractError, sha256_file

SmokeValidationError = vs.SmokeValidationError


def noop_validate_contract(monkeypatch, side_effect=None):
    if side_effect is not None:
        def fake(contract, repo_root):
            raise side_effect
        monkeypatch.setattr(vs, "validate_contract", fake)
    else:
        monkeypatch.setattr(vs, "validate_contract", lambda contract, repo_root: None)


# ---------------------------------------------------------------------------
# validate_version_evidence
# ---------------------------------------------------------------------------

def version_contract(**overrides) -> dict:
    contract = {
        "tool_id": "samtools",
        "contract_status": "CONTRACT_READY",
        "version_output_source": "stdout",
        "version_parser": r"regex:^samtools (\d+\.\d+\.\d+)$",
        "version_observation_parser": r"samtools (\d+\.\d+\.\d+)",
        "version_expected_value": "1.19.0",
        "validation_contract_sha256": "a" * 64,
    }
    contract.update(overrides)
    return contract


def test_version_evidence_happy_path_stdout(monkeypatch):
    noop_validate_contract(monkeypatch)
    result = vs.validate_version_evidence(
        version_contract(), Path("/repo"),
        stdout="samtools 1.19.0\n", stderr="", returncode=0,
    )
    assert result == {
        "tool_id": "samtools",
        "observed_version": "1.19.0",
        "evidence_stream": "stdout",
        "contract_sha256": "a" * 64,
    }


def test_version_evidence_happy_path_either_found_in_stderr(monkeypatch):
    noop_validate_contract(monkeypatch)
    result = vs.validate_version_evidence(
        version_contract(version_output_source="either"), Path("/repo"),
        stdout="", stderr="samtools 1.19.0\n", returncode=0,
    )
    assert result["evidence_stream"] == "stderr"
    assert result["observed_version"] == "1.19.0"


def test_version_evidence_blocked_contract_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="blocked contract"):
        vs.validate_version_evidence(
            version_contract(contract_status="CONTRACT_BLOCKED_VERSION"), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


@pytest.mark.parametrize("returncode", [1, True, "0", None])
def test_version_evidence_bad_returncode_rejected(monkeypatch, returncode):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="did not exit successfully"):
        vs.validate_version_evidence(
            version_contract(), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=returncode,
        )


@pytest.mark.parametrize("stdout,stderr", [(None, ""), ("ok", b"bytes")])
def test_version_evidence_non_text_streams_rejected(monkeypatch, stdout, stderr):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="must be text"):
        vs.validate_version_evidence(
            version_contract(), Path("/repo"),
            stdout=stdout, stderr=stderr, returncode=0,
        )


def test_version_evidence_oversized_streams_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    huge = "samtools 1.19.0\n" + "x" * (1024 * 1024)
    with pytest.raises(SmokeValidationError, match="exceeds 1 MiB limit"):
        vs.validate_version_evidence(
            version_contract(), Path("/repo"),
            stdout=huge, stderr="", returncode=0,
        )


def test_version_evidence_unknown_source_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="unknown version output source"):
        vs.validate_version_evidence(
            version_contract(version_output_source="both"), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


def test_version_evidence_non_text_observation_parser_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="observation parser must be text"):
        vs.validate_version_evidence(
            version_contract(version_observation_parser=None), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


def test_version_evidence_unprefixed_strict_parser_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="unsupported strict version parser"):
        vs.validate_version_evidence(
            version_contract(version_parser=r"^samtools (\d+\.\d+\.\d+)$"), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


@pytest.mark.parametrize("parser_field", ["version_parser", "version_observation_parser"])
def test_version_evidence_multi_group_parser_rejected(monkeypatch, parser_field):
    noop_validate_contract(monkeypatch)
    overrides = {parser_field: r"(\d+)\.(\d+)" if parser_field == "version_observation_parser"
                 else r"regex:(\d+)\.(\d+)"}
    with pytest.raises(SmokeValidationError, match="must capture one value"):
        vs.validate_version_evidence(
            version_contract(**overrides), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


def test_version_evidence_zero_observations_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="missing, duplicate, or conflicting"):
        vs.validate_version_evidence(
            version_contract(), Path("/repo"),
            stdout="no version here\n", stderr="", returncode=0,
        )


def test_version_evidence_duplicate_observations_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="missing, duplicate, or conflicting"):
        vs.validate_version_evidence(
            version_contract(), Path("/repo"),
            stdout="samtools 1.19.0\nsamtools 1.20.0\n", stderr="", returncode=0,
        )


def test_version_evidence_conflicting_across_streams_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="missing, duplicate, or conflicting"):
        vs.validate_version_evidence(
            version_contract(version_output_source="either"), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="samtools 1.20.0\n", returncode=0,
        )


def test_version_evidence_mismatched_expected_value_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="observed tool version mismatch"):
        vs.validate_version_evidence(
            version_contract(version_expected_value="9.9.9"), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


def test_version_evidence_strict_parser_disagrees_rejected(monkeypatch):
    noop_validate_contract(monkeypatch)
    # The loose observation parser matches a banner that the strict parser,
    # anchored to the whole line, correctly refuses to accept.
    with pytest.raises(SmokeValidationError, match="strict version parser rejected"):
        vs.validate_version_evidence(
            version_contract(), Path("/repo"),
            stdout="samtools 1.19.0 (extra banner text)\n", stderr="", returncode=0,
        )


def test_version_evidence_wraps_contract_error(monkeypatch):
    noop_validate_contract(monkeypatch, side_effect=ContractError("dockerfile mismatch"))
    with pytest.raises(SmokeValidationError, match="invalid version evidence: dockerfile mismatch"):
        vs.validate_version_evidence(
            version_contract(), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


def test_version_evidence_wraps_missing_contract_key(monkeypatch):
    noop_validate_contract(monkeypatch)
    contract = version_contract()
    del contract["version_output_source"]
    with pytest.raises(SmokeValidationError, match="invalid version evidence"):
        vs.validate_version_evidence(
            contract, Path("/repo"), stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


def test_version_evidence_wraps_invalid_regex(monkeypatch):
    noop_validate_contract(monkeypatch)
    with pytest.raises(SmokeValidationError, match="invalid version evidence"):
        vs.validate_version_evidence(
            version_contract(version_observation_parser="(unclosed"), Path("/repo"),
            stdout="samtools 1.19.0\n", stderr="", returncode=0,
        )


# ---------------------------------------------------------------------------
# Private helpers: _path, _text, _shape, _number
# ---------------------------------------------------------------------------

def test_path_resolves_nested_relative(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    result = vs._path(tmp_path, "a/b")
    assert result == (tmp_path / "a" / "b").resolve()


@pytest.mark.parametrize("relative", [None, 1, "", ])
def test_path_rejects_non_nonempty_string(tmp_path, relative):
    with pytest.raises(SmokeValidationError, match="expected a nonempty relative path"):
        vs._path(tmp_path, relative)


def test_path_rejects_absolute(tmp_path):
    with pytest.raises(SmokeValidationError, match="expected a nonempty relative path"):
        vs._path(tmp_path, "/etc/passwd")


def test_path_rejects_escape(tmp_path):
    with pytest.raises(SmokeValidationError, match="escapes verification root"):
        vs._path(tmp_path, "../outside")


def test_text_reads_file(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("hello")
    assert vs._text(path) == "hello"


def test_text_rejects_oversized_file(tmp_path):
    path = tmp_path / "big.txt"
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(SmokeValidationError, match="exceeds 1 MiB limit"):
        vs._text(path)


def test_shape_valid():
    assert vs._shape([2, 3]) == (2, 3)


@pytest.mark.parametrize("value", [None, [1], [1, 2, 3], "ab"])
def test_shape_rejects_wrong_container(value):
    with pytest.raises(SmokeValidationError, match="invalid matrix shape"):
        vs._shape(value)


@pytest.mark.parametrize("value", [[0, 1], [1, -1], [1, 100001], [True, 1], [1.5, 1]])
def test_shape_rejects_bad_dimensions(value):
    with pytest.raises(SmokeValidationError, match="invalid matrix dimensions"):
        vs._shape(value)


def test_number_accepts_nonnegative():
    assert vs._number(5) == 5.0
    assert vs._number("2.5") == 2.5


def test_number_rejects_bool():
    with pytest.raises(SmokeValidationError, match="boolean count is invalid"):
        vs._number(True)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, -1])
def test_number_rejects_non_finite_or_negative(value):
    with pytest.raises(SmokeValidationError, match="finite and nonnegative"):
        vs._number(value)


# ---------------------------------------------------------------------------
# validate_smoke_outputs
# ---------------------------------------------------------------------------

def write_matrix_market(path: Path, shape, entries, *, integer=False) -> None:
    kind = "integer" if integer else "real"
    lines = [f"%%MatrixMarket matrix coordinate {kind} general", f"{shape[0]} {shape[1]} {len(entries)}"]
    for row, col, value in entries:
        lines.append(f"{row} {col} {value}")
    path.write_text("\n".join(lines) + "\n")


def build_smoke_fixture(repo_root: Path, workspace: Path, *, shape=(1, 1),
                         entries=((1, 1, 5.0),), tolerance=0.0001,
                         rows=("gene1",), columns=("sample1",), row_totals=(5.0,),
                         output_directory="out", corrupt_fixture_hash=False,
                         output_entries=None, output_shape=None,
                         output_rows=None, output_columns=None):
    repo_root.mkdir(parents=True, exist_ok=True)
    fixtures_dir = repo_root / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    expected = {
        "shape": list(shape),
        "entries": [list(e) for e in entries],
        "tolerance": tolerance,
        "rows": list(rows),
        "columns": list(columns),
        "row_totals": list(row_totals),
    }
    expected_path = fixtures_dir / "expected.json"
    expected_path.write_text(json.dumps(expected))
    digest = sha256_file(expected_path)
    if corrupt_fixture_hash:
        digest = "0" * 64

    out_dir = workspace / output_directory
    out_dir.mkdir(parents=True, exist_ok=True)
    write_matrix_market(
        out_dir / "quants_mat.mtx",
        output_shape if output_shape is not None else shape,
        output_entries if output_entries is not None else entries,
    )
    (out_dir / "quants_mat_rows.txt").write_text(
        "\n".join(output_rows if output_rows is not None else rows) + "\n"
    )
    (out_dir / "quants_mat_cols.txt").write_text(
        "\n".join(output_columns if output_columns is not None else columns) + "\n"
    )

    contract = {
        "contract_status": "CONTRACT_READY",
        "smoke_assertions": {
            "kind": "matrix_market_gene_counts",
            "timeout_seconds": 60,
            "output_directory": output_directory,
            "expected_fixture": "fixtures/expected.json",
        },
        "fixture_sha256": {"fixtures/expected.json": digest},
        "smoke_inputs": ["fixtures/expected.json"],
        "smoke_expected_outputs": [
            f"{output_directory}/quants_mat.mtx",
            f"{output_directory}/quants_mat_rows.txt",
            f"{output_directory}/quants_mat_cols.txt",
        ],
        "validation_contract_sha256": "b" * 64,
    }
    return contract


def test_smoke_outputs_happy_path(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)

    result = vs.validate_smoke_outputs(
        contract, repo_root, workspace, returncode=0, elapsed_seconds=1.5,
    )
    assert result["assertion_kind"] == "matrix_market_gene_counts"
    assert result["shape"] == [1, 1]
    assert result["contract_sha256"] == "b" * 64
    assert result["smoke_outputs_verified"] is True
    assert set(result["output_sha256"]) == set(contract["smoke_expected_outputs"])


def test_smoke_outputs_blocked_contract_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    contract["contract_status"] = "CONTRACT_BLOCKED_SMOKE"
    with pytest.raises(SmokeValidationError, match="blocked contract"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_unsupported_kind_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    contract["smoke_assertions"]["kind"] = "something_else"
    with pytest.raises(SmokeValidationError, match="unsupported smoke assertion kind"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


@pytest.mark.parametrize("timeout", [0, -1, 1.5, "60"])
def test_smoke_outputs_invalid_timeout_rejected(tmp_path, monkeypatch, timeout):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    contract["smoke_assertions"]["timeout_seconds"] = timeout
    with pytest.raises(SmokeValidationError, match="invalid smoke timeout"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_nonzero_returncode_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    with pytest.raises(SmokeValidationError, match="did not exit successfully"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=1, elapsed_seconds=1)


@pytest.mark.parametrize("elapsed", [True, math.inf, math.nan, -1, 61])
def test_smoke_outputs_invalid_elapsed_rejected(tmp_path, monkeypatch, elapsed):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    with pytest.raises(SmokeValidationError, match="duration is invalid or exceeds timeout"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=elapsed)


def test_smoke_outputs_fixture_hash_set_mismatch_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    contract["smoke_inputs"] = ["fixtures/expected.json", "fixtures/extra.json"]
    with pytest.raises(SmokeValidationError, match="fixture hash set mismatch"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_fixture_sha_mismatch_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, corrupt_fixture_hash=True)
    with pytest.raises(SmokeValidationError, match="fixture SHA256 mismatch"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_expected_fixture_not_hash_bound_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    # Point at a fixture path that is not a key of fixture_sha256/smoke_inputs
    # (those two stay mutually consistent, so the earlier hash-set check
    # still passes and we reach the hash-binding check for expected_fixture).
    contract["smoke_assertions"]["expected_fixture"] = "fixtures/other.json"
    with pytest.raises(SmokeValidationError, match="must be hash-bound"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


@pytest.mark.parametrize("tolerance", [0, 0.001])
def test_smoke_outputs_bad_tolerance_rejected(tmp_path, monkeypatch, tolerance):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, tolerance=tolerance)
    with pytest.raises(SmokeValidationError, match="unreviewed numerical tolerance"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_declared_outputs_disagree_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    contract["smoke_expected_outputs"][0] = "out/wrong.mtx"
    with pytest.raises(SmokeValidationError, match="output declarations disagree"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_shape_or_coordinate_mismatch_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(
        repo_root, workspace, output_shape=(2, 1), output_entries=((1, 1, 5.0), (2, 1, 1.0))
    )
    with pytest.raises(SmokeValidationError, match="matrix shape or coordinates mismatch"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_gene_counts_mismatch_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, output_entries=((1, 1, 5.01),))
    with pytest.raises(SmokeValidationError, match="gene counts mismatch"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_invalid_expected_labels_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, rows=("gene1", "gene1"), shape=(1, 1))
    # rows has 2 labels but shape says 1 row -> invalid expected labels.
    with pytest.raises(SmokeValidationError, match="invalid expected labels"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_empty_expected_label_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, rows=("",))
    with pytest.raises(SmokeValidationError, match="invalid expected label"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_label_file_mismatch_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, output_rows=("different_gene",))
    with pytest.raises(SmokeValidationError, match="matrix labels mismatch"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_row_total_count_mismatch_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, row_totals=(5.0, 1.0))
    with pytest.raises(SmokeValidationError, match="row total count mismatch"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_row_total_value_mismatch_rejected(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace, row_totals=(999.0,))
    with pytest.raises(SmokeValidationError, match="row total mismatch"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_wraps_contract_error(tmp_path, monkeypatch):
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    noop_validate_contract(monkeypatch, side_effect=ContractError("bad dockerfile"))
    with pytest.raises(SmokeValidationError, match="invalid smoke evidence: bad dockerfile"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_wraps_missing_key(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    del contract["smoke_assertions"]
    with pytest.raises(SmokeValidationError, match="invalid smoke evidence"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_wraps_missing_fixture_file_as_oserror(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    (repo_root / "fixtures" / "expected.json").unlink()
    with pytest.raises(SmokeValidationError, match="invalid smoke evidence"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_wraps_non_numeric_elapsed_as_typeerror(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    with pytest.raises(SmokeValidationError, match="invalid smoke evidence"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds="1")


def test_smoke_outputs_wraps_invalid_utf8_output_file(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    (workspace / "out" / "quants_mat_rows.txt").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(SmokeValidationError, match="invalid smoke evidence"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


# ---------------------------------------------------------------------------
# _entries / _matrix: directly exercised defensive branches. _matrix always
# feeds _entries exactly-three-field, integer-coordinate rows, so a few of
# _entries's own defensive checks are unreachable through the public API
# and are tested directly against the helper instead.
# ---------------------------------------------------------------------------

def test_entries_rejects_wrong_field_count():
    with pytest.raises(SmokeValidationError, match="three fields"):
        vs._entries([[1, 2]], (5, 5))


def test_entries_rejects_noninteger_coordinates():
    with pytest.raises(SmokeValidationError, match="must be integers"):
        vs._entries([["1", "2", 5]], (5, 5))


def test_entries_rejects_out_of_bounds_coordinate():
    with pytest.raises(SmokeValidationError, match="out of bounds"):
        vs._entries([[2, 1, 5]], (1, 1))


def test_entries_rejects_duplicate_coordinate():
    with pytest.raises(SmokeValidationError, match="duplicate matrix coordinate"):
        vs._entries([[1, 1, 5], [1, 1, 6]], (1, 1))


def test_matrix_rejects_missing_size_line(tmp_path):
    path = tmp_path / "m.mtx"
    path.write_text("%%MatrixMarket matrix coordinate real general\n")
    with pytest.raises(SmokeValidationError, match="missing MatrixMarket size line"):
        vs._matrix(path)


def test_matrix_rejects_entry_count_mismatch(tmp_path):
    path = tmp_path / "m.mtx"
    path.write_text("%%MatrixMarket matrix coordinate real general\n1 1 2\n1 1 5.0\n")
    with pytest.raises(SmokeValidationError, match="entry count mismatch"):
        vs._matrix(path)


def test_matrix_rejects_entry_with_wrong_field_count(tmp_path):
    path = tmp_path / "m.mtx"
    path.write_text("%%MatrixMarket matrix coordinate real general\n1 1 1\n1 1\n")
    with pytest.raises(SmokeValidationError, match="three fields"):
        vs._matrix(path)


# ---------------------------------------------------------------------------
# _matrix / _entries via the public API: header and malformed-input edge
# cases reached through integer-typed MatrixMarket files.
# ---------------------------------------------------------------------------

def test_smoke_outputs_rejects_unsupported_header(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    (workspace / "out" / "quants_mat.mtx").write_text("not a matrix market file\n1 1 1\n1 1 5.0\n")
    with pytest.raises(SmokeValidationError, match="unsupported MatrixMarket header"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)


def test_smoke_outputs_rejects_fractional_value_under_integer_header(tmp_path, monkeypatch):
    noop_validate_contract(monkeypatch)
    repo_root, workspace = tmp_path / "repo", tmp_path / "workspace"
    contract = build_smoke_fixture(repo_root, workspace)
    (workspace / "out" / "quants_mat.mtx").write_text(
        "%%MatrixMarket matrix coordinate integer general\n1 1 1\n1 1 5.5\n"
    )
    with pytest.raises(SmokeValidationError, match="invalid smoke evidence"):
        vs.validate_smoke_outputs(contract, repo_root, workspace, returncode=0, elapsed_seconds=1)
