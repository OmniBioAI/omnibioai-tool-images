from __future__ import annotations

import csv
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
    "alevin_fry": ("alevin-fry", "0.18.3"),
}
PACKAGE_BUILDS = {
    "3ddna_extra": {"hdfd78af_0"},
    "abricate": {"h05cac1d_0"},
    "accelerate": {"pyh5ded981_0"},
    "admixtools": {"h75d7a4a_0", "he9d944c_0"},
    "agat_extra": {"pl5321hdfd78af_0"},
    "aicsimageio_extra": {"pyhd8ed1ab_0"},
    "alevin_fry": {"hd612981_0", "h6789a04_0"},
}
UNPINNED = {"afterqc_extra", "airr_extra", "alevinqc_extra"}
READY = {"3ddna_extra", "abricate", "accelerate", "agat_extra", "aicsimageio_extra"}
VERSION_OUTPUTS = {
    "3ddna_extra": "version: 190716\n",
    "abricate": "abricate 1.4.0\n",
    "accelerate": "- `Accelerate` version: 1.15.0\n",
    "admixtools": "version: 701\n",
    "agat_extra": "v1.7.0\n",
    "aicsimageio_extra": "4.14.0\n",
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
def test_unsupported_architecture_candidate_is_not_forced(tool_id: str) -> None:
    contract = load_contract(tool_id)
    assert contract["scientific_version"] is None
    assert contract["source_immutable_identity"] is None
    assert contract["pinning"]["new_reproducible_baseline_version"] is None
    assert contract["pinning"]["static_contract_status"] == "STATIC_CONTRACT_BLOCKED"
    assert contract["native_arm64_expected"] is False


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
