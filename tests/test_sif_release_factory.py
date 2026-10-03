"""Fail-closed contracts for the manifest-driven immutable SIF release factory."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import bedtools_canary
from scripts import pilot_probe
from scripts import sif_identity as identity
from scripts import sif_release as release
from scripts.pilot_manifest import (
    ARCHES,
    PACKAGE_ALLOWLIST,
    PILOT9_TOOLS,
    get_tool,
    load_and_validate,
    matrix,
    selected_matrix,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pilot-immutable-multiarch-release.yml"
ZERO = "0" * 64
ONE = "1" * 64
TWO = "2" * 64


VERSION_OUTPUTS = {
    "fastqc": "FastQC v0.12.1",
    "samtools": "samtools 1.16.1\nUsing htslib 1.16",
    "bcftools": "bcftools 1.16",
    "bedtools": "bedtools v2.30.0",
    "bwa": "Version: 0.7.17-r1188",
    "minimap2": "2.24-r1122",
    "multiqc": "multiqc, version 1.30",
    "muscle": "muscle 5.2.linux64 x86_64",
    "prodigal": "Prodigal V2.6.3",
    "vcftools": "VCFtools (0.1.16)",
}

def samtools_runtime_evidence(machine):
    raw = (
        "samtools 1.16.1\nUsing htslib 1.16\nCopyright (C) 2022 Genome Research Ltd.\n\n"
        "Samtools compilation details:\n"
        "    Features:       build=configure curses=yes \n"
        "    CC:             gcc\n"
        "    CPPFLAGS:       -frelease  -Wdate-time -D_FORTIFY_SOURCE=2\n"
        "    CFLAGS:         -g -O2 -ffile-prefix-map=\\xabBUILDPATH\\xbb=. "
        "-fstack-protector-strong -Wformat -Werror=format-security\n"
        "    LDFLAGS:        -Wl,-z,relro -Wl,-z,now\n"
        "    HTSDIR:         \n"
        "    LIBS:           \n"
        "    CURSES_LIB:     -lcurses\n\n"
        "HTSlib compilation details:\n"
        "    Features:       build=configure libcurl=yes S3=yes GCS=yes libdeflate=yes "
        "lzma=yes bzip2=yes plugins=yes plugin-path=/usr/local/lib/htslib:"
        f"/usr/local/libexec/htslib:/usr/lib/{machine}-linux-gnu/htslib: htscodecs=1.3.0\n"
        "    CC:             gcc\n"
        "    CPPFLAGS:       -I. -DSAMTOOLS=1 -Wdate-time -D_FORTIFY_SOURCE=2\n"
        "    CFLAGS:         -g -O2  -fstack-protector-strong -Wformat "
        "-Werror=format-security -ffat-lto-objects -ffat-lto-objects\n"
        "    LDFLAGS:        -Wl,-z,relro -Wl,-z,now -Wl,-flto "
        "-fvisibility=hidden -ffat-lto-objects -fvisibility=hidden -rdynamic\n\n"
        "HTSlib URL scheme handlers present:\n"
        "    built-in:\t preload, data, file\n"
        "    S3 Multipart Upload:\t s3w, s3w+https, s3w+http\n"
        "    Amazon S3:\t s3+https, s3+http, s3\n"
        "    libcurl:\t imaps, pop3, gophers, http, smb, gopher, sftp, ftps, imap, "
        "rtmpte, smtp, smtps, rtsp, rtmpe, scp, ftp, telnet, mqtt, rtmp, ldap, "
        "https, ldaps, rtmps, rtmpt, pop3s, rtmpts, tftp, smbs, dict\n"
        "    Google Cloud Storage:\t gs+http, gs+https, gs\n"
        "    crypt4gh-needed:\t crypt4gh\n"
        "    mem:\t mem"
    )
    return {
        "output": raw,
        "meaningful_output": raw.replace("\\xab", "").replace("\\xbb", ""),
    }


# Captured from run 37101905263.  These retain the observed multiline stream,
# stderr/stdout shape, architecture variation, and Samtools byte diagnostics.
RUNTIME_VERSION_EVIDENCE = {
    ("samtools", "amd64"): samtools_runtime_evidence("x86_64"),
    ("samtools", "arm64"): samtools_runtime_evidence("aarch64"),
    ("bcftools", "amd64"): {
        "output": "bcftools 1.16\nUsing htslib 1.16\nCopyright (C) 2022 Genome Research Ltd.\n"
        "License Expat: The MIT/Expat license\nThis is free software: you are free to change "
        "and redistribute it.\nThere is NO WARRANTY, to the extent permitted by law.",
        "meaningful_output": "bcftools 1.16\nUsing htslib 1.16\n"
        "Copyright (C) 2022 Genome Research Ltd.\nLicense Expat: The MIT/Expat license\n"
        "This is free software: you are free to change and redistribute it.\n"
        "There is NO WARRANTY, to the extent permitted by law.",
    },
    ("bcftools", "arm64"): {
        "output": "bcftools 1.16\nUsing htslib 1.16\nCopyright (C) 2022 Genome Research Ltd.\n"
        "License Expat: The MIT/Expat license\nThis is free software: you are free to change "
        "and redistribute it.\nThere is NO WARRANTY, to the extent permitted by law.",
        "meaningful_output": "bcftools 1.16\nUsing htslib 1.16\n"
        "Copyright (C) 2022 Genome Research Ltd.\nLicense Expat: The MIT/Expat license\n"
        "This is free software: you are free to change and redistribute it.\n"
        "There is NO WARRANTY, to the extent permitted by law.",
    },
    ("bwa", "amd64"): {
        "output": "[bwa_index] Pack FASTA... 0.00 sec\n"
        "[bwa_index] Construct BWT for the packed sequence...\n"
        "[bwa_index] 0.00 seconds elapse.\n[bwa_index] Update BWT... 0.00 sec\n"
        "[bwa_index] Pack forward-only FASTA... 0.00 sec\n"
        "[bwa_index] Construct SA from BWT and Occ... 0.00 sec\n"
        "[main] Version: 0.7.17-r1188\n[main] CMD: bwa index reference.fa\n"
        "[main] Real time: 0.004 sec; CPU: 0.010 sec",
        "meaningful_output": "[bwa_index] Pack FASTA... 0.00 sec\n"
        "[bwa_index] Construct BWT for the packed sequence...\n"
        "[bwa_index] 0.00 seconds elapse.\n[bwa_index] Update BWT... 0.00 sec\n"
        "[bwa_index] Pack forward-only FASTA... 0.00 sec\n"
        "[bwa_index] Construct SA from BWT and Occ... 0.00 sec\n"
        "[main] Version: 0.7.17-r1188\n[main] CMD: bwa index reference.fa\n"
        "[main] Real time: 0.004 sec; CPU: 0.010 sec",
    },
    ("bwa", "arm64"): {
        "output": "[bwa_index] Pack FASTA... 0.00 sec\n"
        "[bwa_index] Construct BWT for the packed sequence...\n"
        "[bwa_index] 0.00 seconds elapse.\n[bwa_index] Update BWT... 0.00 sec\n"
        "[bwa_index] Pack forward-only FASTA... 0.00 sec\n"
        "[bwa_index] Construct SA from BWT and Occ... 0.00 sec\n"
        "[main] Version: 0.7.17-r1188\n[main] CMD: bwa index reference.fa\n"
        "[main] Real time: 0.004 sec; CPU: 0.012 sec",
        "meaningful_output": "[bwa_index] Pack FASTA... 0.00 sec\n"
        "[bwa_index] Construct BWT for the packed sequence...\n"
        "[bwa_index] 0.00 seconds elapse.\n[bwa_index] Update BWT... 0.00 sec\n"
        "[bwa_index] Pack forward-only FASTA... 0.00 sec\n"
        "[bwa_index] Construct SA from BWT and Occ... 0.00 sec\n"
        "[main] Version: 0.7.17-r1188\n[main] CMD: bwa index reference.fa\n"
        "[main] Real time: 0.004 sec; CPU: 0.012 sec",
    },
    ("muscle", "amd64"): {
        "output": "muscle 5.2.linux64 [-]\nBuilt Oct  3 2026 06:11:14",
        "meaningful_output": "muscle 5.2.linux64 [-]\nBuilt Oct  3 2026 06:11:14",
    },
    ("muscle", "arm64"): {
        "output": "muscle 5.2.linux64 [-]\nBuilt Oct  3 2026 06:11:31",
        "meaningful_output": "muscle 5.2.linux64 [-]\nBuilt Oct  3 2026 06:11:31",
    },
}

SMOKE_OUTPUTS = {
    "samtools": "1",
    "bcftools": "chr1 10 . A G 60 PASS . GT 0/1",
    "bwa": "bwa-ok",
    "muscle": "muscle-ok",
}


def candidate(tool="fastqc", arch="amd64", version="0.12.1", **changes):
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    value = {
        "schema_version": "1",
        "tool_id": tool,
        "scientific_version": version,
        "target_architecture": arch,
        "source_commit": "a" * 40,
        "dockerfile_path": entry["dockerfile"],
        "dockerfile_sha256": ZERO,
        "base_image_identity": "docker.io/library/python:3.11-slim-bookworm@sha256:" + ONE,
        "oci_source_identity": "sha256:" + TWO,
        "sif_sha256": "3" * 64,
        "expected_executable": entry["expected_executable"],
        "version_evidence": VERSION_OUTPUTS[tool],
        "smoke_status": "PASS",
        "provenance_status": "PASS",
        "build_inputs": {
            "package_identity": f"{tool}={version}",
            "rendered_dockerfile_sha256": "4" * 64,
            "tool_runtime_commit": release.RUNTIME_COMMIT,
        },
        "operational_provenance": {"github_run_id": "1", "workflow_attempt": "1"},
    }
    value.update(changes)
    return value


def artifact(value, *, tag=None, metadata=True, sif_sha=None):
    return {
        "tag": tag or identity.immutable_tag(value),
        "manifest_digest": "sha256:" + "5" * 64,
        "sif_sha256": sif_sha or value["sif_sha256"],
        **({"identity_metadata": identity.identity_metadata(value)} if metadata else {}),
    }


def minimal_release_provenance(tool="fastqc"):
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    version = release.resolve_scientific_version(entry, VERSION_OUTPUTS[tool])
    return {
        "tool": tool,
        "dockerfile": entry["dockerfile"],
        "target_architecture": "amd64",
        "base_image_identity": "docker.io/library/python:3.11-slim-bookworm@sha256:" + ONE,
        "rendered_dockerfile_sha256": ZERO,
        "oci_identity": "sha256:" + TWO,
        "oci_child_digest": "sha256:" + TWO,
        "package_identity": f"{tool}={version}",
        "oci_version": {"output": VERSION_OUTPUTS[tool], "meaningful_output": VERSION_OUTPUTS[tool]},
        "sif_version": {"output": VERSION_OUTPUTS[tool], "meaningful_output": VERSION_OUTPUTS[tool]},
        "scientific_version": version,
        "sbom_status": "SBOM_OPTIONAL_NOT_GENERATED",
    }


def runtime_release_provenance(tool, arch, evidence, sif_sha256):
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    machine = "x86-64" if arch == "amd64" else "ARM aarch64"
    runner_arch = "X64" if arch == "amd64" else "ARM64"
    uname_m = "x86_64" if arch == "amd64" else "aarch64"
    executable = {
        "status": "PASS",
        "name": entry["expected_executable"],
        "executable_type": "native_binary",
        "path": f"/usr/bin/{entry['expected_executable']}",
        "resolved_path": f"/usr/bin/{entry['expected_executable']}",
        "file": f"/usr/bin/{entry['expected_executable']}: ELF 64-bit {machine} executable",
        "invocable": True,
        "interpreters": {},
    }
    version_gate = {
        "status": "PASS",
        "command": entry["version_command"],
        "returncode": 0,
        "output": evidence["output"],
        "meaningful_output": evidence["meaningful_output"],
    }
    smoke_gate = {
        "status": "PASS",
        "command": entry["smoke_command"],
        "returncode": 0,
        "output": SMOKE_OUTPUTS[tool],
        "meaningful_output": SMOKE_OUTPUTS[tool],
    }
    architecture = {"status": "PASS", "target": arch, "uname": uname_m}
    scientific_version = release.resolve_scientific_version(entry, evidence["meaningful_output"])
    package_name = entry["package_identity"]["name"]
    return {
        "tool": tool,
        "source_commit_sha": "a" * 40,
        "dockerfile": entry["dockerfile"],
        "dockerfile_sha256": ZERO,
        "target_architecture": arch,
        "oci_identity": "sha256:" + TWO,
        "oci_child_digest": "sha256:" + TWO,
        "oci_archive_sha256": "5" * 64,
        "sif_sha256": sif_sha256,
        "tool_version_output": evidence["output"],
        "scientific_version": scientific_version,
        "uname_m": uname_m,
        "execution_mode": "NATIVE",
        "runner": {"os": "Linux", "arch": runner_arch},
        "executable_type": "native_binary",
        "build_timestamp": "2026-10-03T06:00:00+00:00",
        "verification_timestamp": "2026-10-03T06:01:00+00:00",
        "verification_result": "PASS",
        "base_image_identity": "docker.io/library/python:3.11-slim-bookworm@sha256:" + ONE,
        "rendered_dockerfile_sha256": "4" * 64,
        "package_identity": f"{package_name}={scientific_version}",
        "sbom_status": "SBOM_OPTIONAL_NOT_GENERATED",
        "sif_inspect": {"status": "PASS", "metadata": {"data": "present"}},
        "oci_architecture": architecture,
        "oci_executable": executable,
        "oci_version": version_gate,
        "oci_smoke": smoke_gate,
        "sif_architecture": architecture,
        "sif_executable": executable,
        "sif_version": version_gate,
        "sif_smoke": smoke_gate,
    }


def test_exact_ten_tool_manifest_is_accepted():
    data = load_and_validate()
    assert len(data["tools"]) == len({tool["tool_id"] for tool in data["tools"]}) == 10


def test_duplicate_manifest_tool_is_rejected():
    data = copy.deepcopy(load_and_validate())
    data["tools"][-1]["tool_id"] = data["tools"][0]["tool_id"]
    with pytest.raises(ValueError, match="duplicate"):
        validate(data)


def test_unknown_tool_selection_is_rejected():
    with pytest.raises(ValueError, match="unknown or incomplete"):
        selected_matrix(load_and_validate(), "not_a_tool", "single")


def test_pilot9_selector_is_exact_and_excludes_bedtools():
    selected = selected_matrix(load_and_validate(), cohort="pilot9")
    assert {item["tool_id"] for item in selected} == set(PILOT9_TOOLS)
    assert "bedtools" not in {item["tool_id"] for item in selected}
    assert len(selected) == 18


def test_full_matrix_is_twenty_entries_and_two_exact_architectures():
    result = matrix(load_and_validate())
    assert len(result) == 20
    assert {item["platform"] for item in result} == set(ARCHES)
    assert all(sum(item["tool_id"] == tool for item in result) == 2 for tool in PACKAGE_ALLOWLIST)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("dockerfile", "dockerfiles/Dockerfile.does_not_exist", "missing/mismatched Dockerfile"),
        ("expected_executable", "", "empty/malformed"),
        ("version_command", "", "empty/malformed"),
        ("smoke_command", "", "empty/malformed"),
        ("scientific_version_pattern", "", "empty/malformed"),
    ],
)
def test_missing_mandatory_manifest_contract_fails_closed(field, value, match):
    data = copy.deepcopy(load_and_validate())
    data["tools"][0][field] = value
    with pytest.raises(ValueError, match=match):
        validate(data)


def test_unknown_or_duplicate_architecture_is_rejected():
    for arches in (["linux/amd64", "linux/riscv64"], ["linux/amd64", "linux/amd64"]):
        data = copy.deepcopy(load_and_validate())
        data["tools"][0]["architectures"] = arches
        with pytest.raises(ValueError, match="architectures"):
            validate(data)


def test_exact_package_mapping_is_manifest_bound_and_unique():
    data = load_and_validate()
    repositories = [tool["ghcr_repository"] for tool in data["tools"]]
    assert len(repositories) == len(set(repositories)) == 10
    assert {tool["tool_id"]: tool["ghcr_repository"] for tool in data["tools"]} == PACKAGE_ALLOWLIST
    changed = copy.deepcopy(data)
    changed["tools"][0]["ghcr_repository"] = PACKAGE_ALLOWLIST["samtools"]
    with pytest.raises(ValueError, match="exact canonical"):
        validate(changed)


@pytest.mark.parametrize("tool", list(VERSION_OUTPUTS))
def test_scientific_version_resolves_from_validated_runtime_output(tool):
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    version = release.resolve_scientific_version(entry, VERSION_OUTPUTS[tool])
    assert version and version[0].isdigit()


def test_missing_or_ambiguous_scientific_version_fails_closed():
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == "fastqc")
    with pytest.raises(release.ReleaseError):
        release.resolve_scientific_version(entry, "")
    with pytest.raises(release.ReleaseError, match="unambiguously"):
        release.resolve_scientific_version(entry, "FastQC v1.0\nFastQC v2.0")


def test_version_evidence_canonicalization_is_deterministic_idempotent_and_whitespace_stable():
    variants = ("Samtools 1.16.1\nUsing htslib 1.16", "Samtools 1.16.1\r\nUsing\thtslib 1.16")
    canonical = [release.canonicalize_version_evidence(value) for value in variants]
    assert canonical == ["Samtools 1.16.1 Using htslib 1.16"] * 2
    assert release.canonicalize_version_evidence(variants[0]) == canonical[0]
    assert release.canonicalize_version_evidence(canonical[0]) == canonical[0]
    assert release.canonicalize_version_evidence("Samtools 1.X") != (
        release.canonicalize_version_evidence("Samtools 1.Y")
    )


@pytest.mark.parametrize(
    "unsafe",
    ["ok\x00bad", "ok\x01bad", "ok\x0bbad", "ok\x0cbad", "ok\x1bbad",
     "ok\x7fbad", "ok\x85bad", "ok\ufffdbad", "ok\\xffbad", "ok\ud800bad"],
)
def test_version_evidence_canonicalization_rejects_unsafe_controls_and_decoding_artifacts(unsafe):
    with pytest.raises(release.ReleaseError, match="control|decoding artifact"):
        release.canonicalize_version_evidence(unsafe)


@pytest.mark.parametrize(("tool", "arch"), RUNTIME_VERSION_EVIDENCE)
def test_affected_runtime_version_evidence_builds_schema_v1_identity_and_provenance(
    tool, arch, tmp_path
):
    evidence = RUNTIME_VERSION_EVIDENCE[(tool, arch)]
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == tool)
    gate = {
        "status": "PASS",
        "command": entry["version_command"],
        "returncode": 0,
        **evidence,
    }
    pilot_probe.validate_command_evidence(gate, entry, "version")
    before = release.resolve_scientific_version(entry, evidence["meaningful_output"])
    canonical = release.canonicalize_version_evidence(evidence["meaningful_output"])
    after = release.resolve_scientific_version(entry, canonical)
    assert before == after
    assert canonical
    assert not any(ord(character) < 32 or ord(character) == 127 for character in canonical)

    sif = tmp_path / f"{tool}-{arch}.sif"
    sif.write_bytes(b"validated-sif")
    provenance = runtime_release_provenance(tool, arch, evidence, release.sha256_file(sif))
    provenance_path = tmp_path / f"{tool}-{arch}-provenance.json"
    provenance_path.write_text(json.dumps(provenance))
    output = tmp_path / f"{tool}-{arch}-candidate.json"
    metadata = release.prepare_candidate(tool, provenance_path, sif, output)
    assert metadata["schema_version"] == identity.SCHEMA_VERSION == "1"
    assert metadata["scientific_version"] == before
    assert metadata["version_evidence"] == canonical
    assert json.loads(provenance_path.read_text())["sif_version"]["output"] == evidence["output"]
    assert identity.identity_metadata(metadata) == metadata
    assert identity.evaluate_publication(metadata, {"artifacts": []}).decision is (
        identity.Decision.PUBLISH_NEW
    )


@pytest.mark.parametrize("tool", ["samtools", "bcftools", "bwa", "muscle"])
def test_affected_fixture_identity_remains_architecture_specific(tool, tmp_path):
    identities = []
    for arch in ("amd64", "arm64"):
        sif = tmp_path / f"{tool}-{arch}.sif"
        sif.write_bytes(f"{tool}-{arch}".encode())
        provenance = runtime_release_provenance(
            tool, arch, RUNTIME_VERSION_EVIDENCE[(tool, arch)], release.sha256_file(sif)
        )
        provenance_path = tmp_path / f"{tool}-{arch}.json"
        provenance_path.write_text(json.dumps(provenance))
        metadata = release.prepare_candidate(
            tool, provenance_path, sif, tmp_path / f"candidate-{tool}-{arch}.json"
        )
        identities.append(metadata["build_identity_sha256"])
    assert identities[0] != identities[1]


@pytest.mark.parametrize(
    "tool", ["fastqc", "bedtools", "minimap2", "multiqc", "prodigal", "vcftools"]
)
def test_unaffected_single_line_evidence_and_identity_input_are_unchanged(tool):
    value = VERSION_OUTPUTS[tool]
    assert release.canonicalize_version_evidence(value) == value
    original = candidate(tool=tool, version=release.resolve_scientific_version(get_tool(tool), value))
    canonical = copy.deepcopy(original)
    canonical["version_evidence"] = release.canonicalize_version_evidence(value)
    assert identity.identity_metadata(original) == identity.identity_metadata(canonical)


def test_bedtools_reference_build_identity_is_unchanged_by_canonicalization():
    value = candidate(tool="bedtools", version="2.30.0")
    assert release.canonicalize_version_evidence(value["version_evidence"]) == "bedtools v2.30.0"
    assert identity.build_identity_sha256(value) == (
        "5bfd3c83d10a2e68224f24ec2747545299b6c3b45bdac9b892ab21df201342fc"
    )


def test_identity_is_deterministic_architecture_bound_and_ignores_operational_fields():
    first = candidate()
    second = copy.deepcopy(first)
    second["operational_provenance"] = {"github_run_id": "999", "timestamp": "tomorrow"}
    assert identity.build_identity_sha256(first) == identity.build_identity_sha256(second)
    arm = candidate(arch="arm64")
    assert identity.build_identity_sha256(first) != identity.build_identity_sha256(arm)
    assert identity.immutable_tag(first) != identity.immutable_tag(arm)


def test_publication_decision_exact_match_skips():
    value = candidate()
    result = identity.evaluate_publication(value, {"artifacts": [artifact(value)]})
    assert result.decision is identity.Decision.SKIP_EXACT_MATCH


def test_publication_decision_collision_identity_mismatch_and_missing_metadata_block():
    value = candidate()
    tag = identity.immutable_tag(value)
    collision = artifact(candidate(arch="arm64"), tag=tag)
    assert identity.evaluate_publication(value, {"artifacts": [collision]}).decision in {
        identity.Decision.BLOCK_TAG_COLLISION, identity.Decision.BLOCK_IDENTITY_MISMATCH
    }
    wrong_identity = artifact(candidate(version="9.9"), tag="other-immutable")
    assert identity.evaluate_publication(value, {"artifacts": [wrong_identity]}).decision in {
        identity.Decision.PUBLISH_NEW, identity.Decision.BLOCK_IDENTITY_MISMATCH
    }
    missing = artifact(value, metadata=False)
    assert identity.evaluate_publication(value, {"artifacts": [missing]}).decision is identity.Decision.BLOCK_METADATA_MISSING


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.pop("base_image_identity"),
        lambda record: record.__setitem__("scientific_version", None),
        lambda record: record.__setitem__("oci_child_digest", "sha256:bad"),
    ],
)
def test_missing_null_or_malformed_release_provenance_fails(monkeypatch, mutation):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    record = minimal_release_provenance()
    mutation(record)
    with pytest.raises(release.ReleaseError):
        release.validate_release_provenance(record, release.release_spec("fastqc"))


def test_empty_sbom_cannot_pass(monkeypatch):
    monkeypatch.setattr(release, "validate_evidence", lambda unused: None)
    record = minimal_release_provenance()
    record["sbom_status"] = "SBOM_PASS"
    with pytest.raises(release.ReleaseError, match="SBOM_PASS"):
        release.validate_release_provenance(record, release.release_spec("fastqc"))


def test_mandatory_command_failure_and_non_utf8_diagnostics_are_not_hidden(monkeypatch, tmp_path):
    raw = subprocess.CompletedProcess([], 7, stdout=b"samtools 1.0\xff", stderr=b"fatal\xfe")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: raw)
    result = pilot_probe.run("samtools --version", tmp_path)
    assert result.returncode == 7
    assert "\\xff" in result.stdout
    entry = next(item for item in load_and_validate()["tools"] if item["tool_id"] == "samtools")
    with pytest.raises(ValueError, match="returncode=7"):
        pilot_probe.command_evidence(result, entry, "version")


def test_generic_secret_redaction_preserves_diagnostics(monkeypatch):
    token = "github_pat_" + "A" * 30
    stderr = f"permission_denied token={token} Authorization: Bearer {token}"
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "partial", stderr),
    )
    with pytest.raises(release.OrasInvocationFailed) as excinfo:
        release.run(["oras", "push", "x"])
    message = str(excinfo.value)
    assert "permission_denied" in message and "exit_code=1" in message
    assert token not in message and "[REDACTED]" in message


def test_staging_containment_accepts_normal_and_rejects_traversal_and_symlink(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    inside = root / "artifact.sif"
    inside.write_bytes(b"data")
    assert release.require_contained_staging_file(inside, root) == inside.resolve()
    outside = tmp_path / "outside.sif"
    outside.write_bytes(b"secret")
    with pytest.raises(release.ReleaseError, match="escapes"):
        release.require_contained_staging_file(root / ".." / "outside.sif", root)
    link = root / "link.sif"
    link.symlink_to(outside)
    with pytest.raises(release.ReleaseError, match="escapes"):
        release.require_contained_staging_file(link, root)


def test_oras_failure_propagates(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "denied"),
    )
    with pytest.raises(release.OrasInvocationFailed) as excinfo:
        release.run(["oras", "push", "x"])
    assert excinfo.value.classification == "ORAS_PERMISSION_FAILURE"


def test_publish_false_performs_zero_writes_and_cannot_invoke_push(tmp_path):
    payload = b"validated"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "fastqc.sif"
    sif.write_bytes(payload)
    evaluation = identity.Evaluation(
        identity.Decision.PUBLISH_NEW, "new", identity.immutable_tag(value),
        identity.build_identity_sha256(value),
    )
    commands = []
    result = release.release_candidate_for_spec(
        release.release_spec("fastqc"), candidate_path, sif, tmp_path / "result.json",
        publish_enabled=False,
        evaluate_live_fn=lambda unused: ({"artifacts": []}, evaluation),
        run_fn=lambda argv, **kwargs: commands.append(list(argv)),
    )
    assert result["decision"] == "WOULD_PUBLISH_NEW"
    assert result["write_performed"] is False
    assert commands == []
    source = (ROOT / "scripts/sif_release.py").read_text()
    workflow = WORKFLOW.read_text()
    assert source.count('"oras", "push"') == 1
    assert "docker push" not in source.lower() and "docker push" not in workflow.lower()


def test_retrieval_absence_and_remote_sha_mismatch_fail(tmp_path):
    value = candidate()
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    spec = release.release_spec("fastqc")
    with pytest.raises(release.ReleaseError, match="absent"):
        release.retrieve_and_verify_for_spec(
            spec, candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: None,
        )
    remote = {
        "manifest": {"annotations": identity.identity_annotations(value)},
        "identity_metadata": identity.identity_metadata(value),
        "sif_sha256": "9" * 64,
    }
    with pytest.raises(release.ReleaseError, match="descriptor differs"):
        release.retrieve_and_verify_for_spec(
            spec, candidate_path, tmp_path / "out.sif", tmp_path / "out.json",
            inspect_reference_fn=lambda repository, tag: remote,
        )


@pytest.mark.parametrize("failure", ["architecture", "version", "smoke"])
def test_retrieved_runtime_architecture_version_and_smoke_failures_propagate(tmp_path, monkeypatch, failure):
    payload = b"sif"
    value = candidate(sif_sha256=hashlib.sha256(payload).hexdigest())
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(identity.identity_metadata(value)))
    sif = tmp_path / "artifact.sif"
    sif.write_bytes(payload)
    workspace = tmp_path / "work"

    def fake_run(argv, **kwargs):
        if "inspect" in argv:
            return subprocess.CompletedProcess(argv, 0, '{"ok":true}', "")
        workspace.mkdir(exist_ok=True)
        output = VERSION_OUTPUTS["fastqc"] if failure != "version" else "FastQC v9.9"
        probe = {
            "architecture": {"status": "PASS", "target": "amd64", "uname": "x86_64"},
            "executable": {"status": "PASS"},
            "version": {"status": "PASS", "output": output, "meaningful_output": output},
            "smoke": {"status": "PASS"},
        }
        (workspace / "retrieved-sif-probe.json").write_text(json.dumps(probe))
        return subprocess.CompletedProcess(argv, 0, "", "")

    if failure in ("architecture", "smoke"):
        monkeypatch.setattr(
            release, "validate_probe_result",
            lambda *args: (_ for _ in ()).throw(ValueError(f"{failure} mismatch")),
        )
    else:
        monkeypatch.setattr(release, "validate_probe_result", lambda *args: None)
    expected = "scientific version differs" if failure == "version" else f"{failure} mismatch"
    with pytest.raises((release.ReleaseError, ValueError), match=expected):
        release.verify_retrieved_runtime_for_spec(
            release.release_spec("fastqc"), candidate_path, sif, workspace,
            tmp_path / "runtime.json", run_fn=fake_run,
        )


def test_bedtools_wrapper_preserves_reference_spec_and_identity_semantics():
    spec = release.release_spec("bedtools")
    assert bedtools_canary.TOOL == spec.tool_id == "bedtools"
    assert bedtools_canary.REPOSITORY == spec.repository
    assert bedtools_canary.SCIENTIFIC_VERSION == release.resolve_scientific_version(
        next(item for item in load_and_validate()["tools"] if item["tool_id"] == "bedtools"),
        VERSION_OUTPUTS["bedtools"],
    )
    value = candidate(tool="bedtools", version="2.30.0")
    assert identity.immutable_tag(value).startswith("sif-v1-2.30.0-amd64-")


def test_workflow_contract_is_manifest_driven_least_privilege_and_default_dry_run():
    data = yaml.safe_load(WORKFLOW.read_text())
    trigger = data.get("on", data.get(True))
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["publish"]["default"] is False
    assert inputs["cohort"]["default"] == "pilot9"
    job = data["jobs"]["build-verify-release-retrieve"]
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert job["strategy"]["fail-fast"] is False
    source = WORKFLOW.read_text()
    assert "scripts/pilot_manifest.py" in source and "--release-factory" in source
    assert "scripts.sif_release release" in source and '--publish "$PUBLISH"' in source
    assert "github.token" in source
    assert not any(marker in source for marker in ("secrets.PAT", "PERSONAL_ACCESS_TOKEN", "docker push", "oras push"))
    assert source.count("if: ${{ inputs.publish }}") == 3


def test_publish_true_cannot_select_bedtools_via_release_factory(tmp_path):
    output = tmp_path / "output"
    completed = subprocess.run(
        [
            "python3", "scripts/pilot_manifest.py", "--release-factory",
            "--publish", "true", "--cohort", "single", "--tool", "bedtools",
            "--github-output", str(output),
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert completed.returncode != 0
    assert "frozen Bedtools" in completed.stderr


def test_legacy_comparison_allows_only_candidate_and_rejects_mutation():
    before = {"repository": "r", "artifacts": [{"tag": "arm64", "manifest_digest": "sha256:a"}]}
    after = {"repository": "r", "artifacts": [
        {"tag": "arm64", "manifest_digest": "sha256:a"},
        {"tag": "sif-v1-x", "manifest_digest": "sha256:b"},
    ]}
    release.verify_legacy_preserved(before, after, "sif-v1-x")
    changed = copy.deepcopy(after)
    changed["artifacts"][0]["manifest_digest"] = "sha256:c"
    with pytest.raises(release.ReleaseError, match="deleted or mutated"):
        release.verify_legacy_preserved(before, changed, "sif-v1-x")
