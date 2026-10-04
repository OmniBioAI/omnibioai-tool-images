# Alevin-fry inference fixture

Independently authored synthetic inputs for the pinned v0.18.3 `infer`
subcommand; static fixture and contract only, **not runtime validated**.

Two cells have counts in three equivalence classes: singleton geneA,
singleton geneB, and an ambiguous geneA/geneB class. Both singleton counts
are equal in each cell. By symmetry the expected EM allocation splits the
ambiguous counts equally: output rows [7, 7] and [3, 3]. This expectation is
analytical, not an observed result. A later native gate must reject any
nonfinite, negative, wrong-shape, wrong-label, wrong-total or mismatched count
(absolute tolerance 0.0001); exit zero alone is insufficient.

The equivalence-class format uses zero-based gene IDs followed by the class
ID. Its first two lines give gene and class counts. `infer` requires gzip
encoding for these labels; `eq_labels.txt.gz` is generated mechanically by
`gzip -n -k eq_labels.txt`, with no timestamp or source filename in its header.
MatrixMarket coordinates are one-based. Column-name companion data names
the two output genes, not the three input classes.

Pinned upstream evidence (no source/test data copied):

- [infer documentation](https://github.com/COMBINE-lab/alevin-fry/blob/ad05742d274230f9141b2eabeebd1c1b31692199/docs/source/infer.rst)
- [CLI input flags](https://github.com/COMBINE-lab/alevin-fry/blob/ad05742d274230f9141b2eabeebd1c1b31692199/src/main.rs)
- [gzip equivalence-class parser](https://github.com/COMBINE-lab/alevin-fry/blob/ad05742d274230f9141b2eabeebd1c1b31692199/src/eq_class.rs)
- [matrix reader, EM invocation and output writer](https://github.com/COMBINE-lab/alevin-fry/blob/ad05742d274230f9141b2eabeebd1c1b31692199/src/infer.rs)

Run only inside an isolated validation workspace, preserving input directory
layout and binding the fixture hashes. Use two threads and a 60-second
timeout. This smoke does not prove read mapping, RAD parsing, permit-list
generation, UMI resolution, USA mode, or full biological pipeline accuracy.
