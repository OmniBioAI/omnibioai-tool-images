"""Coverage for the pilot PASS-provenance recorder.

``record_pilot_provenance.main`` is a thin, fail-closed CLI: it loads an
evidence record, validates it, cross-checks the SIF artifact's SHA256
against the gate declared in the evidence, stamps the record PASS, and
writes it out. The heavy evidence-shape validation itself lives in and is
covered by ``scripts.pilot_integrity``; here we stub ``validate_evidence``
so these tests exercise only this module's own SIF-binding and
output-writing logic, plus every one of its fail-closed branches.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import record_pilot_provenance as rpp

VALID_SHA = "a" * 64
OTHER_SHA = "b" * 64


def base_record(**overrides) -> dict:
    record = {
        "tool": "samtools",
        "source_commit_sha": "c" * 40,
        "dockerfile": "dockerfiles/Dockerfile.samtools",
        "dockerfile_sha256": "d" * 64,
        "target_architecture": "amd64",
        "oci_identity": "sha256:" + "e" * 64,
        "oci_archive_sha256": "f" * 64,
        "sif_sha256": VALID_SHA,
        "tool_version_output": "samtools 1.19",
        "uname_m": "x86_64",
        "execution_mode": "NATIVE",
        "executable_type": "native_binary",
        "build_timestamp": "2026-10-01T00:00:00+00:00",
        "verification_timestamp": "2026-10-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


def write_evidence(path: Path, record: dict) -> Path:
    path.write_text(json.dumps(record))
    return path


def run_main(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["record_pilot_provenance.py", *argv])
    return rpp.main()


def test_success_stamps_pass_and_writes_sorted_json(tmp_path, monkeypatch):
    sif_bytes = b"this is a fake SIF artifact\n" * 10
    actual_sha = hashlib.sha256(sif_bytes).hexdigest()
    sif_path = tmp_path / "tool.sif"
    sif_path.write_bytes(sif_bytes)

    evidence_path = write_evidence(
        tmp_path / "evidence.json", base_record(sif_sha256=actual_sha)
    )
    output_path = tmp_path / "nested" / "out" / "provenance.json"

    validate_mock = MagicMock(return_value=None)
    monkeypatch.setattr(rpp, "validate_evidence", validate_mock)

    rc = run_main(
        monkeypatch,
        ["--evidence", str(evidence_path), "--sif", str(sif_path), "--output", str(output_path)],
    )

    assert rc == 0
    assert validate_mock.call_count == 2

    written = output_path.read_text()
    assert written.endswith("\n")
    record = json.loads(written)
    assert record["sif_sha256"] == actual_sha
    assert record["sif_path"] == "tool.sif"
    assert record["verification_result"] == "PASS"
    assert record["sbom_status"] == "SBOM_OPTIONAL_NOT_GENERATED"
    assert record["verification_timestamp"].endswith("+00:00")

    # Written with sort_keys=True and indent=2, matching byte for byte.
    expected = json.dumps(record, indent=2, sort_keys=True) + "\n"
    assert written == expected

    # The second validate_evidence call must see the fully-updated record.
    second_call_record = validate_mock.call_args_list[1].args[0]
    assert second_call_record["verification_result"] == "PASS"
    assert second_call_record["sif_sha256"] == actual_sha


def test_missing_sif_file_fails_closed(tmp_path, monkeypatch):
    evidence_path = write_evidence(tmp_path / "evidence.json", base_record())
    missing_sif = tmp_path / "does-not-exist.sif"
    output_path = tmp_path / "out.json"

    monkeypatch.setattr(rpp, "validate_evidence", MagicMock(return_value=None))

    with pytest.raises(ValueError, match="SIF missing or empty"):
        run_main(
            monkeypatch,
            ["--evidence", str(evidence_path), "--sif", str(missing_sif), "--output", str(output_path)],
        )
    assert not output_path.exists()


def test_empty_sif_file_fails_closed(tmp_path, monkeypatch):
    evidence_path = write_evidence(tmp_path / "evidence.json", base_record())
    sif_path = tmp_path / "empty.sif"
    sif_path.write_bytes(b"")
    output_path = tmp_path / "out.json"

    monkeypatch.setattr(rpp, "validate_evidence", MagicMock(return_value=None))

    with pytest.raises(ValueError, match="SIF missing or empty"):
        run_main(
            monkeypatch,
            ["--evidence", str(evidence_path), "--sif", str(sif_path), "--output", str(output_path)],
        )
    assert not output_path.exists()


def test_malformed_sif_gate_fails_closed(tmp_path, monkeypatch):
    sif_bytes = b"content"
    sif_path = tmp_path / "tool.sif"
    sif_path.write_bytes(sif_bytes)

    evidence_path = write_evidence(
        tmp_path / "evidence.json", base_record(sif_sha256="not-a-hash")
    )
    output_path = tmp_path / "out.json"

    monkeypatch.setattr(rpp, "validate_evidence", MagicMock(return_value=None))

    with pytest.raises(ValueError, match="SIF SHA256 missing, malformed, or mismatched"):
        run_main(
            monkeypatch,
            ["--evidence", str(evidence_path), "--sif", str(sif_path), "--output", str(output_path)],
        )
    assert not output_path.exists()


def test_mismatched_sif_gate_fails_closed(tmp_path, monkeypatch):
    sif_bytes = b"content"
    sif_path = tmp_path / "tool.sif"
    sif_path.write_bytes(sif_bytes)

    # OTHER_SHA is well-formed (64 lowercase hex) but does not match the
    # actual SHA256 of sif_bytes, so this exercises the mismatch branch
    # rather than the malformed-format branch.
    assert hashlib.sha256(sif_bytes).hexdigest() != OTHER_SHA
    evidence_path = write_evidence(
        tmp_path / "evidence.json", base_record(sif_sha256=OTHER_SHA)
    )
    output_path = tmp_path / "out.json"

    monkeypatch.setattr(rpp, "validate_evidence", MagicMock(return_value=None))

    with pytest.raises(ValueError, match="SIF SHA256 missing, malformed, or mismatched"):
        run_main(
            monkeypatch,
            ["--evidence", str(evidence_path), "--sif", str(sif_path), "--output", str(output_path)],
        )
    assert not output_path.exists()


def test_initial_validation_failure_propagates_before_sif_checks(tmp_path, monkeypatch):
    evidence_path = write_evidence(tmp_path / "evidence.json", base_record())
    # Deliberately point --sif at a missing file: if validate_evidence's
    # failure were not raised first, we'd see the SIF error instead.
    sif_path = tmp_path / "missing.sif"
    output_path = tmp_path / "out.json"

    monkeypatch.setattr(
        rpp, "validate_evidence", MagicMock(side_effect=ValueError("bad evidence shape"))
    )

    with pytest.raises(ValueError, match="bad evidence shape"):
        run_main(
            monkeypatch,
            ["--evidence", str(evidence_path), "--sif", str(sif_path), "--output", str(output_path)],
        )
    assert not output_path.exists()


def test_second_validation_failure_prevents_output_write(tmp_path, monkeypatch):
    sif_bytes = b"content"
    sif_path = tmp_path / "tool.sif"
    sif_path.write_bytes(sif_bytes)
    actual_sha = hashlib.sha256(sif_bytes).hexdigest()

    evidence_path = write_evidence(
        tmp_path / "evidence.json", base_record(sif_sha256=actual_sha)
    )
    output_path = tmp_path / "out.json"

    validate_mock = MagicMock(side_effect=[None, ValueError("post-update evidence invalid")])
    monkeypatch.setattr(rpp, "validate_evidence", validate_mock)

    with pytest.raises(ValueError, match="post-update evidence invalid"):
        run_main(
            monkeypatch,
            ["--evidence", str(evidence_path), "--sif", str(sif_path), "--output", str(output_path)],
        )
    assert validate_mock.call_count == 2
    assert not output_path.exists()
