# Multiarchitecture validation contracts

This directory contains static, deterministic validation-contract discovery
for the non-reference `MULTIARCH_READY` population. It is planning evidence,
not build or publication authorization.

The ten validated reference tools are excluded. Contracts are generated only
for tools that the reviewed package preflight classifies as
`MULTIARCH_READY` with an existing GHCR package. Missing executable, version,
source-identity, smoke, architecture, licensing, or runtime evidence is
recorded as a fail-closed contract status; it is never filled from naming
conventions alone.

Generate and validate from the repository root:

```text
python -m scripts.validation_contracts generate \
  --catalog validation-contracts/evidence/current-package-preflight.csv \
  --output validation-contracts \
  --source-commit <audited-source-commit>
python -m scripts.validation_contracts validate --output validation-contracts
```

`validation_contract_sha256` is the SHA256 of compact, sorted-key canonical
JSON for every semantic contract field except the hash field itself. Output
files use stable ordering and contain no timestamps, hostnames, workflow IDs,
temporary paths, or UUIDs.

`contract-ready-tools.txt` contains only `CONTRACT_READY` tools.
`proposed-batch-10.txt` contains the first ten ready tool IDs in stable sorted
order only when at least ten contracts are ready. Neither file grants release
authorization.
