# Abstract Meaning Representation to Propositional Logic Translator

A rule-based Python translator from PENMAN AMR graphs to propositional formulas
and structured atoms with text verbalizations.

## Installation

Requires Python 3.8 or later. Install from GitHub:

```shell
python -m pip install git+https://github.com/fxy-1117/amr-to-propositional-logic.git
```

## Usage

```python
from amr_translator import translate, formula_ast_to_string

raw_amr = "(r / run-01 :ARG0 (b / boy) :polarity -)"
frame = translate(raw_amr)
print(formula_ast_to_string(frame["formula_ast"]))
for atom in frame["atoms"]:
    print(atom["id"], atom["verbalization"])
```

Output:

```text
~(x1)
x1 boy run
```

## Output and API

`translate(raw_amr)` accepts a non-empty PENMAN AMR string and returns:

| Field | Content |
| --- | --- |
| `atoms` | List of atoms, each with `id`, structured `expression`, and `verbalization`. |
| `formula_ast` | Formula tree using `atom`, `not`, `and`, `or`, and `implies`. Atom references use the corresponding atom IDs. |

Negation is represented in the formula rather than prefixed to the atom's
verbalization. The output contains exactly the atoms referenced by the formula.

Each `verbalization` has leading and trailing whitespace removed and uses
Unicode case folding. Exact matching compares these strings directly.
Internal whitespace and existing punctuation are preserved; no final period is added.

`translate_with_audit(raw_amr)` returns the translation under `frame`, plus
diagnostics for applied rules, merges, warnings, and limitations.

Nonfactual and reported content have limited semantic support; consult the
diagnostics returned by `translate_with_audit`.

See the [technical reference](docs/translation.md) for construction rules,
template tables, scope guards, and worked examples.

## Resources and tests

The package includes a pinned PropBank role index and its upstream notices in
[`amr_translator/resources/propbank/`](amr_translator/resources/propbank/README.md).
`verify_resources()` checks the packaged files against their pinned hashes.

To run the tests, clone the repository and install it:

```shell
git clone https://github.com/fxy-1117/amr-to-propositional-logic.git
cd amr-to-propositional-logic
python -m pip install .
python -B -m unittest discover -s tests -v
```

The suite checks atoms, formulas, and verbalizations for 50 AMR cases, including
four parser-generated cases from MultiNLI training premises.

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
