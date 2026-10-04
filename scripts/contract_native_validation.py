"""Explicitly executed, local-artifact-only native validation of seven contracts.

Planning and reconciliation never execute containers. Execution is a separately
gated CLI action; this module has no publication or registry-write operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from scripts.base_image_identity import extract_base_digest
from scripts.pilot_integrity import archive_identity, validate_runner_evidence
from scripts.sif_identity import canonical_json_bytes, identity_metadata
from scripts.validation_contracts import sha256_file, validate_contract
from scripts.validation_smoke import validate_smoke_outputs, validate_version_evidence


TOOLS = ("3ddna_extra", "abricate", "accelerate", "agat_extra", "aicsimageio_extra", "airr_extra", "alevin_fry")
RUNNERS = {"amd64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"}
GATES = ("runner", "oci_identity", "oci_architecture", "oci_executable", "oci_package", "oci_version", "oci_smoke",
         "sif_structure", "sif_architecture", "sif_executable", "sif_package", "sif_version", "sif_smoke", "oci_sif_binding", "identity", "provenance")


class ValidationError(ValueError):
    """Missing or contradictory evidence; never a warning-only outcome."""


def contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValidationError("invalid relative input path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValidationError("input escapes repository")
    return path


def fixture_hashes(contract: dict, repo: Path) -> dict:
    result = {}
    for name in contract["smoke_inputs"]:
        if name == "in-memory deterministic 2x3 uint8 array" and contract["tool_id"] == "aicsimageio_extra":
            continue  # The reviewed command itself constructs this input.
        result[name] = sha256_file(contained(repo, name))
    if contract.get("fixture_sha256") and result != contract["fixture_sha256"]:
        raise ValidationError("declared fixture hashes differ from source")
    return result


def package_pin(contract: dict, arch: str) -> dict:
    pins = contract["pinning"]["package_artifacts"]
    declaration = pins.get(arch, pins.get("noarch", ""))
    match = re.fullmatch(r"([a-zA-Z0-9_.-]+\.(?:conda|tar\.bz2));(sha256|md5):([0-9a-f]+)", declaration)
    if not match or len(match[3]) != {"sha256": 64, "md5": 32}[match[2]]:
        raise ValidationError("missing immutable package artifact pin")
    filename, algorithm, checksum = match.groups()
    name, version = contract["pinning"]["package_name"], contract["scientific_version"]
    stem = filename.removesuffix(".conda").removesuffix(".tar.bz2")
    prefix = f"{name}-{version}-"
    if not stem.startswith(prefix) or not stem[len(prefix):]:
        raise ValidationError("package pin name/version mismatch")
    return {"name": name, "version": version, "build": stem[len(prefix):], "fn": filename,
            "subdir": "noarch" if arch not in pins else {"amd64": "linux-64", "arm64": "linux-aarch64"}[arch],
            algorithm: checksum}


def verify_package(metadata: dict, contract: dict, arch: str) -> dict:
    expected = package_pin(contract, arch)
    if not isinstance(metadata, dict) or any(metadata.get(key) != value for key, value in expected.items()):
        raise ValidationError("installed package differs from pinned name/version/build/subdir/checksum")
    return expected


def load_contract(repo: Path, tool: str) -> dict:
    if tool not in TOOLS:
        raise ValidationError("tool outside exact approved canary")
    value = json.loads(contained(repo, f"validation-contracts/schema-v1/{tool}.json").read_text())
    validate_contract(value, repo)
    if value["tool_id"] != tool or value["contract_status"] != "CONTRACT_READY":
        raise ValidationError("wrong or blocked contract")
    if value["expected_architectures"] != ["amd64", "arm64"] or any(
        value[key] is not True for key in ("native_amd64_expected", "native_arm64_expected")
    ):
        raise ValidationError("both native architectures must be expected")
    if value["runtime_network_required"] is not False:
        raise ValidationError("only network-free contracts are approved")
    for field in ("version_command", "smoke_command"):
        if not isinstance(value[field], list) or not value[field] or any(
            not isinstance(arg, str) or not arg or "\x00" in arg for arg in value[field]
        ):
            raise ValidationError("commands must be nonempty argument arrays")
    if value.get("version_output_source") not in ("stdout", "stderr", "either") or not value.get("version_observation_parser"):
        raise ValidationError("missing version stream contract")
    for arch in RUNNERS:
        package_pin(value, arch)
    fixture_hashes(value, repo)
    return value


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(canonical_json_bytes({k: v for k, v in plan.items() if k != "plan_sha256"})).hexdigest()


def make_plan(repo: Path, commit: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValidationError("source must be a full Git SHA")
    entries = []
    packages = set()
    for tool in TOOLS:
        contract = load_contract(repo, tool)
        if contract["package"] in packages:
            raise ValidationError("package collision")
        packages.add(contract["package"])
        for arch, runner in RUNNERS.items():
            entries.append({"tool_id": tool, "arch": arch, "runner": runner,
                            "contract_sha256": contract["validation_contract_sha256"],
                            "dockerfile_sha256": contract["dockerfile_sha256"],
                            "fixture_sha256": fixture_hashes(contract, repo)})
    value = {"validation_plan_schema_version": 1, "source_commit": commit,
             "publication_authorized": False, "candidate_count": 14, "entries": entries}
    value["plan_sha256"] = plan_hash(value)
    return value


def validate_plan(plan: dict, repo: Path, expected_hash: str) -> None:
    if not isinstance(plan, dict) or plan.get("plan_sha256") != expected_hash or plan_hash(plan) != expected_hash:
        raise ValidationError("plan hash mismatch")
    if plan.get("publication_authorized") is not False or type(plan.get("validation_plan_schema_version")) is not int:
        raise ValidationError("invalid authorization or schema type")
    if make_plan(repo, plan["source_commit"]) != plan:
        raise ValidationError("plan membership or current source evidence mismatch")


def select_entry(plan: dict, repo: Path, digest: str, tool: str, arch: str) -> tuple[dict, dict]:
    validate_plan(plan, repo, digest)
    matches = [entry for entry in plan["entries"] if (entry["tool_id"], entry["arch"]) == (tool, arch)]
    if len(matches) != 1:
        raise ValidationError("candidate missing or duplicated")
    return matches[0], load_contract(repo, tool)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


class Commands:
    """Bounded argv execution with durable logs, no shell interpolation."""

    def __init__(self, output: Path):
        self.output = output / "commands"
        self.output.mkdir(parents=True)
        self.count = 0

    def __call__(self, argv: list[str], *, timeout: int = 60) -> dict:
        self.count += 1
        stem = self.output / f"{self.count:03}"
        start = time.monotonic()
        timed_out = False
        with stem.with_suffix(".stdout").open("w") as stdout, stem.with_suffix(".stderr").open("w") as stderr:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, text=True, start_new_session=True)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                returncode = process.wait()
        result = {"argv": argv, "returncode": returncode, "elapsed_seconds": time.monotonic() - start, "timed_out": timed_out}
        write_json(stem.with_suffix(".json"), result)
        if returncode != 0 or timed_out:
            raise ValidationError(f"command failed; see {stem.name} logs")
        for stream in ("stdout", "stderr"):
            path = stem.with_suffix(f".{stream}")
            if path.stat().st_size > 1024 * 1024:
                raise ValidationError("command evidence exceeds 1 MiB")
            result[stream] = path.read_text()
        return result


EXECUTABLE_PROBE = r'''set -eu
p=$(command -v "$1")
p=$(readlink -f "$p")
printf 'executable=%s\n' "$p"
magic=$(od -An -t u1 -N4 "$p" | tr -s ' ' | sed 's/^ //;s/ $//')
if test "$magic" != '127 69 76 70'; then
  IFS= read -r line < "$p"
  case "$line" in '#!'*) line=${line#\#!} ;; *) exit 1 ;; esac
  set -- $line
  case "$1" in */env) shift ;; esac
  test "$#" = 1
  p=$(command -v "$1")
  p=$(readlink -f "$p")
  printf 'interpreter=%s\n' "$p"
  magic=$(od -An -t u1 -N4 "$p" | tr -s ' ' | sed 's/^ //;s/ $//')
fi
test "$magic" = '127 69 76 70'
printf 'machine='
od -An -t u1 -j18 -N2 "$p" | tr -s ' ' | sed 's/^ //;s/ $//'
'''


def validate_executable(result: dict, arch: str) -> dict:
    text = result["stdout"]
    if not re.search(r"(?m)^executable=/[^\r\n]+$", text):
        raise ValidationError("missing executable path")
    machines = re.findall(r"(?m)^machine=([0-9]+) ([0-9]+)$", text)
    if machines != [{"amd64": ("62", "0"), "arm64": ("183", "0")}[arch]]:
        raise ValidationError("executable/interpreter ELF architecture mismatch")
    return {"status": "PASS", "architecture": arch, "output": text}


def smoke_result(contract: dict, repo: Path, work: Path, result: dict) -> dict:
    if result["returncode"] != 0 or result["elapsed_seconds"] > 60:
        raise ValidationError("failed or unbounded smoke")
    tool = contract["tool_id"]
    if tool == "alevin_fry":
        return validate_smoke_outputs(contract, repo, work, returncode=result["returncode"], elapsed_seconds=result["elapsed_seconds"])
    if tool == "agat_extra":
        path = work / "smoke.gff3"
        if not path.is_file() or not path.resolve().is_relative_to(work.resolve()) or path.stat().st_size > 1024 * 1024:
            raise ValidationError("missing or unsafe AGAT output")
        text = path.read_text()
        rows = [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]
        if not text.startswith("##gff-version 3") or not rows or any(len(row) != 9 for row in rows):
            raise ValidationError("invalid AGAT GFF3 output")
        ids = {attribute[3:] for row in rows for attribute in row[8].split(";") if attribute.startswith("ID=")}
        if not {"gene1", "transcript1", "exon1", "exon2"}.issubset(ids):
            raise ValidationError("AGAT output missing expected feature IDs")
    elif tool in ("3ddna_extra", "abricate"):
        text = result["stdout"] + "\n" + result["stderr"]
        if any(marker not in text for marker in contract["smoke_expected_outputs"]):
            raise ValidationError("smoke output markers missing")
    elif tool not in ("accelerate", "aicsimageio_extra", "airr_extra"):
        raise ValidationError("unsupported smoke semantics")
    return {"status": "PASS", "scope": contract["smoke_success_condition"]}


def runtime_argv(phase: str, artifact: str, arch: str, work: Path, fixtures: Path, argv: list[str]) -> list[str]:
    # micromamba run writes process bookkeeping below XDG_CACHE_HOME/mamba/proc.
    # Keep it in isolated writable /tmp, never the image's read-only /root cache.
    cache_environment = "XDG_CACHE_HOME=/tmp/omnibioai-validation-cache"
    if phase == "oci":
        prefix = ["docker", "run", "--rm", "--platform", f"linux/{arch}", "--network", "none", "--read-only",
                  "--tmpfs", "/tmp:rw", "--env", cache_environment, "--user", "0:0", "--volume", f"{work}:/work:rw",
                  "--volume", f"{fixtures}:/work/validation-contracts/fixtures:ro", "--workdir", "/work",
                  "--entrypoint", "micromamba", artifact]
    elif phase == "sif":
        prefix = ["sudo", "apptainer", "exec", "--cleanenv", "--containall", "--env", cache_environment, "--net", "--network", "none",
                  "--bind", f"{work}:/work:rw", "--bind", f"{fixtures}:/work/validation-contracts/fixtures:ro",
                  "--pwd", "/work", artifact, "micromamba"]
    else:
        raise ValidationError("unknown artifact phase")
    # SIF cleanenv must not depend on the Docker entrypoint setting MAMBA_ROOT_PREFIX.
    # micromamba 1.5.8 attaches all streams by default. Conda's --no-capture-output
    # is not a micromamba option and would be parsed as the command to execute.
    return prefix + ["run", "--prefix", "/opt/conda", *argv]


def inspect_sif(metadata: dict, expected_labels: dict, arch: str) -> None:
    try:
        labels = metadata["data"]["attributes"]["labels"]
        if labels["org.label-schema.build-arch"] != arch or any(labels.get(k) != v for k, v in expected_labels.items()):
            raise ValidationError("SIF structured architecture or inherited binding labels mismatch")
    except (KeyError, TypeError) as error:
        raise ValidationError("missing structured SIF label metadata") from error


def validate_entry(record: dict, entry: dict, plan: dict, contract: dict, repo: Path) -> None:
    if record.get("publication_authorized") is not False:
        raise ValidationError("publication is not authorized")
    if record.get("tool_id") != entry["tool_id"] or record.get("arch") != entry["arch"]:
        raise ValidationError("result identity mismatch")
    for key, value in (("source_commit", plan["source_commit"]), ("plan_sha256", plan["plan_sha256"]),
                       ("contract_sha256", entry["contract_sha256"]), ("dockerfile_sha256", entry["dockerfile_sha256"]),
                       ("fixture_sha256", entry["fixture_sha256"]), ("publication_authorized", False)):
        if key not in record or record[key] != value:
            raise ValidationError(f"result binding mismatch: {key}")
    if record.get("status") != "PASS" or set(record.get("gates", {})) != set(GATES):
        raise ValidationError("missing or failed terminal gates")
    if any(value != "PASS" for value in record["gates"].values()):
        raise ValidationError("failed gate")
    validate_runner_evidence(record["runner_evidence"])
    if record["runner_evidence"]["target_architecture"] != entry["arch"]:
        raise ValidationError("runner evidence target mismatch")
    identity = identity_metadata(record["candidate"])
    if identity != record["candidate"]:
        raise ValidationError("candidate canonical identity mismatch")
    expected = {"tool_id": entry["tool_id"], "target_architecture": entry["arch"], "source_commit": plan["source_commit"],
                "dockerfile_sha256": entry["dockerfile_sha256"], "sif_sha256": record["sif_sha256"],
                "oci_source_identity": record["oci_identity"]}
    if any(identity[key] != value for key, value in expected.items()):
        raise ValidationError("candidate content identity mismatch")
    if identity["build_inputs"]["validation_contract_sha256"] != entry["contract_sha256"]:
        raise ValidationError("identity contract mismatch")
    if identity["build_inputs"]["fixture_sha256"] != entry["fixture_sha256"] or identity["build_inputs"]["source_immutable_identity"] != contract["source_immutable_identity"]:
        raise ValidationError("identity fixture or source mismatch")
    if identity["scientific_version"] != contract["scientific_version"] or identity["expected_executable"] != contract["expected_executable"] or identity["dockerfile_path"] != contract["dockerfile_path"]:
        raise ValidationError("candidate scientific contract mismatch")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", identity["base_image_identity"]) or not identity["base_image_identity"].startswith(contract["base_image_reference"] + "@"):
        raise ValidationError("unbound base image identity")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", record["oci_identity"]):
        raise ValidationError("malformed OCI identity")
    if not re.fullmatch(r"[0-9a-f]{64}", record["oci_archive_sha256"]):
        raise ValidationError("missing archive identity")
    if record["conversion"] != {"archive_sha256": record["oci_archive_sha256"], "sif_sha256": record["sif_sha256"], "transport": "docker-archive"}:
        raise ValidationError("OCI/SIF conversion binding mismatch")
    inspect_sif(record["sif_inspect"], binding_labels(contract, plan, entry, identity["base_image_identity"]), entry["arch"])
    for phase in ("oci", "sif"):
        evidence = record[phase]
        if verify_package(evidence["package_metadata"], contract, entry["arch"]) != identity["build_inputs"]["package_artifact"]:
            raise ValidationError("installed package identity differs from candidate")
        if evidence["runtime_architecture"] != {"amd64": "x86_64", "arm64": "aarch64"}[entry["arch"]]:
            raise ValidationError("phase runtime architecture mismatch")
        if validate_executable({"stdout": evidence["executable"]["output"]}, entry["arch"]) != evidence["executable"]:
            raise ValidationError("phase executable mismatch")
        raw = evidence["version_raw"]
        if raw["argv"][-len(contract["version_command"]):] != contract["version_command"]:
            raise ValidationError("version command drift")
        if raw["timed_out"] is not False or not 0 <= raw["elapsed_seconds"] <= 60:
            raise ValidationError("version duration mismatch")
        checked = validate_version_evidence(contract, repo, stdout=raw["stdout"], stderr=raw["stderr"], returncode=raw["returncode"])
        if checked != evidence["version"] or identity["version_evidence"] != checked["observed_version"]:
            raise ValidationError("phase version evidence mismatch")
        smoke = evidence["smoke_raw"]
        if smoke["argv"][-len(contract["smoke_command"]):] != contract["smoke_command"] or type(smoke["returncode"]) is not int or smoke["returncode"] != 0 or smoke["timed_out"] is not False or not 0 <= smoke["elapsed_seconds"] <= 60:
            raise ValidationError("phase smoke command, status or duration mismatch")
        if contract["tool_id"] == "alevin_fry":
            if evidence["smoke"].get("smoke_outputs_verified") is not True or evidence["smoke"].get("contract_sha256") != entry["contract_sha256"]:
                raise ValidationError("missing bound numerical smoke evidence")
        elif evidence["smoke"].get("status") != "PASS" or evidence["smoke"].get("scope") != contract["smoke_success_condition"]:
            raise ValidationError("missing smoke result")
    if record["sbom_status"] != "SBOM_OPTIONAL_NOT_GENERATED":
        raise ValidationError("unexpected SBOM semantics")


def reconcile(plan: dict, repo: Path, digest: str, records: list[dict], evidence_roots: dict) -> dict:
    validate_plan(plan, repo, digest)
    keys = [(record.get("tool_id"), record.get("arch")) for record in records]
    expected = [(entry["tool_id"], entry["arch"]) for entry in plan["entries"]]
    if len(keys) != 14 or len(set(keys)) != 14 or set(keys) != set(expected):
        raise ValidationError("terminal matrix missing, duplicate or unexpected entries")
    if set(evidence_roots) != set(expected):
        raise ValidationError("missing or unexpected evidence directories")
    for entry in plan["entries"]:
        key = (entry["tool_id"], entry["arch"])
        record = records[keys.index(key)]
        contract = load_contract(repo, entry["tool_id"])
        validate_entry(record, entry, plan, contract, repo)
        for phase in ("oci", "sif"):
            # Recheck actual downloaded output, not just an asserted PASS flag.
            checked = smoke_result(contract, repo, evidence_roots[key] / f"work-{phase}", record[phase]["smoke_raw"])
            if checked != record[phase]["smoke"]:
                raise ValidationError("retained smoke evidence differs from result")
    return {"status": "PASS", "native_validated_entries": 14, "plan_sha256": digest,
            "publication_authorized": False, "release_complete": False}


def binding_labels(contract: dict, plan: dict, entry: dict, base_identity: str) -> dict:
    return {"org.omnibioai.tool": entry["tool_id"], "org.omnibioai.source-commit": plan["source_commit"],
            "org.omnibioai.dockerfile-sha256": entry["dockerfile_sha256"], "org.omnibioai.target-platform": f"linux/{entry['arch']}",
            "org.omnibioai.validation-contract-sha256": entry["contract_sha256"], "org.omnibioai.base-identity": base_identity}


def execute(plan: dict, repo: Path, digest: str, tool: str, arch: str, output: Path, commands=None) -> dict:
    entry, contract = select_entry(plan, repo, digest, tool, arch)
    output = output.resolve()
    if output.is_relative_to(repo.resolve()) or output.exists():
        raise ValidationError("use a new isolated output directory outside the repository")
    output.mkdir(parents=True)
    record = {"tool_id": tool, "arch": arch, "source_commit": plan["source_commit"], "plan_sha256": digest,
              "contract_sha256": entry["contract_sha256"], "dockerfile_sha256": entry["dockerfile_sha256"],
              "fixture_sha256": entry["fixture_sha256"], "publication_authorized": False, "status": "FAIL", "gates": {}}
    run = commands or Commands(output)
    try:
        if run(["git", "-C", str(repo), "rev-parse", "HEAD"])["stdout"].strip() != plan["source_commit"]:
            raise ValidationError("checkout source SHA differs from plan")
        if run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"])["stdout"].strip():
            raise ValidationError("tracked checkout changes must be reviewed before native execution")
        runner = {"runner": {"os": os.environ.get("RUNNER_OS"), "arch": os.environ.get("RUNNER_ARCH")},
                  "uname_m": run(["uname", "-m"])["stdout"].strip(), "target_architecture": arch, "execution_mode": "NATIVE"}
        validate_runner_evidence(runner)
        record["runner_evidence"] = runner
        record["gates"]["runner"] = "PASS"
        base = contract["base_image_reference"]
        base_identity = base + "@" + extract_base_digest(run(["docker", "buildx", "imagetools", "inspect", base])["stdout"])
        source = contained(repo, contract["dockerfile_path"]).read_text()
        if len(re.findall(r"(?m)^FROM ", source)) != 1 or f"FROM {base}\n" not in source:
            raise ValidationError("unreviewed Dockerfile base syntax")
        rendered = output / "Dockerfile.bound"
        rendered.write_text(source.replace(f"FROM {base}\n", f"FROM {base_identity}\n", 1))
        image = f"omnibioai-native-validation/{tool}:{plan['source_commit']}-{arch}"
        labels = binding_labels(contract, plan, entry, base_identity)
        flags = [arg for k, v in labels.items() for arg in ("--label", f"{k}={v}")]
        run(["docker", "buildx", "build", "--platform", f"linux/{arch}", "--file", str(rendered), "--tag", image, *flags, "--load", str(repo)], timeout=3600)
        archive = output / "oci-image.tar"
        run(["docker", "save", "--output", str(archive), image], timeout=300)
        oci = archive_identity(archive, tool, arch, plan["source_commit"], entry["dockerfile_sha256"])
        record.update(oci)
        record["gates"]["oci_identity"] = "PASS"
        sif = output / "validated.sif"
        fixtures = (repo / "validation-contracts/fixtures").resolve()
        for phase, artifact in (("oci", oci["oci_identity"]), ("sif", str(sif))):
            if phase == "sif":
                if sha256_file(archive) != oci["oci_archive_sha256"]:
                    raise ValidationError("archive changed before conversion")
                run(["sudo", "apptainer", "build", "--disable-cache", "--arch", arch, str(sif), f"docker-archive:{archive}"], timeout=1800)
                metadata = json.loads(run(["sudo", "apptainer", "inspect", "--json", "--labels", str(sif)])["stdout"])
                inspect_sif(metadata, labels, arch)
                record["sif_inspect"] = metadata
                record["gates"]["sif_structure"] = "PASS"
            work = output / f"work-{phase}"
            (work / "validation-contracts/fixtures").mkdir(parents=True)
            def invoke(argv):
                return run(runtime_argv(phase, artifact, arch, work, fixtures, argv), timeout=60)
            machine = invoke(["uname", "-m"])["stdout"].strip()
            if machine != {"amd64": "x86_64", "arm64": "aarch64"}[arch]:
                raise ValidationError("artifact runtime architecture mismatch")
            executable = validate_executable(invoke(["/bin/sh", "-c", EXECUTABLE_PROBE, "probe", contract["expected_executable"]]), arch)
            pin = package_pin(contract, arch)
            metadata_path = f"/opt/conda/conda-meta/{pin['name']}-{pin['version']}-{pin['build']}.json"
            package_metadata = json.loads(invoke(["/bin/cat", metadata_path])["stdout"])
            verify_package(package_metadata, contract, arch)
            version_raw = invoke(contract["version_command"])
            version = validate_version_evidence(contract, repo, stdout=version_raw["stdout"], stderr=version_raw["stderr"], returncode=version_raw["returncode"])
            smoke_raw = invoke(contract["smoke_command"])
            smoke = smoke_result(contract, repo, work, smoke_raw)
            record[phase] = {"runtime_architecture": machine, "executable": executable, "package_metadata": package_metadata, "version": version, "version_raw": version_raw,
                             "smoke": smoke, "smoke_raw": smoke_raw}
            for gate in ("architecture", "executable", "package", "version", "smoke"):
                record["gates"][f"{phase}_{gate}"] = "PASS"
        if sha256_file(archive) != oci["oci_archive_sha256"] or record["oci"]["version"]["observed_version"] != record["sif"]["version"]["observed_version"]:
            raise ValidationError("OCI/SIF binding mismatch")
        record["sif_sha256"] = sha256_file(sif)
        record["conversion"] = {"archive_sha256": oci["oci_archive_sha256"], "sif_sha256": record["sif_sha256"], "transport": "docker-archive"}
        record["gates"]["oci_sif_binding"] = "PASS"
        record["sbom_status"] = "SBOM_OPTIONAL_NOT_GENERATED"
        candidate = {"schema_version": "1", "tool_id": tool, "scientific_version": contract["scientific_version"], "target_architecture": arch,
                     "source_commit": plan["source_commit"], "dockerfile_path": contract["dockerfile_path"], "dockerfile_sha256": entry["dockerfile_sha256"],
                     "base_image_identity": base_identity, "oci_source_identity": oci["oci_identity"], "sif_sha256": record["sif_sha256"],
                     "expected_executable": contract["expected_executable"], "version_evidence": record["oci"]["version"]["observed_version"],
                     "smoke_status": "PASS", "provenance_status": "PASS", "build_inputs": {"validation_contract_sha256": entry["contract_sha256"],
                     "fixture_sha256": entry["fixture_sha256"], "source_immutable_identity": contract["source_immutable_identity"], "package_artifact": package_pin(contract, arch)}}
        record["candidate"] = identity_metadata(candidate)
        record["gates"]["identity"] = "PASS"
        record["gates"]["provenance"] = "PASS"
        record["status"] = "PASS"
        validate_entry(record, entry, plan, contract, repo)
        return record
    except Exception as error:
        record["status"] = "FAIL"
        record["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        write_json(output / "entry.json", record)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "execute", "reconcile"))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source-commit")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--tool", choices=TOOLS)
    parser.add_argument("--arch", choices=tuple(RUNNERS))
    parser.add_argument("--authorize-native-execution", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    records = None
    try:
        if args.command == "plan":
            plan = make_plan(args.repo, args.source_commit or "")
            write_json(args.plan, plan)
            if args.github_output:
                with args.github_output.open("a") as output:
                    output.write(f"matrix={json.dumps(plan['entries'], separators=(',', ':'))}\nplan_sha256={plan['plan_sha256']}\n")
        else:
            plan = json.loads(args.plan.read_text())
            if args.command == "execute":
                if not args.authorize_native_execution or not args.output or not args.tool or not args.arch:
                    raise ValidationError("explicit native execution authorization and exact entry/output required")
                execute(plan, args.repo, args.plan_sha256, args.tool, args.arch, args.output)
            else:
                if not args.records or not args.output:
                    raise ValidationError("records and summary output required")
                paths = sorted(args.records.rglob("entry.json"))
                records = [json.loads(path.read_text()) for path in paths]
                roots = {(record["tool_id"], record["arch"]): path.parent for record, path in zip(records, paths)}
                write_json(args.output, reconcile(plan, args.repo, args.plan_sha256, records, roots))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        if args.command == "reconcile" and args.output:
            write_json(args.output, {"status": "FAIL", "expected_entries": 14,
                                     "observed_records": len(records) if records is not None else None,
                                     "error": f"{type(error).__name__}: {error}", "release_complete": False,
                                     "publication_authorized": False})
        print(f"FAIL: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
