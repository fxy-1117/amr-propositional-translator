# PropBank numbered-role index

`roleset_index.json` is a deterministic compact projection of the official
PropBank Frames `v3.4.0` release. It retains only each roleset's identifier,
name, and numbered-role function/description. It contains no corpus examples
or source sentences.

- Source: https://github.com/propbank/propbank-frames
- Release: `v3.4.0`
- Commit: `4087fa9ab5c40907c34ff91a56acc2cab1670145`
- Source archive SHA256: `b59a978cb05fd7e2d9f175633bf93716ca279b42dd55ab8a425599f5040d67b8`
- AMR/UMR 91-role supplement commit: `c66e0ccf28b53f00051b187db83e937b5bee2e32`
- AMR/UMR 91-role supplement SHA256: `9645673f4ec60c2caa1daa3430f6efa4c21985474e2a66fefc77e124204a6cb0`
- License: CC BY-SA 4.0; see `LICENSE`

The pinned supplemental XML contains one extra `</example>` at original line
5683. The index builder removes exactly that one tag only when the complete
source SHA256 matches the value above; the original downloaded XML is retained
unchanged. Any other parse or hash mismatch remains a hard failure.

The supplement adds 99 rolesets and intentionally overrides the older
`have-degree.92` entry. That single override id is stored in the generated
index metadata.

The generated index is bundled with this package; translation does not require
downloading or rebuilding PropBank. Its SHA256 is
`59f3e380bbead1e75e60bfedfb868c941e53d8e2c63b7ce3341342a8ec47dc7d`.

The AMR translator treats the parser graph as authoritative. This index is
used only to verbalize parser-provided predicate senses and numbered roles; it
does not inspect or repair source text.
