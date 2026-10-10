"""Additional fail-closed coverage for scripts/sif_release.py.

Extends the conventions established in tests/test_sif_release_factory.py:
mock subprocess/network/filesystem boundaries, exercise real error paths, and
reuse the manifest-bound candidate/provenance/plan builders defined there so
constructed fixtures stay consistent with the real identity schema.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import sif_identity as identity
from scripts import sif_release as release
from scripts.pilot_manifest import get_tool

from test_sif_release_factory import (
    ONE,
    RUNTIME_VERSION_EVIDENCE,
    TWO,
    VERSION_OUTPUTS,
    ZERO,
    artifact,
    candidate,
    minimal_release_provenance,
    pilot9_plan_records,
    plan_record,
    planning_provenance,
    runtime_release_provenance,
)


# ---------------------------------------------------------------------------
# resolve_scientific_version / canonicalize_version_evidence edge cases
# ---------------------------------------------------------------------------


def test_resolve_scientific_version_requires_pattern_text():
    entry = {"scientific_version_pattern": ""}
    with pytest.raises(release.ReleaseError, match="pattern is missing"):
        release.resolve_scientific_version(entry, "whatever 1.0")


def test_resolve_scientific_version_rejects_malformed_pattern():
    entry = {"scientific_version_pattern": "(unclosed"}
    with pytest.raises(release.ReleaseError, match="malformed"):
        release.resolve_scientific_version(entry, "whatever 1.0")


def test_resolve_scientific_version_requires_named_version_group():
    entry = {"scientific_version_pattern": r"(\d+\.\d+)"}
    with pytest.raises(release.ReleaseError, match="named version group"):
        release.resolve_scientific_version(entry, "tool 1.0")


def test_resolve_scientific_version_rejects_non_canonical_version():
    entry = {"scientific_version_pattern": r"(?P<version>[^\s]+)"}
    with pytest.raises(release.ReleaseError, match="not canonical"):
        release.resolve_scientific_version(entry, "a/b")


def test_canonicalize_version_evidence_whitespace_only_rejected_before_empty_canonical():
    # Any input that is empty after Python's default strip() is rejected at
    # the "missing or empty" gate before the canonicalization step could ever
    # produce an empty canonical string; the later empty-canonical guard is
    # therefore defensive/unreachable through this public entry point and is
    # intentionally left uncovered (see final report).
    with pytest.raises(release.ReleaseError, match="missing or empty"):
        release.canonicalize_version_evidence("   \t\r\n  ")


# ---------------------------------------------------------------------------
# _sanitize / _classify_oras_failure / run()
# ---------------------------------------------------------------------------


def test_sanitize_fails_closed_when_redaction_itself_errors(monkeypatch):
    class ExplodingPattern:
        def sub(self, replacement, text):
            raise RuntimeError("boom")

    monkeypatch.setattr(release, "_REDACTION_PATTERNS", ((ExplodingPattern(), "x"),))
    assert release._sanitize("hello") == "[OUTPUT UNAVAILABLE: sanitization failed closed]"


def test_classify_oras_failure_defaults_to_unknown():
    assert release._classify_oras_failure("nothing interesting", "still nothing") == (
        "ORAS_UNKNOWN_FAILURE"
    )


def test_run_marks_binary_output_uncaptured_when_text_is_false(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 3, b"\x00\x01", b"\x02\x03"),
    )
    with pytest.raises(release.OrasInvocationFailed) as excinfo:
        release.run(["oras", "blob", "fetch"], text=False)
    assert excinfo.value.stdout == "<binary output not captured>"
    assert excinfo.value.stderr == "<binary output not captured>"
    assert excinfo.value.returncode == 3


def test_run_returns_completed_process_on_success(monkeypatch):
    sentinel = subprocess.CompletedProcess(["x"], 0, "ok", "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: sentinel)
    assert release.run(["oras", "version"]) is sentinel


# ---------------------------------------------------------------------------
# require_contained_staging_file
# ---------------------------------------------------------------------------


def test_require_contained_staging_file_rejects_nonexistent_path(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    with pytest.raises(release.ReleaseError, match="does not resolve to an existing file"):
        release.require_contained_staging_file(root / "missing.sif", root)


def test_require_contained_staging_file_rejects_directory(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    directory = root / "subdir"
    directory.mkdir()
    with pytest.raises(release.ReleaseError, match="not a regular file"):
        release.require_contained_staging_file(directory, root)


# ---------------------------------------------------------------------------
# validate_release_provenance additional mutation branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda record: record.__setitem__("tool", "not-the-tool"), "tool/Dockerfile"),
        (lambda record: record.__setitem__("target_architecture", "riscv64"), "architecture is not supported"),
        (lambda record: record.__setitem__("rendered_dockerfile_sha256", "not-hex"), "rendered Dockerfile identity"),
        (lambda record: record.__setitem__("package_identity", "wrong-package=9.9"), "package identity"),
        (lambda record: record.__setitem__("sbom_status", None), "SBOM status"),
    ],
)
def test_validate_release_provenance_rejects_additional_malformed_fields(monkeypatch, mutation, match):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    record = minimal_release_provenance()
    mutation(record)
    with pytest.raises(release.ReleaseError, match=match):
        release.validate_release_provenance(record, release.release_spec("fastqc"))


# ---------------------------------------------------------------------------
# prepare_candidate_for_spec
# ---------------------------------------------------------------------------


def test_prepare_candidate_rejects_sif_content_mismatch(tmp_path):
    tool, arch = "samtools", "amd64"
    evidence = RUNTIME_VERSION_EVIDENCE[(tool, arch)]
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"validated-bytes")
    provenance = runtime_release_provenance(tool, arch, evidence, release.sha256_file(sif))
    # Tamper with the recorded SIF digest so it disagrees with the real file.
    provenance["sif_sha256"] = "9" * 64
    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text(json.dumps(provenance))
    with pytest.raises(release.ReleaseError, match="candidate SIF differs"):
        release.prepare_candidate(tool, provenance_path, sif, tmp_path / "candidate.json")


# ---------------------------------------------------------------------------
# candidate_record_for_spec additional mutation branches
# ---------------------------------------------------------------------------


def test_candidate_record_rejects_field_mismatch_against_provenance(monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    value = candidate(tool="fastqc", arch="amd64", version="0.12.1")
    provenance = planning_provenance("fastqc", "amd64", "0.12.1")
    provenance["target_architecture"] = "arm64"
    with pytest.raises(release.ReleaseError, match="differs from validated provenance"):
        release.candidate_record_for_spec(release.release_spec("fastqc"), value, provenance)


def test_candidate_record_rejects_malformed_oci_archive_sha256(monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    value = candidate(tool="fastqc", arch="amd64", version="0.12.1")
    provenance = planning_provenance("fastqc", "amd64", "0.12.1")
    provenance["oci_archive_sha256"] = "not-hex"
    with pytest.raises(release.ReleaseError, match="valid OCI archive SHA256"):
        release.candidate_record_for_spec(release.release_spec("fastqc"), value, provenance)


def test_candidate_record_rejects_non_passing_sif_version_status(monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    value = candidate(tool="fastqc", arch="amd64", version="0.12.1")
    provenance = planning_provenance("fastqc", "amd64", "0.12.1")
    provenance["sif_version"]["status"] = "FAIL"
    with pytest.raises(release.ReleaseError, match="passing SIF version evidence"):
        release.candidate_record_for_spec(release.release_spec("fastqc"), value, provenance)


def test_candidate_record_rejects_wrong_native_runner_arch(monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    value = candidate(tool="fastqc", arch="amd64", version="0.12.1")
    provenance = planning_provenance("fastqc", "amd64", "0.12.1")
    provenance["runner"]["arch"] = "ARM64"
    with pytest.raises(release.ReleaseError, match="native runner architecture"):
        release.candidate_record_for_spec(release.release_spec("fastqc"), value, provenance)
    value2 = candidate(tool="fastqc", arch="amd64", version="0.12.1")
    provenance2 = planning_provenance("fastqc", "amd64", "0.12.1")
    provenance2["execution_mode"] = "EMULATED"
    with pytest.raises(release.ReleaseError, match="native runner architecture"):
        release.candidate_record_for_spec(release.release_spec("fastqc"), value2, provenance2)


# ---------------------------------------------------------------------------
# materialize_candidate_record
# ---------------------------------------------------------------------------


def test_materialize_candidate_record_writes_canonical_output(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    value = candidate(tool="fastqc", arch="amd64", version="0.12.1")
    provenance = planning_provenance("fastqc", "amd64", "0.12.1")
    candidate_path = tmp_path / "candidate.json"
    provenance_path = tmp_path / "provenance.json"
    candidate_path.write_text(json.dumps(value))
    provenance_path.write_text(json.dumps(provenance))
    output = tmp_path / "record.json"
    record = release.materialize_candidate_record("fastqc", candidate_path, provenance_path, output)
    expected = release.candidate_record_for_spec(release.release_spec("fastqc"), value, provenance)
    assert record == expected
    assert json.loads(output.read_text()) == expected


# ---------------------------------------------------------------------------
# build_release_plan argument validation
# ---------------------------------------------------------------------------


def test_build_release_plan_rejects_non_boolean_publish():
    with pytest.raises(release.ReleaseError, match="publish intent must be boolean"):
        release.build_release_plan([], cohort="pilot9", publish="true", source_commit="a" * 40)


def test_build_release_plan_rejects_malformed_source_commit():
    with pytest.raises(release.ReleaseError, match="source commit is malformed"):
        release.build_release_plan([], cohort="pilot9", publish=False, source_commit="not-a-commit")


def test_build_release_plan_rejects_non_object_record():
    with pytest.raises(release.ReleaseError, match="not an object"):
        release.build_release_plan(["not-a-mapping"], cohort="pilot9", publish=False, source_commit="a" * 40)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda records: records[0].__setitem__("candidate_record_schema_version", "2"), "candidate record schema is unsupported"),
        (lambda records: records[0].__setitem__("schema_version", "2"), "candidate identity schema is unsupported"),
        (lambda records: records[0].__setitem__("source_commit", "b" * 40), "source commit differs"),
        (lambda records: records[0].__setitem__("dockerfile_path", "dockerfiles/Dockerfile.other"), "scientific contract differs"),
        (lambda records: records[0].__setitem__("dockerfile_sha256", "not-hex"), "is malformed"),
        (lambda records: records[0].__setitem__("build_identity", "a" * 64), "identity/tag differs"),
        (lambda records: records[0]["identity_inputs"].pop("source_commit"), "identity is malformed"),
        (lambda records: records[0].__setitem__("provenance_status", "FAIL"), "embedded canonical identity"),
        (lambda records: records[0].__setitem__("provenance_reference", "bad-reference"), "mandatory provenance"),
        (lambda records: records[0].__setitem__("version_result", {"status": "PASS", "scientific_version": "9.9"}), "version result"),
        (lambda records: records[0].__setitem__("smoke_result", {"status": "FAIL"}), "smoke result"),
        (lambda records: records[0].__setitem__("native_runner_arch", "WRONG"), "runner/SBOM"),
        (lambda records: records[0].__setitem__("sbom_status", "NOPE"), "runner/SBOM"),
    ],
)
def test_release_plan_rejects_additional_malformed_candidate_fields(mutation, match):
    records = pilot9_plan_records()
    mutation(records)
    with pytest.raises(release.ReleaseError, match=match):
        release.build_release_plan(records, cohort="pilot9", publish=False, source_commit="a" * 40)


# ---------------------------------------------------------------------------
# validate_release_plan additional branches
# ---------------------------------------------------------------------------


def test_validate_release_plan_rejects_non_object_plan():
    with pytest.raises(release.ReleaseError, match="must be an object"):
        release.validate_release_plan(["not", "a", "mapping"])


def test_validate_release_plan_rejects_unsupported_schema_version():
    plan = release.build_release_plan(
        pilot9_plan_records(), cohort="pilot9", publish=True, source_commit="a" * 40
    )
    mutated = copy.deepcopy(plan)
    mutated["release_plan_schema_version"] = "2"
    core = {key: value for key, value in mutated.items() if key != "release_plan_sha256"}
    mutated["release_plan_sha256"] = hashlib.sha256(release.canonical_json_bytes(core)).hexdigest()
    with pytest.raises(release.ReleaseError, match="schema is unsupported"):
        release.validate_release_plan(mutated)


def test_validate_release_plan_rejects_non_canonical_content():
    plan = release.build_release_plan(
        pilot9_plan_records(), cohort="pilot9", publish=True, source_commit="a" * 40
    )
    shuffled = copy.deepcopy(plan)
    shuffled["candidates"] = list(reversed(shuffled["candidates"]))
    core = {key: value for key, value in shuffled.items() if key != "release_plan_sha256"}
    shuffled["release_plan_sha256"] = hashlib.sha256(release.canonical_json_bytes(core)).hexdigest()
    with pytest.raises(release.ReleaseError, match="not canonical"):
        release.validate_release_plan(shuffled)


# Note: verify_candidate_in_plan's "package authorization is missing from
# release plan" branch (sif_release.py:579) could not be reached through the
# public API. By the time `matches == [record]` succeeds (the guard right
# before it), `record["package"]` is guaranteed to already be a key of
# `package_authorized_refs` because that mapping is built directly from the
# same validated candidate list that produced the match, and the record's
# own immutable_tag was appended to it. Spoofing spec.repository instead
# changes record["package"] itself (since candidate_record_for_spec sets
# "package": spec.repository), which fails the preceding full-equality check
# first. This line is left deliberately uncovered as defensive/dead code.


# ---------------------------------------------------------------------------
# aggregate_release_plan success path
# ---------------------------------------------------------------------------


def test_aggregate_release_plan_builds_and_writes_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    records = [plan_record("multiqc", arch) for arch in ("amd64", "arm64")]
    for index, record in enumerate(records):
        directory = tmp_path / f"job-{index}"
        directory.mkdir()
        (directory / "candidate-record.json").write_text(json.dumps(record))
    output = tmp_path / "plan.json"
    plan = release.aggregate_release_plan(
        tmp_path, output, cohort="single", publish=True, source_commit="a" * 40, tool="multiqc",
    )
    assert plan["candidate_count"] == 2
    assert json.loads(output.read_text()) == plan


# ---------------------------------------------------------------------------
# inspect_reference
# ---------------------------------------------------------------------------


def test_inspect_reference_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "manifest_unknown: not present"),
    )
    assert release.inspect_reference("ghcr.io/example/tool", "sometag") is None


def test_inspect_reference_raises_on_other_failure(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "weird registry error"),
    )
    with pytest.raises(release.ReleaseError, match="registry inspection failed"):
        release.inspect_reference("ghcr.io/example/tool", "sometag")


def test_inspect_reference_rejects_malformed_manifest_json(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "not-json", ""),
    )
    with pytest.raises(release.ReleaseError, match="manifest/descriptor metadata is malformed"):
        release.inspect_reference("ghcr.io/example/tool", "sometag")


def test_inspect_reference_success_extracts_identity_and_sif_layers(monkeypatch):
    manifest = {
        "layers": [
            {"mediaType": identity.IDENTITY_MEDIA_TYPE, "digest": "sha256:" + "e" * 64},
            {"mediaType": identity.SIF_MEDIA_TYPE, "digest": "sha256:" + "f" * 64},
        ]
    }
    identity_blob = {"tool_id": "fastqc"}
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(manifest), ""),
    )

    def fake_run_fn(argv, **kwargs):
        if "--descriptor" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"digest": "sha256:" + "d" * 64}), "")
        return subprocess.CompletedProcess(argv, 0, json.dumps(identity_blob), "")

    result = release.inspect_reference("ghcr.io/example/tool", "sometag", run_fn=fake_run_fn)
    assert result["manifest_digest"] == "sha256:" + "d" * 64
    assert result["identity_metadata"] == identity_blob
    assert result["identity_metadata_digest"] == "sha256:" + "e" * 64
    assert result["sif_sha256"] == "f" * 64
    assert result["sif_layer_digest"] == "sha256:" + "f" * 64


def test_inspect_reference_rejects_malformed_identity_blob(monkeypatch):
    manifest = {"layers": [{"mediaType": identity.IDENTITY_MEDIA_TYPE, "digest": "sha256:" + "e" * 64}]}
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(manifest), ""),
    )

    def fake_run_fn(argv, **kwargs):
        if "--descriptor" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"digest": "sha256:" + "d" * 64}), "")
        return subprocess.CompletedProcess(argv, 0, "not-json", "")

    with pytest.raises(release.ReleaseError, match="identity metadata is malformed"):
        release.inspect_reference("ghcr.io/example/tool", "sometag", run_fn=fake_run_fn)


# ---------------------------------------------------------------------------
# registry_snapshot
# ---------------------------------------------------------------------------


def test_registry_snapshot_treats_not_found_as_empty_tag_list(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "name_unknown"),
    )
    spec = release.release_spec("fastqc")
    result = release.registry_snapshot(spec)
    assert result == {"repository": spec.repository, "tags": [], "artifacts": []}


def test_registry_snapshot_raises_on_other_tag_listing_failure(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "server exploded"),
    )
    with pytest.raises(release.ReleaseError, match="registry tag listing failed"):
        release.registry_snapshot(release.release_spec("fastqc"))


def test_registry_snapshot_includes_candidate_tag_and_filters_missing_artifacts():
    spec = release.release_spec("fastqc")
    value = candidate(tool="fastqc")
    candidate_tag = identity.immutable_tag(value)

    import scripts.sif_release as release_module

    def fake_subprocess_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "legacy\n\n", "")

    def fake_inspect_fn(repository, tag):
        if tag == "legacy":
            return {"tag": "legacy", "manifest_digest": "sha256:" + "a" * 64}
        if tag == candidate_tag:
            return None
        raise AssertionError(f"unexpected tag inspected: {tag}")

    original_run = subprocess.run
    subprocess.run = fake_subprocess_run
    try:
        result = release.registry_snapshot(spec, value, inspect_fn=fake_inspect_fn)
    finally:
        subprocess.run = original_run
    assert result["tags"] == ["legacy"]
    assert result["artifacts"] == [{"tag": "legacy", "manifest_digest": "sha256:" + "a" * 64}]


# ---------------------------------------------------------------------------
# evaluate_live_for_spec / capture_baseline
# ---------------------------------------------------------------------------


def test_evaluate_live_for_spec_rejects_tool_mismatch():
    spec = release.release_spec("samtools")
    value = candidate(tool="fastqc")
    with pytest.raises(release.ReleaseError, match="does not match exact package specification"):
        release.evaluate_live_for_spec(spec, value)


def test_evaluate_live_for_spec_evaluates_against_registry_snapshot(monkeypatch):
    spec = release.release_spec("fastqc")
    value = candidate(tool="fastqc")
    state = {"repository": spec.repository, "tags": [], "artifacts": []}
    monkeypatch.setattr(release, "registry_snapshot", lambda spec_arg, candidate_arg=None, **kwargs: state)
    returned_state, result = release.evaluate_live_for_spec(spec, value)
    assert returned_state is state
    assert result.decision is identity.Decision.PUBLISH_NEW


def test_capture_baseline_writes_registry_snapshot(tmp_path, monkeypatch):
    spec = release.release_spec("fastqc")
    state = {"repository": spec.repository, "tags": ["t"], "artifacts": []}
    monkeypatch.setattr(release, "registry_snapshot", lambda spec_arg, **kwargs: state)
    output = tmp_path / "baseline.json"
    result = release.capture_baseline("fastqc", output)
    assert result == state
    assert json.loads(output.read_text()) == state


# ---------------------------------------------------------------------------
# release_candidate_for_spec: containment / repository / plan-combination checks
# ---------------------------------------------------------------------------


def test_release_candidate_rejects_tool_architecture_escape(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"data")
    with pytest.raises(release.ReleaseError, match="tool/architecture containment"):
        release.release_candidate_for_spec(
            release.release_spec("samtools"), candidate_path, sif, tmp_path / "out.json",
        )


def test_release_candidate_rejects_repository_differing_from_manifest(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"data")
    spoofed_spec = replace(release.release_spec("fastqc"), repository="ghcr.io/example/spoofed")
    with pytest.raises(release.ReleaseError, match="repository differs from the canonical manifest mapping"):
        release.release_candidate_for_spec(spoofed_spec, candidate_path, sif, tmp_path / "out.json")


def test_release_candidate_requires_plan_and_provenance_together(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"data")
    with pytest.raises(release.ReleaseError, match="must be supplied together"):
        release.release_candidate_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
            release_plan={"anything": True},
        )


def test_release_candidate_rejects_immutable_tag_fn_disagreeing_with_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    value = candidate(tool="fastqc", arch="amd64", version="0.12.1")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"validated")
    provenance = planning_provenance("fastqc", "amd64", "0.12.1")
    records = [plan_record("fastqc", arch, "0.12.1") for arch in ("amd64", "arm64")]
    plan = release.build_release_plan(
        records, cohort="single", tool="fastqc", publish=False, source_commit="a" * 40
    )
    monkeypatch.setattr(release, "sha256_file", lambda unused: value["sif_sha256"])
    with pytest.raises(release.ReleaseError, match="computed immutable tag differs from validated release plan"):
        release.release_candidate_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
            publish_enabled=False,
            release_plan=plan, provenance=provenance,
            expected_plan_sha256=plan["release_plan_sha256"],
            immutable_tag_fn=lambda metadata: "sif-v1-totally-different-tag-000000000000",
        )


def test_release_candidate_refuses_moving_or_non_immutable_tag(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"data")
    with pytest.raises(release.ReleaseError, match="refusing moving, version-only, or non-immutable tag"):
        release.release_candidate_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
            immutable_tag_fn=lambda metadata: "latest",
        )


def test_release_candidate_rejects_sif_content_changed_after_identity_creation(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"mutated-after-identity-was-created")
    with pytest.raises(release.ReleaseError, match="SIF content changed after identity creation"):
        release.release_candidate_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
        )


# ---------------------------------------------------------------------------
# release_candidate_for_spec: publish_enabled=True decision branches
# ---------------------------------------------------------------------------


def test_release_candidate_skip_exact_match_live_publish_path(tmp_path):
    payload = b"validated"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    metadata = identity.identity_metadata(value)
    tag = identity.immutable_tag(metadata)
    existing = {"tag": tag, "manifest_digest": "sha256:" + "c" * 64}
    evaluation = identity.Evaluation(
        identity.Decision.SKIP_EXACT_MATCH, "already published", tag,
        identity.build_identity_sha256(metadata), existing_reference=tag,
    )
    result = release.release_candidate_for_spec(
        release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
        publish_enabled=True,
        evaluate_live_fn=lambda unused: ({"artifacts": []}, evaluation),
        inspect_reference_fn=lambda repository, requested_tag: existing,
        run_fn=lambda argv, **kwargs: pytest.fail("push must not run on SKIP_EXACT_MATCH"),
    )
    assert result == {
        "dry_run": False, "write_performed": False, "reference": tag, "registry": existing,
    }
    assert json.loads((tmp_path / "out.json").read_text()) == result


def test_release_candidate_skip_exact_match_disappeared_reference_fails(tmp_path):
    payload = b"validated"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    metadata = identity.identity_metadata(value)
    tag = identity.immutable_tag(metadata)
    evaluation = identity.Evaluation(
        identity.Decision.SKIP_EXACT_MATCH, "already published", tag,
        identity.build_identity_sha256(metadata), existing_reference=tag,
    )
    with pytest.raises(release.ReleaseError, match="exact-match reference disappeared"):
        release.release_candidate_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
            publish_enabled=True,
            evaluate_live_fn=lambda unused: ({"artifacts": []}, evaluation),
            inspect_reference_fn=lambda repository, requested_tag: None,
        )


def test_release_candidate_blocked_decision_fails_before_any_write(tmp_path):
    payload = b"validated"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    metadata = identity.identity_metadata(value)
    tag = identity.immutable_tag(metadata)
    evaluation = identity.Evaluation(
        identity.Decision.BLOCK_TAG_COLLISION, "collision", tag,
        identity.build_identity_sha256(metadata),
    )
    with pytest.raises(release.ReleaseError, match="publication blocked"):
        release.release_candidate_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
            publish_enabled=True,
            evaluate_live_fn=lambda unused: ({"artifacts": []}, evaluation),
            run_fn=lambda argv, **kwargs: pytest.fail("push must not run when blocked"),
        )


def test_release_candidate_publish_new_full_push_flow(tmp_path):
    payload = b"validated"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    metadata = identity.identity_metadata(value)
    tag = identity.immutable_tag(metadata)
    evaluation = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new", tag, identity.build_identity_sha256(metadata),
    )
    manifest_bytes_holder: dict[str, bytes] = {}

    def fake_run_fn(argv, **kwargs):
        assert argv[0] == "oras" and argv[1] == "push"
        manifest_path = Path(argv[argv.index("--export-manifest") + 1])
        data = b"exported-manifest-bytes"
        manifest_path.write_bytes(data)
        manifest_bytes_holder["data"] = data
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_inspect_fn(repository, requested_tag):
        digest = "sha256:" + hashlib.sha256(manifest_bytes_holder["data"]).hexdigest()
        return {"tag": requested_tag, "manifest_digest": digest}

    result = release.release_candidate_for_spec(
        release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
        publish_enabled=True,
        evaluate_live_fn=lambda unused: ({"artifacts": []}, evaluation),
        inspect_reference_fn=fake_inspect_fn,
        run_fn=fake_run_fn,
    )
    assert result["dry_run"] is False
    assert result["write_performed"] is True
    assert result["reference"] == f"{release.release_spec('fastqc').repository}:{tag}"
    assert json.loads((tmp_path / "out.json").read_text()) == result


def test_release_candidate_publish_new_rejects_unresolvable_published_manifest(tmp_path):
    payload = b"validated"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    metadata = identity.identity_metadata(value)
    tag = identity.immutable_tag(metadata)
    evaluation = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new", tag, identity.build_identity_sha256(metadata),
    )

    def fake_run_fn(argv, **kwargs):
        manifest_path = Path(argv[argv.index("--export-manifest") + 1])
        manifest_path.write_bytes(b"bytes")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(release.ReleaseError, match="cannot be independently resolved by digest"):
        release.release_candidate_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "out.json",
            publish_enabled=True,
            evaluate_live_fn=lambda unused: ({"artifacts": []}, evaluation),
            inspect_reference_fn=lambda repository, requested_tag: None,
            run_fn=fake_run_fn,
        )


# ---------------------------------------------------------------------------
# retrieve_and_verify_for_spec
# ---------------------------------------------------------------------------


def test_retrieve_rejects_tool_mismatch(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    with pytest.raises(release.ReleaseError, match="differs from release specification"):
        release.retrieve_and_verify_for_spec(
            release.release_spec("samtools"), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
        )


def test_retrieve_requires_plan_and_provenance_together(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    with pytest.raises(release.ReleaseError, match="must be supplied together"):
        release.retrieve_and_verify_for_spec(
            release.release_spec("fastqc"), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            release_plan={"anything": True},
        )


def test_retrieve_rejects_annotation_mismatch(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    metadata = identity.identity_metadata(value)
    remote = {
        "manifest": {"annotations": {**identity.identity_annotations(metadata), "org.opencontainers.image.version": "wrong"}},
        "identity_metadata": metadata,
        "sif_sha256": metadata["sif_sha256"],
    }
    with pytest.raises(release.ReleaseError, match="annotations differ"):
        release.retrieve_and_verify_for_spec(
            release.release_spec("fastqc"), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: remote,
        )


def test_retrieve_rejects_identity_metadata_mismatch(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    metadata = identity.identity_metadata(value)
    tampered = dict(metadata)
    tampered["scientific_version"] = metadata["scientific_version"]  # keep annotations consistent
    remote = {
        "manifest": {"annotations": identity.identity_annotations(metadata)},
        "identity_metadata": {**metadata, "operational_provenance": {"github_run_id": "different"}},
        "sif_sha256": metadata["sif_sha256"],
    }
    with pytest.raises(release.ReleaseError, match="retrieved identity JSON differs"):
        release.retrieve_and_verify_for_spec(
            release.release_spec("fastqc"), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: remote,
        )


def test_retrieve_rejects_missing_sif_layer_digest(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    metadata = identity.identity_metadata(value)
    remote = {
        "manifest": {"annotations": identity.identity_annotations(metadata)},
        "identity_metadata": metadata,
        "sif_sha256": metadata["sif_sha256"],
        # sif_layer_digest intentionally omitted
    }
    with pytest.raises(release.ReleaseError, match="SIF layer digest is missing"):
        release.retrieve_and_verify_for_spec(
            release.release_spec("fastqc"), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: remote,
        )


def test_retrieve_rejects_sif_bytes_differing_from_candidate(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    metadata = identity.identity_metadata(value)
    remote = {
        "manifest": {"annotations": identity.identity_annotations(metadata)},
        "identity_metadata": metadata,
        "sif_sha256": metadata["sif_sha256"],
        "sif_layer_digest": "sha256:" + "b" * 64,
    }

    def fake_run_fn(argv, **kwargs):
        destination = Path(argv[argv.index("--output") + 1])
        destination.write_bytes(b"wrong-bytes")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(release.ReleaseError, match="retrieved SIF bytes differ"):
        release.retrieve_and_verify_for_spec(
            release.release_spec("fastqc"), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: remote, run_fn=fake_run_fn,
        )


def test_retrieve_rejects_non_skip_exact_match_post_publication_decision(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    metadata = identity.identity_metadata(value)
    remote = {
        "manifest": {"annotations": identity.identity_annotations(metadata)},
        "identity_metadata": metadata,
        "sif_sha256": metadata["sif_sha256"],
        "sif_layer_digest": "sha256:" + "b" * 64,
    }

    def fake_run_fn(argv, **kwargs):
        destination = Path(argv[argv.index("--output") + 1])
        destination.write_bytes(b"correct-sif-bytes")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch_candidate_sha = hashlib.sha256(b"correct-sif-bytes").hexdigest()
    metadata = dict(metadata)
    metadata["sif_sha256"] = monkeypatch_candidate_sha
    remote["identity_metadata"] = metadata
    remote["sif_sha256"] = monkeypatch_candidate_sha
    remote["manifest"] = {"annotations": identity.identity_annotations(metadata)}
    candidate_path.write_text(json.dumps(metadata))

    not_skip = identity.Evaluation(identity.Decision.PUBLISH_NEW, "not idempotent", None, None)
    with pytest.raises(release.ReleaseError, match="post-publication idempotency failed"):
        release.retrieve_and_verify_for_spec(
            release.release_spec("fastqc"), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: remote, run_fn=fake_run_fn,
            evaluate_live_fn=lambda unused: ({"artifacts": []}, not_skip),
        )


def test_retrieve_and_verify_for_spec_full_success_with_baseline_and_legacy_tags(tmp_path):
    payload = b"candidate-sif-bytes"
    value = candidate(tool="fastqc", sif_sha256=hashlib.sha256(payload).hexdigest())
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    spec = release.release_spec("fastqc")
    tag = identity.immutable_tag(metadata)
    legacy_tag = "legacy-architecture-tag"
    legacy_sha = "7" * 64

    remote = {
        "manifest": {"annotations": identity.identity_annotations(metadata)},
        "identity_metadata": metadata,
        "sif_sha256": metadata["sif_sha256"],
        "sif_layer_digest": "sha256:" + "b" * 64,
        "manifest_digest": "sha256:" + "d" * 64,
        "identity_metadata_digest": "sha256:" + "e" * 64,
    }

    def fake_run_fn(argv, **kwargs):
        destination = Path(argv[argv.index("--output") + 1])
        destination.write_bytes(payload)
        return subprocess.CompletedProcess(argv, 0, "", "")

    state = {
        "repository": spec.repository,
        "artifacts": [
            {"tag": tag, "manifest_digest": "sha256:" + "1" * 64, "sif_sha256": metadata["sif_sha256"]},
            {"tag": legacy_tag, "manifest_digest": "sha256:" + "2" * 64, "sif_sha256": legacy_sha},
        ],
    }
    skip_decision = identity.Evaluation(
        identity.Decision.SKIP_EXACT_MATCH, "idempotent", tag, metadata["build_identity_sha256"],
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({
        "repository": spec.repository,
        "artifacts": [{"tag": legacy_tag, "manifest_digest": "sha256:" + "2" * 64}],
    }))
    output = tmp_path / "out.json"

    record = release.retrieve_and_verify_for_spec(
        spec, candidate_path, tmp_path / "out.sif", output,
        baseline_path=baseline_path,
        required_legacy_tags={legacy_tag: legacy_sha},
        inspect_reference_fn=lambda repository, requested_tag: remote,
        run_fn=fake_run_fn,
        evaluate_live_fn=lambda unused: (state, skip_decision),
    )
    assert record["reference"] == f"{spec.repository}:{tag}"
    assert record["sif_sha256"] == hashlib.sha256(payload).hexdigest()
    assert record["post_publication_decision"] == "SKIP_EXACT_MATCH"
    assert record["registry_state"] == state
    assert json.loads(output.read_text()) == record


def test_retrieve_rejects_mismatched_required_legacy_tag_content(tmp_path):
    payload = b"candidate-sif-bytes"
    value = candidate(tool="fastqc", sif_sha256=hashlib.sha256(payload).hexdigest())
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    spec = release.release_spec("fastqc")
    tag = identity.immutable_tag(metadata)
    legacy_tag = "legacy-architecture-tag"

    remote = {
        "manifest": {"annotations": identity.identity_annotations(metadata)},
        "identity_metadata": metadata,
        "sif_sha256": metadata["sif_sha256"],
        "sif_layer_digest": "sha256:" + "b" * 64,
        "manifest_digest": "sha256:" + "d" * 64,
        "identity_metadata_digest": "sha256:" + "e" * 64,
    }

    def fake_run_fn(argv, **kwargs):
        destination = Path(argv[argv.index("--output") + 1])
        destination.write_bytes(payload)
        return subprocess.CompletedProcess(argv, 0, "", "")

    state = {
        "repository": spec.repository,
        "artifacts": [
            {"tag": tag, "manifest_digest": "sha256:" + "1" * 64, "sif_sha256": metadata["sif_sha256"]},
            {"tag": legacy_tag, "manifest_digest": "sha256:" + "2" * 64, "sif_sha256": "8" * 64},
        ],
    }
    skip_decision = identity.Evaluation(
        identity.Decision.SKIP_EXACT_MATCH, "idempotent", tag, metadata["build_identity_sha256"],
    )
    with pytest.raises(release.ReleaseError, match="legacy .* content identity changed"):
        release.retrieve_and_verify_for_spec(
            spec, candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            required_legacy_tags={legacy_tag: "9" * 64},
            inspect_reference_fn=lambda repository, requested_tag: remote,
            run_fn=fake_run_fn,
            evaluate_live_fn=lambda unused: (state, skip_decision),
        )


# ---------------------------------------------------------------------------
# verify_retrieved_runtime_for_spec
# ---------------------------------------------------------------------------


def test_verify_retrieved_runtime_rejects_tool_or_sif_mismatch(tmp_path):
    value = candidate(tool="fastqc")
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"different-bytes-than-identity")
    with pytest.raises(release.ReleaseError, match="differs before runtime verification"):
        release.verify_retrieved_runtime_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "ws", tmp_path / "out.json",
        )


def test_verify_retrieved_runtime_rejects_malformed_inspect_json(tmp_path, monkeypatch):
    payload = b"sif-bytes"
    value = candidate(tool="fastqc", sif_sha256=hashlib.sha256(payload).hexdigest())
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "not-json", "")

    with pytest.raises(release.ReleaseError, match="inspect metadata is malformed"):
        release.verify_retrieved_runtime_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "ws", tmp_path / "out.json",
            run_fn=fake_run,
        )


def test_verify_retrieved_runtime_rejects_empty_inspect_metadata(tmp_path):
    payload = b"sif-bytes"
    value = candidate(tool="fastqc", sif_sha256=hashlib.sha256(payload).hexdigest())
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    with pytest.raises(release.ReleaseError, match="inspect metadata is empty"):
        release.verify_retrieved_runtime_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, tmp_path / "ws", tmp_path / "out.json",
            run_fn=fake_run,
        )


def test_verify_retrieved_runtime_full_success(tmp_path, monkeypatch):
    payload = b"sif-bytes"
    value = candidate(tool="fastqc", sif_sha256=hashlib.sha256(payload).hexdigest())
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    workspace = tmp_path / "ws"
    monkeypatch.setattr(release, "validate_probe_result", lambda *args: None)

    def fake_run(argv, **kwargs):
        if "inspect" in argv:
            return subprocess.CompletedProcess(argv, 0, '{"ok": true}', "")
        workspace.mkdir(parents=True, exist_ok=True)
        probe = {
            "architecture": {"status": "PASS", "target": "amd64", "uname": "x86_64"},
            "executable": {"status": "PASS"},
            "version": {"status": "PASS", "output": VERSION_OUTPUTS["fastqc"], "meaningful_output": VERSION_OUTPUTS["fastqc"]},
            "smoke": {"status": "PASS"},
        }
        (workspace / "retrieved-sif-probe.json").write_text(json.dumps(probe))
        return subprocess.CompletedProcess(argv, 0, "", "")

    output = tmp_path / "runtime.json"
    result = release.verify_retrieved_runtime_for_spec(
        release.release_spec("fastqc"), candidate_path, sif, workspace, output, run_fn=fake_run,
    )
    assert result["status"] == "PASS"
    assert result["scientific_version"] == metadata["scientific_version"]
    assert json.loads(output.read_text()) == result


# ---------------------------------------------------------------------------
# _bool
# ---------------------------------------------------------------------------


def test_bool_parses_true_and_false_and_rejects_other_values():
    assert release._bool("true") is True
    assert release._bool("false") is False
    with pytest.raises(argparse.ArgumentTypeError):
        release._bool("yes")


# ---------------------------------------------------------------------------
# main() CLI dispatch
# ---------------------------------------------------------------------------


def test_main_prepare_command_invokes_prepare_candidate(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_prepare_candidate(tool, provenance, sif, output):
        captured["args"] = (tool, provenance, sif, output)
        return identity.identity_metadata(candidate(tool=tool))

    monkeypatch.setattr(release, "prepare_candidate", fake_prepare_candidate)
    rc = release.main([
        "prepare", "--tool", "fastqc",
        "--provenance", str(tmp_path / "p.json"),
        "--sif", str(tmp_path / "s.sif"),
        "--output", str(tmp_path / "o.json"),
    ])
    assert rc == 0
    assert captured["args"][0] == "fastqc"
    out = capsys.readouterr().out
    assert "immutable_tag" in out


def test_main_record_command_invokes_materialize_candidate_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        release, "materialize_candidate_record",
        lambda tool, candidate_path, provenance_path, output: {"tool_id": tool},
    )
    rc = release.main([
        "record", "--tool", "fastqc",
        "--candidate", str(tmp_path / "c.json"),
        "--provenance", str(tmp_path / "p.json"),
        "--output", str(tmp_path / "o.json"),
    ])
    assert rc == 0
    assert "fastqc" in capsys.readouterr().out


def test_main_plan_command_writes_github_output_and_prints_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        release, "aggregate_release_plan",
        lambda candidates, output, *, cohort, publish, source_commit, tool: {
            "candidate_count": 2, "release_plan_sha256": "f" * 64,
        },
    )
    github_output = tmp_path / "gh.txt"
    rc = release.main([
        "plan", "--candidates", str(tmp_path / "cands"),
        "--cohort", "pilot9", "--publish", "true",
        "--source-commit", "a" * 40,
        "--output", str(tmp_path / "plan.json"),
        "--github-output", str(github_output),
    ])
    assert rc == 0
    assert github_output.read_text() == f"release_plan_sha256={'f' * 64}\n"
    out = capsys.readouterr().out
    assert "candidate_count" in out


def test_main_plan_command_without_github_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        release, "aggregate_release_plan",
        lambda candidates, output, *, cohort, publish, source_commit, tool: {
            "candidate_count": 0, "release_plan_sha256": "0" * 64,
        },
    )
    rc = release.main([
        "plan", "--candidates", str(tmp_path / "cands"),
        "--cohort", "single", "--publish", "false",
        "--source-commit", "a" * 40,
        "--output", str(tmp_path / "plan.json"),
    ])
    assert rc == 0


def test_main_baseline_command_invokes_capture_baseline(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        release, "capture_baseline",
        lambda tool, output: captured.setdefault("called", (tool, output)),
    )
    rc = release.main(["baseline", "--tool", "fastqc", "--output", str(tmp_path / "b.json")])
    assert rc == 0
    assert captured["called"][0] == "fastqc"


def test_main_release_command_invokes_release_candidate(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        release, "release_candidate",
        lambda *args, **kwargs: captured.setdefault("kwargs", kwargs) or captured.setdefault("args", args),
    )
    rc = release.main([
        "release", "--tool", "fastqc",
        "--candidate", str(tmp_path / "c.json"),
        "--sif", str(tmp_path / "s.sif"),
        "--output", str(tmp_path / "o.json"),
        "--publish", "false",
        "--plan", str(tmp_path / "plan.json"),
        "--plan-sha256", "a" * 64,
        "--provenance", str(tmp_path / "p.json"),
    ])
    assert rc == 0
    assert captured["kwargs"]["publish_enabled"] is False


def test_main_retrieve_command_invokes_retrieve_and_verify(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        release, "retrieve_and_verify",
        lambda *args, **kwargs: captured.setdefault("kwargs", kwargs),
    )
    rc = release.main([
        "retrieve", "--tool", "fastqc",
        "--candidate", str(tmp_path / "c.json"),
        "--destination", str(tmp_path / "d.sif"),
        "--output", str(tmp_path / "o.json"),
        "--baseline", str(tmp_path / "baseline.json"),
        "--plan", str(tmp_path / "plan.json"),
        "--plan-sha256", "a" * 64,
        "--provenance", str(tmp_path / "p.json"),
    ])
    assert rc == 0
    assert captured["kwargs"]["expected_plan_sha256"] == "a" * 64


def test_main_runtime_command_invokes_verify_retrieved_runtime(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        release, "verify_retrieved_runtime",
        lambda *args, **kwargs: captured.setdefault("args", args),
    )
    rc = release.main([
        "runtime", "--tool", "fastqc",
        "--candidate", str(tmp_path / "c.json"),
        "--sif", str(tmp_path / "s.sif"),
        "--workspace", str(tmp_path / "ws"),
        "--output", str(tmp_path / "o.json"),
    ])
    assert rc == 0
    assert captured["args"][0] == "fastqc"


def test_release_plan_rejects_build_inputs_differing_from_manifest_contract(monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    tool = "fastqc"
    version = release.resolve_scientific_version(get_tool(tool), VERSION_OUTPUTS[tool])
    tampered_candidate = candidate(
        tool=tool, version=version,
        build_inputs={
            "package_identity": f"{tool}={version}",
            "rendered_dockerfile_sha256": "4" * 64,
            # Self-consistent but non-compliant: differs from the manifest's
            # pinned tool_runtime_commit, exercising the dedicated contract
            # check rather than the earlier identity/content equality checks.
            "tool_runtime_commit": "f" * 40,
        },
    )
    provenance = planning_provenance(tool, "amd64", version)
    tampered_record = release.candidate_record_for_spec(
        release.release_spec(tool), tampered_candidate, provenance
    )
    good_arm64_record = plan_record(tool, "arm64", version)
    with pytest.raises(release.ReleaseError, match="build inputs differ"):
        release.build_release_plan(
            [tampered_record, good_arm64_record], cohort="single", tool=tool,
            publish=False, source_commit="a" * 40,
        )


# ---------------------------------------------------------------------------
# Thin top-level wrapper functions (release_candidate / retrieve_and_verify /
# verify_retrieved_runtime) that delegate to their `_for_spec` counterparts.
# ---------------------------------------------------------------------------


def test_release_candidate_wrapper_delegates_to_release_candidate_for_spec(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"data")
    with pytest.raises(release.ReleaseError, match="tool/architecture containment"):
        release.release_candidate("samtools", candidate_path, sif, tmp_path / "out.json")


def test_retrieve_and_verify_wrapper_delegates_to_retrieve_and_verify_for_spec(tmp_path):
    value = candidate(tool="fastqc")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    with pytest.raises(release.ReleaseError, match="differs from release specification"):
        release.retrieve_and_verify("samtools", candidate_path, tmp_path / "out.sif", tmp_path / "out.json")


def test_verify_retrieved_runtime_wrapper_delegates_to_verify_retrieved_runtime_for_spec(tmp_path):
    value = candidate(tool="fastqc")
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(b"different-bytes-than-identity")
    with pytest.raises(release.ReleaseError, match="differs before runtime verification"):
        release.verify_retrieved_runtime(
            "fastqc", candidate_path, sif, tmp_path / "ws", tmp_path / "out.json"
        )


def test_retrieve_with_release_plan_and_provenance_succeeds_before_registry_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    tool, arch = "multiqc", "amd64"
    version = release.resolve_scientific_version(get_tool(tool), VERSION_OUTPUTS[tool])
    records = [plan_record(tool, a) for a in ("amd64", "arm64")]
    plan = release.build_release_plan(
        records, cohort="single", tool=tool, publish=True, source_commit="a" * 40
    )
    value = candidate(
        tool=tool, arch=arch, version=version,
        version_evidence=release.canonicalize_version_evidence(VERSION_OUTPUTS[tool]),
    )
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    provenance = planning_provenance(tool, arch, version)
    # verify_candidate_in_plan succeeds (covering the combined release_plan +
    # provenance validation branch) before failing later at the registry
    # lookup, which is already covered by a dedicated test elsewhere.
    with pytest.raises(release.ReleaseError, match="absent"):
        release.retrieve_and_verify_for_spec(
            release.release_spec(tool), candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            release_plan=plan, provenance=provenance,
            expected_plan_sha256=plan["release_plan_sha256"],
            inspect_reference_fn=lambda repository, tag: None,
        )


def test_verify_legacy_preserved_rejects_repository_change():
    before = {"repository": "r1", "artifacts": []}
    after = {"repository": "r2", "artifacts": []}
    with pytest.raises(release.ReleaseError, match="repository changed during legacy comparison"):
        release.verify_legacy_preserved(before, after, "sif-v1-x")


def test_main_reports_release_errors_on_stderr_and_returns_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        release, "capture_baseline",
        lambda tool, output: (_ for _ in ()).throw(release.ReleaseError("boom")),
    )
    rc = release.main(["baseline", "--tool", "fastqc", "--output", str(tmp_path / "b.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "RELEASE_FAIL: boom" in err
