from __future__ import annotations

import csv
import gzip
import json
import re
from pathlib import Path

import pytest

from scripts.validation_contracts import contract_hash, sha256_file


REPO = Path(__file__).resolve().parents[1]
CONTRACTS = REPO / "validation-contracts" / "schema-v1"
CANARY = [
    "3ddna_extra",
    "abricate",
    "accelerate",
    "admixtools",
    "afterqc_extra",
    "agat_extra",
    "aicsimageio_extra",
    "airr_extra",
    "alevin_fry",
    "alevinqc_extra",
]
PINNED = {
    "3ddna_extra": ("3d-dna", "201008"),
    "abricate": ("abricate", "1.4.0"),
    "accelerate": ("accelerate", "1.15.0"),
    "admixtools": ("admixtools", "8.0.2"),
    "agat_extra": ("agat", "1.7.0"),
    "aicsimageio_extra": ("aicsimageio", "4.14.0"),
    "airr_extra": ("r-airr", "2.0.0"),
    "alevin_fry": ("alevin-fry", "0.18.3"),
}
PACKAGE_BUILDS = {
    "3ddna_extra": {"hdfd78af_0"},
    "abricate": {"h05cac1d_0"},
    "accelerate": {"pyh5ded981_0"},
    "admixtools": {"h75d7a4a_0", "he9d944c_0"},
    "agat_extra": {"pl5321hdfd78af_0"},
    "aicsimageio_extra": {"pyhd8ed1ab_0"},
    "airr_extra": {"r45hc72bb7e_0"},
    "alevin_fry": {"hd612981_0", "h6789a04_0"},
}
UNPINNED = {"afterqc_extra", "alevinqc_extra"}
READY = {"3ddna_extra", "abricate", "accelerate", "agat_extra", "aicsimageio_extra", "airr_extra", "alevin_fry"}
VERSION_OUTPUTS = {
    "3ddna_extra": "version: 190716\n",
    "abricate": "abricate 1.4.0\n",
    "accelerate": "- `Accelerate` version: 1.15.0\n",
    "admixtools": "version: 701\n",
    "agat_extra": "v1.7.0\n",
    "aicsimageio_extra": "4.14.0\n",
    "airr_extra": "2.0.0\n",
    "alevin_fry": "alevin-fry 0.18.3\n",
}


def load_contract(tool_id: str) -> dict:
    return json.loads((CONTRACTS / f"{tool_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("tool_id", CANARY)
def test_historical_intent_remains_unknown(tool_id: str) -> None:
    contract = load_contract(tool_id)
    assert contract["pinning"]["historical_intended_version"] == "UNKNOWN"
    assert contract["pinning"]["native_build_validation_status"] == "NOT_RUN"
    assert contract["pinning"]["release_status"] == "NOT_PUBLISHED"


@pytest.mark.parametrize("tool_id", sorted(PINNED))
def test_selected_baseline_is_exactly_pinned(tool_id: str) -> None:
    package, version = PINNED[tool_id]
    contract = load_contract(tool_id)
    dockerfile = REPO / contract["dockerfile_path"]
    assert f"{package}={version}" in dockerfile.read_text(encoding="utf-8")
    assert all(build in dockerfile.read_text(encoding="utf-8") for build in PACKAGE_BUILDS[tool_id])
    assert contract["scientific_version"] == version
    assert contract["pinning"]["new_reproducible_baseline_version"] == version
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", contract["source_immutable_identity"])


@pytest.mark.parametrize("tool_id", sorted(UNPINNED))
def test_blocked_baseline_candidate_is_not_forced(tool_id: str) -> None:
    contract = load_contract(tool_id)
    assert contract["scientific_version"] is None
    assert contract["source_immutable_identity"] is None
    assert contract["pinning"]["new_reproducible_baseline_version"] is None
    assert contract["pinning"]["static_contract_status"] == "STATIC_CONTRACT_BLOCKED"
    assert contract["native_arm64_expected"] is (tool_id == "afterqc_extra")


@pytest.mark.parametrize("tool_id", sorted(VERSION_OUTPUTS))
def test_version_parsers_are_anchored_and_reject_wrong_versions(tool_id: str) -> None:
    contract = load_contract(tool_id)
    expression = contract["version_parser"].removeprefix("regex:")
    parser = re.compile(expression)
    match = parser.search(VERSION_OUTPUTS[tool_id])
    assert match is not None
    assert match.group(1) == contract["version_expected_value"]
    assert parser.search("wrong-version 999.999\n") is None


def test_fastq_fixture_is_syntactically_valid() -> None:
    lines = (REPO / "validation-contracts/fixtures/afterqc-tiny.fastq").read_text().splitlines()
    assert len(lines) == 8
    for offset in (0, 4):
        assert lines[offset].startswith("@")
        assert lines[offset + 2] == "+"
        assert len(lines[offset + 1]) == len(lines[offset + 3])
        assert set(lines[offset + 1]) <= set("ACGTN")


def test_gff3_fixture_has_valid_hierarchy() -> None:
    rows = [
        line.split("\t")
        for line in (REPO / "validation-contracts/fixtures/agat-tiny.gff3").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert all(len(row) == 9 for row in rows)
    assert [row[2] for row in rows] == ["gene", "mRNA", "exon", "exon"]
    attributes = [row[8] for row in rows]
    assert "ID=gene1" in attributes[0]
    assert "ID=transcript1;Parent=gene1" == attributes[1]
    assert all("Parent=transcript1" in value for value in attributes[2:])


def test_airr_fixture_contains_required_rearrangement_fields() -> None:
    path = REPO / "validation-contracts/fixtures/airr-tiny.tsv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required = {
        "sequence_id", "sequence", "rev_comp", "productive", "v_call", "d_call",
        "j_call", "sequence_alignment", "germline_alignment", "junction",
        "junction_aa", "v_cigar", "d_cigar", "j_cigar",
    }
    assert len(rows) == 1
    assert required <= rows[0].keys()
    assert rows[0]["sequence_id"] == "omnibioai_airr_1"
    assert all(rows[0][field] for field in required)


@pytest.mark.parametrize("tool_id", CANARY)
def test_contract_and_dockerfile_hashes_match(tool_id: str) -> None:
    contract = load_contract(tool_id)
    assert contract_hash(contract) == contract["validation_contract_sha256"]
    assert sha256_file(REPO / contract["dockerfile_path"]) == contract["dockerfile_sha256"]


def test_static_ready_set_does_not_claim_native_or_release_validation() -> None:
    observed = {
        tool_id
        for tool_id in CANARY
        if load_contract(tool_id)["pinning"]["static_contract_status"] == "STATIC_CONTRACT_READY"
    }
    assert observed == READY
    for tool_id in observed:
        contract = load_contract(tool_id)
        assert contract["contract_status"] == "CONTRACT_READY"
        assert contract["pinning"]["native_build_validation_status"] == "NOT_RUN"
        assert contract["pinning"]["release_status"] == "NOT_PUBLISHED"


def test_admixtools_synthetic_fixture_has_consistent_eigenstrat_dimensions() -> None:
    fixture = REPO / "validation-contracts/fixtures/admixtools-tiny"
    individuals = [line.split() for line in (fixture / "tiny.ind").read_text().splitlines()]
    snps = [line.split() for line in (fixture / "tiny.snp").read_text().splitlines()]
    genotypes = (fixture / "tiny.geno").read_text().splitlines()
    assert len(individuals) == 6
    assert all(len(row) == 3 and row[1] == "U" for row in individuals)
    assert len({row[0] for row in individuals}) == len(individuals)
    assert len(snps) == len(genotypes) == 24
    assert len({row[0] for row in snps}) == len(snps)
    assert all(len(row) == 6 and len(set(row[4:])) == 2 for row in snps)
    positions = [(int(row[1]), float(row[2]), int(row[3])) for row in snps]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)
    assert {position[0] for position in positions} == {1, 2, 3, 4}
    assert all(position[1] > 0 and position[2] > 0 for position in positions)
    assert all(len(row) == len(individuals) and set(row) <= set("012") for row in genotypes)
    assert all(len(set(row)) > 1 for row in genotypes)
    target_columns = [index for index, row in enumerate(individuals) if row[2] == "TargetC"]
    assert len(target_columns) == 2
    assert all(any(row[index] == "1" for row in genotypes) for index in target_columns)


def test_admixtools_smoke_candidate_references_only_repository_owned_inputs() -> None:
    fixture = REPO / "validation-contracts/fixtures/admixtools-tiny"
    parameters = dict(
        line.split(": ", 1) for line in (fixture / "qp3pop.par").read_text().splitlines()
    )
    assert parameters == {
        "genotypename": "tiny.geno", "snpname": "tiny.snp",
        "indivname": "tiny.ind", "popfilename": "tiny.pops",
    }
    assert all((fixture / name).is_file() for name in parameters.values())
    populations = (fixture / "tiny.pops").read_text().splitlines()
    assert populations == ["SourceA SourceB TargetC"]
    individuals = [line.split() for line in (fixture / "tiny.ind").read_text().splitlines()]
    assert all(sum(row[2] == population for row in individuals) == 2 for population in populations[0].split())


def test_admixtools_fixture_does_not_promote_unexecuted_smoke_or_license_gate() -> None:
    contract = load_contract("admixtools")
    assert contract["contract_status"] == "CONTRACT_BLOCKED_MULTIPLE_REASONS"
    assert contract["smoke_command"] is None
    assert contract["pinning"]["distribution_license_review"] == "REQUIRED_NONCOMMERCIAL_LICENSE"
    assert contract["pinning"]["static_contract_status"] == "STATIC_CONTRACT_BLOCKED"


def test_admixtools_license_evidence_does_not_assume_distribution_permission() -> None:
    contract = load_contract("admixtools")
    review = contract["pinning"]["distribution_license_review_evidence"]
    assert review["source_release"] == "v8.0.2"
    assert review["intended_distribution_scope"] == "UNKNOWN"
    assert review["permission_for_intended_distribution"] == "NOT_ESTABLISHED"
    assert review["copyright_notice_retention_required"] is True
    assert "DISTRIBUTION_LICENSE_REVIEW_REQUIRED" in contract["remediation"]["remaining_deficiency_flags"]
    evidence = [row for row in contract["evidence"] if row["field"] == "distribution_license"]
    assert len(evidence) == 1
    assert evidence[0]["quality"] == "DIRECT_UPSTREAM_EVIDENCE"
    assert evidence[0]["source"] == review["source"]


def test_airr_new_baseline_is_channel_qualified_without_historical_or_runtime_claims() -> None:
    contract = load_contract("airr_extra")
    dockerfile = (REPO / contract["dockerfile_path"]).read_text()
    assert '"conda-forge::r-airr=2.0.0=r45hc72bb7e_0"' in dockerfile
    assert contract["pinning"]["historical_intended_version"] == "UNKNOWN"
    assert contract["pinning"]["previous_rejected_candidate_version"] == "1.2.0"
    assert contract["pinning"]["native_build_validation_status"] == "NOT_RUN"
    assert contract["scientific_version_source"] == "new_reproducible_baseline_package_version"
    assert contract["source_immutable_identity"] == "sha256:e4932ec84dad46d37327ab5aa5a34b95095677b6392bff2f59fd473eb942da52"
    assert all(
        value == "NOARCH_PACKAGE_WITH_NATIVE_DEPENDENCY_SOLVE"
        for value in contract["pinning"]["architecture_availability"].values()
    )


@pytest.mark.parametrize("output", ["", "1.2.0\n", "2.0.0\n1.2.0\n", "2.0.0\n2.0.0\n", "prefix 2.0.0\n"])
def test_airr_parser_rejects_missing_wrong_or_ambiguous_output(output: str) -> None:
    contract = load_contract("airr_extra")
    assert re.search(contract["version_parser"].removeprefix("regex:"), output) is None


def test_alevin_infer_fixture_has_valid_matrix_and_equivalence_class_indices() -> None:
    fixture = REPO / "validation-contracts/fixtures/alevin-infer-tiny"
    labels = (fixture / "eq_labels.txt").read_text().splitlines()
    genes, classes = map(int, labels[:2])
    assert (genes, classes) == (2, 3)
    mapping = {}
    for line in labels[2:]:
        fields = list(map(int, line.split()))
        *gene_ids, class_id = fields
        assert class_id not in mapping and 0 <= class_id < classes
        assert gene_ids and len(set(gene_ids)) == len(gene_ids)
        assert all(0 <= gene_id < genes for gene_id in gene_ids)
        mapping[class_id] = gene_ids
    assert mapping == {0: [0], 1: [1], 2: [0, 1]}
    matrix = (fixture / "counts.mtx").read_text().splitlines()
    assert matrix[0] == "%%MatrixMarket matrix coordinate real general"
    assert list(map(int, matrix[1].split())) == [2, classes, 6]
    entries = [line.split() for line in matrix[2:]]
    assert len(entries) == 6
    assert len({(row, column) for row, column, _ in entries}) == 6
    counts = {(int(row), int(column)): float(value) for row, column, value in entries}
    for row in (1, 2):
        assert counts[row, 1] == counts[row, 2] > 0
        assert counts[row, 3] > 0
    expected = json.loads((fixture / "expected.json").read_text())
    for row, column, value in expected["entries"]:
        assert value == counts[row, column] + counts[row, 3] / 2
    assert expected["row_totals"] == [sum(counts[row, column] for column in (1, 2, 3)) for row in (1, 2)]
    assert (fixture / "quants_mat_rows.txt").read_text().splitlines() == expected["rows"]
    assert (fixture / "quants_mat_cols.txt").read_text().splitlines() == expected["columns"]
    assert all(len(row) == 16 and set(row) <= set("ACGT") for row in expected["rows"])


def test_alevin_equivalence_class_gzip_has_no_volatile_header() -> None:
    fixture = REPO / "validation-contracts/fixtures/alevin-infer-tiny"
    encoded = (fixture / "eq_labels.txt.gz").read_bytes()
    assert encoded[:4] == bytes([31, 139, 8, 0])
    assert encoded[4:8] == bytes(4)
    assert gzip.decompress(encoded) == (fixture / "eq_labels.txt").read_bytes()


def test_alevin_smoke_contract_is_bound_to_fixtures_and_not_release_verified() -> None:
    contract = load_contract("alevin_fry")
    assert contract["smoke_command"][:2] == ["alevin-fry", "infer"]
    assert contract["smoke_assertions"]["kind"] == "matrix_market_gene_counts"
    assert contract["smoke_assertions"]["expectation_basis"] == "ANALYTICAL_SYMMETRY_NOT_OBSERVED_RUNTIME"
    assert contract["smoke_assertions"]["timeout_seconds"] == 60
    assert contract["pinning"]["native_build_validation_status"] == "NOT_RUN"
    assert contract["pinning"]["release_status"] == "NOT_PUBLISHED"
    assert set(contract["fixture_sha256"]) == set(contract["smoke_inputs"])
    assert all(sha256_file(REPO / path) == digest for path, digest in contract["fixture_sha256"].items())


def test_afterqc_native_package_availability_does_not_waive_legacy_runtime_review() -> None:
    contract = load_contract("afterqc_extra")
    assert contract["native_amd64_expected"] is True
    assert contract["native_arm64_expected"] is True
    assert contract["contract_status"] == "CONTRACT_BLOCKED_MULTIPLE_REASONS"
    assert contract["pinning"]["new_reproducible_baseline_version"] is None
    review = contract["pinning"]["legacy_runtime_review"]
    assert review["runtime"] == "Python 2.7.15"
    assert review["upstream_support"] == "END_OF_LIFE"
    assert review["risk_acceptance"] == "NOT_ESTABLISHED"
    assert review["security_scan"] == "NOT_RUN"
    assert "LEGACY_RUNTIME_REVIEW_REQUIRED" in contract["remediation"]["remaining_deficiency_flags"]
    assert "ARCHITECTURE_EVIDENCE_MISSING" not in contract["remediation"]["remaining_deficiency_flags"]


def test_alevinqc_missing_arm64_candidate_cannot_be_replaced_with_old_noarch_release() -> None:
    contract = load_contract("alevinqc_extra")
    review = contract["pinning"]["architecture_candidate_review"]
    assert review["candidate_version"] == "1.26.0"
    assert review["native_arm64_artifact_count"] == 0
    assert review["arm64_solver_exit_code"] == 1
    assert review["arm64_solver_exception"] == "PackagesNotFoundError"
    assert review["historical_noarch_latest_version"] == "1.10.0"
    assert review["historical_noarch_substitution"] == "NOT_AUTHORIZED"
    assert contract["pinning"]["new_reproducible_baseline_version"] is None
    assert contract["contract_status"] == "CONTRACT_BLOCKED_MULTIPLE_REASONS"
    assert contract["native_arm64_expected"] is False
