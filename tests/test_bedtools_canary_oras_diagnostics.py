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

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import bedtools_canary as canary

REAL_SUBPROCESS_RUN = subprocess.run


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
