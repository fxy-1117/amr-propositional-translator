# AMR-to-propositional translator

A deterministic translator from a PENMAN AMR graph to a propositional formula,
structured atoms, and atom verbalizations. The translator is a standalone
Python package with bundled lexical resources.

The input is an existing AMR graph. Sentence parsing, neural models, caches,
semantic linking, and SAT inference belong to the application using this package.

## Installation and use

Requires Python 3.8 or later. Install from GitHub:

```shell
python -m pip install git+https://github.com/fxy-1117/amr-propositional-translator.git
```

Alternatively, clone the repository and install locally:

```shell
git clone https://github.com/fxy-1117/amr-propositional-translator.git
cd amr-propositional-translator
python -m pip install .
```

```python
import json
from amr_translator import translate, translate_with_audit

raw_amr = "(r / run-01 :ARG0 (b / boy) :polarity -)"
frame = translate(raw_amr)
print(json.dumps(frame, indent=2))

audit = translate_with_audit(raw_amr)
print(audit["warnings"])
print(audit["limitations"])
```

Both entry points use the same fixed rules and accept one non-empty AMR string.
They have no model settings or translation-policy flags.

## Output and API

`translate(raw_amr)` returns a dictionary containing exactly:

| Field | Content |
| --- | --- |
| `atoms` | List of atoms, each with `id`, structured `expression`, and `verbalization`. |
| `formula_ast` | Formula tree using `atom`, `not`, `and`, `or`, and `implies`. Atom references use the corresponding atom IDs. |

Negation is represented in the formula rather than prefixed to the atom's
verbalization. The output contains exactly the atoms referenced by the formula.

`translate_with_audit(raw_amr)` returns the same result under `frame`, together
with `repairs`, `warnings`, `limitations`, `merges`, `rejected_templates`,
`quantity_negations`, `quantity_negation_rejected`,
`quantity_negation_rejected_details`, and `diagnostic_provenance`.
The audit can be serialized with `json.dumps`; it contains no live builder or
graph objects. Warnings and limitations identify unresolved cases, not successful
semantic repairs.

Other public helpers are `validate_frame`, `canonical_atom_key`,
`formula_ast_to_string`, `normalize_exact_match_surface`,
`format_nli_prompt_surface`, and `verify_resources`.

## Rules and organization

The package has one translation pipeline:

1. Decode the AMR and establish deterministic graph ownership and ordering.
2. Construct atoms and a Boolean formula from graph roles, connectives,
   conditions, and polarity, using registered structural rules.
3. Render atom surfaces using predicate senses, role templates, metadata, and
   the bundled PropBank role index. Apply registered surface and quantity rules.
4. Merge eligible dyad pairs into triples. Each merge consumes two distinct
   dyads over three distinct graph nodes in the same positive conjunction.
   Registered scope and content boundaries restrict merges; components are
   never reused across accepted merges.
5. Retain active atoms and validate the formula/atom contract.

Nonfactual and reporting constructions are recorded as boundaries or limitations;
the package does not encapsulate their content as a separate scope atom. The
rules do not provide a complete semantic interpretation of arbitrary AMR graphs.

Implementation modules live directly under `amr_translator/`, grouped by
function: graph construction, formula operations, atom identities, verbalization,
merge templates, and scope/quantity checks.

## Resources and checks

The package includes a pinned PropBank role index and its upstream notices in
[`amr_translator/resources/propbank/`](amr_translator/resources/propbank/README.md).
`verify_resources()` checks the packaged files against their pinned hashes.

Run the standalone regression suite after installation:

```shell
python -B -m unittest discover -s tests -v
```

The 50 frozen AMR cases check complete atoms, formulas, and verbalizations.
The four `corpus_*` fixtures contain parser-generated AMRs for premise sentences
from the MultiNLI training split. Additional checks cover merge ownership,
JSON audits, input validation, and the fixed API.

## Citation

If you use this translator in your research, please cite the following preprint:

Xuyao Feng and Antonis Bikakis. 2026.
[Pairwise Logical Selection of Enthymeme Completions under Semantic-Link Uncertainty](https://arxiv.org/abs/2608.18820).
arXiv:2608.18820.

```bibtex
@article{feng2026pairwise,
  title = {Pairwise Logical Selection of Enthymeme Completions under Semantic-Link Uncertainty},
  author = {Feng, Xuyao and Bikakis, Antonis},
  journal = {arXiv preprint arXiv:2608.18820},
  year = {2026},
  doi = {10.48550/arXiv.2608.18820},
  url = {https://arxiv.org/abs/2608.18820}
}
```

## License

The original Python code and documentation are released under the
[MIT License](LICENSE).

Bundled PropBank resources retain their [CC BY-SA 4.0 license](amr_translator/resources/propbank/LICENSE)
and [upstream attribution](amr_translator/resources/propbank/README.md).
Corpus-derived test fixtures retain the terms of their source data;
the MIT license does not replace these third-party terms.
