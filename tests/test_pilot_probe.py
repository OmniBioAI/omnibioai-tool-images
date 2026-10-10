"""Unit coverage for scripts/pilot_probe.py validation helpers, resolve_shebang,
check(), and the CLI entrypoint error paths not already exercised by the
real-process integration tests in test_multiarch_pilot_preflight.py.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import pilot_probe
from scripts.pilot_probe import (
    check,
    resolve_shebang,
    validate_command_evidence,
    validate_executable_evidence,
    validate_probe_result,
)


GENERIC_ENTRY = {
    "version_command": "tool --version",
    "version_pattern": r"v[0-9]",
}


def test_validate_command_evidence_rejects_command_not_matching_manifest():
    gate = {"status": "PASS", "command": "other --version", "returncode": 0,
            "output": "v1", "meaningful_output": "v1"}
    with pytest.raises(ValueError, match="does not match manifest$"):
        validate_command_evidence(gate, GENERIC_ENTRY, "version")


def test_validate_command_evidence_rejects_output_not_matching_pattern():
    entry = {"version_command": "tool --version", "version_pattern": r"v9"}
    gate = {"status": "PASS", "command": "tool --version", "returncode": 0,
            "output": "v1", "meaningful_output": "v1"}
    with pytest.raises(ValueError, match="does not match manifest pattern"):
        validate_command_evidence(gate, entry, "version")


def test_validate_executable_evidence_rejects_unknown_executable_type():
    entry = {"executable_type": "weird", "expected_executable": "tool"}
    with pytest.raises(ValueError, match="unknown executable type"):
        validate_executable_evidence({}, entry, "amd64")


def test_validate_executable_evidence_rejects_identity_mismatch():
    entry = {"executable_type": "native_binary", "expected_executable": "tool"}
    gate = {"status": "PASS", "executable_type": "native_binary", "name": "othertool",
            "path": "/x", "resolved_path": "/x", "file": "/x: ELF 64-bit LSB executable, x86-64",
            "invocable": True}
    with pytest.raises(ValueError, match="executable identity/type differs from manifest"):
        validate_executable_evidence(gate, entry, "amd64")


def test_validate_executable_evidence_rejects_non_script_interpreted_launcher():
    entry = {"executable_type": "interpreted", "expected_executable": "fastqc"}
    gate = {"status": "PASS", "executable_type": "interpreted", "name": "fastqc",
            "path": "/usr/bin/fastqc", "resolved_path": "/usr/bin/fastqc",
            "file": "/usr/bin/fastqc: ELF 64-bit LSB executable, x86-64", "invocable": True}
    with pytest.raises(ValueError, match="interpreted launcher is not identified as script/text"):
        validate_executable_evidence(gate, entry, "amd64")


def test_validate_executable_evidence_rejects_missing_or_failed_interpreter():
    entry = {"executable_type": "interpreted", "expected_executable": "fastqc",
             "runtime_executables": ["perl", "java"], "launcher_interpreter": "perl"}
    gate = {
        "status": "PASS", "executable_type": "interpreted", "name": "fastqc",
        "path": "/usr/bin/fastqc", "resolved_path": "/usr/bin/fastqc",
        "file": "/usr/bin/fastqc: script, ASCII text executable", "invocable": True,
        "launcher_interpreter": "perl", "shebang": "#!/usr/bin/perl",
        "shebang_interpreter_path": "/usr/bin/perl",
        "interpreters": {
            "perl": {"status": "PASS", "invocable": True, "path": "/usr/bin/perl",
                      "resolved_path": "/usr/bin/perl",
                      "file": "/usr/bin/perl: ELF 64-bit LSB shared object, x86-64"},
            "java": {"status": "FAIL", "invocable": True, "path": "/usr/bin/java",
                      "resolved_path": "/usr/bin/java",
                      "file": "/usr/bin/java: ELF 64-bit LSB executable, x86-64"},
        },
    }
    with pytest.raises(ValueError, match="missing/failed interpreter: java"):
        validate_executable_evidence(gate, entry, "amd64")


def test_validate_probe_result_rejects_missing_or_wrong_phase():
    with pytest.raises(ValueError, match="missing/failed or wrong-phase scientific probe"):
        validate_probe_result({"verification_status": "PASS", "phase": "sif"}, {}, "amd64", "oci")
    with pytest.raises(ValueError, match="missing/failed or wrong-phase scientific probe"):
        validate_probe_result("not-a-dict", {}, "amd64", "oci")


def test_resolve_shebang_rejects_missing_shebang(tmp_path):
    exe = tmp_path / "tool"
    exe.write_text("no shebang in this file\n")
    exe.chmod(0o755)
    with pytest.raises(ValueError, match="interpreted launcher has no shebang"):
        resolve_shebang(str(exe))


def test_resolve_shebang_rejects_relative_interpreter(tmp_path):
    exe = tmp_path / "tool"
    exe.write_text("#!python3\n")
    exe.chmod(0o755)
    with pytest.raises(ValueError, match="unsupported launcher shebang"):
        resolve_shebang(str(exe))


def test_resolve_shebang_rejects_env_shebang_option_or_assignment(tmp_path):
    for shebang in ("#!/usr/bin/env -S python3\n", "#!/usr/bin/env FOO=bar\n"):
        exe = tmp_path / "tool"
        exe.write_text(shebang)
        exe.chmod(0o755)
        with pytest.raises(ValueError, match="unsupported env shebang"):
            resolve_shebang(str(exe))


def test_resolve_shebang_rejects_env_interpreter_missing(monkeypatch, tmp_path):
    exe = tmp_path / "tool"
    exe.write_text("#!/usr/bin/env python3\n")
    exe.chmod(0o755)
    monkeypatch.setattr(pilot_probe.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="launcher interpreter is missing/not executable"):
        resolve_shebang(str(exe))


def test_resolve_shebang_rejects_direct_interpreter_not_executable(tmp_path):
    exe = tmp_path / "tool"
    exe.write_text("#!/definitely/not/a/real/interpreter\n")
    exe.chmod(0o755)
    with pytest.raises(ValueError, match="launcher interpreter is missing/not executable"):
        resolve_shebang(str(exe))


def test_check_rejects_unknown_executable_type():
    with pytest.raises(ValueError, match="unknown executable type"):
        check({"executable_type": "weird"}, "amd64", Path("."), "oci")


def test_check_rejects_runtime_architecture_mismatch(monkeypatch):
    monkeypatch.setattr(
        pilot_probe.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, "aarch64\n", ""),
    )
    with pytest.raises(RuntimeError, match="runtime architecture mismatch"):
        check({"executable_type": "native_binary"}, "amd64", Path("."), "oci")


def test_check_rejects_missing_expected_executable(monkeypatch):
    monkeypatch.setattr(
        pilot_probe.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, "x86_64\n", ""),
    )
    monkeypatch.setattr(pilot_probe.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="intended executable missing or not executable"):
        check({"executable_type": "native_binary", "expected_executable": "toolbin"},
              "amd64", Path("."), "oci")


def test_main_success_writes_output_and_prints_result(monkeypatch, tmp_path):
    entry_path = tmp_path / "entry.json"
    entry_path.write_text(json.dumps({"anything": True}))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output_path = tmp_path / "output.json"
    fixed_result = {"verification_status": "PASS", "phase": "oci"}
    monkeypatch.setattr(pilot_probe, "check", lambda entry, arch, ws, phase: fixed_result)
    monkeypatch.setattr(sys, "argv", [
        "pilot_probe", "--entry", str(entry_path), "--arch", "amd64", "--phase", "oci",
        "--workspace", str(workspace), "--output", str(output_path),
    ])
    assert pilot_probe.main() == 0
    assert json.loads(output_path.read_text()) == fixed_result


def test_main_failure_reports_error_and_skips_output(monkeypatch, tmp_path, capsys):
    entry_path = tmp_path / "entry.json"
    entry_path.write_text(json.dumps({}))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output_path = tmp_path / "output.json"

    def fail_check(entry, arch, ws, phase):
        raise ValueError("synthetic failure")

    monkeypatch.setattr(pilot_probe, "check", fail_check)
    monkeypatch.setattr(sys, "argv", [
        "pilot_probe", "--entry", str(entry_path), "--arch", "amd64", "--phase", "sif",
        "--workspace", str(workspace), "--output", str(output_path),
    ])
    assert pilot_probe.main() == 1
    captured = capsys.readouterr()
    assert "sif scientific verification failed: synthetic failure" in captured.err
    assert not output_path.exists()
