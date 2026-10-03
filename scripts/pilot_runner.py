#!/usr/bin/env python3
"""Native, local-only OCI verification and architecture-bound SIF validation."""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

from scripts.pilot_integrity import archive_identity, validate_evidence, validate_runner_evidence
from scripts.pilot_manifest import get_tool
from scripts.pilot_probe import fixtures, validate_probe_result
from scripts.sif_release import resolve_scientific_version


def command(argv: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, text=True, capture_output=capture)


def architecture_bound_dockerfile(source: Path, output: Path, base_identity: str) -> str:
    """Render an optionally digest-pinned Dockerfile without changing its source identity."""
    text = source.read_text()
    if not base_identity:
        return str(source)
    if not base_identity.startswith("docker.io/") or "@sha256:" not in base_identity:
        raise ValueError("base image identity must be a fully qualified SHA256 reference")
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("FROM "):
            parts = line.rstrip("\n").split()
            source_image = parts[1]
            requested_image = base_identity.split("@", 1)[0]
            normalized_source = source_image
            if normalized_source.startswith("python:"):
                normalized_source = "docker.io/library/" + normalized_source
            if normalized_source != requested_image:
                raise ValueError("resolved base identity does not match Dockerfile FROM")
            parts[1] = base_identity
            lines[index] = " ".join(parts) + ("\n" if line.endswith("\n") else "")
            output.write_text("".join(lines))
            return str(output)
    raise ValueError("Dockerfile has no FROM instruction to bind to base identity")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("PILOT_OUTPUT", "pilot-output")))
    parser.add_argument("--runner-only", action="store_true", help="Capture and validate native runner evidence without building")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        tool_id = os.environ["PILOT_TOOL"]
        arch = os.environ["PILOT_ARCH"]
        platform_name = os.environ["PILOT_PLATFORM"]
        dockerfile = os.environ["PILOT_DOCKERFILE"]
        commit = os.environ["PILOT_SOURCE_COMMIT"]
        runner = os.environ["PILOT_RUNNER"]
        tool = get_tool(tool_id)
        if platform_name != f"linux/{arch}" or dockerfile != tool["dockerfile"]:
            raise ValueError("matrix platform or Dockerfile does not bind to manifest tool/architecture")
        machine = command(["uname", "-m"], capture=True).stdout.strip()
        actual_runner = {"runner": {"os": os.environ.get("PILOT_RUNNER_OS"),
                                    "arch": os.environ.get("PILOT_RUNNER_ARCH")},
                         "uname_m": machine, "target_architecture": arch, "execution_mode": "NATIVE"}
        runner_path = out / "runner-evidence.json"
        runner_path.write_text(json.dumps({**actual_runner, "verification_result": "UNVERIFIED"}, indent=2) + "\n")
        validate_runner_evidence(actual_runner)
        if runner != {"amd64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"}[arch]:
            raise ValueError("matrix runner is not the manifest-approved native runner")
        runner_path.write_text(json.dumps({**actual_runner, "verification_result": "PASS"}, indent=2) + "\n")
        print(json.dumps(actual_runner, sort_keys=True), flush=True)
        if args.runner_only:
            return 0

        work = out / "work"
        fixtures(work)
        entry_path = out / "entry.json"
        entry_path.write_text(json.dumps(tool, sort_keys=True))
        dockerfile_sha = hashlib.sha256((Path.cwd() / dockerfile).read_bytes()).hexdigest()
        base_image_identity = os.environ.get("PILOT_BASE_IMAGE_IDENTITY", "")
        rendered_dockerfile = work / "Dockerfile.architecture-bound"
        build_dockerfile = architecture_bound_dockerfile(
            Path.cwd() / dockerfile, rendered_dockerfile, base_image_identity
        )
        rendered_dockerfile_sha = hashlib.sha256(Path(build_dockerfile).read_bytes()).hexdigest()
        image = f"omnibioai-pilot/{tool_id}:{commit}-{arch}"
        archive = out / "oci-image.tar"
        sif = out / f"{tool_id}_{arch}.sif"
        gates: dict = {}

        build_labels = [
            "--label", f"org.omnibioai.tool={tool_id}",
            "--label", f"org.omnibioai.source-commit={commit}",
            "--label", f"org.omnibioai.dockerfile-sha256={dockerfile_sha}",
            "--label", f"org.omnibioai.target-platform={platform_name}",
        ]
        command(["docker", "buildx", "build", "--platform", platform_name,
                 "--file", build_dockerfile, "--tag", image, *build_labels, "--load", "."])
        command(["docker", "save", "--output", str(archive), image])
        identity = archive_identity(archive, tool_id, arch, commit, dockerfile_sha)
        image_id = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture=True).stdout.strip()
        if not image_id.startswith("sha256:"):
            raise ValueError("local image inspect did not provide immutable image config identity")
        gates["oci_image_identity"] = {"status": "PASS", "config": identity["oci_config_architecture"], "os": identity["oci_config_os"]}

        command(["docker", "run", "--rm", "--platform", platform_name,
                 "--volume", f"{work}:/pilot/work:rw",
                 "--volume", f"{Path.cwd() / 'scripts/pilot_probe.py'}:/pilot/pilot_probe.py:ro",
                 "--volume", f"{entry_path}:/pilot/entry.json:ro",
                 "--env", f"PILOT_ARCH={arch}", "--entrypoint", "python3", image,
                 "/pilot/pilot_probe.py", "--entry", "/pilot/entry.json", "--arch", arch,
                 "--phase", "oci", "--workspace", "/pilot/work", "--output", "/pilot/work/oci.json"])
        # The container writes its machine-readable gate record only in the explicit workspace.
        oci = json.loads((work / "oci.json").read_text())
        validate_probe_result(oci, tool, arch, "oci")
        gates["oci_architecture"] = oci["architecture"]
        gates["oci_executable"] = oci["executable"]
        gates["oci_version"] = oci["version"]
        gates["oci_smoke"] = oci["smoke"]

        command(["sudo", "apptainer", "build", "--disable-cache", "--arch", arch,
                 str(sif), f"docker-archive:{archive}"])
        inspect = command(["sudo", "apptainer", "inspect", "--json", str(sif)], capture=True)
        inspect_json = json.loads(inspect.stdout)
        if not isinstance(inspect_json, (dict, list)) or not inspect_json:
            raise ValueError("Apptainer inspect returned no structured metadata")
        gates["sif_inspect"] = {"status": "PASS", "metadata": inspect_json}
        command(["sudo", "apptainer", "exec", "--cleanenv",
                 "--bind", f"{work}:/pilot/work", "--bind", f"{Path.cwd() / 'scripts/pilot_probe.py'}:/pilot/pilot_probe.py:ro",
                 "--bind", f"{entry_path}:/pilot/entry.json:ro", str(sif), "python3",
                 "/pilot/pilot_probe.py", "--entry", "/pilot/entry.json", "--arch", arch,
                 "--phase", "sif", "--workspace", "/pilot/work", "--output", "/pilot/work/sif.json"])
        sif_result = json.loads((work / "sif.json").read_text())
        validate_probe_result(sif_result, tool, arch, "sif")
        gates["sif_architecture"] = sif_result["architecture"]
        gates["sif_executable"] = sif_result["executable"]
        gates["sif_version"] = sif_result["version"]
        gates["sif_smoke"] = sif_result["smoke"]
        gates["sif_sha256"] = hashlib.sha256(sif.read_bytes()).hexdigest()
        gates["verification_result"] = "PASS"
        oci_scientific_version = resolve_scientific_version(tool, oci["version"]["meaningful_output"])
        sif_scientific_version = resolve_scientific_version(tool, sif_result["version"]["meaningful_output"])
        if oci_scientific_version != sif_scientific_version:
            raise ValueError("OCI and SIF scientific version evidence differs")
        package_contract = tool["package_identity"]
        package_name = package_contract["name"]
        if package_contract["kind"] == "dpkg":
            package_version = command(
                ["docker", "run", "--rm", "--platform", platform_name, image,
                 "dpkg-query", "-W", "-f=${Version}", package_name], capture=True
            ).stdout.strip()
            if not package_version:
                raise ValueError("package manager returned empty package version")
            package_identity = f"{package_name}={package_version}"
        elif package_contract["kind"] == "runtime_version":
            package_identity = f"{package_name}={oci_scientific_version}"
        else:
            raise ValueError("unsupported package identity contract")
        record = {
            "tool": tool_id, "source_commit_sha": commit, "dockerfile": dockerfile,
            "dockerfile_sha256": dockerfile_sha, "target_architecture": arch,
            "oci_identity": identity["oci_identity"], "oci_archive_sha256": identity["oci_archive_sha256"],
            "oci_child_digest": identity["oci_identity"],
            "oci_local_image_id": image_id, "tool_version_output": oci["version"]["output"],
            "scientific_version": oci_scientific_version,
            **actual_runner, "runner_label": runner, "executable_type": tool["executable_type"],
            "build_timestamp": datetime.now(timezone.utc).isoformat(),
            "verification_timestamp": datetime.now(timezone.utc).isoformat(),
            "base_image_identity": base_image_identity,
            "rendered_dockerfile_sha256": rendered_dockerfile_sha,
            "package_identity": package_identity,
            "sbom_status": "SBOM_OPTIONAL_NOT_GENERATED", **gates,
        }
        validate_evidence(record)
        record_path = out / "provenance.json"
        record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(f"PASS {tool_id} linux/{arch}; NATIVE; OCI {identity['oci_identity']}; SIF SHA256 {record['sif_sha256']}")
        return 0
    except Exception as exc:  # Write an explicit failed diagnostic; never turn it into PASS.
        (out / "failure.json").write_text(json.dumps({"verification_result": "FAIL", "error": str(exc)}, indent=2) + "\n")
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
