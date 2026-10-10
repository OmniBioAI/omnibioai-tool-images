"""Dedicated unit tests for scripts/pilot_integrity.py.

Covers archive_identity()'s fail-closed Docker archive parsing, the
standalone validate_runner_evidence()/validate_evidence() branches not
already exercised by tests/test_multiarch_pilot_safety.py and
tests/test_multiarch_pilot_preflight.py, and the `main()` CLI entrypoint.
"""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import pilot_integrity
from scripts.pilot_integrity import archive_identity, main, validate_evidence, validate_runner_evidence

from test_multiarch_pilot_safety import archive_fixture, complete_record


def build_tar(path: Path, members: list[tuple[str, bytes, int]]) -> None:
    """members: list of (name, payload, type) where type is a tarfile *TYPE constant."""
    with tarfile.open(path, "w") as tar:
        for name, payload, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))


# ---------------------------------------------------------------------------
# archive_identity(): fail-closed Docker archive parsing
# ---------------------------------------------------------------------------

def test_archive_missing_manifest_rejected(tmp_path):
    path = tmp_path / "oci.tar"
    build_tar(path, [("not-manifest.json", b"[]", tarfile.REGTYPE)])
    with pytest.raises(ValueError, match="exactly one manifest"):
        archive_identity(path, "fastqc", "amd64", "a" * 40, "b" * 64)


def test_archive_duplicate_manifest_rejected(tmp_path):
    path = tmp_path / "oci.tar"
    build_tar(path, [
        ("manifest.json", b"[]", tarfile.REGTYPE),
        ("manifest.json", b"[]", tarfile.REGTYPE),
    ])
    with pytest.raises(ValueError, match="exactly one manifest"):
        archive_identity(path, "fastqc", "amd64", "a" * 40, "b" * 64)


def test_archive_manifest_directory_cannot_be_read(tmp_path):
    path = tmp_path / "oci.tar"
    build_tar(path, [("manifest.json", b"", tarfile.DIRTYPE)])
    with pytest.raises(ValueError, match="no readable manifest"):
        archive_identity(path, "fastqc", "amd64", "a" * 40, "b" * 64)


@pytest.mark.parametrize("manifest_payload", [b"{}", b"[]", b"[{}, {}]"])
def test_archive_manifest_must_be_single_item_list(tmp_path, manifest_payload):
    path = tmp_path / "oci.tar"
    build_tar(path, [("manifest.json", manifest_payload, tarfile.REGTYPE)])
    with pytest.raises(ValueError, match="exactly one image"):
        archive_identity(path, "fastqc", "amd64", "a" * 40, "b" * 64)


@pytest.mark.parametrize("manifest_payload", [
    json.dumps([{"Config": 7}]).encode(),
    json.dumps([{"Config": "missing-from-archive.json"}]).encode(),
    json.dumps([{}]).encode(),
])
def test_archive_config_reference_must_exist_in_archive(tmp_path, manifest_payload):
    path = tmp_path / "oci.tar"
    build_tar(path, [("manifest.json", manifest_payload, tarfile.REGTYPE)])
    with pytest.raises(ValueError, match="config is missing"):
        archive_identity(path, "fastqc", "amd64", "a" * 40, "b" * 64)


def test_archive_config_directory_cannot_be_read(tmp_path):
    path = tmp_path / "oci.tar"
    manifest_payload = json.dumps([{"Config": "config.json"}]).encode()
    build_tar(path, [
        ("manifest.json", manifest_payload, tarfile.REGTYPE),
        ("config.json", b"", tarfile.DIRTYPE),
    ])
    with pytest.raises(ValueError, match="config cannot be read"):
        archive_identity(path, "fastqc", "amd64", "a" * 40, "b" * 64)


def test_archive_manifest_config_identity_mutation_rejected(tmp_path, monkeypatch):
    """Defends against a manifest whose Config reference changes between reads."""
    path = tmp_path / "oci.tar"
    commit, dockerfile_sha = archive_fixture(path, "amd64")

    class FlakyConfig(dict):
        def __init__(self, value):
            super().__init__(Config=value)
            self.reads = 0

        def get(self, key, default=None):
            if key == "Config":
                self.reads += 1
                return "config.json" if self.reads == 1 else "mutated.json"
            return super().get(key, default)

    real_load = pilot_integrity.json.load

    def fake_load(member):
        value = real_load(member)
        if isinstance(value, list) and value and "Config" in value[0]:
            return [FlakyConfig(value[0]["Config"])]
        return value

    monkeypatch.setattr(pilot_integrity.json, "load", fake_load)
    with pytest.raises(ValueError, match="manifest/config identity mismatch"):
        archive_identity(path, "fastqc", "amd64", commit, dockerfile_sha)


def test_archive_label_mismatch_rejected(tmp_path):
    path = tmp_path / "oci.tar"
    commit, dockerfile_sha = archive_fixture(path, "amd64")
    # Correct archive, but request identity for a different tool: labels won't bind.
    with pytest.raises(ValueError, match="do not bind tool, source, Dockerfile, and architecture"):
        archive_identity(path, "samtools", "amd64", commit, dockerfile_sha)


def test_archive_platform_os_mismatch_rejected(tmp_path):
    path = tmp_path / "oci.tar"
    config = {"os": "windows", "architecture": "amd64"}
    build_tar(path, [
        ("manifest.json", json.dumps([{"Config": "config.json"}]).encode(), tarfile.REGTYPE),
        ("config.json", json.dumps(config).encode(), tarfile.REGTYPE),
    ])
    with pytest.raises(ValueError, match="archive platform mismatch"):
        archive_identity(path, "fastqc", "amd64", "a" * 40, "b" * 64)


def test_archive_identity_happy_path(tmp_path):
    path = tmp_path / "oci.tar"
    commit, dockerfile_sha = archive_fixture(path, "arm64")
    identity = archive_identity(path, "fastqc", "arm64", commit, dockerfile_sha)
    assert identity["oci_config_architecture"] == "arm64"
    assert identity["oci_identity"].startswith("sha256:")
    assert identity["docker_archive_manifest_count"] == 1


# ---------------------------------------------------------------------------
# validate_runner_evidence()
# ---------------------------------------------------------------------------

def test_validate_runner_evidence_rejects_invalid_architecture():
    with pytest.raises(ValueError, match="invalid target architecture"):
        validate_runner_evidence({"target_architecture": "mips"})


def test_validate_runner_evidence_requires_dict_runner():
    with pytest.raises(ValueError, match="missing actual GitHub runner evidence"):
        validate_runner_evidence({"target_architecture": "amd64", "runner": "not-a-dict"})


def test_validate_runner_evidence_rejects_os_arch_mismatch():
    with pytest.raises(ValueError, match="runner.os/runner.arch do not match"):
        validate_runner_evidence({
            "target_architecture": "amd64",
            "runner": {"os": "Linux", "arch": "ARM64"},
        })


def test_validate_runner_evidence_rejects_uname_execution_mode_mismatch():
    with pytest.raises(ValueError, match="uname -m/execution mode do not match"):
        validate_runner_evidence({
            "target_architecture": "amd64",
            "runner": {"os": "Linux", "arch": "X64"},
            "uname_m": "aarch64",
            "execution_mode": "NATIVE",
        })


# ---------------------------------------------------------------------------
# validate_evidence(): branches not already covered elsewhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("record", [[], "a string", 42, None])
def test_validate_evidence_requires_object(record):
    with pytest.raises(ValueError, match="provenance must be an object"):
        validate_evidence(record)


def test_validate_evidence_rejects_non_pass_verification_result():
    record = complete_record()
    record["verification_result"] = "FAIL"
    with pytest.raises(ValueError, match="input gate status is not PASS"):
        validate_evidence(record)


def test_validate_evidence_rejects_executable_type_mismatch():
    record = complete_record()
    record["executable_type"] = "not-a-real-type"
    with pytest.raises(ValueError, match="provenance executable type does not match manifest"):
        validate_evidence(record)


def test_validate_evidence_rejects_invalid_target_architecture():
    record = complete_record()
    record["target_architecture"] = "mips"
    with pytest.raises(ValueError, match="invalid target architecture"):
        validate_evidence(record)


def test_validate_evidence_rejects_dockerfile_tool_mismatch():
    record = complete_record()
    record["dockerfile"] = "dockerfiles/Dockerfile.samtools"
    with pytest.raises(ValueError, match="tool/Dockerfile provenance identity mismatch"):
        validate_evidence(record)


def test_validate_evidence_rejects_malformed_source_commit_sha():
    record = complete_record()
    record["source_commit_sha"] = "not-hex!!"
    with pytest.raises(ValueError, match="malformed source commit SHA"):
        validate_evidence(record)


@pytest.mark.parametrize("key", ["build_timestamp", "verification_timestamp"])
def test_validate_evidence_rejects_malformed_timestamps(key):
    record = complete_record()
    record[key] = "not-a-timestamp"
    with pytest.raises(ValueError, match=f"malformed {key}"):
        validate_evidence(record)


@pytest.mark.parametrize("inspect", [
    {"status": "FAIL", "metadata": {"a": 1}},
    {"status": "PASS", "metadata": {}},
    {"status": "PASS", "metadata": []},
    {"status": "PASS"},
    "not-a-dict",
])
def test_validate_evidence_rejects_bad_sif_inspect(inspect):
    record = complete_record()
    record["sif_inspect"] = inspect
    with pytest.raises(ValueError, match="structured SIF inspect evidence"):
        validate_evidence(record)


def test_validate_evidence_rejects_tool_version_summary_mismatch():
    record = complete_record()
    record["tool_version_output"] = "a completely different version string"
    with pytest.raises(ValueError, match="tool version summary differs"):
        validate_evidence(record)


def test_validate_evidence_rejects_malformed_oci_identity():
    record = complete_record()
    record["oci_identity"] = "not-a-digest"
    with pytest.raises(ValueError, match="malformed local OCI config digest"):
        validate_evidence(record)


@pytest.mark.parametrize("checksum", ["oci_archive_sha256", "sif_sha256", "dockerfile_sha256"])
def test_validate_evidence_rejects_malformed_checksums(checksum):
    record = complete_record()
    record[checksum] = "not-hex"
    with pytest.raises(ValueError, match=f"malformed {checksum}"):
        validate_evidence(record)


def test_validate_evidence_happy_path_passes():
    record = complete_record("multiqc", "arm64")
    validate_evidence(record)  # must not raise


# ---------------------------------------------------------------------------
# CLI entrypoint (main)
# ---------------------------------------------------------------------------

def run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["pilot_integrity.py", *argv])
    return main()


def test_main_archive_subcommand_prints_identity(monkeypatch, tmp_path, capsys):
    path = tmp_path / "oci.tar"
    commit, dockerfile_sha = archive_fixture(path, "amd64")
    code = run_main(monkeypatch, [
        "archive", "--archive", str(path), "--tool", "fastqc", "--arch", "amd64",
        "--commit", commit, "--dockerfile-sha256", dockerfile_sha,
    ])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["oci_config_architecture"] == "amd64"


def test_main_evidence_subcommand_validates_record(monkeypatch, tmp_path, capsys):
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(complete_record()))
    code = run_main(monkeypatch, ["evidence", "--record", str(record_path)])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "PROVENANCE_EVIDENCE_VALID"


def test_main_evidence_subcommand_rejects_invalid_record(monkeypatch, tmp_path):
    record_path = tmp_path / "record.json"
    bad = complete_record()
    del bad["sif_sha256"]
    record_path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        run_main(monkeypatch, ["evidence", "--record", str(record_path)])
