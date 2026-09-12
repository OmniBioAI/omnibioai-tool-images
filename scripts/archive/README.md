# Archived build scripts

Superseded one-off / historical batch scripts (March–July 2026), kept for
debugging and historical reference. None of these are referenced by
README.md, the API, or the test suite. **The current build scripts live in
the parent `scripts/` directory:** `build_all.sh`, `build_missing_sifs.sh`,
`build_new_tools.sh`, `build_multiarch_sifs.sh`.

| Script | Why archived |
|---|---|
| `build_failed_tools.sh` | one-off rerun of a fixed 26-tool failure list |
| `build_remaining.sh` | one-off rerun of a fixed 168-tool list |
| `build_fix_batch.sh` | one-off rerun of a fixed 62-tool list |
| `build_200_new.sh` | one-off batch of named new tools |
| `build_60_new_tools.sh` | one-off batch of named new tools |
| `build_20_more_tools.sh` | one-off batch of named new tools |
| `build_tier3_pip_tools.sh` | one-off batch; all of its target tools now have SIFs built |
| `build_fallback_chain.sh` | generic pip→QEMU-emulation fallback chain from the same build push; unreferenced elsewhere |
| `build_200_new_sifs.sh` | **do not run** — confirmed to have produced x86_64 binaries mislabeled `*_arm64.sif`; see in-file warning |
| `retry_failed_sifs_depot.sh` | **do not run** — pulls from depot.galaxyproject.org, which has no real arm64 builds; see in-file warning |
