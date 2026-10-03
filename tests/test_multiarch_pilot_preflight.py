"""Preflight regressions: real clean Python CLIs, no Docker/Apptainer execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import pilot_probe, pilot_runner
from scripts.pilot_integrity import validate_evidence
from scripts.pilot_manifest import get_tool, load_and_validate, validate
from test_multiarch_pilot_safety import complete_record


def workflow_job():
    return yaml.safe_load((ROOT / '.github/workflows/pilot-multiarch-sifs.yml').read_text())["jobs"]["build-verify-and-convert"]


@pytest.fixture
def clean_checkout(tmp_path):
    """No installed project, site initialization, existing imports or developer PYTHONPATH."""
    root = tmp_path / "checkout"
    root.mkdir()
    shutil.copytree(ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / ".github/pilot", root / ".github/pilot")
    (root / "dockerfiles").mkdir()
    for entry in load_and_validate()["tools"]:
        shutil.copy2(ROOT / entry["dockerfile"], root / entry["dockerfile"])
    return root


def workflow_argv():
    step = next(s for s in workflow_job()["steps"] if s.get("name", "").startswith("Build, verify"))
    argv = shlex.split(step["run"])
    assert argv[0] == "python3"
    # Use the real workflow arguments. -E/-S prevent local setup from hiding imports.
    return [sys.executable, "-B", "-E", "-S", *argv[1:]]


@pytest.mark.parametrize("poison_pythonpath", [False, True])
def test_real_workflow_entrypoint_clean_checkout(clean_checkout, poison_pythonpath):
    env = {"PATH": os.defpath}
    if poison_pythonpath:
        env["PYTHONPATH"] = "/definitely-not-the-checkout"
    result = subprocess.run(workflow_argv() + ["--help"], cwd=clean_checkout, env=env,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "--runner-only" in result.stdout
    assert not (clean_checkout / "pilot-output").exists()


def test_real_entrypoint_reaches_runner_gate_before_any_build(clean_checkout):
    env = {"PATH": os.defpath, "PILOT_TOOL": "fastqc", "PILOT_ARCH": "amd64",
           "PILOT_PLATFORM": "linux/amd64", "PILOT_DOCKERFILE": "dockerfiles/Dockerfile.fastqc",
           "PILOT_SOURCE_COMMIT": "a" * 40, "PILOT_RUNNER": "ubuntu-24.04"}
    result = subprocess.run(workflow_argv(), cwd=clean_checkout, env=env,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 1
    failure = json.loads((clean_checkout / "pilot-output/failure.json").read_text())
    assert "runner.os/runner.arch" in failure["error"]
    runner = json.loads((clean_checkout / "pilot-output/runner-evidence.json").read_text())
    assert runner["verification_result"] == "UNVERIFIED"
    assert runner["runner"] == {"os": None, "arch": None}
    assert runner["uname_m"] == subprocess.check_output(["uname", "-m"], text=True).strip()
    assert not (clean_checkout / "pilot-output/work").exists()


def test_provenance_module_help_works_without_installed_package(clean_checkout):
    result = subprocess.run([sys.executable, "-B", "-E", "-S", "-m", "scripts.record_pilot_provenance", "--help"],
                            cwd=clean_checkout, env={"PATH": os.defpath}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "--evidence" in result.stdout


@pytest.mark.parametrize("tool", ["samtools", "fastqc", "multiqc"])
@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_valid_native_and_interpreted_provenance(tool, arch):
    record = complete_record(tool, arch)
    validate_evidence(record)
    if tool != "samtools":
        assert "ELF" not in record["oci_executable"]["file"]
        assert set(record["sif_executable"]["interpreters"]) == set(get_tool(tool)["runtime_executables"])


@pytest.mark.parametrize("phase", ["oci", "sif"])
@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_native_binary_wrong_architecture_rejected(phase, arch):
    record = complete_record("samtools", arch)
    opposite = "ARM aarch64" if arch == "amd64" else "x86-64"
    record[f"{phase}_executable"]["file"] = f"/usr/bin/samtools: ELF 64-bit LSB executable, {opposite}"
    with pytest.raises(ValueError, match="architecture mismatch"):
        validate_evidence(record)


@pytest.mark.parametrize("tool", ["fastqc", "multiqc"])
@pytest.mark.parametrize("phase", ["oci", "sif"])
@pytest.mark.parametrize("defect", ["missing", "wrong_arch", "not_invocable", "wrong_runtime"])
def test_interpreted_evidence_fails_closed(tool, phase, defect):
    record = complete_record(tool, "arm64")
    gate = record[f"{phase}_executable"]
    runtime = get_tool(tool)["runtime_executables"][-1]
    if defect == "missing":
        del gate["interpreters"][runtime]
    elif defect == "wrong_arch":
        gate["interpreters"][runtime]["file"] = "/usr/bin/runtime: ELF 64-bit LSB executable, x86-64"
    elif defect == "not_invocable":
        gate["invocable"] = False
    else:
        record[f"{phase}_architecture"]["uname"] = "x86_64"
    with pytest.raises(ValueError):
        validate_evidence(record)


@pytest.mark.parametrize("value", [None, "", "java", "script", "unknown"])
def test_unknown_executable_type_rejected_at_manifest_and_provenance(value):
    data = load_and_validate()
    data["tools"][0]["executable_type"] = value
    with pytest.raises(ValueError):
        validate(data)
    record = complete_record()
    record["executable_type"] = value
    with pytest.raises(ValueError):
        validate_evidence(record)


@pytest.mark.parametrize("phase", ["oci", "sif"])
@pytest.mark.parametrize("defect", ["null", "empty", "whitespace", "missing", "list", "object", "nonzero", "missing_code", "bool_code", "string_code"])
def test_version_requires_zero_exit_and_meaningful_string(phase, defect):
    record = complete_record()
    gate = record[f"{phase}_version"]
    values = {"null": None, "empty": "", "whitespace": " \n\t ", "list": [], "object": {}}
    if defect in values:
        gate["output"] = values[defect]
    elif defect == "missing":
        del gate["output"]
    elif defect == "nonzero":
        gate["returncode"] = 7
    elif defect == "missing_code":
        del gate["returncode"]
    elif defect == "bool_code":
        gate["returncode"] = False
    else:
        gate["returncode"] = "0"
    # Retain status PASS and other nonempty metadata: neither can fill in evidence.
    gate["extra"] = "must not mask missing scientific evidence"
    with pytest.raises(ValueError):
        validate_evidence(record)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_version_normalizes_both_streams(stream):
    entry = get_tool("bwa")
    result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    setattr(result, stream, " \nVersion: 0.7.17\n ")
    gate = pilot_probe.command_evidence(result, entry, "version")
    assert gate["output"] == "Version: 0.7.17"
    assert gate[stream] == " \nVersion: 0.7.17\n "
    assert gate["returncode"] == 0


@pytest.mark.parametrize("field", ["os", "arch", "uname_m"])
@pytest.mark.parametrize("value", [None, "", " ", "unsupported", []])
def test_missing_or_invalid_actual_runner_evidence_rejected(field, value):
    record = complete_record()
    if field in ("os", "arch"):
        record["runner"][field] = value
    else:
        record[field] = value
    with pytest.raises(ValueError):
        validate_evidence(record)


@pytest.mark.parametrize("defect", ["missing_runner", "missing_arch", "missing_os", "missing_uname", "emulated", "wrong_arch", "wrong_uname", "wrong_os"])
def test_native_runner_mapping_cannot_fall_back(defect):
    record = complete_record("multiqc", "arm64")
    if defect == "missing_runner":
        del record["runner"]
    elif defect == "missing_arch":
        del record["runner"]["arch"]
    elif defect == "missing_os":
        del record["runner"]["os"]
    elif defect == "missing_uname":
        del record["uname_m"]
    elif defect == "emulated":
        record["execution_mode"] = "EMULATED"
    elif defect == "wrong_arch":
        record["runner"]["arch"] = "X64"
    elif defect == "wrong_uname":
        record["uname_m"] = "x86_64"
    else:
        record["runner"]["os"] = "macOS"
    with pytest.raises(ValueError):
        validate_evidence(record)


def test_workflow_captures_context_in_early_and_build_steps():
    job = workflow_job()
    assert job["defaults"]["run"]["working-directory"] == "${{ github.workspace }}"
    steps = [s for s in job["steps"] if "scripts.pilot_runner" in s.get("run", "")]
    assert len(steps) == 2
    assert "--runner-only" in steps[0]["run"]
    for step in steps:
        assert step["env"]["PILOT_RUNNER_OS"] == "${{ runner.os }}"
        assert step["env"]["PILOT_RUNNER_ARCH"] == "${{ runner.arch }}"
    install = next(s for s in job["steps"] if s.get("name") == "Install native Apptainer")
    assert job["steps"].index(steps[0]) < job["steps"].index(install)


@pytest.mark.parametrize("tool", ["fastqc", "multiqc"])
@pytest.mark.parametrize("arch", ["amd64", "arm64"])
@pytest.mark.parametrize("defect", [None, "missing_interpreter", "wrong_interpreter_arch", "wrong_shebang"])
def test_real_probe_emits_script_evidence_and_rejects_bad_interpreters(monkeypatch, tmp_path, tool, arch, defect):
    """Only external OS/tool responses are mocked; actual probe/schema code runs."""
    entry = get_tool(tool)
    record = complete_record(tool, arch)
    paths = {}
    for runtime in entry["runtime_executables"]:
        path = tmp_path / runtime
        path.write_text("synthetic interpreter; never executed\n")
        path.chmod(0o755)
        paths[runtime] = str(path)
    launcher = tmp_path / tool
    actual_interpreter = paths[entry["launcher_interpreter"]]
    if defect == "wrong_shebang":
        other = tmp_path / "other-interpreter"
        other.write_text("synthetic wrong interpreter\n")
        other.chmod(0o755)
        actual_interpreter = str(other)
    launcher.write_text(f"#!{actual_interpreter}\n# synthetic launcher\n")
    launcher.chmod(0o755)
    paths[tool] = str(launcher)
    bad_runtime = entry["runtime_executables"][-1]
    if defect == "missing_interpreter":
        del paths[bad_runtime]
    monkeypatch.setattr(pilot_probe.shutil, "which", lambda name: paths.get(name))
    scientific_commands = []

    def fake_run(argv, **kwargs):
        stderr = ""
        if argv == ["uname", "-m"]:
            stdout = record["uname_m"] + "\n"
        elif argv[:2] == ["file", "-L"]:
            path = argv[-1]
            native = "x86-64" if arch == "amd64" else "ARM aarch64"
            if defect == "wrong_interpreter_arch" and path == paths[bad_runtime]:
                native = "ARM aarch64" if arch == "amd64" else "x86-64"
            description = "script, ASCII text executable" if path == str(launcher) else f"ELF 64-bit LSB executable, {native}"
            stdout = f"{path}: {description}\n"
        elif argv[:4] == ["/bin/bash", "-euo", "pipefail", "-c"]:
            scientific_commands.append(argv[-1])
            if argv[-1] == entry["version_command"]:
                stdout, stderr = "", record["oci_version"]["output"] + "\n"
            else:
                assert argv[-1] == entry["smoke_command"]
                stdout = record["oci_smoke"]["output"] + "\n"
        else:
            pytest.fail(f"unexpected actual probe command: {argv}")
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)

    monkeypatch.setattr(pilot_probe.subprocess, "run", fake_run)
    if defect:
        with pytest.raises((ValueError, RuntimeError)):
            pilot_probe.check(entry, arch, tmp_path, "oci")
        assert scientific_commands == []
    else:
        for phase in ("oci", "sif"):
            actual = pilot_probe.check(entry, arch, tmp_path, phase)
            for key in ("architecture", "executable", "version", "smoke"):
                record[f"{phase}_{key}"] = actual[key]
            assert actual["version"]["stdout"] == ""
            assert actual["version"]["stderr"].strip() == actual["version"]["output"]
        validate_evidence(record)


@pytest.mark.parametrize("phase", ["oci", "sif"])
@pytest.mark.parametrize("defect", ["nonzero", "missing_result", "failed"])
def test_smoke_evidence_is_mandatory(phase, defect):
    record = complete_record()
    gate = record[f"{phase}_smoke"]
    if defect == "nonzero":
        gate["returncode"] = 6
    elif defect == "missing_result":
        del gate["output"]
    else:
        gate["status"] = "FAIL"
    with pytest.raises(ValueError):
        validate_evidence(record)


@pytest.mark.parametrize("key", ["tool", "source_commit_sha", "dockerfile", "target_architecture", "runner",
                                 "uname_m", "execution_mode", "executable_type", "oci_identity", "sif_sha256",
                                 "oci_architecture", "sif_architecture", "oci_executable", "sif_executable",
                                 "oci_version", "sif_version", "oci_smoke", "sif_smoke", "verification_result"])
def test_mandatory_provenance_fields_cannot_be_omitted(key):
    record = complete_record()
    del record[key]
    with pytest.raises(ValueError):
        validate_evidence(record)


@pytest.mark.parametrize("tool", ["fastqc", "multiqc"])
@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_runner_preserves_probe_and_actual_runner_evidence_without_builds(monkeypatch, tmp_path, tool, arch):
    """Exercise the real orchestration with every Docker/Apptainer call replaced."""
    record = complete_record(tool, arch)
    entry = get_tool(tool)
    output = tmp_path / "output"
    env = {"PILOT_TOOL": tool, "PILOT_ARCH": arch, "PILOT_PLATFORM": f"linux/{arch}",
           "PILOT_DOCKERFILE": entry["dockerfile"], "PILOT_SOURCE_COMMIT": "a" * 40,
           "PILOT_RUNNER": "ubuntu-24.04" if arch == "amd64" else "ubuntu-24.04-arm",
           "PILOT_RUNNER_OS": "Linux", "PILOT_RUNNER_ARCH": record["runner"]["arch"]}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "argv", ["pilot_runner", "--output", str(output)])
    calls = []

    def fake_command(argv, *, capture=False):
        calls.append(argv)
        stdout = ""
        if argv == ["uname", "-m"]:
            stdout = record["uname_m"] + "\n"
        elif argv[:3] == ["docker", "buildx", "build"]:
            pass
        elif argv[:2] == ["docker", "save"]:
            (output / "oci-image.tar").write_bytes(b"synthetic archive")
        elif argv[:3] == ["docker", "image", "inspect"]:
            stdout = record["oci_identity"]
        elif argv[:2] == ["docker", "run"] and "dpkg-query" in argv:
            stdout = "0.12.1-1"
        elif argv[:2] == ["docker", "run"] or argv[:3] == ["sudo", "apptainer", "exec"]:
            phase = "oci" if argv[0] == "docker" else "sif"
            gates = {k: record[f"{phase}_{k}"] for k in ("architecture", "executable", "version", "smoke")}
            (output / "work" / f"{phase}.json").write_text(json.dumps({**gates, "verification_status": "PASS", "phase": phase}))
        elif argv[:3] == ["sudo", "apptainer", "build"]:
            (output / f"{tool}_{arch}.sif").write_bytes(b"synthetic SIF bytes; not a real image")
        elif argv[:3] == ["sudo", "apptainer", "inspect"]:
            stdout = '{"data": {"attributes": {"labels": {"fixture": "synthetic"}}}}'
        else:
            pytest.fail(f"unexpected subprocess: {argv}")
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(pilot_runner, "command", fake_command)
    monkeypatch.setattr(pilot_runner, "archive_identity", lambda *a: {
        "oci_identity": record["oci_identity"], "oci_archive_sha256": "d" * 64,
        "oci_config_architecture": arch, "oci_config_os": "linux"})
    assert pilot_runner.main() == 0
    actual = json.loads((output / "provenance.json").read_text())
    validate_evidence(actual)
    assert actual["runner"] == record["runner"]
    assert actual["uname_m"] == record["uname_m"]
    for phase in ("oci", "sif"):
        assert actual[f"{phase}_executable"]["interpreters"] == record[f"{phase}_executable"]["interpreters"]
        assert actual[f"{phase}_version"]["returncode"] == 0
    assert all("push" not in argv and "--push" not in argv for argv in calls)
