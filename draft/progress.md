# OmniBioAI Tool-Image Rollout Progress

## 2026-10-04 — Scale-up gate

- Scope: deterministic rollout preparation toward the eligible 1,000+ tool population.
- Current repository: `omnibioai-tool-images`.
- Reference factory: preserved; no workflow, release-engine, identity, provenance, or publication logic changes.
- Contract inventory: 601 eligible targets from the current reviewed population snapshot.
- Static contract validation: PASS.
- Contract determinism: PASS for the generator's current catalog-only output; two independent generations produced byte-identical output. The generated baseline remains 601 blocked because the source-pinning evidence artifact is not yet wired into the generic generator.
- Focused tests: 74/74 passed with `PYTHONPATH=.` and coverage disabled for this contract-only selection.
- The default pytest coverage gate is unrelated to these tests and reports 0% for `api/server.py`; no contract test failed.
- Current static-ready contracts: 5 (`3ddna_extra`, `abricate`, `accelerate`, `agat_extra`, `aicsimageio_extra`).
- Current blocked contracts: 596.
- Immediate next gate: separately authorized native amd64/arm64 validation of the five static-ready tools.
- Integration blocker: reconcile the five source-pinning-ready canary contracts with the generic generator without changing the frozen release factory.
- Integration implementation: the generator now consumes only reviewed canary contracts whose `pinning_canary_schema_version` is 1 and whose Dockerfile SHA matches current source; stale overrides fail closed.
- Post-integration generation: 601 targets, 5 ready, 596 blocked; output validator PASS.
- Post-integration focused tests: 75/75 passed.
- No Docker/Buildx/Apptainer/Singularity builds were run.
- No workflow dispatches, registry writes, publications, or GHCR mutations were run.
- Existing local symlink/generated artifacts remain preserved and untouched.

### Blocking canary entries

- `admixtools`: fixture and license review required.
- `afterqc_extra`: Python 2.7 and native arm64 dependency evidence unresolved.
- `airr_extra`: native arm64 dependency evidence unresolved.
- `alevin_fry`: valid RAD/permit-list smoke fixture required.
- `alevinqc_extra`: no stable linux-aarch64 package artifact.
