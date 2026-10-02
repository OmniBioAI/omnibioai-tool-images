"""Regression coverage for Bedtools ORAS staging-path containment.

Run 37029461338 proved (via the diagnostics repaired in the prior task)
that `oras push` 1.3.0 rejects the absolute staged-file paths this canary
has always supplied, with: "Error: absolute file path detected ... use
--disable-path-validation flag to skip this check". `--disable-path-validation`
is the documented, correct remedy for this intentional case (the files are
staged by this same process moments earlier), but it must not be applied
blindly: these tests guard the application-level containment check that
runs before the flag is ever used, using real resolved filesystem ancestry
rather than string-prefix comparison, so it still catches `..` traversal,
symlink escapes, and textually-similar sibling directories.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import bedtools_canary
from scripts import sif_identity as identity

_ZERO = "0" * 64
_ONE = "1" * 64
_TWO = "2" * 64


def candidate(**changes):
    # Mirrors tests/test_sif_identity.py::candidate -- duplicated rather than
    # imported since `tests/` is not a package (no __init__.py) here.
    value = {
        "schema_version": "1",
        "tool_id": "bedtools",
        "scientific_version": "2.30.0",
        "target_architecture": "amd64",
        "source_commit": "2622bbf99f9518015f5b8414b8e537f1e0416101",
        "dockerfile_path": "dockerfiles/Dockerfile.bedtools",
        "dockerfile_sha256": _ZERO,
        "base_image_identity": "python:3.11-slim-bookworm@sha256:" + _ONE,
        "oci_source_identity": "sha256:" + _TWO,
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


# 1: valid staging file accepted
def test_valid_staging_file_is_accepted(tmp_path: Path):
    root = tmp_path / "staging"
    root.mkdir()
    target = root / "file.sif"
    target.write_bytes(b"data")
    resolved = bedtools_canary.require_contained_staging_file(target, root)
    assert resolved == target.resolve()


# 2: multiple valid staging files accepted
def test_multiple_valid_staging_files_are_accepted(tmp_path: Path):
    root = tmp_path / "staging"
    root.mkdir()
    a = root / "a.sif"
    b = root / "identity.json"
    a.write_bytes(b"data")
    b.write_bytes(b"{}")
    assert bedtools_canary.require_contained_staging_file(a, root) == a.resolve()
    assert bedtools_canary.require_contained_staging_file(b, root) == b.resolve()


# 3: external absolute path rejected
def test_external_absolute_path_is_rejected(tmp_path: Path):
    root = tmp_path / "staging"
    root.mkdir()
    outside = tmp_path / "outside.sif"
    outside.write_bytes(b"data")
    with pytest.raises(bedtools_canary.CanaryError, match="escapes the canary staging root"):
        bedtools_canary.require_contained_staging_file(outside, root)


# 4: ".." traversal escape rejected
def test_dot_dot_traversal_escape_is_rejected(tmp_path: Path):
    root = tmp_path / "staging"
    root.mkdir()
    outside = tmp_path / "outside.sif"
    outside.write_bytes(b"data")
    traversal_path = root / ".." / "outside.sif"
    with pytest.raises(bedtools_canary.CanaryError, match="escapes the canary staging root"):
        bedtools_canary.require_contained_staging_file(traversal_path, root)


# 5: symlink escape rejected
def test_symlink_escape_is_rejected(tmp_path: Path):
    root = tmp_path / "staging"
    root.mkdir()
    outside = tmp_path / "secret.sif"
    outside.write_bytes(b"data")
    link = root / "innocuous.sif"
    link.symlink_to(outside)
    with pytest.raises(bedtools_canary.CanaryError, match="escapes the canary staging root"):
        bedtools_canary.require_contained_staging_file(link, root)


# 6: sibling-prefix path rejected (string-prefix would wrongly accept this)
def test_sibling_prefix_path_is_rejected(tmp_path: Path):
    root = tmp_path / "canary"
    root.mkdir()
    sibling = tmp_path / "canary-evil"
    sibling.mkdir()
    sneaky = sibling / "file.sif"
    sneaky.write_bytes(b"data")
    assert str(sneaky).startswith(str(root)), "test setup must reproduce the string-prefix trap"
    with pytest.raises(bedtools_canary.CanaryError, match="escapes the canary staging root"):
        bedtools_canary.require_contained_staging_file(sneaky, root)


# 7: nonexistent path rejected
def test_nonexistent_path_is_rejected(tmp_path: Path):
    root = tmp_path / "staging"
    root.mkdir()
    missing = root / "does-not-exist.sif"
    with pytest.raises(bedtools_canary.CanaryError, match="does not resolve to an existing file"):
        bedtools_canary.require_contained_staging_file(missing, root)


# 8: directory rejected where file required
def test_directory_is_rejected_where_file_required(tmp_path: Path):
    root = tmp_path / "staging"
    root.mkdir()
    subdir = root / "a_directory"
    subdir.mkdir()
    with pytest.raises(bedtools_canary.CanaryError, match="not a regular file"):
        bedtools_canary.require_contained_staging_file(subdir, root)


# 9: ORAS cannot execute when containment fails
def test_oras_never_invoked_when_containment_fails(tmp_path: Path, monkeypatch):
    payload = b"validated-sif"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    sif = tmp_path / "bedtools.sif"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif.write_bytes(payload)
    result = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new", identity.immutable_tag(value),
        identity.build_identity_sha256(value),
    )
    monkeypatch.setattr(bedtools_canary, "evaluate_live", lambda unused: ({}, result))
    called = []
    monkeypatch.setattr(bedtools_canary, "run", lambda argv, **kwargs: called.append(argv))

    def fail_containment(path, staging_root):
        raise bedtools_canary.CanaryError("simulated containment failure")

    monkeypatch.setattr(bedtools_canary, "require_contained_staging_file", fail_containment)
    with pytest.raises(bedtools_canary.CanaryError, match="simulated containment failure"):
        bedtools_canary.publish(candidate_path, sif, tmp_path / "result.json")
    assert called == []


def _run_publish_capturing_command(tmp_path: Path, monkeypatch) -> list[str]:
    payload = b"validated-sif"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    sif = tmp_path / "bedtools.sif"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif.write_bytes(payload)
    result = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new", identity.immutable_tag(value),
        identity.build_identity_sha256(value),
    )
    monkeypatch.setattr(bedtools_canary, "evaluate_live", lambda unused: ({}, result))
    commands: list[list[str]] = []
    manifest_bytes = b'{"schemaVersion":2}'

    def fake_run(argv, **kwargs):
        commands.append(list(argv))
        manifest_path = Path(argv[argv.index("--export-manifest") + 1])
        manifest_path.write_bytes(manifest_bytes)
        return subprocess.CompletedProcess(argv, 0, "", "")

    digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    monkeypatch.setattr(bedtools_canary, "run", fake_run)
    monkeypatch.setattr(bedtools_canary, "inspect_reference", lambda repository, tag: {
        "manifest_digest": digest, "tag": tag,
    })
    bedtools_canary.publish(candidate_path, sif, tmp_path / "result.json")
    assert len(commands) == 1
    return commands[0]


# 10: --disable-path-validation is present for the controlled Bedtools push
def test_disable_path_validation_flag_is_present_on_bedtools_push(tmp_path, monkeypatch):
    command = _run_publish_capturing_command(tmp_path, monkeypatch)
    assert command[:2] == ["oras", "push"]
    assert "--disable-path-validation" in command


# 11: flag is not injected into unrelated ORAS commands
def test_disable_path_validation_flag_is_not_used_elsewhere():
    source = Path(bedtools_canary.__file__).read_text()
    occurrences = source.count("--disable-path-validation")
    assert occurrences == 1, "the flag must appear exactly once, on the controlled push only"
    # The one occurrence must be inside the oras push command list, not
    # attached to "oras manifest fetch" / "oras blob fetch" / "oras repo tags".
    idx = source.index("--disable-path-validation")
    window = source[max(0, idx - 700):idx]
    assert '"oras", "push"' in window
    assert '"manifest", "fetch"' not in window
    assert '"blob", "fetch"' not in window
    assert '"repo", "tags"' not in window


# Staging paths passed to ORAS are the validated/resolved ones, not the raw ones
def test_command_uses_resolved_contained_paths(tmp_path, monkeypatch):
    command = _run_publish_capturing_command(tmp_path, monkeypatch)
    sif_arg = next(a for a in command if a.endswith(f":{bedtools_canary.SIF_MEDIA_TYPE}"))
    identity_arg = next(a for a in command if a.endswith(f":{bedtools_canary.IDENTITY_MEDIA_TYPE}"))
    sif_path = Path(sif_arg.rsplit(":", 1)[0])
    identity_path = Path(identity_arg.rsplit(":", 1)[0])
    assert sif_path == sif_path.resolve()
    assert identity_path == identity_path.resolve()


# 17 + 19: blocked identity decisions never invoke ORAS; only PUBLISH_NEW does
@pytest.mark.parametrize(
    "decision",
    [identity.Decision.BLOCK_TAG_COLLISION, identity.Decision.BLOCK_IDENTITY_MISMATCH,
     identity.Decision.BLOCK_METADATA_MISSING, identity.Decision.BLOCK_LEGACY_CONFLICT,
     identity.Decision.ERROR],
)
def test_blocked_decisions_never_invoke_oras_or_require_containment(tmp_path, monkeypatch, decision):
    payload = b"validated-sif"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    sif = tmp_path / "bedtools.sif"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif.write_bytes(payload)
    result = identity.Evaluation(decision, "blocked", identity.immutable_tag(value),
                                 identity.build_identity_sha256(value))
    monkeypatch.setattr(bedtools_canary, "evaluate_live", lambda unused: ({}, result))
    run_called = []
    containment_called = []
    monkeypatch.setattr(bedtools_canary, "run", lambda argv, **kwargs: run_called.append(argv))
    monkeypatch.setattr(
        bedtools_canary, "require_contained_staging_file",
        lambda path, root: containment_called.append(path) or path,
    )
    with pytest.raises(bedtools_canary.CanaryError, match="publication blocked"):
        bedtools_canary.publish(candidate_path, sif, tmp_path / "result.json")
    assert run_called == []
    assert containment_called == []


# 18: SKIP_EXACT_MATCH performs zero writes (and never invokes ORAS push)
def test_skip_exact_match_performs_zero_oras_writes(tmp_path, monkeypatch):
    payload = b"validated-sif"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    sif = tmp_path / "bedtools.sif"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif.write_bytes(payload)
    tag = identity.immutable_tag(value)
    result = identity.Evaluation(
        identity.Decision.SKIP_EXACT_MATCH, "already published", tag,
        identity.build_identity_sha256(value), existing_reference=tag,
    )
    monkeypatch.setattr(bedtools_canary, "evaluate_live", lambda unused: ({}, result))
    run_called = []
    monkeypatch.setattr(bedtools_canary, "run", lambda argv, **kwargs: run_called.append(argv))
    monkeypatch.setattr(bedtools_canary, "inspect_reference", lambda repository, existing_tag: {
        "manifest_digest": "sha256:" + "9" * 64, "tag": existing_tag,
    })
    published = bedtools_canary.publish(candidate_path, sif, tmp_path / "result.json")
    assert published["write_performed"] is False
    assert run_called == []


# 20 + 21 + 22 + 23: fixed repository; legacy/moving/version-only tags can never be destinations
def test_moving_legacy_and_version_only_tags_cannot_become_push_destinations(tmp_path, monkeypatch):
    payload = b"validated-sif"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    sif = tmp_path / "bedtools.sif"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif.write_bytes(payload)
    result = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new", identity.immutable_tag(value),
        identity.build_identity_sha256(value),
    )
    monkeypatch.setattr(bedtools_canary, "evaluate_live", lambda unused: ({}, result))
    run_called = []
    monkeypatch.setattr(bedtools_canary, "run", lambda argv, **kwargs: run_called.append(argv))
    for alias in bedtools_canary.MOVING_ALIASES:
        # publish() calls the name bound into its own module namespace via
        # `from scripts.sif_identity import immutable_tag`, so that binding
        # -- not sif_identity's own attribute -- must be patched here.
        monkeypatch.setattr(bedtools_canary, "immutable_tag", lambda unused, alias=alias: alias)
        with pytest.raises(bedtools_canary.CanaryError, match="refusing moving, version-only, or non-immutable tag"):
            bedtools_canary.publish(candidate_path, sif, tmp_path / "result.json")
    assert run_called == []
    assert bedtools_canary.REPOSITORY == "ghcr.io/omnibioai/omnibioai-sif/bedtools"


# 24: maximum publication count remains two (one per authorized architecture)
def test_maximum_publication_candidates_is_two():
    assert bedtools_canary.ALLOWED_ARCHITECTURES == ("amd64", "arm64")
    assert len(bedtools_canary.ALLOWED_ARCHITECTURES) == 2
