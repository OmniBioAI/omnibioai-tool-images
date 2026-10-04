# ADMIXTOOLS synthetic smoke candidate

Status: **STATIC_FIXTURE_ONLY — NOT_RUNTIME_VALIDATED**.
This repository-owned synthetic dataset has 24 biallelic autosomal sites,
six diploid samples, and three artificial populations. It contains no human
or external dataset. Counts represent reference-allele dosage, not phased
haplotypes. Target heterozygotes are present; no missing calls are encoded.
Genetic coordinates span multiple candidate jackknife blocks, but block
retention and numerical behavior require execution evidence.

Format evidence is pinned to ADMIXTOOLS v8.0.2:

- [EIGENSTRAT format](https://github.com/DReichLab/AdmixTools/blob/v8.0.2/convertf/README): one genotype row per site, one dosage character per sample; associated SNP and individual tables.
- [qp3Pop input/output](https://github.com/DReichLab/AdmixTools/blob/v8.0.2/README.3PopTest): parameter-file invocation and ordered source/source/target population triple.

Proposed invocation, with this directory as the working directory:

```sh
qp3Pop -p qp3pop.par
```

No invocation has been performed for this fixture. Before binding it into
the contract, require exit zero, exactly one result for the planned triple,
finite f3/standard-error/Z values, positive standard error and retained SNP
count, and normal completion. Retained counts and numerical expectations
must be established on the exact pinned package, not guessed. The synthetic
result must not be interpreted as evidence about real population ancestry.

The tool remains blocked pending this execution evidence and distribution
license review. Creating independently authored input data neither approves
software redistribution nor resolves that license gate. No contract, pin,
factory, or readiness status is changed by this fixture.
