"""Static/local-only regression tests for pilot safety properties."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tarfile
import io

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.pilot_integrity import archive_identity, validate_evidence
from scripts.pilot_manifest import get_tool, load_and_validate, matrix, require_nonpublishing
from scripts.pilot_probe import run

WORKFLOW = ROOT / ".github/workflows/pilot-multiarch-sifs.yml"


def archive_fixture(path: Path, arch: str) -> tuple[str, str]:
    tool, commit = "fastqc", "a" * 40
    dockerfile_sha = "b" * 64
    config = {"os": "linux", "architecture": arch, "config": {"Labels": {
        "org.omnibioai.tool": tool, "org.omnibioai.source-commit": commit,
        "org.omnibioai.dockerfile-sha256": dockerfile_sha,
        "org.omnibioai.target-platform": f"linux/{arch}",
    }}}
    raw = json.dumps(config, sort_keys=True).encode()
    config_name = "config.json"
    with tarfile.open(path, "w") as tar:
        for name, payload in (("manifest.json", json.dumps([{"Config": config_name}]).encode()),
                              (config_name, raw)):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return commit, dockerfile_sha


@pytest.mark.parametrize(("source_arch", "requested_arch"), [("amd64", "arm64"), ("arm64", "amd64")])
def test_architecture_source_cannot_be_swapped(tmp_path, source_arch, requested_arch):
    path = tmp_path / "oci.tar"
    commit, dockerfile_sha = archive_fixture(path, source_arch)
    with pytest.raises(ValueError, match="platform mismatch"):
        archive_identity(path, "fastqc", requested_arch, commit, dockerfile_sha)


def test_publish_true_fails_before_matrix_creation():
    with pytest.raises(ValueError, match="publication is disabled"):
        require_nonpublishing(True)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/pilot_manifest.py"), "--publish", "true", "--matrix"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize("command", ["false", "printf x | grep absent", "echo x | false"])
def test_scientific_command_failure_propagates(command, tmp_path):
    result = run(command, tmp_path)
    assert result.returncode != 0


def test_provenance_missing_or_malformed_gates_fail_closed():
    for record in ({}, {"verification_result": "PASS", "sif_sha256": ""}):
        with pytest.raises((ValueError, KeyError)):
            validate_evidence(record)
    record = complete_record()
    record["sif_smoke"] = {"status": "PASS", "output": None}
    # Gate evidence must include successful check output, not merely a PASS label.
    with pytest.raises(ValueError):
        validate_evidence(record)


def complete_record(tool="fastqc", arch="amd64"):
    """Synthetic evidence with the same schema/types emitted by the probe."""
    entry = get_tool(tool)
    kind = entry["executable_type"]
    version, smoke = {
        "fastqc": ("FastQC v0.12.1", "fastqc-ok"),
        "multiqc": ("multiqc, version 1.25", "multiqc-ok"),
        "samtools": ("samtools 1.20", "1"),
    }[tool]
    machine = {"amd64": "x86_64", "arm64": "aarch64"}[arch]
    native = "x86-64" if arch == "amd64" else "ARM aarch64"
    gates = {}
    for phase in ("oci", "sif"):
        for check_name, output in (("version", version), ("smoke", smoke)):
            gates[f"{phase}_{check_name}"] = {"status": "PASS", "command": entry[f"{check_name}_command"],
                                              "returncode": 0, "stdout": output + "\n", "stderr": "",
                                              "output": output, "meaningful_output": output}
    gates["sif_inspect"] = {"status": "PASS", "metadata": {"data": "verified"}}
    for name in ("oci_architecture", "sif_architecture"):
        gates[name] = {"status": "PASS", "uname": machine, "target": arch}
    for name in ("oci_executable", "sif_executable"):
        path = f"/usr/bin/{tool}"
        gate = {"status": "PASS", "name": tool, "path": path, "resolved_path": path,
                "executable_type": kind, "invocable": True,
                "file": f"{path}: ELF 64-bit LSB pie executable, {native}", "interpreters": {}}
        if kind == "interpreted":
            gate["file"] = f"{path}: script, ASCII text executable"
            interpreter = entry["launcher_interpreter"]
            gate.update(launcher_interpreter=interpreter, shebang=f"#!/usr/bin/{interpreter}",
                        shebang_interpreter_path=f"/usr/bin/{interpreter}")
            gate["interpreters"] = {runtime: {"status": "PASS", "path": f"/usr/bin/{runtime}",
                                              "resolved_path": f"/usr/bin/{runtime}", "invocable": True,
                                              "file": f"/usr/bin/{runtime}: ELF 64-bit LSB pie executable, {native}"}
                                    for runtime in entry["runtime_executables"]}
        gates[name] = gate
    return {"tool": tool, "source_commit_sha": "a" * 40, "dockerfile": entry["dockerfile"],
            "dockerfile_sha256": "b" * 64, "target_architecture": arch, "oci_identity": "sha256:" + "c" * 64,
            "oci_archive_sha256": "d" * 64, "sif_sha256": "e" * 64, "tool_version_output": version,
            "runner": {"os": "Linux", "arch": "X64" if arch == "amd64" else "ARM64"},
            "uname_m": machine, "executable_type": kind, "execution_mode": "NATIVE", "verification_result": "PASS",
            "build_timestamp": datetime.now(timezone.utc).isoformat(),
            "verification_timestamp": datetime.now(timezone.utc).isoformat(), **gates}


def test_provenance_rejects_failed_or_absent_evidence():
    record = complete_record()
    record["sif_version"] = {"status": "FAIL", "output": "error"}
    with pytest.raises(ValueError):
        validate_evidence(record)
    record = complete_record()
    del record["oci_smoke"]
    with pytest.raises(ValueError):
        validate_evidence(record)


def test_provenance_cli_rejects_empty_sif(tmp_path):
    evidence, sif, output = tmp_path / "evidence.json", tmp_path / "image.sif", tmp_path / "out.json"
    record = complete_record()
    record["sif_sha256"] = hashlib.sha256(b"").hexdigest()
    evidence.write_text(json.dumps(record))
    sif.write_bytes(b"")
    result = subprocess.run([sys.executable, "-B", "-E", "-S", "-m", "scripts.record_pilot_provenance",
                             "--evidence", str(evidence), "--sif", str(sif), "--output", str(output)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "SIF missing or empty" in result.stderr
    assert not output.exists()


def test_optional_sbom_is_not_misreported_as_pass():
    data = load_and_validate()
    assert data["sbom_policy"] == "optional"
    runner = (ROOT / "scripts/pilot_runner.py").read_text()
    assert '"SBOM_OPTIONAL_NOT_GENERATED"' in runner
    assert "SBOM_PASS" not in runner


def test_runner_architecture_and_native_strategy_are_explicit():
    data = load_and_validate()
    expected = {"amd64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"}
    assert {item["arch"]: item["runner"] for item in matrix(data)} == expected
    workflow = WORKFLOW.read_text()
    assert "runs-on: ${{ matrix.runner }}" in workflow
    assert "docker/setup-qemu-action" not in workflow
    assert '"NATIVE"' in (ROOT / "scripts/pilot_runner.py").read_text()
    assert '["sudo", "apptainer", "exec"' in (ROOT / "scripts/pilot_runner.py").read_text()


def test_workflow_has_read_only_permissions_and_fail_closed_matrix():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["build-verify-and-convert"]["strategy"]["fail-fast"] is False
    assert workflow["jobs"]["build-verify-and-convert"]["strategy"]["max-parallel"] == 4
    assert "continue-on-error" not in WORKFLOW.read_text()
    assert 'PUBLISH: ${{ inputs.publish }}' in WORKFLOW.read_text()
    assert '--publish "$PUBLISH"' in WORKFLOW.read_text()
    assert 'TOOL: ${{ inputs.tool }}' in WORKFLOW.read_text()
    assert '--tool "$TOOL"' in WORKFLOW.read_text()
    assert "docker push" not in WORKFLOW.read_text().lower()
    assert "oras push" not in WORKFLOW.read_text().lower()
    assert "--push" not in WORKFLOW.read_text()


def test_oci_archive_requires_one_manifest_and_bound_labels(tmp_path):
    path = tmp_path / "oci.tar"
    commit, dockerfile_sha = archive_fixture(path, "amd64")
    identity = archive_identity(path, "fastqc", "amd64", commit, dockerfile_sha)
    assert identity["oci_config_architecture"] == "amd64"
    assert identity["oci_identity"].startswith("sha256:")
