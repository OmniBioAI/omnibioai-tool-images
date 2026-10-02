#!/usr/bin/env python3
"""Resolve and validate the immutable digest of a remote base image.

Runs ``docker buildx imagetools inspect`` to full completion, captures its
complete output, and only then parses it for the authoritative top-level
digest. This avoids piping the live process into a line-oriented consumer
that could exit before the producer has finished writing.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

_DIGEST_LINE = re.compile(r"^\s*Digest:\s+(\S+)\s*$")
_VALID_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class BuildxInspectFailed(RuntimeError):
    """docker buildx imagetools inspect exited non-zero."""


class DigestParseFailed(RuntimeError):
    """No authoritative Digest: field could be found in buildx output."""


class DigestValidationFailed(RuntimeError):
    """A Digest: field was found but is malformed or ambiguous."""


def run_imagetools_inspect(base_ref: str) -> str:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", base_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BuildxInspectFailed(
            f"docker buildx imagetools inspect {base_ref!r} exited "
            f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def extract_base_digest(inspect_output: str) -> str:
    """Return the single authoritative top-level digest from captured output.

    Only the header block is considered (everything before the first blank
    line or the ``Manifests:`` section), matching the top-level OCI index (or
    single-manifest) digest that buildx reports first. Per-platform child
    manifest digests listed under ``Manifests:`` are intentionally ignored.
    """
    header_lines: list[str] = []
    for line in inspect_output.splitlines():
        stripped = line.strip()
        if stripped == "" or stripped == "Manifests:":
            break
        header_lines.append(line)

    candidates = [
        match.group(1)
        for line in header_lines
        if (match := _DIGEST_LINE.match(line)) is not None
    ]

    if not candidates:
        raise DigestParseFailed(
            "No 'Digest:' field found in buildx imagetools inspect header output"
        )

    unique = set(candidates)
    if len(unique) > 1:
        raise DigestValidationFailed(
            f"Multiple conflicting top-level digests found: {sorted(unique)}"
        )

    digest = candidates[0]
    if not _VALID_DIGEST.match(digest):
        raise DigestValidationFailed(
            f"Digest {digest!r} is not a canonical sha256:<64 lowercase hex> value"
        )

    return digest


def resolve_base_image_identity(base_ref: str) -> str:
    inspect_output = run_imagetools_inspect(base_ref)
    digest = extract_base_digest(inspect_output)
    return f"{base_ref}@{digest}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--github-env", default=os.environ.get("GITHUB_ENV"))
    args = parser.parse_args(argv)

    try:
        identity = resolve_base_image_identity(args.base_ref)
    except (BuildxInspectFailed, DigestParseFailed, DigestValidationFailed) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    line = f"PILOT_BASE_IMAGE_IDENTITY={identity}"
    if args.github_env:
        with open(args.github_env, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    else:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
