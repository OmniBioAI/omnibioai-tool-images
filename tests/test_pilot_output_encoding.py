"""Runtime regressions for fail-closed scientific command-output decoding."""
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.pilot_manifest import get_tool
from scripts.pilot_probe import command_evidence, run


GENERIC_ENTRY = {
    "version_command": "tool --version",
    "version_pattern": r"Tool v[0-9]",
}


def version(command, tmp_path):
    return command_evidence(run(command, tmp_path), GENERIC_ENTRY, "version")


def test_valid_utf8_stdout_is_accepted(tmp_path):
    assert version(r"printf 'Tool v1.2.3\n'", tmp_path)["meaningful_output"] == "Tool v1.2.3"


def test_valid_utf8_stderr_is_accepted_when_contract_pattern_allows_it(tmp_path):
    gate = version(r"printf 'Tool v1.2.3\n' >&2", tmp_path)
    assert gate["stdout"] == ""
    assert gate["meaningful_output"] == "Tool v1.2.3"


@pytest.mark.parametrize("payload", [
    r"printf 'Tool v1.2.3\nnotes: \253legacy\273\n'",
    r"printf 'Tool v1.\2532.3\n'",
])
def test_useful_version_with_invalid_utf8_is_accepted(payload, tmp_path):
    gate = version(payload, tmp_path)
    assert r"\xab" in gate["output"]
    assert "Tool v1" in gate["meaningful_output"]


def test_non_utf8_build_flags_preserve_version_evidence(tmp_path):
    result = run(r"printf 'samtools 1.16.1\nCFLAGS: -ffile-prefix-map=\253BUILDPATH\273=.\n'", tmp_path)
    assert result.returncode == 0
    assert r"\xabBUILDPATH\xbb" in result.stdout
    gate = command_evidence(result, get_tool("samtools"), "version")
    assert gate["output"].startswith("samtools 1.16.1\n")
    assert r"\xabBUILDPATH\xbb" in gate["output"]
    assert gate["meaningful_output"].startswith("samtools 1.16.1\n")


def test_non_utf8_stderr_does_not_hide_command_failure(tmp_path):
    result = run(r"printf 'samtools 1.16.1\n\253' >&2; exit 7", tmp_path)
    assert result.returncode == 7
    assert r"\xab" in result.stderr
    with pytest.raises(ValueError, match="returncode"):
        command_evidence(result, get_tool("samtools"), "version")


@pytest.mark.parametrize("payload", [r"printf '\253\273'", r"printf '\000\001\002'"])
def test_undecodable_or_control_bytes_alone_cannot_pass_version(payload, tmp_path):
    with pytest.raises(ValueError, match="meaningful output"):
        version(payload, tmp_path)


@pytest.mark.parametrize("stdout,stderr", [
    ("", ""),
    (" \n\t ", ""),
    (None, None),
])
def test_empty_null_or_whitespace_output_fails_closed(stdout, stderr):
    result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr=stderr)
    with pytest.raises(ValueError, match="output"):
        command_evidence(result, GENERIC_ENTRY, "version")


@pytest.mark.parametrize("payload", [
    r"printf 'Tool v1.2.3\n'; exit 9",
    r"printf 'Tool v1.2.3 \253\n'; exit 9",
])
def test_nonzero_return_code_fails_even_with_version_text(payload, tmp_path):
    result = run(payload, tmp_path)
    assert result.returncode == 9
    with pytest.raises(ValueError, match="returncode"):
        command_evidence(result, GENERIC_ENTRY, "version")


def test_execution_exception_is_not_swallowed(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("synthetic execution failure")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(OSError, match="synthetic execution failure"):
        run("tool --version", tmp_path)
