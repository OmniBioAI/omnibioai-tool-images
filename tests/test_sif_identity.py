"""Regression contracts for immutable, fail-closed SIF artifact identity."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import bedtools_canary
from scripts import sif_identity as identity
from scripts import pilot_runner


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/sif_identity.py"
LEGACY_PUBLISHER = ROOT / "scripts/push_to_ghcr.sh"
CANARY_WORKFLOW = ROOT / ".github/workflows/bedtools-immutable-canary.yml"
ZERO = "0" * 64
ONE = "1" * 64
TWO = "2" * 64


def candidate(**changes):
    value = {
        "schema_version": "1",
        "tool_id": "bedtools",
        "scientific_version": "2.30.0",
        "target_architecture": "amd64",
        "source_commit": "2622bbf99f9518015f5b8414b8e537f1e0416101",
        "dockerfile_path": "dockerfiles/Dockerfile.bedtools",
        "dockerfile_sha256": ZERO,
        "base_image_identity": "python:3.11-slim-bookworm@sha256:" + ONE,
        "oci_source_identity": "sha256:" + TWO,
        "sif_sha256": "3" * 64,
        "expected_executable": "bedtools",
        "version_evidence": "bedtools v2.30.0",
        "smoke_status": "PASS",
        "provenance_status": "PASS",
        "build_inputs": {
            "package_revision": "2.30.0+dfsg-3",
            "runtime_commit": "a4f3da56c2e6e9fd85e54ba009f6f3456de3b5b5",
            "source_checksum": "4" * 64,
        },
        "operational_provenance": {
            "timestamp": "2026-10-01T00:00:00Z",
            "github_run_id": "1",
            "runner_hostname": "runner-a",
            "workflow_attempt": 1,
        },
    }
    value.update(changes)
    return value


def artifact(value, *, tag=None, sif_sha256=None, metadata=True):
    return {
        "tag": tag or identity.immutable_tag(value),
        "manifest_digest": "sha256:" + "5" * 64,
        "sif_sha256": sif_sha256 or value["sif_sha256"],
        **({"identity_metadata": identity.identity_metadata(value)} if metadata else {}),
    }


def evaluate(value, artifacts=()):
    return identity.evaluate_publication(value, {"artifacts": list(artifacts)})


def test_canonical_serialization_is_key_order_independent_and_compact_utf8():
    left = {"z": "μ", "a": {"y": 2, "x": 1}}
    right = {"a": {"x": 1, "y": 2}, "z": "μ"}
    assert identity.canonical_json_bytes(left) == identity.canonical_json_bytes(right)
    assert identity.canonical_json_bytes(left) == '{"a":{"x":1,"y":2},"z":"μ"}'.encode()


def test_build_id_is_full_deterministic_sha256():
    first = identity.build_identity_sha256(candidate())
    assert first == identity.build_identity_sha256(candidate())
    assert len(first) == identity.BUILD_ID_HEX_LENGTH == 64
    int(first, 16)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [("amd64", "amd64"), ("x86_64", "amd64"), ("X64", "amd64"),
     ("arm64", "arm64"), ("aarch64", "arm64"), ("ARM64", "arm64")],
)
def test_architecture_normalization(alias, expected):
    assert identity.normalize_architecture(alias) == expected


@pytest.mark.parametrize("architecture", ["", "ppc64le", "unknown", None])
def test_unknown_architecture_fails_closed(architecture):
    result = evaluate(candidate(target_architecture=architecture))
    assert result.decision is identity.Decision.ERROR


def test_immutable_tag_contains_version_architecture_and_96_bit_prefix():
    value = candidate()
    build_id = identity.build_identity_sha256(value)
    assert identity.immutable_tag(value) == f"sif-v1-2.30.0-amd64-{build_id[:24]}"
    assert identity.TAG_HASH_PREFIX_LENGTH == 24


def test_new_artifact_is_publish_new_without_writing():
    result = evaluate(candidate())
    assert result.decision is identity.Decision.PUBLISH_NEW
    assert result.registry_write_performed is False


def test_exact_match_is_skipped():
    value = candidate()
    result = evaluate(value, [artifact(value)])
    assert result.decision is identity.Decision.SKIP_EXACT_MATCH


def test_exact_artifact_under_another_immutable_reference_is_skipped():
    value = candidate()
    result = evaluate(value, [artifact(value, tag="sif-v1-historical-exact")])
    assert result.decision is identity.Decision.SKIP_EXACT_MATCH
    assert result.existing_reference == "sif-v1-historical-exact"


def test_same_tag_with_different_full_build_identity_blocks_collision(monkeypatch):
    value = candidate()
    other = candidate(source_commit="f" * 40)
    tag = identity.immutable_tag(value)
    monkeypatch.setattr(identity, "immutable_tag", lambda unused: tag)
    result = evaluate(value, [artifact(other, tag=tag)])
    assert result.decision is identity.Decision.BLOCK_TAG_COLLISION


def test_same_build_identity_with_different_sif_blocks_identity_mismatch():
    value = candidate()
    other = candidate(sif_sha256="9" * 64)
    result = evaluate(value, [artifact(other, tag=identity.immutable_tag(value))])
    assert result.decision is identity.Decision.BLOCK_IDENTITY_MISMATCH


def test_same_build_identity_elsewhere_with_different_sif_also_blocks():
    value = candidate()
    other = candidate(sif_sha256="9" * 64)
    result = evaluate(value, [artifact(other, tag="sif-v1-other-reference")])
    assert result.decision is identity.Decision.BLOCK_IDENTITY_MISMATCH


def test_missing_identity_metadata_blocks_immutable_tag():
    value = candidate()
    result = evaluate(value, [artifact(value, metadata=False)])
    assert result.decision is identity.Decision.BLOCK_METADATA_MISSING


def test_malformed_identity_metadata_blocks_immutable_tag():
    value = candidate()
    entry = artifact(value)
    entry["identity_metadata"] = {"schema_version": "1"}
    result = evaluate(value, [entry])
    assert result.decision is identity.Decision.BLOCK_METADATA_MISSING


def test_stored_metadata_missing_full_build_identity_blocks_immutable_tag():
    value = candidate()
    entry = artifact(value)
    del entry["identity_metadata"]["build_identity_sha256"]
    result = evaluate(value, [entry])
    assert result.decision is identity.Decision.BLOCK_METADATA_MISSING


def test_missing_sif_sha256_fails_closed():
    value = candidate()
    del value["sif_sha256"]
    assert evaluate(value).decision is identity.Decision.ERROR


def test_missing_supplied_build_identity_is_computed():
    metadata = identity.identity_metadata(candidate())
    assert metadata["build_identity_sha256"] == identity.build_identity_sha256(candidate())


def test_incorrect_supplied_build_identity_fails_closed():
    value = candidate(build_identity_sha256="f" * 64)
    assert evaluate(value).decision is identity.Decision.ERROR


def test_unknown_candidate_field_fails_closed():
    assert evaluate(candidate(untracked_build_knob="changed")).decision is identity.Decision.ERROR


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("dockerfile_sha256", "a" * 64),
        ("base_image_identity", "python@sha256:" + "b" * 64),
        ("target_architecture", "arm64"),
        ("scientific_version", "2.31.0"),
        ("source_commit", "c" * 40),
        ("oci_source_identity", "sha256:" + "d" * 64),
    ],
)
def test_identity_relevant_top_level_input_changes_build_id(field, replacement):
    assert identity.build_identity_sha256(candidate()) != identity.build_identity_sha256(
        candidate(**{field: replacement})
    )


def test_source_checksum_change_changes_build_id():
    changed = candidate()["build_inputs"].copy()
    changed["source_checksum"] = "e" * 64
    assert identity.build_identity_sha256(candidate()) != identity.build_identity_sha256(
        candidate(build_inputs=changed)
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("timestamp", "2030-01-01T00:00:00Z"),
        ("github_run_id", "999"),
        ("runner_hostname", "runner-b"),
        ("workflow_attempt", 8),
    ],
)
def test_operational_metadata_does_not_change_build_id(field, replacement):
    operational = candidate()["operational_provenance"].copy()
    operational[field] = replacement
    assert identity.build_identity_sha256(candidate()) == identity.build_identity_sha256(
        candidate(operational_provenance=operational)
    )


def test_idempotency_publish_then_skip_then_skip():
    value = candidate()
    first = evaluate(value)
    state = [artifact(value)]
    second = evaluate(value, state)
    third = evaluate(value, state)
    assert [first.decision, second.decision, third.decision] == [
        identity.Decision.PUBLISH_NEW,
        identity.Decision.SKIP_EXACT_MATCH,
        identity.Decision.SKIP_EXACT_MATCH,
    ]


def test_legacy_architecture_tag_does_not_block_new_immutable_tag():
    value = candidate(target_architecture="arm64")
    legacy = {"tag": "arm64", "sif_sha256": value["sif_sha256"]}
    result = evaluate(value, [legacy])
    assert result.decision is identity.Decision.PUBLISH_NEW
    assert result.legacy_status is identity.LegacyStatus.LEGACY_ARTIFACT_EXACT_CONTENT


def test_exact_bedtools_legacy_different_content_is_preserved_and_new_tag_allowed():
    value = candidate(
        target_architecture="arm64",
        sif_sha256="3f311d15a0b2307abbd4734e8504c354a2e67a32e68ac5e94110fe042f7d4fbe",
    )
    legacy = {
        "tag": "arm64",
        "sif_sha256": "50d024aacfcd5caa1da804792dcc5cba6f002295d0ba4802ff8bf3fd67756fde",
    }
    result = evaluate(value, [legacy])
    assert result.decision is identity.Decision.PUBLISH_NEW
    assert result.legacy_status is identity.LegacyStatus.LEGACY_ARTIFACT_DIFFERENT_CONTENT
    assert result.registry_write_performed is False


def test_version_only_tag_is_not_authoritative_and_is_never_overwritten():
    value = candidate()
    result = evaluate(value, [{"tag": "2.30.0", "sif_sha256": "8" * 64}])
    assert result.decision is identity.Decision.PUBLISH_NEW
    assert result.immutable_tag != "2.30.0"


def test_moving_alias_mutation_is_disabled():
    assert identity.MOVING_ALIAS_MUTATION_DEFAULT is False
    assert identity.immutable_tag(candidate()) not in {"latest", "stable", "amd64", "arm64"}


def test_metadata_has_required_scientific_build_content_and_gate_fields():
    metadata = identity.identity_metadata(candidate())
    required = identity.REQUIRED_CANDIDATE_FIELDS | {"build_identity_sha256"}
    assert required <= set(metadata)
    assert identity.scientific_identity(candidate()) == {
        "tool_id": "bedtools",
        "scientific_version": "2.30.0",
    }
    assert identity.content_identity(candidate()) == "3" * 64


def test_oci_metadata_transport_has_dedicated_layer_type_and_identity_annotations():
    annotations = identity.identity_annotations(candidate())
    assert identity.IDENTITY_MEDIA_TYPE.endswith("+json")
    assert identity.SIF_MEDIA_TYPE.startswith("application/vnd.sylabs.sif")
    assert annotations["org.omnibioai.sif.build.sha256"] == identity.build_identity_sha256(candidate())
    assert annotations["org.omnibioai.sif.content.sha256"] == "3" * 64


def test_dry_run_cli_reports_zero_writes(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    state_path = tmp_path / "state.json"
    candidate_path.write_text(json.dumps(candidate()))
    state_path.write_text(json.dumps({"artifacts": []}))
    completed = subprocess.run(
        [sys.executable, str(CLI), "--candidate", str(candidate_path),
         "--registry-state", str(state_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "DECISION=PUBLISH_NEW" in completed.stdout
    assert "REGISTRY_WRITE_PERFORMED=NO" in completed.stdout


def test_module_contains_no_registry_mutation_commands():
    source = CLI.read_text() + LEGACY_PUBLISHER.read_text()
    prohibited = ("oras push", "docker push", "buildx --push", "apptainer push",
                  "singularity push", "gh api --method", "DELETE ")
    assert all(command not in source for command in prohibited)


def test_historical_publisher_is_a_write_free_dry_run_wrapper(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    state_path = tmp_path / "state.json"
    candidate_path.write_text(json.dumps(candidate()))
    state_path.write_text(json.dumps({"artifacts": []}))
    completed = subprocess.run(
        [str(LEGACY_PUBLISHER), "--candidate", str(candidate_path),
         "--registry-state", str(state_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "DECISION=PUBLISH_NEW" in completed.stdout
    assert "REGISTRY_WRITE_PERFORMED=NO" in completed.stdout


def test_canary_workflow_is_exactly_bedtools_on_two_native_architectures():
    workflow = yaml.safe_load(CANARY_WORKFLOW.read_text())
    jobs = workflow["jobs"]
    job = jobs["build-verify-publish-retrieve"]
    matrix = job["strategy"]["matrix"]["include"]
    assert matrix == [
        {"tool": "bedtools", "arch": "amd64", "platform": "linux/amd64", "runner": "ubuntu-24.04"},
        {"tool": "bedtools", "arch": "arm64", "platform": "linux/arm64", "runner": "ubuntu-24.04-arm"},
    ]
    assert job["strategy"]["fail-fast"] is False
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert "inputs" not in workflow[True]


def test_canary_workflow_has_no_dynamic_tool_or_moving_alias_publication():
    source = CANARY_WORKFLOW.read_text()
    assert "PILOT_TOOL: bedtools" in source
    assert "Dockerfile.bedtools" in source
    assert "matrix.tool" not in source
    assert "scripts.bedtools_canary publish" in source
    for alias in (":arm64", ":amd64", ":latest", ":stable", ":2.30.0"):
        assert alias not in source


def test_canary_publisher_is_fixed_to_authoritative_bedtools_package():
    assert bedtools_canary.REPOSITORY == "ghcr.io/omnibioai/omnibioai-sif/bedtools"
    assert bedtools_canary.TOOL == "bedtools"
    assert bedtools_canary.ALLOWED_ARCHITECTURES == ("amd64", "arm64")
    assert bedtools_canary.MOVING_ALIASES == {"arm64", "amd64", "latest", "stable", "2.30.0"}


@pytest.mark.parametrize(
    "decision",
    [identity.Decision.BLOCK_TAG_COLLISION, identity.Decision.BLOCK_IDENTITY_MISMATCH,
     identity.Decision.BLOCK_METADATA_MISSING, identity.Decision.BLOCK_LEGACY_CONFLICT,
     identity.Decision.ERROR],
)
def test_canary_publish_never_writes_for_blocking_decisions(tmp_path, monkeypatch, decision):
    payload = b"validated-sif"
    value = candidate(sif_sha256=__import__("hashlib").sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    sif = tmp_path / "bedtools.sif"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif.write_bytes(payload)
    result = identity.Evaluation(decision, "blocked", identity.immutable_tag(value),
                                 identity.build_identity_sha256(value))
    monkeypatch.setattr(bedtools_canary, "evaluate_live", lambda unused: ({}, result))
    called = []
    monkeypatch.setattr(bedtools_canary, "run", lambda argv, **kwargs: called.append(argv))
    with pytest.raises(bedtools_canary.CanaryError, match="publication blocked"):
        bedtools_canary.publish(candidate_path, sif, tmp_path / "result.json")
    assert called == []


def test_publish_new_has_one_fixed_immutable_oras_write(tmp_path, monkeypatch):
    payload = b"validated-sif"
    value = candidate(sif_sha256=__import__("hashlib").sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    sif = tmp_path / "bedtools.sif"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif.write_bytes(payload)
    result = identity.Evaluation(identity.Decision.PUBLISH_NEW, "new", identity.immutable_tag(value),
                                 identity.build_identity_sha256(value))
    monkeypatch.setattr(bedtools_canary, "evaluate_live", lambda unused: ({}, result))
    commands = []
    manifest_bytes = b'{"schemaVersion":2}'

    def fake_run(argv, **kwargs):
        commands.append(list(argv))
        manifest_path = Path(argv[argv.index("--export-manifest") + 1])
        manifest_path.write_bytes(manifest_bytes)
        return subprocess.CompletedProcess(argv, 0, "", "")

    digest = "sha256:" + __import__("hashlib").sha256(manifest_bytes).hexdigest()
    monkeypatch.setattr(bedtools_canary, "run", fake_run)
    monkeypatch.setattr(bedtools_canary, "inspect_reference", lambda repository, tag: {
        "manifest_digest": digest, "tag": tag,
    })
    published = bedtools_canary.publish(candidate_path, sif, tmp_path / "result.json")
    assert published["write_performed"] is True
    assert len(commands) == 1
    command = commands[0]
    assert command[:2] == ["oras", "push"]
    assert f"{bedtools_canary.REPOSITORY}:{identity.immutable_tag(value)}" in command
    assert all(alias not in command for alias in bedtools_canary.MOVING_ALIASES)


def test_canary_build_dockerfile_binds_the_resolved_base_digest(tmp_path):
    source = tmp_path / "Dockerfile"
    rendered = tmp_path / "Dockerfile.rendered"
    source.write_text("FROM python:3.11-slim-bookworm\nRUN true\n")
    base = "docker.io/library/python:3.11-slim-bookworm@sha256:" + "a" * 64
    result = pilot_runner.architecture_bound_dockerfile(source, rendered, base)
    assert result == str(rendered)
    assert rendered.read_text() == f"FROM {base}\nRUN true\n"


def test_canary_build_rejects_a_base_identity_for_another_image(tmp_path):
    source = tmp_path / "Dockerfile"
    source.write_text("FROM python:3.11-slim-bookworm\n")
    base = "docker.io/library/debian@sha256:" + "a" * 64
    with pytest.raises(ValueError, match="does not match"):
        pilot_runner.architecture_bound_dockerfile(source, tmp_path / "rendered", base)


def test_normal_validation_path_keeps_the_authoritative_dockerfile(tmp_path):
    source = tmp_path / "Dockerfile"
    source.write_text("FROM scratch\n")
    assert pilot_runner.architecture_bound_dockerfile(source, tmp_path / "unused", "") == str(source)
