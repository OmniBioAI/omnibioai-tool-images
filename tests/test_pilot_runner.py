"""Unit coverage for scripts/pilot_runner.py orchestration paths not already
exercised by test_multiarch_pilot_preflight.py / test_multiarch_pilot_safety.py:
the real `command()` helper, architecture_bound_dockerfile() rendering, and the
fail-closed error branches of main() (matrix binding, runner mapping,
--runner-only, image identity, SIF inspect, scientific version agreement,
and package identity resolution).
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import pilot_runner
from scripts.pilot_manifest import get_tool
from test_multiarch_pilot_safety import complete_record


def test_command_executes_a_real_subprocess():
    result = pilot_runner.command(["printf", "hello"], capture=True)
    assert result.returncode == 0
    assert result.stdout == "hello"


def test_command_default_does_not_capture():
    result = pilot_runner.command(["true"])
    assert result.returncode == 0
    assert result.stdout is None


def test_command_propagates_nonzero_exit():
    with pytest.raises(subprocess.CalledProcessError):
        pilot_runner.command(["false"])


# --- architecture_bound_dockerfile -----------------------------------------

def test_architecture_bound_dockerfile_rejects_malformed_base_identity(tmp_path):
    source = tmp_path / "Dockerfile"
    source.write_text("FROM python:3.11-slim-bookworm\n")
    with pytest.raises(ValueError, match="fully qualified SHA256 reference"):
        pilot_runner.architecture_bound_dockerfile(
            source, tmp_path / "out", "python:3.11-slim-bookworm"
        )


def test_architecture_bound_dockerfile_rejects_from_mismatch(tmp_path):
    source = tmp_path / "Dockerfile"
    source.write_text("FROM python:3.11-slim-bookworm\nRUN true\n")
    base_identity = "docker.io/library/ubuntu@sha256:" + "a" * 64
    with pytest.raises(ValueError, match="resolved base identity does not match Dockerfile FROM"):
        pilot_runner.architecture_bound_dockerfile(source, tmp_path / "out", base_identity)


def test_architecture_bound_dockerfile_rejects_missing_from(tmp_path):
    source = tmp_path / "Dockerfile"
    source.write_text("RUN true\n")
    base_identity = "docker.io/library/python@sha256:" + "a" * 64
    with pytest.raises(ValueError, match="no FROM instruction"):
        pilot_runner.architecture_bound_dockerfile(source, tmp_path / "out", base_identity)


def test_architecture_bound_dockerfile_normalizes_bare_python_image_and_renders(tmp_path):
    source = tmp_path / "Dockerfile"
    source.write_text("FROM python:3.11-slim-bookworm\nRUN true\n")
    output = tmp_path / "Dockerfile.architecture-bound"
    base_identity = "docker.io/library/python:3.11-slim-bookworm@sha256:" + "a" * 64
    rendered = pilot_runner.architecture_bound_dockerfile(source, output, base_identity)
    assert rendered == str(output)
    text = output.read_text()
    assert text.splitlines()[0] == f"FROM {base_identity}"
    assert "RUN true" in text


# --- main(): matrix binding / runner mapping / --runner-only ---------------

def _base_env(tool="fastqc", arch="amd64", runner="ubuntu-24.04"):
    entry = get_tool(tool)
    return {
        "PILOT_TOOL": tool, "PILOT_ARCH": arch, "PILOT_PLATFORM": f"linux/{arch}",
        "PILOT_DOCKERFILE": entry["dockerfile"], "PILOT_SOURCE_COMMIT": "a" * 40,
        "PILOT_RUNNER": runner,
    }


def test_main_rejects_platform_dockerfile_not_bound_to_manifest(monkeypatch, tmp_path):
    env = _base_env()
    env["PILOT_PLATFORM"] = "linux/arm64"  # does not match PILOT_ARCH=amd64
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(ROOT)
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["pilot_runner", "--output", str(output)])
    assert pilot_runner.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    assert "matrix platform or Dockerfile does not bind to manifest tool/architecture" in failure["error"]
    assert not (output / "work").exists()


def test_main_rejects_runner_not_manifest_approved(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pilot_runner, "command",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, "x86_64\n", ""),
    )
    env = _base_env(runner="not-the-approved-runner")
    env["PILOT_RUNNER_OS"] = "Linux"
    env["PILOT_RUNNER_ARCH"] = "X64"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(ROOT)
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["pilot_runner", "--output", str(output)])
    assert pilot_runner.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    assert "matrix runner is not the manifest-approved native runner" in failure["error"]
    runner_evidence = json.loads((output / "runner-evidence.json").read_text())
    assert runner_evidence["verification_result"] == "UNVERIFIED"
    assert not (output / "work").exists()


def test_main_runner_only_returns_before_any_build(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pilot_runner, "command",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, "x86_64\n", ""),
    )
    env = _base_env()
    env["PILOT_RUNNER_OS"] = "Linux"
    env["PILOT_RUNNER_ARCH"] = "X64"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(ROOT)
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["pilot_runner", "--output", str(output), "--runner-only"])
    assert pilot_runner.main() == 0
    assert not (output / "work").exists()
    runner_evidence = json.loads((output / "runner-evidence.json").read_text())
    assert runner_evidence["verification_result"] == "PASS"


# --- main(): full mocked pipeline fail-closed branches ----------------------

def _run_full_pipeline(
    monkeypatch, tmp_path, *, tool="fastqc", arch="amd64",
    image_id="sha256:" + "f" * 64,
    inspect_stdout='{"data": {"attributes": {"labels": {"fixture": "synthetic"}}}}',
    dpkg_stdout="0.12.1-1",
    sif_version_override=None,
    get_tool_override=None,
):
    """Mirror the real orchestration (test_multiarch_pilot_preflight.py's
    test_runner_preserves_probe_and_actual_runner_evidence_without_builds) with
    every Docker/Apptainer call replaced, but with hooks to inject the specific
    malformed evidence that exercises each fail-closed branch in main().
    """
    record = complete_record(tool, arch)
    entry = get_tool(tool)
    if get_tool_override is not None:
        entry = get_tool_override(entry)
        monkeypatch.setattr(pilot_runner, "get_tool", lambda tool_id: entry)
    if sif_version_override is not None:
        record["sif_version"] = {**record["sif_version"], **sif_version_override}

    output = tmp_path / "output"
    env = {
        "PILOT_TOOL": tool, "PILOT_ARCH": arch, "PILOT_PLATFORM": f"linux/{arch}",
        "PILOT_DOCKERFILE": entry["dockerfile"], "PILOT_SOURCE_COMMIT": "a" * 40,
        "PILOT_RUNNER": "ubuntu-24.04" if arch == "amd64" else "ubuntu-24.04-arm",
        "PILOT_RUNNER_OS": "Linux", "PILOT_RUNNER_ARCH": record["runner"]["arch"],
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(sys, "argv", ["pilot_runner", "--output", str(output)])

    def fake_command(argv, *, capture=False):
        stdout = ""
        if argv == ["uname", "-m"]:
            stdout = record["uname_m"] + "\n"
        elif argv[:3] == ["docker", "buildx", "build"]:
            pass
        elif argv[:2] == ["docker", "save"]:
            (output / "oci-image.tar").write_bytes(b"synthetic archive")
        elif argv[:3] == ["docker", "image", "inspect"]:
            stdout = image_id
        elif argv[:2] == ["docker", "run"] and "dpkg-query" in argv:
            stdout = dpkg_stdout
        elif argv[:2] == ["docker", "run"] or argv[:3] == ["sudo", "apptainer", "exec"]:
            phase = "oci" if argv[0] == "docker" else "sif"
            gates = {k: record[f"{phase}_{k}"] for k in ("architecture", "executable", "version", "smoke")}
            (output / "work" / f"{phase}.json").write_text(
                json.dumps({**gates, "verification_status": "PASS", "phase": phase})
            )
        elif argv[:3] == ["sudo", "apptainer", "build"]:
            (output / f"{tool}_{arch}.sif").write_bytes(b"synthetic SIF bytes; not a real image")
        elif argv[:3] == ["sudo", "apptainer", "inspect"]:
            stdout = inspect_stdout
        else:
            pytest.fail(f"unexpected subprocess: {argv}")
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(pilot_runner, "command", fake_command)
    monkeypatch.setattr(pilot_runner, "archive_identity", lambda *a: {
        "oci_identity": record["oci_identity"], "oci_archive_sha256": "d" * 64,
        "oci_config_architecture": arch, "oci_config_os": "linux",
    })
    return output, record


def test_main_rejects_non_sha256_local_image_id(monkeypatch, tmp_path):
    output, _ = _run_full_pipeline(monkeypatch, tmp_path, image_id="not-a-digest")
    assert pilot_runner.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    assert "local image inspect did not provide immutable image config identity" in failure["error"]


@pytest.mark.parametrize("inspect_stdout", ["{}", "[]"])
def test_main_rejects_empty_apptainer_inspect_metadata(monkeypatch, tmp_path, inspect_stdout):
    output, _ = _run_full_pipeline(monkeypatch, tmp_path, inspect_stdout=inspect_stdout)
    assert pilot_runner.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    assert "Apptainer inspect returned no structured metadata" in failure["error"]


def test_main_rejects_oci_sif_scientific_version_disagreement(monkeypatch, tmp_path):
    output, _ = _run_full_pipeline(
        monkeypatch, tmp_path, sif_version_override={"meaningful_output": "FastQC v0.11.9"}
    )
    assert pilot_runner.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    assert "OCI and SIF scientific version evidence differs" in failure["error"]


def test_main_rejects_empty_dpkg_package_version(monkeypatch, tmp_path):
    output, _ = _run_full_pipeline(monkeypatch, tmp_path, tool="fastqc", dpkg_stdout="")
    assert pilot_runner.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    assert "package manager returned empty package version" in failure["error"]


def test_main_rejects_unsupported_package_identity_kind(monkeypatch, tmp_path):
    def override(entry):
        return {**entry, "package_identity": {"kind": "weird", "name": entry["tool_id"]}}

    output, _ = _run_full_pipeline(monkeypatch, tmp_path, tool="fastqc", get_tool_override=override)
    assert pilot_runner.main() == 1
    failure = json.loads((output / "failure.json").read_text())
    assert "unsupported package identity contract" in failure["error"]
