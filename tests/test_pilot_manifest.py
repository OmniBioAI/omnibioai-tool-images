"""Dedicated unit tests for scripts/pilot_manifest.py.

These target the specific validation branches, cohort-selection defensive
checks, and the CLI entrypoint (`main`) that are not already exercised by
tests/test_multiarch_pilot.py and tests/test_multiarch_pilot_safety.py.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import pilot_manifest
from scripts.pilot_manifest import (
    ARCHES,
    PILOT9_TOOLS,
    get_tool,
    load_and_validate,
    main,
    manifest_digest,
    matrix,
    selected_matrix,
    validate,
)


def valid_data() -> dict:
    return copy.deepcopy(load_and_validate())


def evidence_copy() -> dict:
    return copy.deepcopy(json.loads(pilot_manifest.EVIDENCE.read_text()))


def write_evidence(tmp_path: Path, monkeypatch, payload: dict) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(pilot_manifest, "EVIDENCE", path)


# ---------------------------------------------------------------------------
# Top-level manifest schema/policy checks
# ---------------------------------------------------------------------------

def test_unsupported_schema_version_rejected():
    data = valid_data()
    data["schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported manifest schema"):
        validate(data)


def test_non_dict_manifest_rejected():
    with pytest.raises(ValueError, match="unsupported manifest schema"):
        validate([])


def test_explicit_sbom_policy_required():
    data = valid_data()
    data["sbom_policy"] = "mandatory"
    with pytest.raises(ValueError, match="SBOM policy must be explicit"):
        validate(data)


def test_exact_arches_rejects_wrong_architecture_set():
    data = valid_data()
    data["architectures"] = ["linux/amd64", "linux/riscv64"]
    with pytest.raises(ValueError, match="exactly two unique linux/amd64 and linux/arm64"):
        validate(data)


def test_wrong_tool_count_rejected():
    data = valid_data()
    data["tools"] = data["tools"][:-1]
    with pytest.raises(ValueError, match="exactly 10 tools"):
        validate(data)


def test_missing_required_tool_field_rejected():
    data = valid_data()
    del data["tools"][0]["license"]
    with pytest.raises(ValueError, match="missing required tool fields"):
        validate(data)


def test_invalid_tool_id_format_rejected():
    data = valid_data()
    data["tools"][0]["tool_id"] = "Not-Valid!"
    with pytest.raises(ValueError, match="invalid tool ID"):
        validate(data)


def test_duplicate_tool_id_rejected():
    data = valid_data()
    data["tools"][1]["tool_id"] = data["tools"][0]["tool_id"]
    with pytest.raises(ValueError, match="duplicate pilot tool"):
        validate(data)


# ---------------------------------------------------------------------------
# Audit evidence cross-checks (require monkeypatching EVIDENCE to a tmp file)
# ---------------------------------------------------------------------------

def test_audit_source_identity_mismatch_rejected(tmp_path, monkeypatch):
    evidence = evidence_copy()
    evidence["schema_version"] = 2
    write_evidence(tmp_path, monkeypatch, evidence)
    with pytest.raises(ValueError, match="audit source identities"):
        validate(valid_data())


def test_audit_source_files_mismatch_rejected(tmp_path, monkeypatch):
    evidence = evidence_copy()
    evidence["source_files"] = {}
    write_evidence(tmp_path, monkeypatch, evidence)
    with pytest.raises(ValueError, match="audit source identities"):
        validate(valid_data())


def test_invalid_authoritative_snapshot_rejected(tmp_path, monkeypatch):
    evidence = evidence_copy()
    evidence["multiarch_ready"] = evidence["multiarch_ready"][:-1]
    write_evidence(tmp_path, monkeypatch, evidence)
    with pytest.raises(ValueError, match="invalid authoritative audit snapshot"):
        validate(valid_data())


def test_published_not_subset_of_ready_rejected(tmp_path, monkeypatch):
    evidence = evidence_copy()
    # Swap in an entry that is not part of multiarch_ready, keeping length identical.
    evidence["published_multiarch_ready"][0] = "definitely-not-in-ready-set"
    write_evidence(tmp_path, monkeypatch, evidence)
    with pytest.raises(ValueError, match="invalid authoritative audit snapshot"):
        validate(valid_data())


def test_unknown_tool_with_no_dockerfile_rejected():
    data = valid_data()
    data["tools"][0]["tool_id"] = "not_a_real_pilot_tool"
    with pytest.raises(ValueError, match="unknown tool"):
        validate(data)


def test_dockerfile_field_mismatch_rejected():
    data = valid_data()
    data["tools"][0]["dockerfile"] = "dockerfiles/Dockerfile.samtools"
    with pytest.raises(ValueError, match="missing/mismatched Dockerfile"):
        validate(data)


def test_missing_reconciliation_evidence_rejected(tmp_path, monkeypatch):
    evidence = evidence_copy()
    evidence["records"]["fastqc"]["ghcr_present"] = "NO"
    write_evidence(tmp_path, monkeypatch, evidence)
    with pytest.raises(ValueError, match="missing matching reconciliation evidence"):
        validate(valid_data())


def test_tool_not_in_ready_or_published_rejected(tmp_path, monkeypatch):
    evidence = evidence_copy()
    idx = evidence["multiarch_ready"].index("fastqc")
    evidence["multiarch_ready"][idx] = "zzz-placeholder-not-a-tool"
    pub_idx = evidence["published_multiarch_ready"].index("fastqc")
    evidence["published_multiarch_ready"][pub_idx] = "zzz-placeholder-not-a-tool"
    write_evidence(tmp_path, monkeypatch, evidence)
    with pytest.raises(ValueError, match="is not an audited published MULTIARCH_READY tool"):
        validate(valid_data())


def test_manifest_category_contradicts_audit_rejected():
    data = valid_data()
    data["tools"][0]["category"] = "OTHER"
    with pytest.raises(ValueError, match="manifest contradicts audit"):
        validate(data)


def test_manifest_published_flag_contradicts_audit_rejected():
    data = valid_data()
    data["tools"][0]["published_in_ghcr"] = False
    with pytest.raises(ValueError, match="manifest contradicts audit"):
        validate(data)


# ---------------------------------------------------------------------------
# Per-tool structural checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["stub_or_placeholder", "architecture_dependent", "license_restricted"])
def test_disallowed_flag_rejected(flag):
    data = valid_data()
    data["tools"][0][flag] = True
    with pytest.raises(ValueError, match=f"disallowed/undeclared {flag}"):
        validate(data)


def test_empty_required_field_rejected():
    data = valid_data()
    data["tools"][0]["selection_reason"] = "   "
    with pytest.raises(ValueError, match="empty/malformed selection_reason"):
        validate(data)


def test_unsupported_executable_type_rejected():
    data = valid_data()
    data["tools"][0]["executable_type"] = "bytecode"
    with pytest.raises(ValueError, match="unsupported executable type"):
        validate(data)


def test_ghcr_repository_must_match_canonical_mapping():
    data = valid_data()
    data["tools"][0]["ghcr_repository"] = "ghcr.io/someone-else/fastqc"
    with pytest.raises(ValueError, match="not the exact canonical pilot mapping"):
        validate(data)


def test_base_image_must_be_the_reviewed_pilot_base():
    data = valid_data()
    data["tools"][0]["base_image"] = "docker.io/library/python:3.12-slim-bookworm"
    with pytest.raises(ValueError, match="not the reviewed pilot base"):
        validate(data)


def test_scientific_version_pattern_needs_named_group():
    data = valid_data()
    data["tools"][0]["scientific_version_pattern"] = r"[0-9]+(?:\.[0-9]+)+"
    with pytest.raises(ValueError, match="needs a named version group"):
        validate(data)


@pytest.mark.parametrize("package_identity", [
    {"kind": "rpm", "name": "fastqc"},
    {"kind": "dpkg", "name": "wrong-name"},
    {"kind": "dpkg", "name": "fastqc", "extra": "x"},
    "not-a-dict",
])
def test_invalid_package_identity_contract_rejected(package_identity):
    data = valid_data()
    data["tools"][0]["package_identity"] = package_identity
    with pytest.raises(ValueError, match="invalid package identity contract"):
        validate(data)


def test_expected_executable_must_equal_tool_id():
    data = valid_data()
    data["tools"][0]["expected_executable"] = "not-fastqc"
    with pytest.raises(ValueError, match="tool/executable identity mismatch"):
        validate(data)


def test_invalid_runtime_executable_name_rejected():
    data = valid_data()
    data["tools"][0]["runtime_executables"] = ["perl!"]
    with pytest.raises(ValueError, match="invalid runtime executable"):
        validate(data)


def test_duplicate_runtime_executables_rejected():
    data = valid_data()
    data["tools"][0]["runtime_executables"] = ["perl", "perl"]
    with pytest.raises(ValueError, match="invalid runtime executable"):
        validate(data)


def test_interpreted_tool_requires_launcher_in_runtimes():
    data = valid_data()
    tool = data["tools"][0]
    assert tool["executable_type"] == "interpreted"
    tool["launcher_interpreter"] = "ruby"
    with pytest.raises(ValueError, match="needs a declared launcher interpreter"):
        validate(data)


def test_native_binary_cannot_declare_runtimes():
    data = valid_data()
    native = next(t for t in data["tools"] if t["executable_type"] == "native_binary")
    native["runtime_executables"] = ["bash"]
    with pytest.raises(ValueError, match="must not declare launcher interpreters"):
        validate(data)


def test_native_binary_cannot_declare_launcher_interpreter():
    data = valid_data()
    native = next(t for t in data["tools"] if t["executable_type"] == "native_binary")
    native["launcher_interpreter"] = "bash"
    with pytest.raises(ValueError, match="must not declare launcher interpreters"):
        validate(data)


def test_dockerfile_stub_or_platform_marker_rejected(monkeypatch):
    import pathlib

    original_read_text = pathlib.Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if self.name == "Dockerfile.fastqc":
            return "FROM docker.io/library/python:3.11-slim-bookworm\n# stub placeholder\n"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    with pytest.raises(ValueError, match="excluded architecture/stub marker"):
        validate(valid_data())


@pytest.mark.parametrize("command", ["fastqc --version:latest", "fastqc `id`"])
def test_mutable_or_unsafe_command_rejected(command):
    data = valid_data()
    data["tools"][0]["version_command"] = command
    with pytest.raises(ValueError, match="mutable/unsafe command"):
        validate(data)


@pytest.mark.parametrize("command", [
    "fastqc --version && true",
    "fastqc --version || true",
    "eval fastqc --version",
    "fastqc --version; if true; then true; fi",
])
def test_failure_suppressing_command_syntax_rejected(command):
    data = valid_data()
    data["tools"][0]["smoke_command"] = command
    with pytest.raises(ValueError, match="failure-suppressing command syntax"):
        validate(data)


# ---------------------------------------------------------------------------
# matrix()/selected_matrix() defensive invariants and cohort logic
# ---------------------------------------------------------------------------

def test_matrix_rejects_corrupted_duplicate_architecture_pairs(monkeypatch):
    data = valid_data()
    data["architectures"] = ["linux/amd64", "linux/amd64"]
    for tool in data["tools"]:
        tool["architectures"] = ["linux/amd64", "linux/amd64"]
    monkeypatch.setattr(pilot_manifest, "ARCHES", ("linux/amd64", "linux/amd64"))
    with pytest.raises(ValueError, match="matrix must contain exactly 20"):
        matrix(data)


def test_pilot9_cohort_returns_exactly_nine_canonical_tools():
    data = load_and_validate()
    selected = selected_matrix(data, cohort="pilot9")
    assert len(selected) == 18
    assert {item["tool_id"] for item in selected} == set(PILOT9_TOOLS)
    assert all(item["tool_id"] != "bedtools" for item in selected)


def test_pilot9_cohort_cannot_be_combined_with_tool_selector():
    data = load_and_validate()
    with pytest.raises(ValueError, match="cannot be combined with a tool selector"):
        selected_matrix(data, tool_id="muscle", cohort="pilot9")


def test_pilot9_cohort_rejects_corrupted_entries(monkeypatch):
    def fake_matrix(_data):
        # Only 8 of the 9 canonical tools represented: breaks the invariant.
        return [
            {"tool_id": tool, "platform": arch}
            for tool in PILOT9_TOOLS[:8]
            for arch in ARCHES
        ]

    monkeypatch.setattr(pilot_manifest, "matrix", fake_matrix)
    with pytest.raises(ValueError, match="must contain exactly nine canonical non-Bedtools tools"):
        selected_matrix({}, cohort="pilot9")


@pytest.mark.parametrize("tool_id", [None, "", "all"])
def test_single_cohort_requires_explicit_tool(tool_id):
    data = load_and_validate()
    with pytest.raises(ValueError, match="single cohort requires one canonical tool ID"):
        selected_matrix(data, tool_id=tool_id, cohort="single")


def test_single_cohort_with_explicit_tool_succeeds():
    data = load_and_validate()
    selected = selected_matrix(data, tool_id="muscle", cohort="single")
    assert {item["tool_id"] for item in selected} == {"muscle"}


def test_explicit_unknown_tool_selection_rejected():
    data = load_and_validate()
    with pytest.raises(ValueError, match="unknown or incomplete pilot tool selection"):
        selected_matrix(data, tool_id="not-a-real-tool")


def test_unknown_cohort_rejected():
    data = load_and_validate()
    with pytest.raises(ValueError, match="unknown cohort: bogus"):
        selected_matrix(data, cohort="bogus")


@pytest.mark.parametrize("tool_id", [None, "", "all"])
@pytest.mark.parametrize("cohort", [None, "all"])
def test_default_selection_returns_full_matrix(tool_id, cohort):
    data = load_and_validate()
    selected = selected_matrix(data, tool_id=tool_id, cohort=cohort)
    assert len(selected) == 20


def test_get_tool_returns_matching_entry():
    tool = get_tool("muscle")
    assert tool["tool_id"] == "muscle"


def test_get_tool_rejects_unknown_tool():
    with pytest.raises(ValueError, match="unknown pilot tool"):
        get_tool("does-not-exist")


def test_manifest_digest_is_stable_sha256_hex():
    digest = manifest_digest()
    assert len(digest) == 64
    assert digest == manifest_digest()
    int(digest, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# CLI entrypoint (main) — exercised in-process so coverage is attributed.
# ---------------------------------------------------------------------------

def run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["pilot_manifest.py", *argv])
    return main()


def test_main_default_invocation_succeeds_silently(monkeypatch, capsys):
    code = run_main(monkeypatch, [])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


def test_main_matrix_flag_prints_full_payload(monkeypatch, capsys):
    code = run_main(monkeypatch, ["--matrix"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert len(payload) == 20


def test_main_github_output_appends_matrix_line(monkeypatch, tmp_path):
    output_file = tmp_path / "github_output.txt"
    output_file.write_text("existing=line\n")
    code = run_main(monkeypatch, ["--github-output", str(output_file)])
    assert code == 0
    content = output_file.read_text()
    assert content.startswith("existing=line\n")
    assert content.splitlines()[-1].startswith("matrix=")


def test_main_tool_selection_narrows_matrix(monkeypatch, capsys):
    code = run_main(monkeypatch, ["--tool", "muscle", "--matrix"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert {item["tool_id"] for item in payload} == {"muscle"}


def test_main_pilot9_cohort_via_cli(monkeypatch, capsys):
    code = run_main(monkeypatch, ["--cohort", "pilot9", "--matrix"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert len(payload) == 18


def test_main_release_factory_refuses_bedtools_publication(monkeypatch, capsys):
    code = run_main(monkeypatch, ["--release-factory", "--publish", "true"])
    captured = capsys.readouterr()
    assert code == 1
    assert "pilot validation failed" in captured.err
    assert "refuses to publish the frozen Bedtools" in captured.err


def test_main_release_factory_allows_non_bedtools_publication(monkeypatch, capsys):
    code = run_main(monkeypatch, ["--release-factory", "--publish", "true", "--tool", "muscle"])
    assert code == 0


def test_main_publish_without_release_factory_rejected(monkeypatch, capsys):
    code = run_main(monkeypatch, ["--publish", "true"])
    captured = capsys.readouterr()
    assert code == 1
    assert "publication is disabled" in captured.err


@pytest.mark.parametrize("exc", [
    OSError("disk exploded"),
    json.JSONDecodeError("bad json", "{}", 0),
    ValueError("synthetic validation failure"),
])
def test_main_reports_load_failures_and_exits_nonzero(monkeypatch, capsys, exc):
    def fail():
        raise exc

    monkeypatch.setattr(pilot_manifest, "load_and_validate", fail)
    code = run_main(monkeypatch, ["--matrix"])
    captured = capsys.readouterr()
    assert code == 1
    assert "pilot validation failed" in captured.err
    assert captured.out == ""
