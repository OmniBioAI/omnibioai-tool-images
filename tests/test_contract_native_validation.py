"""Synthetic adapter tests: no container or scientific tool is executed."""
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest
import yaml

from scripts import contract_native_validation as native

REPO = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
BANNERS = {"3ddna_extra": "version: 190716\nUSAGE\n3D de novo assembly\n", "abricate": "abricate 1.4.0\n",
           "accelerate": "- `Accelerate` version: 1.15.0\n", "agat_extra": "v1.7.0\n",
           "aicsimageio_extra": "4.14.0\n", "airr_extra": "2.0.0\n", "alevin_fry": "alevin-fry 0.18.3\n"}


@pytest.fixture(autouse=True)
def forbid_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("external execution forbidden in unit tests")
    monkeypatch.setattr(native.subprocess, "Popen", forbidden)


@pytest.fixture
def plan():
    return native.make_plan(REPO, COMMIT)


def test_plan_determinism_and_exact_membership(plan):
    assert plan == native.make_plan(REPO, COMMIT)
    assert len(plan["entries"]) == len({(e["tool_id"], e["arch"]) for e in plan["entries"]}) == 14
    assert {e["tool_id"] for e in plan["entries"]} == set(native.TOOLS)
    assert {e["runner"] for e in plan["entries"]} == set(native.RUNNERS.values())
    assert plan["publication_authorized"] is False


@pytest.mark.parametrize("tool", ["bedtools", "admixtools", "afterqc_extra", "alevinqc_extra", "unknown"])
def test_scope_rejection(tool):
    with pytest.raises(native.ValidationError):
        native.load_contract(REPO, tool)


@pytest.mark.parametrize("change", [
    lambda p: p["entries"].pop(), lambda p: p["entries"].append(p["entries"][0]),
    lambda p: p["entries"][0].update(arch="ppc64le"), lambda p: p["entries"][0].update(tool_id="bedtools"),
    lambda p: p.update(publication_authorized=True), lambda p: p["entries"][0].update(contract_sha256="b" * 64),
    lambda p: p["entries"][0].update(fixture_sha256={"unknown": "a" * 64}),
    lambda p: p["entries"][0].update(runner="ubuntu-latest"),
])
def test_changed_plan_rejected_even_after_rehash(plan, change):
    change(plan)
    plan["plan_sha256"] = native.plan_hash(plan)
    with pytest.raises(native.ValidationError):
        native.validate_plan(plan, REPO, plan["plan_sha256"])


def test_expected_plan_hash_is_mandatory(plan):
    with pytest.raises(native.ValidationError):
        native.validate_plan(plan, REPO, "b" * 64)


def test_execute_requires_explicit_flag(plan, tmp_path):
    path = tmp_path / "plan.json"
    native.write_json(path, plan)
    assert native.main(["execute", "--repo", str(REPO), "--plan", str(path), "--plan-sha256", plan["plan_sha256"],
                        "--tool", "abricate", "--arch", "amd64", "--output", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()


class FakeCommands:
    """Create synthetic artifacts and output; never invoke a process."""

    def __init__(self, tool, arch, output):
        self.tool, self.arch, self.output = tool, arch, output
        self.contract = native.load_contract(REPO, tool)
        self.calls, self.labels = [], {}

    def __call__(self, argv, *, timeout=60):
        self.calls.append(argv)
        text = ""
        if argv[0] == "git":
            text = COMMIT + "\n" if "rev-parse" in argv else ""
        elif argv == ["uname", "-m"]:
            text = "x86_64\n" if self.arch == "amd64" else "aarch64\n"
        elif argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            text = "Name: base\nDigest: sha256:" + "b" * 64 + "\n"
        elif argv[:3] == ["docker", "buildx", "build"]:
            self.labels = dict(argv[i + 1].split("=", 1) for i, arg in enumerate(argv) if arg == "--label")
            assert "--load" in argv and "--push" not in argv
        elif argv[:2] == ["docker", "save"]:
            config = json.dumps({"os": "linux", "architecture": self.arch, "config": {"Labels": self.labels}}).encode()
            files = {"manifest.json": json.dumps([{"Config": "config.json", "Layers": []}]).encode(), "config.json": config}
            with tarfile.open(self.output / "oci-image.tar", "w") as archive:
                for name, data in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        elif argv[:3] == ["sudo", "apptainer", "build"]:
            (self.output / "validated.sif").write_bytes(b"synthetic SIF; not a container")
        elif argv[:3] == ["sudo", "apptainer", "inspect"]:
            text = json.dumps({"data": {"attributes": {"labels": {**self.labels, "org.label-schema.build-arch": self.arch}}}})
        else:
            assert argv[:2] == ["docker", "run"] or argv[:3] == ["sudo", "apptainer", "exec"]
            command = argv[argv.index("/opt/conda") + 1:]
            if command == ["uname", "-m"]:
                text = "x86_64\n" if self.arch == "amd64" else "aarch64\n"
            elif command[:2] == ["/bin/sh", "-c"]:
                text = "executable=/opt/conda/bin/tool\ninterpreter=/opt/conda/bin/python\nmachine=" + ("62 0\n" if self.arch == "amd64" else "183 0\n")
            elif command == self.contract["version_command"]:
                text = BANNERS[self.tool]
            elif command == self.contract["smoke_command"]:
                text = "\n".join(self.contract["smoke_expected_outputs"])
                work = self.output / ("work-oci" if argv[0] == "docker" else "work-sif")
                if self.tool == "agat_extra":
                    (work / "smoke.gff3").write_text("##gff-version 3\n" + "".join(f"chr1\tfixture\tgene\t1\t2\t.\t+\t.\tID={f}\n" for f in ("gene1", "transcript1", "exon1", "exon2")))
                elif self.tool == "alevin_fry":
                    directory = work / "smoke-output/alevin-infer"
                    directory.mkdir(parents=True)
                    (directory / "quants_mat.mtx").write_text("%%MatrixMarket matrix coordinate real general\n2 2 4\n1 1 7\n1 2 7\n2 1 3\n2 2 3\n")
                    fixture = REPO / "validation-contracts/fixtures/alevin-infer-tiny"
                    for name in ("quants_mat_rows.txt", "quants_mat_cols.txt"):
                        (directory / name).write_bytes((fixture / name).read_bytes())
            else:
                pytest.fail(f"unexpected synthetic command {command}")
        return {"argv": argv, "stdout": text, "stderr": "", "returncode": 0, "elapsed_seconds": 0.1, "timed_out": False}


def synthetic_run(plan, tool, arch, output, monkeypatch):
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64" if arch == "amd64" else "ARM64")
    fake = FakeCommands(tool, arch, output)
    return native.execute(plan, REPO, plan["plan_sha256"], tool, arch, output, fake), fake


@pytest.mark.parametrize("tool", native.TOOLS)
@pytest.mark.parametrize("arch", native.RUNNERS)
def test_synthetic_pipeline_all_pairs(plan, tool, arch, tmp_path, monkeypatch):
    output = tmp_path / f"{tool}-{arch}"
    record, fake = synthetic_run(plan, tool, arch, output, monkeypatch)
    assert record["status"] == "PASS"
    assert record["publication_authorized"] is False
    assert set(record["gates"]) == set(native.GATES)
    assert record == json.loads((output / "entry.json").read_text())
    assert all("push" not in argv and "--push" not in argv for argv in fake.calls)
    for argv in fake.calls:
        if argv[:2] == ["docker", "run"]:
            assert argv[argv.index("--network") + 1] == "none"
            assert "--read-only" in argv
            assert argv[argv.index("--entrypoint") + 2].startswith("sha256:")


@pytest.fixture
def records(plan, tmp_path, monkeypatch):
    return [synthetic_run(plan, e["tool_id"], e["arch"], tmp_path / f"{e['tool_id']}-{e['arch']}", monkeypatch)[0] for e in plan["entries"]]


def evidence_roots(plan, tmp_path):
    return {(e["tool_id"], e["arch"]): tmp_path / f"{e['tool_id']}-{e['arch']}" for e in plan["entries"]}


def test_complete_reconciliation(plan, records, tmp_path):
    result = native.reconcile(plan, REPO, plan["plan_sha256"], list(reversed(records)), evidence_roots(plan, tmp_path))
    assert result["native_validated_entries"] == 14
    assert result["release_complete"] is False


@pytest.mark.parametrize("change", [
    lambda r: r.pop(), lambda r: r.append(r[0]), lambda r: r.__setitem__(1, copy.deepcopy(r[0])),
    lambda r: r[0].update(tool_id="bedtools"), lambda r: r[0].update(status="FAIL"),
    lambda r: r[0]["gates"].pop("oci_smoke"), lambda r: r[0]["gates"].update(sif_version="FAIL"),
    lambda r: r[0].update(source_commit="b" * 40), lambda r: r[0].update(plan_sha256="b" * 64),
    lambda r: r[0].update(sif_sha256="b" * 64), lambda r: r[0].update(publication_authorized=True),
    lambda r: r[0]["runner_evidence"]["runner"].update(arch="ARM64"),
    lambda r: r[0]["oci"]["version_raw"].update(stdout="wrong version"),
    lambda r: r[0]["sif"]["smoke_raw"].update(returncode=1),
    lambda r: r[0]["conversion"].update(archive_sha256="b" * 64),
    lambda r: r[0]["sif_inspect"]["data"]["attributes"]["labels"].update({"org.label-schema.build-arch": "arm64"}),
    lambda r: r[0]["candidate"]["build_inputs"].update(source_immutable_identity="mutable"),
])
def test_terminal_negative_evidence(plan, records, change, tmp_path):
    change(records)
    with pytest.raises((native.ValidationError, ValueError)):
        native.reconcile(plan, REPO, plan["plan_sha256"], records, evidence_roots(plan, tmp_path))


def test_wrong_runner_fails_before_build_and_records_failure(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "ARM64")
    output = tmp_path / "failure"
    fake = FakeCommands("abricate", "amd64", output)
    with pytest.raises(ValueError):
        native.execute(plan, REPO, plan["plan_sha256"], "abricate", "amd64", output, fake)
    assert json.loads((output / "entry.json").read_text())["status"] == "FAIL"
    assert not any(argv[0] == "docker" for argv in fake.calls)


def test_workflow_static_safety():
    path = REPO / ".github/workflows/contract-native-canary-validation.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["native-validation"]["needs"] == "selection"
    assert workflow["jobs"]["native-validation"]["strategy"]["fail-fast"] == "false"
    assert workflow["jobs"]["reconcile"]["needs"] == ["selection", "native-validation"]
    payload = [step for step in workflow["jobs"]["native-validation"]["steps"] if step.get("with", {}).get("name", "").startswith("contract-native-payload-")]
    assert len(payload) == 1
    assert payload[0]["if"] == "${{ success() }}"
    assert "validated.sif" in payload[0]["with"]["path"]
    assert "entry.json" in payload[0]["with"]["path"]
    for forbidden in ("packages: write", "oras", "docker login", "docker push", "--push", "sif_release", "setup-qemu", "publish=true"):
        assert forbidden not in path.read_text()


def test_argv_boundaries_and_network(tmp_path):
    command = ["python", "-c", "print('literal ; not a shell command')"]
    for phase in ("oci", "sif"):
        argv = native.runtime_argv(phase, "artifact", "arm64", tmp_path, tmp_path, command)
        assert argv[-len(command):] == command
        assert argv[argv.index("--network") + 1] == "none"
        assert any(value.endswith(":ro") for value in argv)


@pytest.fixture
def repo_copy(tmp_path):
    root = tmp_path / "repo"
    (root / "dockerfiles").mkdir(parents=True)
    (root / "validation-contracts/schema-v1").mkdir(parents=True)
    shutil.copytree(REPO / "validation-contracts/fixtures", root / "validation-contracts/fixtures")
    for tool in native.TOOLS:
        for name in (f"dockerfiles/Dockerfile.{tool}", f"validation-contracts/schema-v1/{tool}.json"):
            shutil.copyfile(REPO / name, root / name)
    return root


@pytest.mark.parametrize("change", ["dockerfile", "contract", "fixture", "missing-fixture"])
def test_stale_source_evidence_rejected(plan, repo_copy, change):
    if change == "dockerfile":
        (repo_copy / "dockerfiles/Dockerfile.abricate").write_text("FROM wrong\n")
    elif change == "contract":
        path = repo_copy / "validation-contracts/schema-v1/abricate.json"
        value = json.loads(path.read_text())
        value["scientific_version"] = "wrong"
        path.write_text(json.dumps(value))
    else:
        path = repo_copy / "validation-contracts/fixtures/airr-tiny.tsv"
        if change == "fixture":
            path.write_text("wrong content")
        else:
            path.unlink()
    with pytest.raises((ValueError, OSError)):
        native.validate_plan(plan, repo_copy, plan["plan_sha256"])


@pytest.mark.parametrize("tool,phase,filename", [
    ("agat_extra", "oci", "smoke.gff3"),
    ("agat_extra", "sif", "smoke.gff3"),
    ("alevin_fry", "oci", "smoke-output/alevin-infer/quants_mat.mtx"),
    ("alevin_fry", "sif", "smoke-output/alevin-infer/quants_mat_rows.txt"),
])
def test_retained_outputs_rechecked(plan, records, tmp_path, tool, phase, filename):
    (tmp_path / f"{tool}-amd64" / f"work-{phase}" / filename).write_text("corrupt output")
    with pytest.raises(ValueError):
        native.reconcile(plan, REPO, plan["plan_sha256"], records, evidence_roots(plan, tmp_path))


@pytest.mark.parametrize("failure", ["dirty-source", "source-sha", "command", "wrong-version", "conflicting-version", "smoke", "sif-metadata"])
def test_pipeline_failures_are_terminal_not_repaired(plan, tmp_path, monkeypatch, failure):
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")
    output = tmp_path / "failed"
    fake = FakeCommands("abricate", "amd64", output)
    def run(argv, **kwargs):
        if failure == "command" and argv[:3] == ["docker", "buildx", "build"]:
            raise native.ValidationError("synthetic build failure")
        value = fake(argv, **kwargs)
        if failure == "dirty-source" and argv[0] == "git" and "status" in argv:
            value["stdout"] = " M Dockerfile\n"
        elif failure == "source-sha" and argv[0] == "git" and "rev-parse" in argv:
            value["stdout"] = "b" * 40
        elif argv[-2:] == ["abricate", "--version"] and failure in ("wrong-version", "conflicting-version"):
            value["stdout"] = "abricate 9.9\n" + (value["stdout"] if failure == "conflicting-version" else "")
        elif failure == "smoke" and argv[-2:] == ["abricate", "--check"]:
            value["stdout"] = "dependency missing"
        elif failure == "sif-metadata" and argv[:3] == ["sudo", "apptainer", "inspect"]:
            value["stdout"] = "{}"
        return value
    with pytest.raises(ValueError):
        native.execute(plan, REPO, plan["plan_sha256"], "abricate", "amd64", output, run)
    record = json.loads((output / "entry.json").read_text())
    assert record["status"] == "FAIL" and record["error"]
    assert sum(argv[:3] == ["docker", "buildx", "build"] for argv in fake.calls) <= 1


@pytest.mark.parametrize("state", ["success", "nonzero", "timeout"])
def test_process_logs_and_timeout_are_durable(tmp_path, monkeypatch, state):
    killed = []
    class FakeProcess:
        pid = 123456
        calls = 0
        def wait(self, timeout=None):
            self.calls += 1
            if state == "timeout" and self.calls == 1:
                raise subprocess.TimeoutExpired("fake", timeout)
            return 0 if state == "success" else -9
    def popen(argv, **kwargs):
        kwargs["stdout"].write("synthetic output")
        kwargs["stderr"].write("synthetic diagnostic")
        assert kwargs["start_new_session"] is True
        return FakeProcess()
    monkeypatch.setattr(native.subprocess, "Popen", popen)
    monkeypatch.setattr(native.os, "killpg", lambda pid, signal: killed.append(pid))
    commands = native.Commands(tmp_path)
    if state == "success":
        assert commands(["fake"], timeout=1)["stdout"] == "synthetic output"
    else:
        with pytest.raises(native.ValidationError):
            commands(["fake"], timeout=1)
    record = json.loads((tmp_path / "commands/001.json").read_text())
    assert record["timed_out"] == (state == "timeout")
    assert bool(killed) == (state == "timeout")
