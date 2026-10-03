"""Fail-closed contracts for the manifest-driven immutable SIF release factory."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import bedtools_canary
from scripts import pilot_probe
from scripts import sif_identity as identity
from scripts import sif_release as release
from scripts.pilot_manifest import (
    ARCHES,
    PACKAGE_ALLOWLIST,
    PILOT9_TOOLS,
    load_and_validate,
    matrix,
    selected_matrix,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pilot-immutable-multiarch-release.yml"
ZERO = "0" * 64
ONE = "1" * 64
TWO = "2" * 64


VERSION_OUTPUTS = {
    "fastqc": "FastQC v0.12.1",
    "samtools": "samtools 1.16.1\nUsing htslib 1.16",
    "bcftools": "bcftools 1.16",
    "bedtools": "bedtools v2.30.0",
    "bwa": "Version: 0.7.17-r1188",
    "minimap2": "2.24-r1122",
    "multiqc": "multiqc, version 1.30",
    "muscle": "muscle 5.2.linux64 x86_64",
    "prodigal": "Prodigal V2.6.3",
    "vcftools": "VCFtools (0.1.16)",
}


def candidate(tool="fastqc", arch="amd64", version="0.12.1", **changes):
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    value = {
        "schema_version": "1",
        "tool_id": tool,
        "scientific_version": version,
        "target_architecture": arch,
        "source_commit": "a" * 40,
        "dockerfile_path": entry["dockerfile"],
        "dockerfile_sha256": ZERO,
        "base_image_identity": "docker.io/library/python:3.11-slim-bookworm@sha256:" + ONE,
        "oci_source_identity": "sha256:" + TWO,
        "sif_sha256": "3" * 64,
        "expected_executable": entry["expected_executable"],
        "version_evidence": VERSION_OUTPUTS[tool],
        "smoke_status": "PASS",
        "provenance_status": "PASS",
        "build_inputs": {
            "package_identity": f"{tool}={version}",
            "rendered_dockerfile_sha256": "4" * 64,
            "tool_runtime_commit": release.RUNTIME_COMMIT,
        },
        "operational_provenance": {"github_run_id": "1", "workflow_attempt": "1"},
    }
    value.update(changes)
    return value


def artifact(value, *, tag=None, metadata=True, sif_sha=None):
    return {
        "tag": tag or identity.immutable_tag(value),
        "manifest_digest": "sha256:" + "5" * 64,
        "sif_sha256": sif_sha or value["sif_sha256"],
        **({"identity_metadata": identity.identity_metadata(value)} if metadata else {}),
    }


def minimal_release_provenance(tool="fastqc"):
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    version = release.resolve_scientific_version(entry, VERSION_OUTPUTS[tool])
    return {
        "tool": tool,
        "dockerfile": entry["dockerfile"],
        "target_architecture": "amd64",
        "base_image_identity": "docker.io/library/python:3.11-slim-bookworm@sha256:" + ONE,
        "rendered_dockerfile_sha256": ZERO,
        "oci_identity": "sha256:" + TWO,
        "oci_child_digest": "sha256:" + TWO,
        "package_identity": f"{tool}={version}",
        "oci_version": {"output": VERSION_OUTPUTS[tool], "meaningful_output": VERSION_OUTPUTS[tool]},
        "sif_version": {"output": VERSION_OUTPUTS[tool], "meaningful_output": VERSION_OUTPUTS[tool]},
        "scientific_version": version,
        "sbom_status": "SBOM_OPTIONAL_NOT_GENERATED",
    }


def test_exact_ten_tool_manifest_is_accepted():
    data = load_and_validate()
    assert len(data["tools"]) == len({tool["tool_id"] for tool in data["tools"]}) == 10


def test_duplicate_manifest_tool_is_rejected():
    data = copy.deepcopy(load_and_validate())
    data["tools"][-1]["tool_id"] = data["tools"][0]["tool_id"]
    with pytest.raises(ValueError, match="duplicate"):
        validate(data)


def test_unknown_tool_selection_is_rejected():
    with pytest.raises(ValueError, match="unknown or incomplete"):
        selected_matrix(load_and_validate(), "not_a_tool", "single")


def test_pilot9_selector_is_exact_and_excludes_bedtools():
    selected = selected_matrix(load_and_validate(), cohort="pilot9")
    assert {item["tool_id"] for item in selected} == set(PILOT9_TOOLS)
    assert "bedtools" not in {item["tool_id"] for item in selected}
    assert len(selected) == 18


def test_full_matrix_is_twenty_entries_and_two_exact_architectures():
    result = matrix(load_and_validate())
    assert len(result) == 20
    assert {item["platform"] for item in result} == set(ARCHES)
    assert all(sum(item["tool_id"] == tool for item in result) == 2 for tool in PACKAGE_ALLOWLIST)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("dockerfile", "dockerfiles/Dockerfile.does_not_exist", "missing/mismatched Dockerfile"),
        ("expected_executable", "", "empty/malformed"),
        ("version_command", "", "empty/malformed"),
        ("smoke_command", "", "empty/malformed"),
        ("scientific_version_pattern", "", "empty/malformed"),
    ],
)
def test_missing_mandatory_manifest_contract_fails_closed(field, value, match):
    data = copy.deepcopy(load_and_validate())
    data["tools"][0][field] = value
    with pytest.raises(ValueError, match=match):
        validate(data)


def test_unknown_or_duplicate_architecture_is_rejected():
    for arches in (["linux/amd64", "linux/riscv64"], ["linux/amd64", "linux/amd64"]):
        data = copy.deepcopy(load_and_validate())
        data["tools"][0]["architectures"] = arches
        with pytest.raises(ValueError, match="architectures"):
            validate(data)


def test_exact_package_mapping_is_manifest_bound_and_unique():
    data = load_and_validate()
    repositories = [tool["ghcr_repository"] for tool in data["tools"]]
    assert len(repositories) == len(set(repositories)) == 10
    assert {tool["tool_id"]: tool["ghcr_repository"] for tool in data["tools"]} == PACKAGE_ALLOWLIST
    changed = copy.deepcopy(data)
    changed["tools"][0]["ghcr_repository"] = PACKAGE_ALLOWLIST["samtools"]
    with pytest.raises(ValueError, match="exact canonical"):
        validate(changed)


@pytest.mark.parametrize("tool", list(VERSION_OUTPUTS))
def test_scientific_version_resolves_from_validated_runtime_output(tool):
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    version = release.resolve_scientific_version(entry, VERSION_OUTPUTS[tool])
    assert version and version[0].isdigit()


def test_missing_or_ambiguous_scientific_version_fails_closed():
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == "fastqc")
    with pytest.raises(release.ReleaseError):
        release.resolve_scientific_version(entry, "")
    with pytest.raises(release.ReleaseError, match="unambiguously"):
        release.resolve_scientific_version(entry, "FastQC v1.0\nFastQC v2.0")


def test_identity_is_deterministic_architecture_bound_and_ignores_operational_fields():
    first = candidate()
    second = copy.deepcopy(first)
    second["operational_provenance"] = {"github_run_id": "999", "timestamp": "tomorrow"}
    assert identity.build_identity_sha256(first) == identity.build_identity_sha256(second)
    arm = candidate(arch="arm64")
    assert identity.build_identity_sha256(first) != identity.build_identity_sha256(arm)
    assert identity.immutable_tag(first) != identity.immutable_tag(arm)


def test_publication_decision_exact_match_skips():
    value = candidate()
    result = identity.evaluate_publication(value, {"artifacts": [artifact(value)]})
    assert result.decision is identity.Decision.SKIP_EXACT_MATCH


def test_publication_decision_collision_identity_mismatch_and_missing_metadata_block():
    value = candidate()
    tag = identity.immutable_tag(value)
    collision = artifact(candidate(arch="arm64"), tag=tag)
    assert identity.evaluate_publication(value, {"artifacts": [collision]}).decision in {
        identity.Decision.BLOCK_TAG_COLLISION, identity.Decision.BLOCK_IDENTITY_MISMATCH
    }
    wrong_identity = artifact(candidate(version="9.9"), tag="other-immutable")
    assert identity.evaluate_publication(value, {"artifacts": [wrong_identity]}).decision in {
        identity.Decision.PUBLISH_NEW, identity.Decision.BLOCK_IDENTITY_MISMATCH
    }
    missing = artifact(value, metadata=False)
    assert identity.evaluate_publication(value, {"artifacts": [missing]}).decision is identity.Decision.BLOCK_METADATA_MISSING


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.pop("base_image_identity"),
        lambda record: record.__setitem__("scientific_version", None),
        lambda record: record.__setitem__("oci_child_digest", "sha256:bad"),
    ],
)
def test_missing_null_or_malformed_release_provenance_fails(monkeypatch, mutation):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    record = minimal_release_provenance()
    mutation(record)
    with pytest.raises(release.ReleaseError):
        release.validate_release_provenance(record, release.release_spec("fastqc"))


def test_empty_sbom_cannot_pass(monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    record = minimal_release_provenance()
    record["sbom_status"] = "SBOM_PASS"
    with pytest.raises(release.ReleaseError, match="SBOM_PASS"):
        release.validate_release_provenance(record, release.release_spec("fastqc"))


def test_mandatory_command_failure_and_non_utf8_diagnostics_are_not_hidden(monkeypatch, tmp_path):
    raw = subprocess.CompletedProcess([], 7, stdout=b"samtools 1.0\xff", stderr=b"fatal\xfe")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: raw)
    result = pilot_probe.run("samtools --version", tmp_path)
    assert result.returncode == 7
    assert "\\xff" in result.stdout
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == "samtools")
    with pytest.raises(ValueError, match="returncode=7"):
        pilot_probe.command_evidence(result, entry, "version")


def test_generic_secret_redaction_preserves_diagnostics(monkeypatch):
    token = "github_pat_" + "A" * 30
    stderr = f"permission_denied token={token} Authorization: Bearer {token}"
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "partial", stderr),
    )
    with pytest.raises(release.OrasInvocationFailed) as excinfo:
        release.run(["oras", "push", "x"])
    message = str(excinfo.value)
    assert "permission_denied" in message and "exit_code=1" in message
    assert token not in message and "[REDACTED]" in message


def test_staging_containment_accepts_normal_and_rejects_traversal_and_symlink(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    inside = root / "artifact.sif"
    inside.write_bytes(b"data")
    assert release.require_contained_staging_file(inside, root) == inside.resolve()
    outside = tmp_path / "outside.sif"
    outside.write_bytes(b"secret")
    with pytest.raises(release.ReleaseError, match="escapes"):
        release.require_contained_staging_file(root / ".." / "outside.sif", root)
    link = root / "link.sif"
    link.symlink_to(outside)
    with pytest.raises(release.ReleaseError, match="escapes"):
        release.require_contained_staging_file(link, root)


def test_oras_failure_propagates(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "denied"),
    )
    with pytest.raises(release.OrasInvocationFailed) as excinfo:
        release.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_PERMISSION_FAILURE"


def test_publish_false_performs_zero_writes_and_cannot_invoke_push(tmp_path):
    payload = b"validated"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    evaluation = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new", identity.immutable_tag(value),
        identity.build_identity_sha256(value),
    )
    commands = []
    result = release.release_candidate_for_spec(
        release.release_spec("fastqc"), candidate_path, sif, tmp_path / "result.json",
        publish_enabled=False,
        evaluate_live_fn=lambda unused: ({"artifacts": []}, evaluation),
        run_fn=lambda argv, **kwargs: commands.append(list(argv)),
    )
    assert result["decision"] == "WOULD_PUBLISH_NEW"
    assert result["write_performed"] is False
    assert commands == []
    source = (ROOT / "scripts/sif_release.py").read_text()
    workflow = WORKFLOW.read_text()
    assert source.count('"oras", "push"') == 1
    assert "docker push" not in source.lower() and "docker push" not in workflow.lower()


def test_retrieval_absence_and_remote_sha_mismatch_fail(tmp_path):
    value = candidate()
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    spec = release.release_spec("fastqc")
    with pytest.raises(release.ReleaseError, match="absent"):
        release.retrieve_and_verify_for_spec(
            spec, candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: None,
        )
    remote = {
        "manifest": {"annotations": identity.identity_annotations(value)},
        "identity_metadata": identity.identity_metadata(value),
        "sif_sha256": "9" * 64,
    }
    with pytest.raises(release.ReleaseError, match="descriptor differs"):
        release.retrieve_and_verify_for_spec(
            spec, candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: remote,
        )


@pytest.mark.parametrize("failure", ["architecture", "version", "smoke"])
def test_retrieved_runtime_architecture_version_and_smoke_failures_propagate(tmp_path, monkeypatch, failure):
    payload = b"sif"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "artifact.sif"
    sif.write_bytes(payload)
    workspace = tmp_path / "work"

    def fake_run(argv, **kwargs):
        if "inspect" in argv:
            return subprocess.CompletedProcess(argv, 0, '{"ok":true}', "")
        workspace.mkdir(exist_ok=True)
        output = VERSION_OUTPUTS["fastqc"] if failure != "version" else "FastQC v9.9"
        probe = {
            "architecture": {"status": "PASS", "target": "amd64", "uname": "x86_64"},
            "executable": {"status": "PASS"},
            "version": {"status": "PASS", "output": output, "meaningful_output": output},
            "smoke": {"status": "PASS"},
        }
        (workspace / "retrieved-sif-probe.json").write_text(json.dumps(probe))
        return subprocess.CompletedProcess(argv, 0, "", "")

    if failure in ("architecture", "smoke"):
        monkeypatch.setattr(
            release, "validate_probe_result",
            lambda *args: (_ for _ in ()).throw(ValueError(f"{failure} mismatch")),
        )
    else:
        monkeypatch.setattr(release, "validate_probe_result", lambda *args: None)
    expected = "scientific version differs" if failure == "version" else f"{failure} mismatch"
    with pytest.raises((release.ReleaseError, ValueError), match=expected):
        release.verify_retrieved_runtime_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, workspace,
            tmp_path / "runtime.json", run_fn=fake_run,
        )


def test_bedtools_wrapper_preserves_reference_spec_and_identity_semantics():
    spec = release.release_spec("bedtools")
    assert bedtools_canary.TOOL == spec.tool_id == "bedtools"
    assert bedtools_canary.REPOSITORY == spec.repository
    assert bedtools_canary.SCIENTIFIC_VERSION == release.resolve_scientific_version(
        next(item for item in load_and_validate()["tools"] if item["tool_id"] == "bedtools"),
        VERSION_OUTPUTS["bedtools"],
    )
    value = candidate(tool="bedtools", version="2.30.0")
    assert identity.immutable_tag(value).startswith("sif-v1-2.30.0-amd64-")


def test_workflow_contract_is_manifest_driven_least_privilege_and_default_dry_run():
    data = yaml.safe_load(WORKFLOW.read_text())
    trigger = data.get("on", data.get(True))
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["publish"]["default"] is False
    assert inputs["cohort"]["default"] == "pilot9"
    job = data["jobs"]["build-verify-release-retrieve"]
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert job["strategy"]["fail-fast"] is False
    source = WORKFLOW.read_text()
    assert "scripts/pilot_manifest.py" in source and "--release-factory" in source
    assert "scripts.sif_release release" in source and '--publish "$PUBLISH"' in source
    assert "github.token" in source
    assert not any(marker in source for marker in ("secrets.PAT", "PERSONAL_ACCESS_TOKEN", "docker push", "oras push"))
    assert source.count("if: ${{ inputs.publish }}") == 3


def test_publish_true_cannot_select_bedtools_via_release_factory(tmp_path):
    output = tmp_path / "output"
    completed = subprocess.run(
        [
            "python3", "scripts/pilot_manifest.py", "--release-factory",
            "--publish", "true", "--cohort", "single", "--tool", "bedtools",
            "--github-output", str(output),
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert completed.returncode != 0
    assert "frozen Bedtools" in completed.stderr


def test_legacy_comparison_allows_only_candidate_and_rejects_mutation():
    before = {"repository": "r", "artifacts": [{"tag": "arm64", "manifest_digest": "sha256:a"}]}
    after = {"repository": "r", "artifacts": [
        {"tag": "arm64", "manifest_digest": "sha256:a"},
        {"tag": "sif-v1-x", "manifest_digest": "sha256:b"},
    ]}
    release.verify_legacy_preserved(before, after, "sif-v1-x")
    changed = copy.deepcopy(after)
    changed["artifacts"][0]["manifest_digest"] = "sha256:c"
    with pytest.raises(release.ReleaseError, match="deleted or mutated"):
        release.verify_legacy_preserved(before, changed, "sif-v1-x")
