"""Regression coverage for ORAS publication diagnostics.

Run 37027520763 reached `oras push` on both architectures and it exited
non-zero, but the real cause was unrecoverable: `run()` used
`subprocess.run(check=True)` and the top-level handler only ever printed
`str(CalledProcessError)`, which never includes the captured stdout/stderr.
These tests guard the fix: `run()` now classifies and preserves sanitized
ORAS diagnostics itself, so any caller (including the existing generic
`except Exception` in main()) surfaces them for free.

Items 17-20 of the authorizing task's regression list (blocked decisions
never invoke ORAS; only PUBLISH_NEW reaches ORAS; fixed Bedtools repository;
moving/legacy/version-only tags can't become destinations) are already
covered by `tests/test_sif_identity.py::test_canary_publish_never_writes_for_blocking_decisions`
and `::test_publish_new_has_one_fixed_immutable_oras_write`, which this
change does not touch or weaken.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import bedtools_canary as canary
from scripts import sif_identity as identity

REAL_SUBPROCESS_RUN = subprocess.run

_ZERO = "0" * 64
_ONE = "1" * 64
_TWO = "2" * 64


def _candidate_dict(**changes):
    # Mirrors tests/test_bedtools_canary_staging_containment.py::candidate --
    # duplicated rather than imported since `tests/` is not a package here.
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


def fake_subprocess(returncode: int, stdout: str = "", stderr: str = ""):
    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return _fake_run


# 1: ORAS success
def test_oras_success_returns_completed_process(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(0, "ok", ""))
    result = canary.run(["oras", "manifest", "fetch", "x"])
    assert result.returncode == 0
    assert result.stdout == "ok"


# 2 + 16: ORAS nonzero exit always raises, never silently becomes success
def test_oras_nonzero_exit_raises_and_cannot_become_pass(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", "denied: access denied"))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.returncode == 1


# 3: stderr preservation
def test_stderr_is_preserved_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", "manifest unknown: not found"))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert "manifest unknown" in excinfo.value.stderr
    assert "manifest unknown" in str(excinfo.value)


# 4: stdout preservation
def test_stdout_is_preserved_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "partial progress output", ""))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert "partial progress output" in excinfo.value.stdout
    assert "partial progress output" in str(excinfo.value)


# 5: empty stderr with nonzero exit
def test_empty_stderr_with_nonzero_exit_does_not_crash(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "some stdout", ""))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.stderr == ""


# 6: empty stdout/stderr with nonzero exit
def test_empty_stdout_and_stderr_with_nonzero_exit_falls_back_to_unknown(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", ""))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_UNKNOWN_FAILURE"
    assert excinfo.value.returncode == 1


# 7: multiline error output
def test_multiline_error_output_is_preserved(monkeypatch):
    stderr = "Error: failed to push\ncaused by: denied: requested access is denied\n"
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", stderr))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.stderr == stderr
    assert excinfo.value.classification == "ORAS_PERMISSION_FAILURE"


# 8: non-UTF-8 output must not crash the subprocess call itself
def test_non_utf8_output_does_not_raise_unicode_error(tmp_path: Path):
    script = tmp_path / "emit_bad_bytes.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'before-\\xff-after')\n"
        "sys.exit(1)\n"
    )
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run([sys.executable, str(script)])
    assert "before-" in excinfo.value.stdout
    assert "after" in excinfo.value.stdout


# 9 + 10: bearer-token / generic token redaction
def test_bearer_token_is_redacted(monkeypatch):
    stderr = "request failed: Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", stderr))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in excinfo.value.stderr
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in str(excinfo.value)
    assert "[REDACTED]" in excinfo.value.stderr


# 11: Authorization-header (Basic) redaction
def test_basic_auth_header_is_redacted(monkeypatch):
    stderr = "sent header Authorization: Basic dXNlcjpwYXNzd29yZA=="
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", stderr))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert "dXNlcjpwYXNzd29yZA==" not in excinfo.value.stderr
    assert "[REDACTED]" in excinfo.value.stderr


def test_generic_token_keyvalue_is_redacted(monkeypatch):
    stderr = "config dump: token=supersecretvalue123 other=fine"
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", stderr))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert "supersecretvalue123" not in excinfo.value.stderr
    assert "other=fine" in excinfo.value.stderr


# 12: permission-denied classification
def test_permission_denied_is_classified(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        fake_subprocess(1, "", "denied: requested access to the resource is denied"),
    )
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_PERMISSION_FAILURE"


# 13: authentication-failure classification
def test_authentication_failure_is_classified(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", fake_subprocess(1, "", "Error: authentication required")
    )
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_AUTH_FAILURE"


# 14: network-failure classification where deterministically recognizable
def test_network_failure_is_classified(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        fake_subprocess(1, "", "Error: dial tcp 140.82.1.1:443: connection refused"),
    )
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_NETWORK_FAILURE"


# 15: unknown-error fallback (no recognizable marker)
def test_unrecognized_error_falls_back_to_unknown(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", "gremlins ate the manifest"))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_UNKNOWN_FAILURE"


def test_reference_failure_is_classified(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "", "Error: manifest unknown"))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_REFERENCE_FAILURE"


def test_media_type_failure_is_classified(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", fake_subprocess(1, "", "Error: unsupported media type")
    )
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_MANIFEST_OR_MEDIA_FAILURE"


def test_sanitize_fails_closed_on_internal_error(monkeypatch):
    # Patch only this module's lookup of the redaction table, not the global
    # `re` machinery, so the failure is localized to _sanitize's own logic.
    monkeypatch.setattr(canary, "_REDACTION_PATTERNS", None)
    result = canary._sanitize("anything")
    assert result == "[OUTPUT UNAVAILABLE: sanitization failed closed]"


def test_main_surfaces_oras_failure_classification_and_exit_code(tmp_path, monkeypatch, capsys):
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("{}")
    sif = tmp_path / "bedtools.sif"
    sif.write_bytes(b"x")

    def fake_publish(*args, **kwargs):
        raise canary.OrasInvocationFailed(["oras", "push"], 1, "", "denied", "ORAS_PERMISSION_FAILURE")

    monkeypatch.setattr(canary, "publish", fake_publish)
    rc = canary.main([
        "publish", "--candidate", str(candidate_path), "--sif", str(sif),
        "--output", str(tmp_path / "out.json"),
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "ORAS_INVOCATION_FAILED" in captured.err
    assert "ORAS_PERMISSION_FAILURE" in captured.err


def test_module_never_bypasses_failure_with_true_or_ignored_returncode():
    source = Path(canary.__file__).read_text()
    assert "|| true" not in source
    assert "check=True" not in source


# run(): binary-mode (text=False) failures must not attempt to decode output.
def test_oras_binary_mode_failure_uses_placeholder_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_subprocess(1, "raw-bytes", "raw-err"))
    with pytest.raises(canary.OrasInvocationFailed) as excinfo:
        canary.run(["oras", "blob", "fetch", "x"], text=False)
    assert excinfo.value.stdout == "<binary output not captured>"
    assert excinfo.value.stderr == "<binary output not captured>"


def test_spec_rejects_mismatched_manifest_mapping(monkeypatch):
    bogus = canary.engine.ReleaseSpec(
        tool_id="bedtools",
        dockerfile="dockerfiles/Dockerfile.wrong",
        expected_executable="bedtools",
        repository=canary.REPOSITORY,
        scientific_version_pattern=r"(?P<version>\d+\.\d+\.\d+)",
        package_identity_kind="apt",
        package_identity_name="bedtools",
    )
    monkeypatch.setattr(canary.engine, "release_spec", lambda tool_id: bogus)
    with pytest.raises(canary.CanaryError, match="Bedtools manifest mapping differs"):
        canary._spec()


def test_prepare_candidate_accepts_matching_scientific_version(tmp_path, monkeypatch):
    fake_metadata = {"scientific_version": canary.SCIENTIFIC_VERSION, "tool_id": "bedtools"}
    monkeypatch.setattr(
        canary.engine, "prepare_candidate_for_spec",
        lambda spec, provenance_path, sif, output: fake_metadata,
    )
    result = canary.prepare_candidate(
        tmp_path / "prov.json", tmp_path / "x.sif", tmp_path / "out.json"
    )
    assert result == fake_metadata


def test_prepare_candidate_rejects_mismatched_scientific_version(tmp_path, monkeypatch):
    fake_metadata = {"scientific_version": "9.9.9"}
    monkeypatch.setattr(
        canary.engine, "prepare_candidate_for_spec", lambda *a, **k: fake_metadata
    )
    with pytest.raises(canary.CanaryError, match="not Bedtools 2.30.0"):
        canary.prepare_candidate(
            tmp_path / "prov.json", tmp_path / "x.sif", tmp_path / "out.json"
        )


def test_inspect_reference_rejects_non_authoritative_repository():
    with pytest.raises(canary.CanaryError, match="not the authoritative Bedtools package"):
        canary.inspect_reference("ghcr.io/other/bedtools", "v1")


def test_inspect_reference_delegates_to_engine_with_run_fn(monkeypatch):
    captured = {}

    def fake_inspect(repository, tag, run_fn=None):
        captured["args"] = (repository, tag, run_fn)
        return {"manifest_digest": "sha256:" + "a" * 64}

    monkeypatch.setattr(canary.engine, "inspect_reference", fake_inspect)
    result = canary.inspect_reference(canary.REPOSITORY, "v2.30.0-amd64-deadbeef")
    assert result == {"manifest_digest": "sha256:" + "a" * 64}
    assert captured["args"] == (canary.REPOSITORY, "v2.30.0-amd64-deadbeef", canary.run)


def test_registry_snapshot_rejects_non_authoritative_repository():
    with pytest.raises(canary.CanaryError, match="not the authoritative Bedtools package"):
        canary.registry_snapshot("ghcr.io/other/bedtools", {})


def test_registry_snapshot_delegates_to_engine(monkeypatch):
    captured = {}

    def fake_snapshot(spec, candidate, inspect_fn=None):
        captured["args"] = (spec, candidate, inspect_fn)
        return {"entries": []}

    monkeypatch.setattr(canary.engine, "registry_snapshot", fake_snapshot)
    candidate = {"target_architecture": "amd64"}
    result = canary.registry_snapshot(canary.REPOSITORY, candidate)
    assert result == {"entries": []}
    assert captured["args"][1] is candidate
    assert captured["args"][2] is canary.inspect_reference


def test_evaluate_live_combines_snapshot_and_publication_decision(monkeypatch):
    sentinel_state = {"entries": []}
    monkeypatch.setattr(canary, "registry_snapshot", lambda repo, cand: sentinel_state)
    sentinel_result = object()
    monkeypatch.setattr(identity, "evaluate_publication", lambda cand, state: sentinel_result)
    candidate = {"target_architecture": "amd64"}
    state, result = canary.evaluate_live(candidate)
    assert state is sentinel_state
    assert result is sentinel_result


def test_retrieve_and_verify_delegates_with_required_legacy_tags(tmp_path, monkeypatch):
    captured = {}

    def fake_retrieve(spec, candidate_path, destination, output, **kwargs):
        captured["candidate_path"] = candidate_path
        captured["destination"] = destination
        captured["output"] = output
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(canary.engine, "retrieve_and_verify_for_spec", fake_retrieve)
    candidate_path = tmp_path / "c.json"
    destination = tmp_path / "dest.sif"
    output = tmp_path / "out.json"
    result = canary.retrieve_and_verify(candidate_path, destination, output)
    assert result == {"ok": True}
    assert captured["candidate_path"] == candidate_path
    assert captured["destination"] == destination
    assert captured["output"] == output
    assert captured["kwargs"]["required_legacy_tags"] == {"arm64": canary.LEGACY_ARM64_SHA256}
    assert captured["kwargs"]["run_fn"] is canary.run


def test_verify_retrieved_runtime_delegates_to_engine(tmp_path, monkeypatch):
    captured = {}

    def fake_verify(spec, candidate_path, sif, workspace, output, run_fn=None):
        captured["args"] = (spec, candidate_path, sif, workspace, output, run_fn)
        return {"ok": True}

    monkeypatch.setattr(canary.engine, "verify_retrieved_runtime_for_spec", fake_verify)
    candidate_path = tmp_path / "c.json"
    sif = tmp_path / "x.sif"
    workspace = tmp_path / "ws"
    output = tmp_path / "out.json"
    result = canary.verify_retrieved_runtime(candidate_path, sif, workspace, output)
    assert result == {"ok": True}
    assert captured["args"][1:5] == (candidate_path, sif, workspace, output)
    assert captured["args"][5] is canary.run


def test_main_prepare_command_returns_zero_and_prints_tag(tmp_path, monkeypatch, capsys):
    value = _candidate_dict()
    metadata = identity.identity_metadata(value)
    monkeypatch.setattr(canary, "prepare_candidate", lambda provenance, sif, output: metadata)
    provenance_path = tmp_path / "prov.json"
    provenance_path.write_text("{}")
    sif = tmp_path / "bedtools.sif"
    sif.write_bytes(b"x")
    output = tmp_path / "candidate.json"
    rc = canary.main(
        ["prepare", "--provenance", str(provenance_path), "--sif", str(sif), "--output", str(output)]
    )
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["candidate"]["tool_id"] == "bedtools"
    assert printed["immutable_tag"] == identity.immutable_tag(metadata)


def test_main_decide_command_publish_new_returns_zero_and_writes_output(tmp_path, monkeypatch, capsys):
    value = _candidate_dict()
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    output = tmp_path / "decision.json"
    tag = identity.immutable_tag(metadata)
    result = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new candidate", tag, metadata["build_identity_sha256"]
    )
    monkeypatch.setattr(canary, "evaluate_live", lambda cand: ({"entries": []}, result))
    rc = canary.main(["decide", "--candidate", str(candidate_path), "--output", str(output)])
    assert rc == 0
    written = json.loads(output.read_text())
    assert written["decision"]["decision"] == "PUBLISH_NEW"
    printed = json.loads(capsys.readouterr().out)
    assert printed["decision"] == "PUBLISH_NEW"


def test_main_decide_command_blocked_decision_returns_two(tmp_path, monkeypatch):
    value = _candidate_dict()
    metadata = identity.identity_metadata(value)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(metadata))
    output = tmp_path / "decision.json"
    result = identity.Evaluation(
        identity.Decision.BLOCK_TAG_COLLISION, "collision", None, metadata["build_identity_sha256"]
    )
    monkeypatch.setattr(canary, "evaluate_live", lambda cand: ({"entries": []}, result))
    rc = canary.main(["decide", "--candidate", str(candidate_path), "--output", str(output)])
    assert rc == 2


def test_main_publish_command_success_returns_zero(tmp_path, monkeypatch):
    called = {}

    def fake_publish(candidate_path, sif, output):
        called["ok"] = True
        return {}

    monkeypatch.setattr(canary, "publish", fake_publish)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("{}")
    sif = tmp_path / "x.sif"
    sif.write_bytes(b"x")
    rc = canary.main(
        [
            "publish", "--candidate", str(candidate_path), "--sif", str(sif),
            "--output", str(tmp_path / "out.json"),
        ]
    )
    assert rc == 0
    assert called["ok"] is True


def test_main_retrieve_command_delegates_and_returns_zero(tmp_path, monkeypatch):
    called = {}

    def fake_retrieve(candidate_path, destination, output):
        called["args"] = (candidate_path, destination, output)
        return {}

    monkeypatch.setattr(canary, "retrieve_and_verify", fake_retrieve)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("{}")
    destination = tmp_path / "dest.sif"
    output = tmp_path / "out.json"
    rc = canary.main(
        [
            "retrieve", "--candidate", str(candidate_path),
            "--destination", str(destination), "--output", str(output),
        ]
    )
    assert rc == 0
    assert called["args"] == (candidate_path, destination, output)


def test_main_runtime_command_delegates_and_returns_zero(tmp_path, monkeypatch):
    called = {}

    def fake_verify(candidate_path, sif, workspace, output):
        called["args"] = (candidate_path, sif, workspace, output)
        return {}

    monkeypatch.setattr(canary, "verify_retrieved_runtime", fake_verify)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text("{}")
    sif = tmp_path / "x.sif"
    workspace = tmp_path / "ws"
    output = tmp_path / "out.json"
    rc = canary.main(
        [
            "runtime", "--candidate", str(candidate_path), "--sif", str(sif),
            "--workspace", str(workspace), "--output", str(output),
        ]
    )
    assert rc == 0
    assert called["args"] == (candidate_path, sif, workspace, output)
