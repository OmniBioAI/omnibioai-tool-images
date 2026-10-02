"""Regression coverage for base-image digest capture.

These tests specifically guard against reintroducing an early-closing
producer/parser pipeline (``docker buildx imagetools inspect | awk ...``)
that can fail the build even when a valid digest was available, and against
any weakening of fail-closed digest validation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import base_image_identity as bii

BASE_REF = "docker.io/library/python:3.11-slim-bookworm"
VALID_DIGEST = "sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb"
OTHER_DIGEST = "sha256:0000000000000000000000000000000000000000000000000000000000000f"


def header(digest: str = VALID_DIGEST) -> str:
    return (
        f"Name:      {BASE_REF}\n"
        "MediaType: application/vnd.oci.image.index.v1+json\n"
        f"Digest:    {digest}\n"
        "           \n"
    )


def full_output(digest: str = VALID_DIGEST, *, platforms: int = 2) -> str:
    out = header(digest)
    out += "Manifests: \n"
    for i in range(platforms):
        out += (
            f"  Name:        {BASE_REF}@sha256:{i:064d}\n"
            "  MediaType:   application/vnd.oci.image.manifest.v1+json\n"
            f"  Platform:    linux/amd64\n"
            "  Annotations: \n"
            "    org.opencontainers.image.version:         3.11.17-slim-bookworm\n"
        )
    return out


# 1 + 11: valid output with digest near the beginning and substantial trailing output
def test_valid_output_with_substantial_trailing_output():
    output = full_output(platforms=50)
    assert len(output) > 4096
    assert bii.extract_base_digest(output) == VALID_DIGEST


# 2: parser consumes safely without ever invoking a subprocess (no pipe to fail)
def test_parser_is_pure_and_never_shells_out(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("extract_base_digest must not invoke subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    assert bii.extract_base_digest(full_output()) == VALID_DIGEST


# 3: valid digest extraction (minimal header only, no Manifests section)
def test_valid_digest_extraction_header_only():
    assert bii.extract_base_digest(header()) == VALID_DIGEST


# 4: empty buildx output
def test_empty_output_fails_closed():
    with pytest.raises(bii.DigestParseFailed):
        bii.extract_base_digest("")


# 5: buildx command failure propagates and is distinguishable
def test_buildx_command_failure_is_fatal_and_distinct(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="unauthorized: access denied"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(bii.BuildxInspectFailed) as excinfo:
        bii.run_imagetools_inspect(BASE_REF)
    assert "unauthorized" in str(excinfo.value)


# 6: output with no digest at all
def test_output_with_no_digest_fails_closed():
    output = "Name:      docker.io/library/python:3.11-slim-bookworm\nMediaType: x\n"
    with pytest.raises(bii.DigestParseFailed):
        bii.extract_base_digest(output)


# 7: malformed digest (wrong length)
def test_malformed_digest_wrong_length_fails_closed():
    output = header("sha256:deadbeef")
    with pytest.raises(bii.DigestValidationFailed):
        bii.extract_base_digest(output)


# 8: uppercase / noncanonical digest is rejected
def test_uppercase_digest_fails_closed():
    output = header("sha256:" + "A" * 64)
    with pytest.raises(bii.DigestValidationFailed):
        bii.extract_base_digest(output)


# 9: multiple identical digest observations in the header are fine
def test_duplicate_identical_header_digest_lines_are_accepted():
    output = (
        f"Name:      {BASE_REF}\n"
        f"Digest:    {VALID_DIGEST}\n"
        f"Digest:    {VALID_DIGEST}\n"
        "           \n"
    )
    assert bii.extract_base_digest(output) == VALID_DIGEST


# 10: multiple conflicting digest observations fail closed, no silent choice
def test_conflicting_header_digests_fail_closed():
    output = (
        f"Name:      {BASE_REF}\n"
        f"Digest:    {VALID_DIGEST}\n"
        f"Digest:    {OTHER_DIGEST}\n"
        "           \n"
    )
    with pytest.raises(bii.DigestValidationFailed):
        bii.extract_base_digest(output)


# 11: trailing per-platform manifests with their own differing digests are ignored
def test_per_platform_digests_do_not_override_top_level_digest():
    output = full_output(digest=VALID_DIGEST, platforms=3)
    assert OTHER_DIGEST not in output
    assert bii.extract_base_digest(output) == VALID_DIGEST


# 12: whitespace variation supported by actual buildx output (tabs / extra padding)
def test_whitespace_variation_in_digest_field_is_tolerated():
    output = f"Name:\tdocker.io/x\nDigest:\t\t{VALID_DIGEST}   \n\n"
    assert bii.extract_base_digest(output) == VALID_DIGEST


# 13: full set -euo pipefail execution context, no pipe to fail, no registry write
def test_module_runs_cleanly_under_pipefail_with_fake_docker(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        f'printf "Name:      {BASE_REF}\\n"\n'
        f'printf "Digest:    {VALID_DIGEST}\\n"\n'
        'printf "           \\n"\n'
        'printf "Manifests: \\n"\n'
        # 2000 platform-like entries (~130KB) to exceed the OS pipe buffer
        # many times over -- a piped `| awk ... exit` consumer would have
        # raced the producer here; capture-then-parse must not care.
        "i=0\n"
        "while [ $i -lt 2000 ]; do\n"
        '  printf "  Platform:    linux/amd64\\n"\n'
        '  printf "  Annotations:    org.opencontainers.image.version=3.11.17\\n"\n'
        "  i=$((i + 1))\n"
        "done\n"
        "exit 0\n"
    )
    fake_docker.chmod(0o755)

    github_env = tmp_path / "github_env.txt"
    github_env.write_text("")

    script = (
        "set -euo pipefail\n"
        f'PATH="{fake_bin}:$PATH" python3 -m scripts.base_image_identity '
        f'--base-ref {BASE_REF} --github-env "{github_env}"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert github_env.read_text().strip() == f"PILOT_BASE_IMAGE_IDENTITY={BASE_REF}@{VALID_DIGEST}"


# 14: no registry write reachable during digest resolution
def test_module_never_references_registry_write_operations():
    source = Path(bii.__file__).read_text()
    for forbidden in ("push", "login", "oras", "imagetools create"):
        assert forbidden not in source.lower()


def test_resolve_base_image_identity_end_to_end(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=full_output(), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert bii.resolve_base_image_identity(BASE_REF) == f"{BASE_REF}@{VALID_DIGEST}"


def test_main_fails_closed_and_writes_nothing_on_buildx_failure(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="EOF"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    github_env = tmp_path / "env.txt"
    github_env.write_text("")
    rc = bii.main(["--base-ref", BASE_REF, "--github-env", str(github_env)])
    assert rc == 1
    assert github_env.read_text() == ""
