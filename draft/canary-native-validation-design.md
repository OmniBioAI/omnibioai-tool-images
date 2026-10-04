# Contract-driven native canary validation

Status: separate local implementation and synthetic/static tests complete;
review, remote verification and real native execution remain pending.

Implementation: `scripts/contract_native_validation.py`,
`.github/workflows/contract-native-canary-validation.yml`, and
`tests/test_contract_native_validation.py`. No workflow dispatch, container
execution or publication occurred during implementation. Synthetic PASS
records are test data, never production evidence.

## Exact scope

Use the seven original canary contracts currently classified CONTRACT_READY:
`3ddna_extra`, `abricate`, `accelerate`, `agat_extra`, `aicsimageio_extra`, `airr_extra`, `alevin_fry`.
Require exactly fourteen unique tool/architecture pairs. Exclude all reference
tools and every blocked contract. This phase establishes native build and
scientific validation evidence; publication remains separately gated.

## Existing integration boundary

`scripts/pilot_runner.py` obtains its entry using `pilot_manifest.get_tool`.
The Pilot manifest fixes ten reference tools, package mappings, and a reviewed
Python base. Rollout contracts instead contain argument-array commands,
`regex:` parsers, API validation, and repository-owned fixtures. Neither
existing Pilot workflow can select these seven rollout tools. Preserve these
components and introduce a separate contract-consuming validation runner and
workflow only under explicit implementation authorization.

## Workflow topology

1. Validate selected contract hashes, Dockerfile hashes, source revision,
   eligibility, exact tool set, and fixture requirements. Emit fourteen pairs.
2. Run amd64 pairs on `ubuntu-24.04`; arm64 pairs on `ubuntu-24.04-arm`.
   Require runner OS Linux, runner architecture X64/ARM64, and matching uname.
   No emulated execution qualifies. Set fail-fast false.
3. Build/load OCI locally, save its archive, validate config architecture and
   labels, inspect executable/interpreter architecture, and run the contract's
   version and smoke commands with bounded execution.
4. Convert the same OCI archive to SIF. Validate structured SIF metadata,
   architecture, executable/interpreter, exact version, and the same smoke.
   Bind OCI/SIF evidence through archive identity, labels, and content hashes.
5. Upload per-entry evidence including failures; reconcile all fourteen terminal
   entries and fail if any required gate is absent or failed.

Use contents-read permissions and artifact upload only. No registry login,
package-write permission, publication commands, or publication inputs belong
in this workflow. It must run only on explicit dispatch, never on source push.

## Contract execution

- Load and validate current schema-v1 contracts; verify hash and source files
  before invoking Docker. Preserve argument boundaries with subprocess argv.
- Reuse established architecture/identity validators where their documented
  inputs fit; do not fabricate Pilot entries or alter frozen implementations.
- Strip only the documented `regex:` prefix. Require a unique expected version
  match; reject missing, conflicting, or incorrect evidence in either stream.
- Keep scientific/package version and observed internal banner distinct:
  3D-DNA is package baseline 201008 with documented banner 190716; AGAT package
  version 1.7.0 prints v1.7.0. Do not substitute banner values for source identity.
- Copy declared fixtures into an isolated workspace, mount inputs read-only,
  and assert declared output properties. AGAT must produce nonempty GFF3 with
  gene1, transcript1, exon1, exon2. API assertion commands must exit zero.
- Treat API validation as native interpreter plus package functionality,
  rather than demanding a console launcher that the package does not supply.
- AIRR uses Rscript, pinned R package baseline 2.0.0, and the existing tiny
  rearrangement TSV. Its metadata-only cross-platform dependency solves are
  not build/runtime evidence. Both native executions remain unproven; this
  newly pinned baseline must not inherit the other five local ARM64 results.
- Alevin-fry uses `infer` with the hash-bound tiny MatrixMarket/equivalence-class
  fixtures. Implement its `matrix_market_gene_counts` assertion kind explicitly;
  reject unknown assertion kinds rather than accepting exit zero. Require exact
  row/column labels, shape, unique coordinates, finite nonnegative values,
  expected entries within 0.0001 absolute tolerance and row totals. This scope
  excludes RAD processing and full quantification. Runtime output is unproven.
  The local read-only checker is now implemented in `scripts/validation_smoke.py`
  as `validate_smoke_outputs`; the future execution adapter must supply measured
  exit status/duration and an isolated output workspace. Its synthetic verifier
  tests are not native execution or release evidence.
- Preserve ABRicate's reference-data requirement and dependency-check scope;
  its current check does not establish database-backed resistance screening.

## Evidence and acceptance

Record source SHA, contract hash, Dockerfile hash, resolved base identity,
package/source identity, runner evidence, OCI config/archive hashes, SIF hash,
executable/interpreter evidence, commands, raw stdout/stderr, return codes,
version results, smoke assertions, and OCI-to-SIF binding. Missing mandatory
evidence fails. Optional absent SBOM remains OPTIONAL_NOT_GENERATED.

Unit/static checks must cover exact matrix membership, blocked/reference tool
exclusion, stale hashes, wrong runner architecture, command failures, wrong or
conflicting versions, missing fixtures/output, OCI/SIF mismatches, failure
aggregation, and absence of write-capable steps. Validate YAML and actionlint.

Current evidence: five local ARM64 builds/smokes and runtime uname checks.
Native AMD64, SIF conversion, full identity/provenance gates, and remote
workflow execution remain unproven. No release-completion claim follows from
this design or the existing local checks.
