# Translator technical reference

This document specifies the rule-based translation of a PENMAN AMR graph into
structured atoms, their text verbalizations, and a propositional formula. It
covers the public interface, graph interpretation, atom construction, Boolean
scope, lexical templates, guarded atom merges, and diagnostics.

The notation `u :r v` denotes an AMR role occurrence from node `u` to node `v`.
An **atom** is one propositional variable with a structured expression and a
verbalization. A **record** is an input edge or attribute occurrence. An atom's
**owner** determines where it is introduced during formula traversal. A
**template** is a registered graph or lexical pattern with explicit eligibility
conditions. Atom identifiers such as `x1` are local to one translation.

- [Input and output](#input-and-output)
- [Translation order](#translation-order)
- [Records and identities](#records-and-identities)
- [Base atom construction](#base-atom-construction)
- [Boolean structure and ownership](#boolean-structure-and-ownership)
- [Participant polarity](#participant-polarity)
- [Negative quantity metadata](#negative-quantity-metadata)
- [Unresolved content boundaries](#unresolved-content-boundaries)
- [Lexical rendering](#lexical-rendering)
- [Registered atom merges](#registered-atom-merges)
- [Diagnostics and failures](#diagnostics-and-failures)
- [Implementation map](#implementation-map)

## Input and output

Source: [compiler.py](../amr_translator/compiler.py),
[frame.py](../amr_translator/frame.py).

```python
from amr_translator import translate, translate_with_audit

frame = translate(raw_amr)
audit = translate_with_audit(raw_amr)
```

`raw_amr` is a non-empty string in PENMAN notation. The translator decodes it
with `penman` and its AMR model. Its public input is the AMR string itself;
parser status records and original sentence text are not arguments to these
functions. There is no sentence-text lookup during translation.

Both functions execute the same translation path. `translate` returns its
`frame`; `translate_with_audit` also returns the diagnostic records described
under [Diagnostics and failures](#diagnostics-and-failures).

The frame contains exactly two fields:

| Field | Value |
| --- | --- |
| `atoms` | A list of active atom records. Each record has exactly `id`, `expression`, and `verbalization`. |
| `formula_ast` | A Boolean abstract syntax tree whose leaves reference the atom IDs. |

For example, `translate("(b / boy)")` returns:

```json
{
  "formula_ast": {"op": "atom", "id": "x1"},
  "atoms": [{
    "id": "x1",
    "expression": {
      "type": "unary-v1.2",
      "kind": "unary",
      "node": {"concept": "boy", "metadata": [], "name": ""}
    },
    "verbalization": "boy exists"
  }]
}
```

The `type` strings shown here are literal serialization tags. The supported
type/kind pairs are `unary-v1.2`/`unary`, `dyad-v1.2`/`dyadic`,
`triple-v1.2`/`triple`, and `opaque-v1.2`/`opaque`.

Let $A$ be the set of emitted atom IDs and $\Phi$ the emitted formula. The
output contract is

$$
\operatorname{LeafIds}(\Phi)=A.
$$

Every emitted atom has a non-empty ID and verbalization. IDs are unique within
the frame, but may contain gaps after filtering and merges. Formula leaf order
need not match atom-list order. Do not use a numeric ID as a persistent identity
across independent translations.

### Formula AST schema

| Operator | Shape | Meaning |
| --- | --- | --- |
| `atom` | `{"op": "atom", "id": "x1"}` | Reference to an emitted atom. |
| `not` | `{"op": "not", "arg": F}` | $\neg F$. |
| `and` | `{"op": "and", "args": [F, G, ...]}` | Conjunction of at least two arguments. |
| `or` | `{"op": "or", "args": [F, G, ...]}` | Disjunction of at least two arguments. |
| `implies` | `{"op": "implies", "antecedent": F, "consequent": G}` | $F\to G$. |

`F` and `G` in the table stand for nested AST objects. Boolean constants are
used during construction, normalized, and rejected if any remain in the
public output. An empty inventory or a formula consisting only of a constant
is therefore not a successful translation.

`formula_ast_to_string(frame["formula_ast"])` renders `not`, `and`, `or`, and
`implies` with `~`, `&`, `|`, and `>>`. This is a convenience representation;
`formula_ast` is the structured result.

## Translation order

The order matters because later templates operate on the active atoms and
formula produced by earlier steps.

| Stage | Operation |
| --- | --- |
| 1. Decode | Validate the input type, decode PENMAN, and reject duplicate concept-instance definitions. |
| 2. Establish ownership | Derive a traversal layout from graph roles, explicit scopes, shared participants, and polarity; sort graph records deterministically. |
| 3. Build records and atoms | Separate structural/metadata records, compose same-event triples, retain residual dyads and required unary/opaque atoms. |
| 4. Build the formula | Compile connective branches and conditions, isolate shared-node contexts, and project participant polarity. |
| 5. Render | Apply PropBank-aware role verbalization and registered surface rules; normalize constants and retain active atoms. |
| 6. Handle quantity negation | Plan eligible quantity rewrites, perform an isolated numeric re-render when required, and place accepted negation in the formula. |
| 7. Detect semantic boundaries | Record nonfactual, reporting, and property-content limitations. These records also restrict later merges. |
| 8. Apply merge templates | Replace eligible pairs of active dyads with registered triple atoms, subject to formula and graph guards. |
| 9. Validate | Check the final frame, atom/formula closure, absence of scope atoms, and the component constraints of accepted ordinary merges. |

Base same-event composition in stage 3 and registered dyad merging in stage 8
are different operations. In particular, their component-sharing rules differ;
see [Base atom construction](#base-atom-construction) and the merge catalogue.

## Records and identities

Source: [graph.py](../amr_translator/graph.py),
[atoms.py](../amr_translator/atoms.py),
[metadata.py](../amr_translator/metadata.py).

### Record partition

PENMAN decoding uses the AMR model's inverse-role normalization. Node-valued
edges receive `e1`, `e2`, ... record IDs; literal-valued attributes receive
`a1`, `a2`, ... IDs after graph ordering. These record IDs are diagnostic
provenance, not public atom IDs.

| Input pattern | Interpretation |
| --- | --- |
| `and` / `or` with `:opN` edges | Ordered Boolean branches. |
| `multi-sentence` with `:sntN`, and its `:rel` / `:sntN` envelope edges | Sentence branches conjoined during formula construction. |
| `:condition` | A condition contributing to an implication antecedent. |
| Eligible `have-condition-91` with `:ARG1` and `:ARG2` | A reified condition; detailed guards appear below. |
| `:polarity -` | A polarity marker, handled by formula construction and polarity projection. |
| `:name` targeting a `name` node, and the name's `:opN` records | Name metadata. |
| `:wiki` | Metadata; it does not create an independent atom. |
| `:li`, `:mode` | List/force metadata. The lexical-node `:mode` projection is an explicit exception described below. |
| `:quant` and `:unit` on any source; `:scale` on a `*-quantity` concept | Quantity metadata. |
| `date-entity` date fields | Date metadata; the complete field set is given in the verbalization section. |
| `:value` / `:range` on `ordinal-entity`, `percentage-entity`, `score-entity`, or `value-interval` | Value metadata. |
| Descendants of a node-valued metadata target | A descriptor subtree rather than independent semantic relations. |
| Remaining records | Semantic relations that can contribute dyads or triples. |

Metadata influences endpoint identity or surface rendering. It is not generally
an additional proposition. `:mode` is retained as an active dyad when attached
to a lexical/event node; mode on a structural connective remains metadata.

Exact duplicate semantic occurrences are grouped using record type, source
node, case-folded role, and target node or typed literal. Their provenance is
retained together. This is occurrence deduplication, not a global merge of all
atoms that happen to have identical verbalizations.

### Endpoint descriptors

A node descriptor stores:

```text
{
  concept: case-folded AMR concept including its sense suffix,
  name: normalized name or "",
  metadata: sorted [{role, value}, ...]
}
```

Direct `name`, name-part, and `wiki` records are excluded from the descriptor's
metadata list. The name has its own field. Node-valued metadata is serialized
recursively; a revisited node is represented by its concept and `cycle_ref:
true`. Literal descriptors retain `raw`, evaluated `value`, and `value_type`.
For example, the quoted string `"2"` and numeric token `2` retain different
typed literal information even if a text rendering looks similar.

PENMAN variable names and graph-record IDs are not fields of these endpoint
descriptors. They remain relevant to occurrence identity and graph traversal.
The graph ordering is not a claim of complete graph-isomorphism
canonicalization.

### Atom expressions and keys

| Kind | Principal expression fields |
| --- | --- |
| Unary | `kind`, `node`. |
| Dyadic | `kind`, `role`, `source`, `target`, `coreference`. Node targets are wrapped under `node`; literal targets under `literal`. `coreference` is `same`, `distinct`, or `literal`. |
| Same-event triple | `kind`, `join_signature`, `subject`, `predicate`, `object`, `subject_object_coreference`. |
| Opaque | `kind`, `role`, a structured connective `source`, and `target`. |
| Registered merged triple | The triple's template-specific expression, including its component relation identities; see the merge catalogue. |

All expressions also contain the corresponding `type` serialization tag.
`canonical_atom_key(expression)` checks the type/kind pair and serializes the
remaining payload with sorted JSON keys, ASCII escaping, and compact
separators, prefixed by the type and a colon. The key retains structure that a
verbalization may omit, including predicate senses and role distinctions.
It is not a semantic-equivalence test.

The same expression can occur in different graph contexts. Formula ownership
and source-record provenance are stored in construction diagnostics rather
than in the minimal public atom record.

## Base atom construction

Source: `FlatTripleBuilder` in [atoms.py](../amr_translator/atoms.py), with
formula/carrier extensions in [builder.py](../amr_translator/builder.py).

### Dyads and same-event triples

Write $\delta(u)$ for a node descriptor. A semantic relation occurrence
`p :r u` contributes a role-aware dyadic record

$$
D_r(\delta(p),\delta(u)).
$$

Literal targets use their typed literal descriptor instead of $\delta(u)$.
Same-event composition considers node-valued semantic edges, not literal
attributes. For a nonconnective source node, it selects the first
available anchor role in the order `ARG0`, `ARG1`, `ARG2`, `ARG3`, `ARG4`.
It pairs each edge of that anchor role with each other composable role edge
from the same source occurrence.

The composable role set is exactly:

```text
ARG0 ARG1 ARG2 ARG3 ARG4
accompanier beneficiary cause destination direction duration extent
instrument location manner medium path purpose source time topic
```

For anchor `p :ra u` and companion `p :rb v`, the result has the ordered terms

$$
T_{r_a,r_b}(\delta(u),\delta(p),\delta(v)).
$$

The middle term is the shared source occurrence. This stage does not require
all three terms to be distinct, and its implementation does not impose a
predicate-sense regex on the nonconnective source. Those restrictions must not
be inferred from the word “event.”

An `ARG0`/`ARG1`/`ARG2` event produces the pairs `ARG0+ARG1` and
`ARG0+ARG2`; it does not also form `ARG1+ARG2`. Here the anchor record can
participate in both triples. If `ARG0` is absent, `ARG1+ARG2` is permitted.
This shared-anchor behavior is specific to base composition: the later
ordinary-merge pass forbids reusing a component across accepted merges.

Consumed dyads remain construction records but are not separately emitted
active atoms. Remaining semantic occurrences become residual dyads.

### Coordinated endpoints

If exactly one endpoint is a structural connective, it is projected through
the ordered leaf branches, preserving nested conjunction/disjunction in the
formula. If both endpoints are coordinated, no Cartesian product of triple
candidates is formed; this also applies when both refer to the same connective.
The relations remain projected dyads.

For example:

```penman
(e / eat-01
   :ARG0 (b / boy)
   :ARG1 (o / or :op1 (a / apple) :op2 (p / pear)))
```

```text
Formula: (x1 | x2)
x1: boy eat apple
x2: boy eat pear
```

With both endpoints coordinated:

```penman
(e / eat-01
   :ARG0 (a / and :op1 (b / boy) :op2 (g / girl))
   :ARG1 (o / or :op1 (p / pear) :op2 (x / apple)))
```

```text
Formula: (x1 & x2 & (x4 | x3))
x1: boy eat
x2: girl eat
x3: apple is eaten
x4: pear is eaten
```

### Unary and opaque atoms

A unary carrier is added for an otherwise unrepresented concept or an
otherwise empty negative scope. Name nodes, metadata-subtree nodes, and
structural connectives do not automatically create unary propositions.
A node already represented as an owner or relation term generally needs no
extra unary. Negative participants and empty disjunction operands receive
the narrower carrier handling described under Boolean scope and polarity.

Unary surfaces use `C occurs` for a predicate-sense concept, `C exist` for a
quantity-bearing concept whose quantity is not `1`, and `C exists` otherwise.
For example, `(r / rain-01)` emits `rain occurs`.

A semantic relation whose **source** is a structural connective becomes an
opaque atom retaining that source's connective identity. Its relation is not
distributed over source branches. The connective's own branch formula is
still compiled. For example:

```penman
(a / and :op1 (b / boy) :op2 (g / girl) :location (r / room))
```

```text
Formula: (x1 & x2 & x3)
x1: boy girl occurs at room    [opaque]
x2: boy exists                [unary]
x3: girl exists              [unary]
```

The terse opaque verbalization is a record rendering, not an additional
semantic interpretation of how location distributes over the group.

### Worked construction

```penman
(r / read-01
   :ARG0 (s / student :mod (c / careful))
   :ARG1 (b / book))
```

The `ARG0` and `ARG1` records form a same-event triple. The `mod` relation
remains a dyad:

```text
Formula: (x2 & x1)
x1: careful student           [dyadic]
x2: student read book         [triple]
```

The structured predicate retains `read-01`, while the verbalization uses
`read`. `careful` is an independent relation here; it is not silently absorbed
into the triple's subject descriptor.

## Boolean structure and ownership

Source: [graph.py](../amr_translator/graph.py),
[builder.py](../amr_translator/builder.py),
[atoms.py](../amr_translator/atoms.py),
[formula.py](../amr_translator/formula.py).

### Graph-defined traversal

The translator rebuilds PENMAN traversal ownership from the decoded graph.
Initial node colors depend on concept, top-node status, and sorted attributes;
they are refined using incoming and outgoing role/color neighborhoods. A
ranked path search then prefers entrances that respect explicit statement and
scope boundaries.

Path costs are compared lexicographically by scope/reference penalties,
actor-reference penalties, inverse steps, path length, and a role/node
signature. Recognized `:sntN`, `:opN`, and `:ARGN` roles are ordered numerically.
The ownership rules account for explicit connective operands and conditions,
event-valued content, shared actors, object descriptions, reporting agents,
negative contexts, and references through lexical `:opN` relations. Nominal
`:part` relations receive an inverse traversal view so descriptions follow the
described part.

This stage sorts records and changes traversal annotations. It does not
infer a new semantic edge from the original sentence.

### Recursive formula

Let $L(v)$ be the conjunction of active construction atoms owned by node $v$,
and let $H(v)$ conjoin the ordinary semantic children permitted in the current
branch context. Structural and metadata records are excluded from ordinary
child traversal. If a connective has branch roots $u_i$, its branch formula is

$$
K(v)=
\begin{cases}
\bigwedge_i F(u_i), & v\text{ is an and or multi-sentence node},\\
\bigvee_i F(u_i), & v\text{ is an or node}.
\end{cases}
$$

The local body is $B(v)=L(v)\land H(v)$, additionally conjoined with $K(v)$
when branches exist. Empty local conjunctions are temporary true constants.
Node polarity gives $N(v)=\neg B(v)$ when `:polarity -` is present and
$N(v)=B(v)$ otherwise. For native condition targets $q_j$,

$$
F(v)=
\begin{cases}
(\bigwedge_j F(q_j))\to N(v), & v\text{ has conditions},\\
N(v), & \text{otherwise}.
\end{cases}
$$

Thus a conditioned negative node negates its consequent, not the entire
implication. The formula also includes disconnected active components. If
initial traversal omits an active atom, its owner is replayed as a root;
replay must add formula leaves or translation fails.

The construction helpers flatten nested occurrences of the same `and`/`or`
operator, eliminate identical repeated subtrees, and normalize Boolean
constants. Constant identities include `True & F = F`, `False | F = F`,
`False -> F = True`, and `F -> False = not F`. The process is not a general
SAT simplifier: it does not decide that arbitrary formulas such as
`F & not F` are false.

### Conditions and cycles

```penman
(g / go-01 :ARG0 (b / boy) :polarity - :condition (r / rain-01))
```

```text
Formula: ((x2) >> (~(x1)))
x1: boy go
x2: rain occurs
```

`have-condition-91` is recognized as a reified condition under these guards:

- Exactly one `ARG1` consequence and one `ARG2` condition.
- At most three outgoing relations. An optional extra relation must be
  `:mod` to a leaf `still`, `likewise`, or `especially`, with no attributes
  and exactly one incoming edge.
- No attributes on the condition node except `:wiki`.
- The condition node, consequence, and antecedent are distinct, and neither
  branch can reach back to the condition node through raw graph edges.

The `ARG1`/`ARG2` records become structural, and no unary proposition is
invented for the condition connective. An optional modifier remains a
recorded relation; its discourse-focus semantics is unresolved.

```penman
(h / have-condition-91
   :ARG1 (g / go-01 :ARG0 (b / boy))
   :ARG2 (r / rain-01))
```

```text
Formula: ((x2) >> (x1))
x1: boy go
x2: rain occurs
repairs: reified_condition
```

Rejected reified-condition patterns receive `unsupported_reified_condition`
and retain their ordinary graph treatment. Accepted ones report
`reified_condition`; the optional modifier additionally reports
`modified_reified_condition` and `condition_modifier_scope_uninterpreted`.

Ordinary semantic reentrancy to an open node does not recursively expand that
node again. A native condition referring to a proper open ancestor uses that
ancestor's locally owned atoms as a finite antecedent; an empty local
antecedent or a self-condition fails. A cycle made entirely of connective or
multi-sentence branches also fails.

A native condition may refer to a sibling conjunct when that sibling has no
condition of its own and does not reach back to the owner. A successful replay
reports `sibling_condition_reference`. The corresponding cross-disjunct case
is left unresolved with `sibling_disjunct_condition_unrepaired`.

### Shared nodes and branch isolation

An entity shared by two event branches does not cause both event atoms to be
asserted in each branch. Atom owners, event anchors, explicit branch paths,
and inverse traversal filters determine which occurrences belong there.
Inverse references must not pull an independently owned event out of another
disjunct, negative context, or condition.

For example:

```penman
(a / and
   :op1 (r / read-01 :ARG0 (s / student) :ARG1 (b / book))
   :op2 (w / write-01 :ARG0 s :ARG1 (e / essay)))
```

```text
Formula: (x1 & x2)
x1: student read book
x2: student write essay
```

The current branch rules include these narrower patterns:

| Pattern | Behavior and diagnostic |
| --- | --- |
| Explicit `and`/`or` within a multi-sentence envelope | Preserve the raw connective when the sentence-branch structure is acyclic: `sentence_connective_preserved`. A detected raw cycle reports `cyclic_sentence_connective_unrepaired`. |
| A qualified connective whose rendered name obscures `and`/`or` | Use the raw operator in the formula: `raw_connective_operator`; report `qualified_connective_atomization_unchanged`, since this does not distribute its quantity metadata. |
| A shared participant carries projected atoms from other events | Keep only projections licensed in the current declared branch: `shared_participant_projection_isolation`. |
| A projected relation is explicitly reached through its connective path | Bind its formula occurrence to that branch: `projected_branch_binding`. |
| An exclusive negative relative describes a nominal coordination | Bind its negated projected dyad within each declared operand branch: `negative_relative_branch_binding`; see the guards below. |
| A nominal is an otherwise empty disjunction operand | Add its existing unary carrier so the disjunct is not erased: `empty_or_operand_unary_carrier`. |
| A direct explicit Boolean operand is also referenced elsewhere | Preserve its declared occurrence: `explicit_boolean_reference_preserved`. |
| Inverse descriptions conflict with a declared context | Exclude that traversal entrance: `shared_entity_inverse_scope_isolation` or `semantic_shared_event_branch_isolation`. |

#### Negative relative on coordinated participants

`negative_relative_branch_binding` applies only when all of these conditions
hold:

- The `and`/`or` group has no attributes and at least two outgoing edges, all
  operand edges, with distinct targets. Each operand has exactly one incoming
  edge, no negative polarity or condition, no `-NN` predicate-sense suffix, and
  is not itself `and` or `or`.
- The negative relative node is introduced through the group's inverse
  traversal view. It has no incoming edge and exactly one outgoing edge,
  `:ARG1` to that group. Its attributes are limited to polarity and wiki.
- The group has exactly one other incoming relation, from an outer governor,
  and that relation is a forward entrance rather than another inverse
  description. The outer governor's coordinated atom projections cover every
  operand.
- The relative contributes exactly one coordinated dyad per operand, with
  no other relative atom. During compilation, the projected dyad must be
  reached along its exact declared operand path while the outer governor is
  active.

The relative dyad is negated inside each operand branch. The empty inverse
traversal does not create a separate unary proposition for the relative.
For example:

```penman
(u / use-01
   :ARG0 (m / man)
   :ARG1 (o / or
      :op1 (b / bike)
      :op2 (s / scooter)
      :ARG1-of (r / break-01 :polarity -)))
```

```text
Formula: ((~(x1) & x3) | (~(x2) & x4))
x1: bike is broken
x2: scooter is broken
x3: man use bike
x4: man use scooter
repairs: negative_relative_branch_binding, projected_branch_binding
```

### Closed entity disjunction

A further ownership rule applies to a closed `or` whose distinct operands
each participate in exactly one semantic edge to the same nominal center.
The `or` has at least two operands, no locally owned atom, no condition or
other outgoing edge, and no attributes except polarity/wiki. Each operand has
no condition or structural-connective role, no attributes except polarity/wiki, and no other
incident relation beyond its operand edge and that one semantic edge.

The common center must have no negative polarity, predicate-sense concept,
structural-connective role, attributes, condition, or incident edges outside
the selected relations. Each selected edge must correspond to exactly one
dyad without a coordinated-endpoint projection, with that edge as its sole
provenance. Participant-polarity projection is allowed only when its recorded
negative participant is exactly that branch's operand. No other atom may be
owned by the operands or common center.

The rule places each existing dyad in its operand's branch, applies that
operand's polarity locally, and then applies any polarity on the outer
`or`. It reports `closed_entity_or_edge_ownership`. It does not introduce a
new merged atom.

```penman
(o / or
   :op1 (r / red-02 :ARG1 (c / car))
   :op2 (b / blue-01 :ARG1 c :polarity -))
```

```text
Formula: (x2 | ~(x1))
x1: car is blue
x2: car is red
repairs: closed_entity_or_edge_ownership
```

Participant-polarity handling exempts the isolated dyads compiled by this
rule from a second global projection. Other polarity cases retain their
normal checks.

## Participant polarity

Event polarity is handled by the Boolean traversal described above. A negative
participant is handled separately: the translator marks each relation atom whose
participant endpoint has an explicit `:polarity -` attribute, then projects that
mark to the atom's formula occurrences.

For a triple, the participant endpoints are its subject and object. For a dyad,
the endpoint is the node-valued target of its underlying relation. Predicate,
governor, and event nodes are excluded from this endpoint test. Unary and opaque
atoms are not subject to participant projection. Two negative endpoints mark an
atom once; they are not treated as two negations that cancel each other.

Let `P` be the set of marked atom IDs. Projection traverses the existing formula
with a parity bit, initially zero:

| Formula node | Projection |
| --- | --- |
| Atom `a` | Replace it by `not(a)` if `a` is in `P` and the current parity is zero; otherwise retain it. |
| `not(phi)` | Retain `not` and project `phi` with the parity toggled. |
| `and` or `or` | Retain the operator and project each child at the current parity. |
| `implies` | Project both antecedent and consequent at the current parity. |
| `true` or `false` | Retain the constant. |

Parity counts explicit `not` nodes. An implication antecedent does not itself
toggle this bit. The projection preserves atom occurrences, ownership, and
conjunction/disjunction/implication branch positions. It does not apply De Morgan's
law or turn participant negation into event-head negation. Finalization applies
Boolean constant identities; conjunction/disjunction flattening and duplicate
removal belong to the earlier formula-combination helper.

The builder checks that projected atoms have the same structural occurrence
paths before and after this operation and that their resulting occurrences have
negative parity. The provenance entries identify the projected atoms through
`participant_polarity_projected`, `participant_polarity_source_nodes`, and
`participant_polarity_projection_id`. Projection leaves the atom verbalization
positive.

The [closed entity-disjunction rule](#closed-entity-disjunction) compiles operand
polarity locally and excludes its selected dyads from this later projection pass.

Sources: [participant selection and validation](../amr_translator/atoms.py),
[parity projection](../amr_translator/polarity.py), and
[closed entity disjunction](../amr_translator/builder.py).

### Example: negative participant

```penman
(p / play-01
   :ARG0 (c / child :polarity -)
   :location (s / statue))
```

```text
Formula: ~(x1)
x1: child play at statue
```

`diagnostic_provenance` marks `x1` as projected from participant `c`.
`warnings`, `limitations`, and `quantity_negations` are empty.

### Example: event negation already covers the participant occurrence

```penman
(g / give-01
   :polarity -
   :ARG0 (t / teacher)
   :ARG1 (b / book :polarity -)
   :ARG2 (s / student))
```

```text
Formula: ~((x1 & x2))
x1: teacher give book
x2: teacher give to student
```

`x1` is marked for participant projection from `b`, but it already occurs under
one formula negation. No second `not` is added. The shared event negation remains
around the conjunction; it is not distributed across the two atoms.

## Negative quantity metadata

Quantity negation handles a restricted case: an explicit negative quantifier
whose owner participates in one complete, independently asserted atom. For an
accepted case, the atom keeps the positive quantity phrase and the formula
negates the atom. It does not introduce quantified variables, arithmetic, or
generalized-quantifier logic.

### Candidate forms and lexical registries

A candidate is a node-valued `:quant` or `:value` metadata target with an explicit
negative polarity attribute. The metadata node must have exactly one incoming
edge, exactly one literal-minus polarity attribute, and no outgoing node edges.
`:wiki` attributes do not affect the form check. The remaining accepted forms are:

| Form | Accepted concepts and operands |
| --- | --- |
| Bare quantifier | `many`, `much`, `more`, `less`, `few`, `little`, `several`, `lot`, `enough`, `all`, `both`, `far`; no attributes other than polarity and optional `:wiki`. |
| Numeric comparator | `more-than`, `less-than`, `at-least`, `at-most`; exactly one additional attribute, `:op1`, containing a numeric literal. |

The numeric syntax is
`^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$`.
This accepts signed integers, decimal forms, and scientific notation. It is a
syntax check, not a comparison or numeric evaluation. Comparator surfaces replace
hyphens with spaces and retain the literal, such as `more than 3`.

For relation atoms, the assertion governor must be one of:

```text
use-01 run-01 walk-01 work-01 play-01 eat-01 drink-01 wear-01 carry-01
hold-01 have-03 exist-01 be-located-at-91 live-01 go-01 come-01 buy-01
sell-01 vote-01 attend-01 travel-01 spend-01 sleep-01 stand-01 sit-01
read-01 write-01 watch-01
```

For `far`, the quantified owner must additionally have one of these concepts:

```text
after before away ahead behind above below under over beyond near outside inside
```

The final metadata role must be `:quant`. Negative `:value` targets are detected
for diagnostics but are not currently accepted by this rule.

### Isolation and scope guards

The translator associates a candidate's owner with active atoms using the atoms'
node fields and source-edge provenance. An accepted candidate must satisfy all of
the following:

1. The owner occurs in exactly one active atom, and that atom has exactly one
   formula occurrence. The occurrence is either the formula root or beneath
   `and` nodes only. Existing negation, disjunction, and implication scopes are
   excluded.
2. No other negative-quantity candidate belongs to a node represented in the same
   atom. Across its represented nodes, the atom contains exactly one `:quant`
   metadata record. A second positive quantifier also fails this test.
3. The quantified owner is not itself explicitly negative or a connective
   (`and`, `or`, or `multi-sentence`).
4. The assertion is a root or a branch reached only through independent `and` or
   `multi-sentence` envelopes. Every incoming path must be unique and acyclic.
   Envelope edges must all use positive-index `:opN` or `:sntN` roles, and envelope
   attributes may only be `:wiki`. A `:mode` attribute on the assertion or an
   ancestor disqualifies the case.
5. For a unary atom, the governor must be the quantified owner, whose concept must
   not end in `-NN` or be `and`, `or`, `multi-sentence`, or `amr-unknown`. For a
   relation atom, the governor must be in the assertion registry above and may
   govern no other active atom.
6. The lexical and metadata-role conditions above must hold.

Thus a quantity inside `say-01`, `want-01`, another embedding predicate, a shared
assertion, or a multi-atom event is not automatically converted to negation of
that relation or event.

### Surface rendering and formula update

Quantity planning follows initial atom construction, surface repair, participant
projection, and frame finalization. Accepted numeric comparators require a second
surface-rendering pass because their raw polarity attribute otherwise blocks the
positive numeric renderer. In this pass, only the accepted quantity nodes ignore
their polarity attribute for rendering.

The second pass is adopted only if the formula, atom IDs, all structured atom
expressions, and the accepted node-to-atom bindings remain unchanged. Every atom
outside the numeric targets must be identical, and each target verbalization must
contain its expected positive comparator phrase. If this check fails, the numeric
candidates are rejected with `numeric_render_not_isolated` and the initial frame
is retained for them.

The accepted atom leaves are then wrapped in `not`. Finalization runs again
before ordinary template merging. This ordering prevents later positive-context
merges from treating an accepted negative quantity atom as an unnegated component.

Each accepted binding is listed in `quantity_negations`, with its metadata node,
owner, role, associated atom, governor, and positive phrase. It also adds the
`quantity_metadata_negation` repair and removes the matching
`unsupported_nested_quantity` warning for that node. A successful numeric surface
render may additionally record `numeric_operand_surface`.

### Rejection diagnostics

`quantity_negation_rejected_details` contains one row per rejected candidate.
`quantity_negation_rejected` counts these rows by reason. Each rejected row also
adds `quantity_negation_deferred_<reason>` to `warnings`; warnings are deduplicated
by `(rule, node)`, so warning-list length need not equal rejected-candidate count.
Only the first failing guard for each candidate is reported. A candidate rejected
early may have no `atom_id`, `governor`, or `positive_quantity` field.

| Reason | Meaning |
| --- | --- |
| `shared_metadata_node` | The negative quantity node does not have exactly one incoming edge. |
| `nonliteral_or_repeated_polarity` | Its polarity attributes do not consist of exactly one literal `-`. |
| `nested_quantity` | The quantity has an outgoing node edge. |
| `unsupported_quantity_form` | Its concept or remaining literal attributes do not match an accepted quantifier/comparator form. |
| `not_single_complete_atom` | The quantified owner is associated with zero or multiple active atoms. |
| `existing_or_nested_formula_scope` | The atom is absent, occurs more than once, or appears under an operator other than `and`. |
| `shared_quantified_owner` | A repeated owner-membership check finds that the owner is not represented by exactly one atom. This follows the earlier single-atom check. |
| `multiple_negative_quantities` | Another negative-quantity candidate has an owner represented in the same atom. |
| `multiple_quantifier_scopes` | The represented nodes do not contain exactly one `:quant` metadata record. |
| `negative_quantified_owner` | The quantified owner also has explicit negative polarity. |
| `embedded_or_shared_assertion` | The governor fails the independent-root/AND/sentence-envelope context check. |
| `unsupported_unary_quantity` | A unary candidate has an ineligible governor or owner concept. |
| `predicate_scope_not_registered` | A relation's governor is absent from the assertion registry. |
| `multi_atom_event` | Another active atom has the same governor. |
| `quantified_connective` | The owner is `and`, `or`, or `multi-sentence`. |
| `distance_scope_not_registered` | `far` modifies an owner outside the spatial registry. |
| `value_scope_not_registered` | The metadata role is not `:quant`. Earlier guards can reject a `:value` candidate before this guard is reached. |
| `numeric_render_not_isolated` | The numeric rerender fails the formula, identity, expression, binding, or surface-isolation checks. |

A rejection leaves this quantity-negation repair unapplied; it does not guarantee
that the remaining positive-looking surface expresses the original quantity's
meaning. `unsupported_nested_quantity` can remain alongside the rejection warning.
Absence of a rejection is also not proof of semantic coverage: quantity nodes
without the candidate trigger are outside this audit pass.

Sources: [quantity planner and formula update](../amr_translator/quantity_negation.py),
[numeric rendering](../amr_translator/builder.py), and
[pipeline isolation checks](../amr_translator/compiler.py).

### Example: accepted negative quantifier

```penman
(u / use-01
   :ARG0 (p / person)
   :ARG1 (t / tool :quant (m / many :polarity -)))
```

```text
Formula: ~(x1)
x1: person use many tools
```

The audit accepts quantity node `m`, owner `t`, atom `x1`, governor `u`, and
positive phrase `many`. It records `quantity_metadata_negation`; quantity
rejections and warnings are empty. The word `many` remains a lexical part of the
atom, with no numerical threshold assigned by the translator.

### Example: accepted negative comparator

```penman
(u / use-01
   :ARG0 (p / person)
   :ARG1 (t / tool :quant (m / more-than :op1 3 :polarity -)))
```

```text
Formula: ~(x1)
x1: person use more than 3 tools
```

The accepted positive phrase is `more than 3`; the audit records both
`numeric_operand_surface` and `quantity_metadata_negation`. The result negates
the positive proposition; it does not rewrite the comparator to `at most 3`.

### Example: quantity inside reported content is deferred

```penman
(s / say-01
   :ARG0 (p / person)
   :ARG1 (u / use-01
            :ARG0 p
            :ARG1 (t / tool :quant (m / many :polarity -))))
```

```text
Formula: (x2 & x1)
x1: person use many tools
x2: person say use
```

No quantity negation is applied. `quantity_negation_rejected` is
`{"embedded_or_shared_assertion": 1}`. The warnings are
`quantity_negation_deferred_embedded_or_shared_assertion` and
`unsupported_nested_quantity`, both for `m`. The reporting boundary is also
listed as `unresolved_reporting_content`. This output does not resolve the
reported proposition's truth or the embedded quantity's negative meaning.

## Unresolved content boundaries

The translator detects selected attitude, modal, reporting, and property-content
boundaries to restrict atom merging and report semantic limitations. A detected
boundary does not create an intensional logic operator or an encapsulated content
atom. In the current pipeline, a returned atom with an `expression.scope_type`
would be rejected as an internal contract error.

### Detected boundary families

| Boundary | Trigger | Audit status |
| --- | --- | --- |
| Attitude | `:ARG1` of `want-01`, `believe-01`, `think-01`, `hope-01`, `fear-01`, `wish-01`, `intend-01`, `pretend-01`, or `plan-01`; target concept ends in `-NN` or is `and`/`or`. | `unresolved_nonfactive_content` |
| Modal | `:ARG1` of `possible-01`, with the same target-shape condition. | `unresolved_nonfactive_content` |
| Reporting | `:ARG1` of `say-01`; target concept ends in `-NN` or is `and`/`or`. | `unresolved_reporting_content` |
| Alternate property clause | An as-yet-unlisted `:ARG1` target of any governor above, when the target has a `:domain` edge, or its concept is in the property registry below and it has an `:ARG1` edge. | `unresolved_property_content`, with `merge_guard_only: true` |

The alternate property registry is `PROPERTIES` union the keys of
`PHYSICAL_PROPERTY_SURFACES`:

```text
bad bad-07 beautiful-02 big black black-04 blue blue-02 bright brown brown-01
cheap close cold dark deep difficult easy easy-05 expensive fast fast-02
funny-02 good good-02 gray gray-02 green green-02 grey happy happy-01 hard-02
high high-02 hot important important-01 intense-02 large large-02 lax lax-01
long long-03 loud low muddy-01 new nice-01 old old-02 pink pink-04 popular
popular-02 quick-02 quiet red red-02 rough-04 sad sad-02 short slow small
small-02 strong strong-02 tall tan-01 warm weak well-09 wet-01 white white-03
wide yellow yellow-02 young young-01
```

The `:domain` test is structural and does not require membership in that
registry. A property boundary only extends merge guards; it does not add support
to the content traversal or provide a separate property-scope compiler.

Each limitation records `governor`, `concept`, `content`, `edge`, and `status`.
The detectors use the listed concepts and structures, rather than recognizing all
possible nonfactual language. A missing limitation is not evidence that an
unregistered attitude or report has received a complete semantic treatment.

### What the guards do

Ordinary merges may be rejected if their nodes include both a boundary's governor
and content, or if their source edges cross different content contexts. For the
latter test, the translator computes inside/outside edge membership by graph
reachability with the content edge blocked. It follows AMR relations and declared
inverse-description children; sharing an entity does not by itself pull every
incoming event into the content scope. A merge containing the boundary edge, or
mixing distinct inside/outside memberships when any selected edge is inside,
receives `cross_unresolved_scope` if it reaches that guard. Earlier guards may
give a different reason.

Metadata scope checks used by templates inspect the selected nodes and their
metadata descendants. Once traversal enters metadata, it examines all descendants
so nested polarity or mode cannot be silently absorbed into a positive term. The
possible metadata rejection reasons are:

| Reason | Meaning |
| --- | --- |
| `mode_deferred` | An inspected node has a `:mode` attribute or edge. |
| `unsupported_polarity_deferred` | Polarity is not exactly one literal-minus attribute. |
| `nested_polarity_deferred` | A literal-minus polarity occurs on a node not explicitly allowed by that template. |

These checks preserve the unresolved relation atoms when a proposed merge is
refused. They do not repair the broader factivity or reporting semantics. In
particular, the returned propositional formula may still conjoin an embedded
content atom with the attitude or reporting atom, as the examples below show.
Applications that need sound treatment of such content must use the diagnostics
when deciding whether to accept the translation.

Sources: [boundary detectors](../amr_translator/builder.py),
[reporting detector](../amr_translator/reporting.py),
[scope guards](../amr_translator/boundaries.py), and
[public pipeline](../amr_translator/compiler.py).

### Example: desired content remains unresolved

```penman
(w / want-01
   :ARG0 (b / boy)
   :ARG1 (f / fly-01 :ARG0 b))
```

```text
Formula: (x2 & x1)
x1: boy fly
x2: boy want fly
```

The limitation has governor `w`, content `f`, and status
`unresolved_nonfactive_content`. No scope atom or modal operator is added.

### Example: reported content remains unresolved

```penman
(s / say-01
   :ARG0 (g / girl)
   :ARG1 (f / fly-01 :ARG0 (b / boy)))
```

```text
Formula: (x2 & x1)
x1: boy fly
x2: girl say fly
```

The limitation has governor `s`, content `f`, and status
`unresolved_reporting_content`. The output does not establish that the reported
flying event happened.

### Example: alternate property content

```penman
(w / want-01
   :ARG0 (b / boy)
   :ARG1 (r / red :domain (c / car)))
```

```text
Formula: (x2 & x1)
x1: car is red
x2: boy want red
```

The limitation has governor `w`, content `r`, status
`unresolved_property_content`, and `merge_guard_only: true`. The property clause
is detected for merge isolation but is not enclosed in a nonfactual scope.

## Lexical rendering

The verbalizer reads AMR concepts, role labels, literals, names, and metadata. Each public atom contains an unsigned `verbalization`; formula negation does not insert `not` into that string. Ordinary templates use single spaces and do not append a sentence-final period. Predicates generally remain in their base form, as in `Ada give book`.

Source: [concept and literal rendering](../amr_translator/primitives.py#L26), [endpoint rendering](../amr_translator/atoms.py#L695), [role templates](../amr_translator/role_templates.py#L567), [final role dispatch](../amr_translator/verbalization.py#L221), [surface repairs](../amr_translator/surfaces.py#L84), and [public frame projection](../amr_translator/frame.py#L91).

### Concepts, literals, names, and metadata

Concept display removes a terminal hyphen followed by digits, replaces remaining hyphens and underscores with spaces, and collapses whitespace. Thus `give-01` displays as `give`; a literal concept `-` remains `-`. This display transformation does not erase the original concept or sense from the structured expression. Role lookup likewise uses the original sense-tagged concept.

Quoted literal text is decoded as JSON when possible; if decoding fails, its enclosing quotes are removed. Literal rendering replaces underscores with spaces and collapses whitespace. Literal identity separately retains the raw spelling, decoded value, and value type.

An entity with a `:name` edge to a `name` node displays the literal `:opN` name parts in numeric operand order. The first matching name edge is used. Base endpoint rendering prefers a nonempty name to concept and quantity text; the registered measurement rules can override that display. Entity identity retains its concept and a normalized name. `:wiki` metadata does not become display text or part of the canonical entity identity.

Metadata contributes to an endpoint descriptor rather than automatically creating an extra proposition. The lexical metadata families are:

| Family | Recognized roles or concepts | Rendering |
| --- | --- | --- |
| Names | `:name`, and `:opN` attributes of a `name` node | Joined name parts. |
| Quantity | `:quant` and `:unit` on any source; `:scale` on a `*-quantity` node | See quantity rules below. |
| Dates | `date-entity` with `:calendar`, `:century`, `:day`, `:dayperiod`, `:decade`, `:era`, `:month`, `:quarter`, `:season`, `:timezone`, `:weekday`, `:year` | The first year, month, and day values, when any are present, are joined with hyphens; integer month and day values are padded to width two. Missing components are omitted, without inferred placeholders. Otherwise recognized date values are joined in alphabetical role order. |
| Ordinal and value metadata | `:value`, `:range` on `ordinal-entity`, `percentage-entity`, `score-entity`, `value-interval` | An ordinal with a value displays as `ordinal VALUE`; the other listed concepts preserve this metadata structurally but have no general percentage, score, or interval display formatter at this stage. Numeric interval operands have the separate formatter below. |
| Clause and list metadata | `:mode`, `:li` | Retained as metadata; eligible mode projection has its own template below. |

The date formatter is a lexical formatter, not a calendar validator. If year/month/day are present, other date fields do not appear in that date string, although their metadata remains in the expression.

Multiple distinct targets for the same `:quant`, `:unit`, `:scale`, `:value`, or
`:polarity` role on one node trigger `ambiguous_repeated_metadata_preserved`.
The records are retained; the warning does not resolve their ambiguity.

### Quantity rendering

Source: [pluralization](../amr_translator/metadata.py#L34), [numeric operand rendering](../amr_translator/builder.py#L414), [metadata value rendering](../amr_translator/builder.py#L479), and [quantity-aware endpoint rendering](../amr_translator/builder.py#L491).

For a plain quantified noun, the first quantity precedes the noun: `1 box`, `2 boxes`. A quantity string other than exactly `1` triggers simple pluralization. A `*-quantity` concept uses its quantity and unit; without an explicit unit it uses the concept's quantity-kind word. For example, a distance quantity can display as `2 meters`.

Simple pluralization changes the last word. Its irregular dictionary is `child → children`, `man → men`, `mouse → mice`, `person → people`, `woman → women`. Otherwise endings `s`, `x`, `z`, `ch`, `sh` take `es`; consonant-plus-`y` becomes `ies`; other words take `s`. Quantity-unit rendering additionally maps `foot → feet`. This is a fixed inflector, not a general morphology engine.

Nested numeric expressions are accepted only when all their non-`:wiki` records fit the following shapes, roles are unique, and recursive operands are numeric or another supported numeric expression. Traversal rejects cycles and stops when the recursion stack reaches six nodes.

| Concept | Required operand roles | Surface |
| --- | --- | --- |
| `more-than`, `less-than`, `at-least`, `at-most`, `approximately`, `about`, `almost`, `nearly` | Exactly `:op1` | `more than N`, `less than N`, `at least N`, `at most N`, `approximately N`, `about N`, `almost N`, `nearly N`, respectively. |
| `between` | Exactly `:op1`, `:op2` | `between N and M` |
| `value-interval` | Exactly `:op1`, `:op2` | `from N to M` |
| `or` in a numeric operand | Exactly `:op1`, `:op2` | `N or M` |
| Numeric concept | No records | The numeric spelling. |
| `*-quantity` | `:quant`, `:unit`, optionally `:scale` | `VALUE [SCALE] UNIT`, with unit pluralization. |

Numeric spellings allow an optional sign, integer or decimal notation, and an optional `e`/`E` exponent. A node-valued unit must have no outgoing edges or attributes. Supported magnitude scales are exactly `hundred`, `thousand`, `million`, `billion`, `trillion`; a node-valued scale must also be a leaf. The scale is printed, not multiplied into a new number. A scaled unit is pluralized even for quantity `1`.

These numeric surfaces are used for node-valued `:quant` and `:value` metadata. An unsupported structured operand retains the base metadata display and records `unsupported_nested_quantity`. An unsupported quantity scale retains the base quantity display and records `unsupported_quantity_scale`.

Additional surface rules distinguish a measurement from a count:

- A nominal with exactly one node-valued `:quant` pointing to a supported `*-quantity` displays as `MEASUREMENT of NOUN`, for example `1 cup of coffee`. This excludes named entities, sense-tagged event concepts ending in two digits, `and`, `or`, `multi-sentence`, `name`, `amr-unknown`, other quantity concepts, and the measurement relations listed next.
- For `after`, `before`, `ago`, `away`, `apart`, `ahead`, `behind`, `above`, `below`, `under`, `over`, `beyond`, `along`, `around`, `near`, `outside`, `inside`, `across`, exactly one quantity is printed before the relation, without pluralizing it.
- A polarity record is ignored by numeric formatting only for a quantity node already accepted by the complete-proposition quantity-negation rule; that rule supplies formula negation. The numeric formatter does not independently decide negation scope.

Successful nested quantity/unit rendering records `nested_quantity_unit_surface`;
inserting a magnitude scale records `quantity_magnitude_surface`; rendering a
measured nominal as `MEASUREMENT of NOUN` records `measured_nominal_surface`.

### PropBank role resolution

Source: [numbered-role lookup](../amr_translator/propbank.py#L134) and [semantic-relation resolution](../amr_translator/role_templates.py#L368).

Numbered-role lookup accepts `ARG` followed by digits and a concept ending in a two- or three-digit sense suffix. Lookup is case-insensitive. For `lemma-NN`, it first tries `lemma.NN`, then the spelling with hyphens in the lemma changed to underscores. It selects the first candidate roleset that exists. It does not search other senses, back off to another sense, or infer a missing role from the argument number.

Resolution has six outcomes:

| Status | Meaning |
| --- | --- |
| `exact` | The roleset exists and the requested role has exactly one metadata candidate. |
| `not-numbered-role` | The role is not an `ARG` followed by digits. |
| `not-sense-predicate` | The predicate lacks the required sense suffix. |
| `missing-roleset` | Neither candidate roleset spelling exists. |
| `missing-role` | The selected roleset has no entry for this role. |
| `ambiguous-role` | The role does not have exactly one metadata candidate. |

Only `exact` supplies a semantic relation. Function tags determine the broad relation; the ordered, case-insensitive substring rules below refine it. The first matching refinement wins.

| Function | Ordered relation selection |
| --- | --- |
| `PAG` | `agent`. |
| `CAU` | `cause`. |
| `PPT` | See the ordered list below. |
| `GOL` | `beneficiary` for `benefactive`, `beneficiary`, `ordered-for`; else `result` for `attribute`, `characteristic`, `resulting state`; else `location` for `location`, `position`, `place`, `site`; else `instrument` for `instrument`, `equipment`, `tool`; else `goal`. |
| `LOC` | `path` for `path`, `route`, `course`, `trajectory`; else `source` for `source`, `origin`, `start point`; else `goal` for `destination`, `goal`, `end point`; else `location`. |
| `DIR` | `patient` for `entity left behind`, `thing left behind`, `thing milked`, `thing that is clear`; else `source` for `source`, `origin`, `start point`, `from`, `seller`, `giver`, `former job`, `place left`, `location left`, `original set`; else `goal` for `destination`, `goal`, `end point`; else `path` for `path`, `route`, `course`, `trajectory`; else `location` for `location`, `position`, `site`; else `beneficiary` for `benefactive`, `beneficiary`; else `direction`. |
| `SRC` | `source`. |
| `MNR` | `instrument` for `instrument`, `equipment`, `tool`, `means of`, `medium used`; else `accompaniment` for `decoration`, `covering`; else `manner`. |
| `COM` | `opponent` for `opponent`, `against`, `adversary`, `competitor`, `fighting`, `fighter`, `wrestler`, `arguer`; else `companion`. |
| `PRD` | `patient` for `ARG1`; otherwise `result`. |
| `EXT` | `extent`. |
| `PRP` | The `for` template; the final audit relation is `purpose`. |
| `TMP` | `time`. |
| `VSP` or any other tag | `unresolved`. |

`PPT` uses this exact order:

1. `ARG0` becomes `agent`.
2. `ARG1` with a predicate in the property-predicate set below becomes `property`.
3. Description contains `instrument`, `equipment`, `tool`, `means of`, or `medium used`: `instrument`.
4. `decoration`, `covering`, `clothing`, or `clothes`: `accompaniment`.
5. `path`, `route`, `course`, or `trajectory`: `path`.
6. `source`, `origin`, `start point`, or the substring ` from`: `source`.
7. `location`, `position`, `place`, or `site`: `location`.
8. `topic` or `subject matter`: `topic`.
9. `amount`, `extent`, `difference`, or `distance`: `extent`.
10. `attribute` or `characteristic`: `attribute`.
11. `ARG1` with a predicate in the active-ARG1 set below: `theme-active`.
12. The rule-based progressive form of the predicate occurs in the description: `theme-active`.
13. Description contains `logical subject`, `entity in motion`, `thing arising`, `thing appearing`, `thing falling`, `thing freezing`, `thing leaning`, `thing shining`, `thing sliding`, `thing spinning`, `thing swinging`, `thing trembling`, `thing forming a curve`, `swimmer`, `jumper`, `runner`, or `comer`: `theme-active`.
14. Description contains `experiencer`, `experiencing`, `now-`, `degrees (temperature`, `positive state`, `whose degree is emphasized`, or `what was fun`: `state`.
15. Description starts with `PREDICATE entity`: `state`.
16. Otherwise: `patient`.

The property-predicate set, after sense removal and hyphen-to-space conversion, is:

```text
afraid alive angry available bald bad bare beautiful big black blue bright
brown busy clean clear closed cold cool dead dark different dirty dry empty
fast friendly full good gray green grey happy high hot hurt important large
late likely little light long near necessary new old open orange pale patient
pink possible public purple quiet ready red sad safe same short similar small
snowy superior tall tan thin upset warm well wet white yellow young
```

The active-ARG1 set is:

```text
appear; arrive; bend; come; come out; come up; crouch; die; disappear; emerge;
exist; fall; fly; freeze; get; grow; hang; happen; kneel; lean; lie; occur;
remain; rest; ring; rise; shine; sit; sit down; sit up; sleep; slide; spin;
stand; stand by; stand up; step; swing; wait
```

These lexical sets apply only at the specified decision points; they do not make every occurrence of those words a property or active subject.

### Dyad templates

Source: [numbered-role templates](../amr_translator/role_templates.py#L567), [non-numbered templates](../amr_translator/role_templates.py#L185), and [non-numbered dispatch](../amr_translator/role_templates.py#L628).

Let `C` be the source/predicate surface and `D` the target surface. For a numbered role, first resolve its relation as above, then apply:

| Relation | Surface |
| --- | --- |
| `agent`, `theme-active` | `D C` |
| `patient` | `D is pp(C)` |
| `property` | `D is C` |
| `state` | `D is in state C` |
| `attribute` | `C has attribute D` |
| `goal` | `C is directed to D` |
| `location` | `C occurs at D` |
| `path` | `C occurs along D` |
| `direction` | `C proceeds toward D` |
| `source` | `C originates from D` |
| `instrument` | `C uses D` |
| `manner` | `C occurs by D` |
| `companion`, `accompaniment` | `C occurs with D` |
| `opponent` | `C occurs against D` |
| `beneficiary`, `purpose` | `C is for D` |
| `result` | `C results in D` |
| `extent` | `C has extent D` |
| `cause` | `C is caused by D` |
| `topic` | `C is about D` |
| `time` | `C occurs during D` |
| `unresolved` | Ordered base fallback: `D C` for `ARG0`, otherwise `C D`. |

For non-numbered roles, the complete fixed table is:

| AMR role | Surface |
| --- | --- |
| `purpose`, `beneficiary` | `C is for D` |
| `time`, `location` | `C occurs at D` |
| `direction` | `C proceeds toward D` |
| `domain` | `D is C` |
| `mod` | `D C` |
| `manner` | `C occurs in manner D` |
| `poss` | `D's C` |
| `poss-of` | `C's D` |
| `topic` | `C is about D` |
| `part`, `subevent-of` | `D is part of C` |
| `part-of`, `subevent` | `C is part of D` |
| `consist` | `D consists of C` |
| `consist-of` | `C consists of D` |
| `location-of` | `D occurs at C` |
| `dayperiod` | `C occurs during D` |
| `destination` | `C proceeds to D` |
| `source` | `C originates from D` |
| `instrument` | `C uses D` |
| `accompanier` | `C occurs with D` |
| `path` | `C proceeds along D` |
| `medium` | `C occurs via D` |
| `cause` | `C is caused by D` |
| `concession` | `C occurs despite D` |
| `duration` | `C lasts for D` |
| `degree` | `C has degree D` |
| `age` | `C has age D` |
| `frequency` | `C has frequency D` |
| `extent` | `C has extent D` |
| `example` | `C has example D` |

If no explicit table entry exists, `prep-TEXT` renders as `C TEXT D`, changing hyphens in `TEXT` to spaces. An `op` followed by digits renders as `C D` when it reaches this lexical stage. Other roles retain their ordered base fallback. This table does not cause metadata or connective edges to emit propositions; atom construction determines which roles reach the verbalizer. PENMAN canonicalization may also change an inverse role to its forward orientation before this stage.

An eligible reference-mode projection takes precedence over the ordinary dyad table and renders `C has D mode`. A unary carrier renders `C occurs` for a sense-tagged concept; otherwise it renders `C exist` when a quantity is present and differs from the string `1`, and `C exists` otherwise. Unary rendering is preserved instead of being reduced to the concept word alone. An opaque atom uses the dyad renderer for its retained role.

### Triple templates and precedence

Source: [triple role templates](../amr_translator/role_templates.py#L690), [triple rendering](../amr_translator/role_templates.py#L717), and [final precedence and fallback guards](../amr_translator/verbalization.py#L221).

These rules render base same-event triples; ordinary merged triples use the
[merge templates](#registered-atom-merges). Let `S`, `P`, `O` denote the selected
anchor, event-predicate, and other endpoint surfaces. Surface selection renders
a triple that base atom construction has already accepted.

1. A triple whose second role is an adjunct uses `S P PREPOSITION O`, from the full adjunct table below.
2. A triple with second role `ARG1` renders `S P O`, including when role metadata is unavailable. Its audit still records the lookup outcome.
3. Other numbered second roles use the resolved relation table below. The same relation renderer is used for a non-ARG0 anchor, while preserving the actual anchor role in the template identifier.
4. An absent or unsupported relation retains the triple's base fallback, ordinarily `S P O`.
5. A rendered triple with relation `agent`, `patient`, or `theme-active` and the literal substring ` with ` is replaced by its base fallback. This guard applies to the complete rendered string, not only to an inserted preposition.

| Adjunct second role | Preposition |
| --- | --- |
| `accompanier` | `with` |
| `beneficiary`, `duration`, `purpose` | `for` |
| `cause` | `because of` |
| `destination` | `to` |
| `direction` | `toward` |
| `extent`, `manner` | `by` |
| `instrument` | `using` |
| `location` | `at` |
| `medium` | `via` |
| `path` | `along` |
| `source` | `from` |
| `time` | `during` |
| `topic` | `about` |

| Resolved numbered-role relation | Surface |
| --- | --- |
| `goal` | `S P to O` |
| `location` | `S P at O` |
| `path` | `S P along O` |
| `direction` | `S P toward O` |
| `source` | `S P from O` |
| `instrument` | `S P using O` |
| `manner`, `extent` | `S P by O` |
| `companion`, `accompaniment` | `S P with O` |
| `opponent` | `S P against O` |
| `beneficiary`, `purpose` | `S P for O` |
| `result` | `S P O` |
| `cause` | `S P because of O` |
| `topic` | `S P about O` |
| `time` | `S P during O` |
| `property`, `state`, `attribute` | `S P as O` |
| `agent`, `patient`, `theme-active` | Base fallback after the `with` guard. |

### Inflection and local surface repairs

Source: [participle inflection](../amr_translator/role_templates.py#L258), [lexical helpers](../amr_translator/surfaces.py#L27), and [repair application](../amr_translator/surfaces.py#L144).

The past-participle function `pp` lowercases a predicate phrase and inflects its first word, retaining the remaining words. After checking the irregular dictionary below, it adds `d` to an `e` ending, changes consonant-plus-`y` to `ied`, doubles the final consonant for its explicit doubling set, and otherwise adds `ed`.

```text
bear:borne be:been bend:bent begin:begun blow:blown break:broken bring:brought
build:built buy:bought catch:caught choose:chosen cut:cut do:done drive:driven
draw:drawn eat:eaten feel:felt find:found fly:flown freeze:frozen get:gotten
give:given go:gone have:had keep:kept hide:hidden hold:held know:known leave:left
lose:lost make:made meet:met pay:paid read:read ride:ridden ring:rung rise:risen
run:run say:said see:seen send:sent sell:sold set:set shake:shaken shoot:shot
show:shown sling:slung sit:sat speak:spoken stand:stood steal:stolen strew:strewn
strike:struck string:strung spread:spread swim:swum swing:swung take:taken
teach:taught tell:told think:thought throw:thrown tear:torn upset:upset wear:worn
win:won write:written
```

The doubling set is exactly:

```text
admit beg bar chop clap commit drag drop fit gas grab hug jog knit nod plan pot
prefer refer rub step stop strap strip tag thin trap wed wrap
```

The progressive form used in role-description matching also inflects only the first word. Its ordered rules are `be → being`; final `ie → ying`; drop final `e` except after `ee` or `ye`; double the final consonant for the above set plus `begin`, `control`, `get`, `run`, `sit`, `swim`; otherwise append `ing`.

The following surface repairs operate after role templates without changing the represented AMR concept:

| Rule family | Eligibility and result |
| --- | --- |
| Possessive pronouns | A bare pronoun concept whose surface still equals that pronoun, with no `:name` edge, uses `i → my`, `you → your`, `he → his`, `she → her`, `it → its`, `we → our`, `they → their`. This repairs `poss` dyads and recorded possessive components in triples. Text inside a named entity is not reinterpreted as a pronoun. |
| Possessive phrase helper | For other owners, append `'s `, or `' ` if the owner surface ends in `s`. |
| Object pronouns | `i → me`, `he → him`, `she → her`, `we → us`, `they → them`, when an eligible recorded pronoun occupies the relevant prepositional position. The `part` dyad uses this for its owner; merge renderers reuse the helper where they specify a prepositional endpoint. |
| Copula agreement | Bare `i` takes `am`. Bare `you`, `we`, `they`, the exact plural-only noun surfaces `clothes`, `people`, `shorts`, `pants`, `trousers`, `jeans`, `glasses`, `scissors`, or a first quantity other than `1` take `are`, unless the subject concept ends in `-quantity`. Otherwise use `is`. |
| Part relation | Render `TARGET COPULA part of SOURCE`, using object-pronoun correction for an eligible source pronoun and target agreement. A target measured by exactly `1 UNIT` receives singular agreement only if its quantity node has exactly one `:unit` edge and one literal `:quant 1` attribute. This local exception does not infer agreement for approximate or nested measurements. |
| Adjectival state | An `ARG1` dyad from one of the exact state concepts below to a concept node renders `TARGET COPULA ADJECTIVE`. |
| General copular agreement | For `ARG1` or `domain`, if the existing surface starts exactly with `TARGET is ` and the target is not canonical `and`, `or`, or `multi sentence`, replace `is` with the applicable copula. |
| Compound modifier | A leaf `swim-01` modifying a `pool` through `:mod`, with no outgoing edges or attributes of its own, displays as `swimming` when the registered modifier merge renders it. Its concept identity remains `swim-01`. |
| Purpose metadata | A resolved `PRP` role is labeled `purpose` in the final metadata and template identifier. Its already selected `for` surface is unchanged. |

The exact adjectival-state map is:

```text
important-01:important easy-05:easy hard-02:hard long-03:long fast-02:fast
funny-02:funny well-09:well black-04:black white-03:white red-02:red
green-02:green yellow-02:yellow blue-02:blue brown-01:brown pink-04:pink
gray-02:gray small-02:small large-02:large young-01:young old-02:old
quick-02:quick intense-02:intense wet-01:wet
```

Additionally, `empty-02 → empty` is accepted only when it has exactly one outgoing `ARG1` edge and every outgoing edge is `ARG1` or `degree`. It does not expand the other state or merge registries.

When a merge replaces one dyad endpoint with a longer noun phrase, `render_dyad_with_term` reruns the same role template and applicable dyad repair. It accepts that rendering only if the replacement endpoint text occurs exactly once, allowing the specified object-pronoun substitution for `part`. A fallback that ignores or duplicates the replacement text does not approve the merge.

### Bundled role resources

The package includes a compact PropBank numbered-role index, its provenance README and license, and the original AMR/UMR supplemental XML. No download is needed during translation. The index stores roleset identifiers, names, and role functions/descriptions, without corpus examples or source sentences.

| Property | Pinned value |
| --- | --- |
| PropBank Frames release | `v3.4.0` |
| Frames commit | `4087fa9ab5c40907c34ff91a56acc2cab1670145` |
| AMR/UMR supplement commit | `c66e0ccf28b53f00051b187db83e937b5bee2e32` |
| Rolesets | 11,304 |
| Numbered roles | 28,810 |
| Ambiguous role entries | 1 |
| Index SHA-256 | `59f3e380bbead1e75e60bfedfb868c941e53d8e2c63b7ce3341342a8ec47dc7d` |
| Resource license | CC BY-SA 4.0 |

The supplement contributes 99 rolesets and overrides the earlier `have-degree.92` entry. The bundled XML preserves its original bytes; the index construction documented in the resource README removed one unmatched `</example>` tag under an exact source-hash check. Runtime translation uses the already bundled index, not an XML repair step.

The loader checks the index hash, schema, provenance fields, counts, and roleset-map size. `verify_resources()` additionally checks all four resource files against pinned hashes. Missing or changed resources raise `PropBankRoleIndexError`. A valid resource with an absent or ambiguous lexical entry instead produces the ordinary lookup status and surface fallback described above. Resource checks are cached within the process.

Source and full provenance: [propbank.py](../amr_translator/propbank.py), [resource README](../amr_translator/resources/propbank/README.md), and [resource license](../amr_translator/resources/propbank/LICENSE).

### Identity and text helpers

Source: [text helpers](../amr_translator/frame.py#L38) and [canonical key serialization](../amr_translator/frame.py#L64).

These helpers serve different operations; none changes the translated frame in place.

| Helper | Exact behavior |
| --- | --- |
| `canonical_atom_key(expression)` | Returns `TYPE:JSON`, with the expression type as a prefix and the remaining fields serialized using sorted keys and compact separators. It validates the type/kind pairing and does not derive identity from verbalization text. |
| `normalize_exact_match_surface(value)` | Returns `str(value).strip().casefold()`. It trims outer whitespace and performs Unicode case folding. It does not collapse internal whitespace or remove punctuation. |
| `format_nli_prompt_surface(value)` | Returns `str(value).strip()` followed by one additional literal period. It preserves case and internal whitespace. It does not check existing punctuation: `"Text."` becomes `"Text.."`. |

```python
normalize_exact_match_surface("  Ada READ book  ")  # "ada read book"
normalize_exact_match_surface("Ada  read book")    # "ada  read book"
format_nli_prompt_surface("  Ada read book  ")     # "Ada read book."
format_nli_prompt_surface("Ada read book.")        # "Ada read book.."
```

### Checked lexical examples

The following are actual `translate` outputs; the displayed formula comes from `formula_ast_to_string`. Atom identifiers may have gaps after a merge replaces earlier atoms.

**Numbered roles and a name**

```lisp
(g / give-01
   :ARG0 (p / person :name (n / name :op1 "Ada"))
   :ARG1 (b / book)
   :ARG2 (s / student))
```

```text
formula: (x1 & x2)
x1: Ada give book
x2: Ada give to student
```

**Possessive pronoun and adjectival state**

```lisp
(r / red-02 :ARG1 (c / car :poss (i / i)))
```

```text
formula: x3
x3: my car is red
```

**Measurement, date, and adjunct**

```lisp
(d / drink-01
   :ARG0 (p / person :name (n / name :op1 "Ada"))
   :ARG1 (c / coffee
      :quant (v / volume-quantity :quant 1 :unit (u / cup)))
   :time (t / date-entity :year 2026 :month 9 :day 13))
```

```text
formula: (x1 & x2)
x1: Ada drink 1 cup of coffee
x2: Ada drink during 2026-09-13
```

**Restricted adjective rule and plural agreement**

```lisp
(e / empty-02 :ARG1 (b / box :quant 2))
```

```text
formula: x1
x1: 2 boxes are empty
```

**Unknown predicate sense with an ordered fallback**

```lisp
(f / foobar-99 :ARG0 (p / person) :ARG2 (b / box))
```

```text
formula: x1
x1: person foobar box
```

**Formula negation with unchanged atom text**

```lisp
(r / read-01 :ARG0 (s / student) :ARG1 (b / book) :polarity -)
```

```text
formula: ~(x1)
x1: student read book
```

## Registered atom merges

The translator can replace two active dyadic atoms in one conjunction with one
atom whose recorded definition is their conjunction. These merges are distinct
from the initial same-event role composition: they also cover nominal modifiers,
properties, possessors, parts, and spatial paths.

The registry contains **35 rule IDs and 52 role/shape entries**. The tables below
give every rule ID, its priority, and its graph pattern. Smaller priority numbers
are considered first. The implementation is
[`templates.py`](../amr_translator/templates.py); shared scope checks are in
[`boundaries.py`](../amr_translator/boundaries.py) and
[`builder.py`](../amr_translator/builder.py).

### Candidate contract and selection

A graph match is eligible only when its two relations still have separate active
dyadic atoms after initial atom construction. Relations already absorbed by a
same-event triple are unavailable. Literal attributes, unary atoms, triples,
opaque atoms, coordination projections, participant-polarity projections, and
reference-mode projections are not ordinary merge components.

All candidates must satisfy these requirements:

1. The two node-valued edges cover exactly three distinct nodes. They have one
   shared source, form a directed path, or share a target, as specified by the rule.
2. Each component atom occurs exactly once in the formula. Both occurrences are
   immediate children of the same `and` node and neither is anywhere beneath a
   `not`. Being somewhere in the same larger formula is insufficient. An `and`
   inside a positive branch can qualify; atoms in distinct `or` or implication
   branches cannot be paired.
3. None of the three nodes has negative polarity, a condition edge, a structural
   connective role, or membership in the builder's condition mapping. A candidate
   containing both a detected content governor and its content is rejected.
4. Metadata on the candidate nodes is inspected recursively. `:mode`, unsupported
   polarity values, and nested negative polarity prevent merging. The check
   follows metadata descendants rather than absorbing unrelated event arguments.
5. The edges must have compatible ownership relative to each detected unresolved
   content boundary. The boundary edge itself cannot be consumed. Sharing an
   entity does not make an inside-content property and an outside event belong
   to one context. Rules whose IDs begin `local_` additionally check graph-based
   ownership across `or` and condition boundaries.
6. The rule-specific lexical and structural requirements below must pass. Rules
   that re-render a relation with a modified endpoint also require that the new
   endpoint text appear exactly once in the resulting surface. An unrecognized
   role realization that drops the modification is rejected.

Candidates are ordered by priority, canonical center descriptor and structural
color, rule ID, and the roles and endpoint descriptors/colors of their component
edges. Structural colors are obtained by refining node signatures with their
incoming and outgoing neighborhoods. This avoids choosing a merge simply from
the written order of AMR relations.

Selection is greedy: once an atom is consumed, later candidates using it receive
`overlapping_candidate`. Each accepted merge consumes exactly two original atoms
and two source graph records. A newly created merge atom is not reconsidered by
this pass, so a chain of three relations is not recursively compressed into a
larger atom. Unconsumed atoms remain in the frame.

For components `x1` and `x2`, a merge record has a definition of this form:

```json
{"op": "and", "args": [{"op": "atom", "id": "x1"}, {"op": "atom", "id": "x2"}]}
```

The two leaves are replaced by the new atom at their existing conjunction. Other
operators and branches remain in place. The new atom's structured expression
stores both component expressions and a `join_signature` of
`ordinary:<rule_id>`. The audit record additionally stores the component atom,
dyad, and graph-record IDs, the three nodes, the two edges, and the new surface.
`expand_macros` in `templates.py` substitutes these definitions back into a
formula. This is a definition of the Boolean macro; it is not a guarantee that
every generated English phrase captures all possible natural-language readings.

### Pattern notation and guard terms

Patterns use normalized role directions. `h`, `s`, `p`, `m`, and `o` denote AMR
nodes, not literal strings. In `p -r-> h -:mod-> m`, `h` is the shared center.
`r ∈ {…}` denotes separately registered role alternatives. Surface descriptions
below use each node's normal verbalization, including applicable quantity/name
metadata and pronoun agreement.

The guard terms used in the tables are defined precisely as follows:

| Term | Requirement |
| --- | --- |
| Nominal | Concept has no final `-NN` predicate-sense suffix and is not `and`, `or`, `name`, `amr-unknown`, or `multi-sentence`. |
| Registered modifier leaf | Concept occurs in `MODIFIER_GROUPS`; node has no outgoing edges or attributes and exactly one incoming edge. |
| Physical modifier leaf | A registered modifier leaf outside the `classifying` group. |
| Plain nominal | Nominal; not a possessive pronoun or `this`, `that`, `these`, `those`, `someone`, `anyone`, `everyone`, `nobody`; no outgoing argument role or restrictive modifier; every rendered quantity matches `[0-9]+(?:\.[0-9]+)?`. |
| Local nominal | Nominal; normally not a possessive pronoun; no outgoing argument role, `:quant`, `:ord`, `:ordinal`, `:degree`, `:mode`, `:polarity`, or `:condition`; no modifier in the local blocked set. Attributes are limited to `:wiki` and nonnegative decimal numeric `:quant`. Where a table explicitly permits pronouns, the other checks still apply. |
| Simple property | The property node has exactly one outgoing edge, of the specified role, and no attributes; its concept belongs to the stated property registry. |
| Simple spatial carrier | Concept is in `SPATIAL_CARRIERS`; exactly one outgoing edge, `:op1`, exactly one incoming edge, and no attributes. |

### Shared-source and spatial-path rules

| Priority | Rule ID | Graph pattern | Additional eligibility and surface |
| --- | --- | --- | --- |
| 0 | `attribute_degree` | `p -:ARG1-> s`, `p -:degree-> d` | `p` belongs to `PROPERTIES`; `s` is not a predicate-sense concept. `d` is a degree leaf with no outgoing edges or attributes. The set of outgoing roles at `p` must be exactly `{:ARG1, :degree}`. `real` and `extreme` additionally require one incoming edge. Surface: `s <copula> degree p`. |
| 1 | `domain_modifier` | `h -:domain-> s`, `h -:mod-> m` | Nominal center without argument roles or blocked modifiers; registered modifier leaf. Exactly one `:domain`, and its target is neither `name` nor `amr-unknown`. Surface: `s <copula> modified h`; add `a/an` for a registered countable type without quantity metadata. |
| 2 | `possessive_modifier` | `h -:mod-> m`, `h -:poss-> o` | Same nominal/modifier checks as priority 1. Surface: possessive form of `o` followed by `modified h`. This rule has no additional nominal-only restriction on `o`; the shared guards still apply. |
| 3 | `entity_two_modifiers` | `h -:mod-> m1`, `h -:mod-> m2` | Same nominal/modifier checks; the modifier concepts must differ and at most one may be classifying. Surface: ordered modifiers followed by `h`. Same-category modifiers are joined with `and`. |
| 4 | `event_simple_modifier` | `p -r-> s`, `p -:mod-> m`; `r ∈ {:ARG0, :ARG1}` | `p` is in `JOINT_ACTIVITIES`; its modifier edges contain no blocked modifier. `m` must be the exclusive leaf `together`, and `s` must not be a predicate-sense concept. Surface: the existing participant dyad surface followed by `together`. |
| 5 | `spatial_path` | `s -:location-> p -:op1-> o` | `p` must be a simple spatial carrier. Surface: `s p o`, for example `car behind house`. |
| 6 | `domain_degree` | `p -:domain-> s`, `p -:degree-> d` | Same property and degree checks as priority 0, but `p` must have exactly two outgoing edges and the degree leaf must always have exactly one incoming edge. Surface: `s <copula> degree p`. |

Priorities 1–3 also require a plain nominal center when a modifier is `male`,
`female`, `elderly`, `adult`, `teenage`, or `middle-aged`. For the modifier heads
listed in `NOMINAL_COMPOUNDS`, the exact registered modifier/head pair is required.
Other classifying entries remain eligible at these three priorities, subject to
the one-classifying-modifier limit.

The priority-0 check compares the **set of outgoing roles**, not the number of
edges. Priority 6 requires two edges as well. Neither rule is an unrestricted
degree operation over arbitrary predicates or arbitrary quantification.

### Participant, property, possession, and part rules

Priorities 7–13 require an unfocused nominal center: it must be nominal and have
no modifier in `BLOCKED_MODIFIERS`. Their modifier rules accept registered
modifier leaves, with classifying modifiers restricted to exact
`NOMINAL_COMPOUNDS` pairs. The six modifier words listed after the preceding
table additionally require a plain nominal center.

| Priority | Rule ID | Graph pattern | Additional eligibility and surface |
| --- | --- | --- | --- |
| 7 | `participant_leaf_modifier` | `p -r-> h -:mod-> m`; `r ∈ {:ARG0, :ARG1}` | `p` has a `-NN` predicate-sense suffix. Re-render its relation with `modified h` as the participant endpoint. The required surface-preservation check can still reject an otherwise matching predicate/role. |
| 8 | `participant_possessor` | `p -:ARG1-> h -:poss-> o` | Predicate-sense governor and nominal possessor. Re-render the participant relation with the possessive phrase for `h`. There is no registered `:ARG0 + :poss` variant. |
| 9 | `shared_target_properties` | `p -r1-> h <-r2- q`; `(r1,r2) ∈ {(:ARG1,:ARG1), (:ARG1,:domain), (:domain,:domain)}` | Both property sources are simple members of `PROPERTIES`, and their node surfaces differ. Surface: `h <copula> p and q`, with property order determined by modifier category, then surface. |
| 10 | `located_entity_modifier` | `h -:location-> o`, `h -:mod-> m` | Nominal location target. Surface: `modified h <copula> at o`. Omit `at` for `outdoors`, `indoors`, `here`, `there`, `outside`, `inside`. |
| 11 | `part_leaf_modifier` | `o -:part-> h -:mod-> m` | Re-render the whole-to-part relation with the modified part: `modified h <copula> part of o`. There is no additional nominal-only guard on the whole in this rule. |
| 12 | `landmark_leaf_modifier` | `s -:location-> h -:mod-> m` | Nominal source `s`. Surface: `s <copula> at modified h`. |
| 13 | `located_entity_possessor` | `h -:location-> o`, `h -:poss-> s` | Both location and possessor must be nominal. Surface: possessive phrase for `h`, followed by `<copula> at o`; the six locative adverbs listed at priority 10 omit `at`. |

Priorities 14–17 require a plain nominal center as well as the common guards.

| Priority | Rule ID | Graph pattern | Additional eligibility and surface |
| --- | --- | --- | --- |
| 14 | `physical_property_relation` | `p -r1-> h <-r2- q`; `(r1,r2) ∈ {(:ARG0,:ARG1), (:ARG0,:domain), (:ARG1,:ARG1), (:ARG1,:domain), (:domain,:domain)}` | Either both sources are simple `PHYSICAL_PROPERTY_SURFACES` properties with different mapped surfaces, or exactly one is such a property and the other relation belongs to `PROPERTY_EVENT_ROLES`. In the latter case, the event source has no attributes or restrictive modifier. Two properties produce `h <copula> property1 and property2` in lexical order. One property modifies the shared participant of the other relation. |
| 15 | `spatial_landmark_modifier` | `p -:op1-> h -:mod-> m` | `p` is a simple spatial carrier and `m` is a physical modifier leaf. Re-render the spatial relation with `modified h` as landmark. |
| 15 | `spatial_landmark_property` | `p -r-> h <-:op1- q`; `r ∈ {:ARG1, :domain}` | Exactly one simple physical property and one simple spatial carrier. Re-render the carrier relation with the property's mapped surface prefixed to the landmark. |
| 16 | `modified_whole_part` | `h -:mod-> m`, `h -:part-> o` | Physical modifier leaf and nominal part. Modify the **whole** endpoint, giving `o <copula> part of modified h`. |
| 17 | `modified_possessor` | `s -:poss-> h -:mod-> m` | Physical modifier leaf; the possessed source `s` must also be a plain nominal. Re-render possession with `modified h` as the possessor. |

Priority 9 precedes priority 14, so two eligible shared-target properties may be
accepted under `shared_target_properties` even though the physical-property rule
also matches. Likewise, a `:location + :op1` spatial path at priority 5 can consume
the carrier relation before a landmark-modifier rule is selected.

### Local property and material rules

These rules add the local nominal checks and the additional graph-based
`or`/condition ownership check. Except for `local_brand_new`, the shared center
must be a local nominal without pronouns.

| Priority | Rule ID | Graph pattern | Additional eligibility and surface |
| --- | --- | --- | --- |
| 20 | `local_domain_possessor` | `p -:domain-> h -:poss-> o` | `p` is a simple member of `LOCAL_DOMAIN_PROPERTIES`; `o` is a local nominal with pronouns permitted. Re-render the property with the possessive subject. Registered countable property types receive `a/an`: e.g. `her boy is an asset`. |
| 21 | `local_property_location` | `p -r-> h -:location-> o` **or** `p -r-> h <-:location- s`; `r ∈ {:ARG1, :domain}` | `p` is a simple `LOCAL_PROPERTIES` property. The other location endpoint is a local nominal, with pronouns permitted. Prefix the mapped property to `h` and render the location with the modified source or target, preserving which endpoint is described. |
| 22 | `local_property_part` | `p -r-> h -:part-> o` **or** `p -r-> h <-:part- s`; `r ∈ {:ARG1, :domain}` | Same property and endpoint checks as priority 21. Prefix the property to `h`, then re-render the part relation. The directed-path pattern modifies the whole; the shared-target pattern modifies the part. |
| 23 | `local_spatial_material` | `p -:op1-> h -:consist-of-> m` | Simple spatial carrier; `m` is an exclusive leaf in `LOCAL_MATERIALS`. Re-render the carrier relation with `h made of m` as landmark. |
| 24 | `local_material_modifier` | `h -:consist-of-> m`, `h -:mod-> a` | `m` is an exclusive material leaf and `a` is a physical modifier leaf. Surface: `modified h <copula> made of m`. |
| 25 | `local_brand_new` | `p -r-> s`, `p -:degree-> d`; `r ∈ {:ARG1, :domain}` | `p` is exactly `new` or `new-01`, with exactly two outgoing edges and no attributes. `d` is the exclusive leaf `brand`. Subject is a local nominal with pronouns permitted. Surface: `s <copula> brand new`. |

The local location renderer omits `at` for the six locative adverbs listed
earlier and uses object-case pronouns for unmodified pronominal locations.

### Head-specific lexical rules

The following ten rule IDs use the graph shapes of the named base patterns but
have their own stricter local guards. At least one modifier must be an exact
`LOCAL_WORD_HEADS` or `LOCAL_COMPOUNDS` match. Other modifiers must have a
registered modifier category. Every modifier is an exclusive leaf; classifying
modifiers must be exact `LOCAL_COMPOUNDS` or `NOMINAL_COMPOUNDS` pairs. Repeated
modifier concepts and two classifying modifiers are rejected.

| Priority | Rule ID | Pattern and endpoint checks |
| --- | --- | --- |
| 41 | `local_lexical_domain_modifier` | `h -:domain-> s`, `h -:mod-> m`; `s` is a local nominal with pronouns permitted. |
| 42 | `local_lexical_possessive_modifier` | `h -:poss-> o`, `h -:mod-> m`; `o` is a local nominal with pronouns permitted. |
| 43 | `local_lexical_entity_two_modifiers` | `h -:mod-> m1`, `h -:mod-> m2`. |
| 47 | `local_lexical_participant_leaf_modifier` | `p -r-> h -:mod-> m`, `r ∈ {:ARG0, :ARG1}`; `(p,r)` is in `PROPERTY_EVENT_ROLES`, or `r` is `:ARG1` and `p` has a registered adjective-state realization. `p` has no attributes or restrictive modifier. |
| 50 | `local_lexical_located_entity_modifier` | `h -:location-> o`, `h -:mod-> m`; `o` is a local nominal with pronouns permitted. |
| 51 | `local_lexical_part_leaf_modifier` | `s -:part-> h -:mod-> m`; `s` is a local nominal with pronouns permitted. |
| 52 | `local_lexical_landmark_leaf_modifier` | `s -:location-> h -:mod-> m`; `s` is a local nominal with pronouns permitted. |
| 55 | `local_lexical_spatial_landmark_modifier` | `p -:op1-> h -:mod-> m`; `p` is a simple spatial carrier. |
| 56 | `local_lexical_modified_whole_part` | `h -:part-> o`, `h -:mod-> m`; `o` is a local nominal with pronouns permitted. |
| 57 | `local_lexical_modified_possessor` | `s -:poss-> h -:mod-> m`; `s` is a local nominal with pronouns permitted. |

All ten require a local nominal center `h` without pronouns. The modified
endpoint is substituted into its existing relation realization; two-modifier
rules emit the modified head directly. Countable domain types without a quantity
receive `a/an`. `swim-01` becomes `swimming` only in the registered
`swim-01 + pool` compound. The property-state exception at priority 47 uses the
actual `adjective_state` function in
[`surfaces.py`](../amr_translator/surfaces.py); its `empty-02` case additionally
requires exactly one `:ARG1` and no outgoing role other than `:ARG1` or `:degree`.

### Lexical registries

The following registries constrain the preceding templates; they are not
synonym lists or instructions to rewrite arbitrary occurrences of these words.
Concept senses matter. For example, registering `small-02` does not register
every predicate whose lemma is `small`.

**Modifier categories and order (`MODIFIER_GROUPS`).** Category rank increases
down this table. Within a category, modifiers are ordered by their node surface,
then node identifier.

| Category | Registered concepts |
| --- | --- |
| Size | `small little big large huge tiny tall short long wide narrow giant massive thin thick` |
| Age | `young old new ancient modern elderly adult teenage middle-aged` |
| Shape | `round square rectangular circular triangular flat` |
| Color | `black white red blue green yellow brown orange purple pink gray grey golden silver beige tan blond blonde striped spotted` |
| Material | `wooden metal metallic plastic leather wool cotton stone brick glass jean denim cloth silk rubber` |
| Sex | `male female` |
| Classifying | `computer ski-01 heritage wedding sports sport office school kitchen garden dining billboard camouflage polka forest dirt hockey soccer` |

Ordinary two-modifier surfaces use `and` when both modifiers share a category.
Head-specific lexical rules use `and` only when both modifiers have color rank
(including `colorful`); their other modifiers are joined with spaces. A numeric
quantity already prefixed to the head stays before the modifier phrase.

**Blocked and restrictive modifiers.** `BLOCKED_MODIFIERS` is
`fake former alleged possible potential only even just almost merely supposedly so-called`.
`RESTRICTIVE_MODIFIERS` adds
`all each every both either neither this that these those another same other entire whole`.
The local nominal guard further adds
`most some any no much many few several various`.

**Degree concepts.**
`very really extremely quite slightly somewhat rather` retain their surfaces;
`extreme` maps to `extremely` and `real` to `really` within an accepted degree
merge. `brand` is handled only by `local_brand_new`.

**Nominal compound pairs (`NOMINAL_COMPOUNDS`).**
`billboard/sign`, `camouflage/uniform`, `polka/dot`, `forest/path`,
`forest/area`, `dirt/road`, `dirt/path`, `hockey/game`, `soccer/game`.
Each pair is `modifier/head`.

**Property concepts (`PROPERTIES`).** This is the following set plus every key
in `ADJECTIVE_STATES` listed immediately afterward:

```text
good-02 good bad-07 bad popular-02 popular lax happy-01 happy sad-02 sad
tall short long big large small young old new fast slow hot cold warm loud
quiet bright dark difficult easy expensive cheap important strong weak high
low close wide deep lax-01 nice-01 high-02 strong-02 beautiful-02 rough-04 muddy-01
```

**Adjective states (`ADJECTIVE_STATES`).** The mappings in
[`surfaces.py`](../amr_translator/surfaces.py) are:

```text
important-01 -> important    easy-05 -> easy       hard-02 -> hard
long-03 -> long              fast-02 -> fast       funny-02 -> funny
well-09 -> well              black-04 -> black     white-03 -> white
red-02 -> red                green-02 -> green     yellow-02 -> yellow
blue-02 -> blue              brown-01 -> brown     pink-04 -> pink
gray-02 -> gray              small-02 -> small     large-02 -> large
young-01 -> young            old-02 -> old         quick-02 -> quick
intense-02 -> intense        wet-01 -> wet
```

**Physical properties (`PHYSICAL_PROPERTY_SURFACES`).** The mapped concepts are
`black-04 white-03 red-02 green-02 yellow-02 blue-02 brown-01 pink-04 gray-02 small-02 large-02 young-01 old-02`
using the mappings above, plus `tan-01 -> tan`. The unsuffixed concepts
`tall short big large small young old new black white red blue green yellow brown pink gray grey`
map to themselves.

**Local properties (`LOCAL_PROPERTIES`).** This contains all physical properties,
plus unsuffixed `long wide narrow thin thick deep cold hot warm dry wet`, and
`wet-01 -> wet`, `long-03 -> long`, `rough-04 -> rough`, `muddy-01 -> muddy`.
`LOCAL_DOMAIN_PROPERTIES` contains `PROPERTIES`, the keys of `LOCAL_PROPERTIES`,
all countable types below, and
`accurate intact female male alive dead ready empty full`.

**Countable types (`COUNTABLE_TYPES`).**
`asset car desk house hotel girl creature boy man woman book city building company person amount`.

**Joint activities (`JOINT_ACTIVITIES`).**

```text
chat-01 sit-01 stand-01 walk-01 run-01 play-01 sing-01 dance-01 work-01
travel-01 eat-01 talk-01 live-01 gather-01 meet-03 party-01 huddle-01
converse-01 march-01 exercise-02 skate-01
```

**Physical-property event roles (`PROPERTY_EVENT_ROLES`).**

| Role | Registered predicates |
| --- | --- |
| `:ARG0` | `race-02 run-01 run-02 walk-01 play-01 look-01 sleep-01 sit-01 stand-01 jump-01 jump-03 eat-01 dance-01 work-01 swim-01 fly-01 move-01` |
| `:ARG1` | `carry-01 hold-01 wear-01 ride-01 drive-01 paint-01 kick-01 throw-01 pull-01 push-01 chase-01 eat-01 see-01 watch-01 fix-01 repair-01` |

**Spatial carriers (`SPATIAL_CARRIERS`).**

```text
outside inside behind above below beside near under over across around next-to
in-front-of along by beyond beneath through throughout among amongst
```

**Head-specific modifiers (`LOCAL_WORD_HEADS`).**

| Modifier | Permitted heads |
| --- | --- |
| `rocky` | `ground terrain road path area mountain hill beach shore shoreline land surface` |
| `colorful` | `shirt blouse dress clothes clothing costume umbrella painting bird flag toy building flower ball` |
| `topless` | `man woman person boy girl people` |

`colorful` has color rank; `rocky` and `topless` have material rank for ordering.
The local compound pairs are `park/bench`, `sound/equipment`, `swim-01/pool`,
`coffee/kiosk`, and `sushi/restaurant`; these follow the other modifier groups.
Literal local materials are
`wood rock stone marble metal steel iron brick concrete plastic glass`.

### Merge diagnostics and unresolved content

`translate_with_audit` returns accepted merges in `merges` and a count of rejected
candidate/template matches in `rejected_templates`. These counts do not count
failed translations or necessarily count distinct AMR subgraphs: one pair can
match several registry entries, with one accepted and another rejected. A
nonempty rejection dictionary is therefore compatible with successful merging.

Common rejection categories are:

| Category | Diagnostic IDs and meaning |
| --- | --- |
| Shape and formula placement | `not_three_distinct_nodes`, `path_not_three_nodes`, `not_unique_same_positive_conjunction`, `path_not_same_positive_conjunction`: the candidate fails the three-node or unique positive-conjunction contract. |
| Scope | `scope_boundary`, `path_scope_boundary`, `nonfactive_boundary`, `path_nonfactive_boundary`, `cross_unresolved_scope`, `local_graph_cross_boolean_scope`: polarity, connectives, conditions, or incompatible edge ownership block the merge. |
| Metadata | `mode_deferred`, `unsupported_polarity_deferred`, `nested_polarity_deferred`: the term/metadata subtree has a scope feature the merge cannot absorb. |
| Modifier/head | `modifier_not_registered_leaf`, `modifier_not_physical_leaf`, `nominal_compound_head_not_registered`, `repeated_modifier_not_compacted`, `multiple_classifying_modifiers`, `nonintersective_or_focusing_modifier`: the modifier is not an eligible local description. |
| Nominal restrictions | `not_nominal_centre`, `nominal_centre_has_argument_roles`, `not_unfocused_nominal_centre`, `new_modifier_requires_simple_local_nominal`, `not_simple_local_nominal`: the head is outside the relevant nominal guard. |
| Degree/property | `property_not_registered`, `property_of_event_not_registered`, `degree_not_simple`, `new_degree_not_exclusive`, `additional_property_relations`, `domain_degree_not_exclusive`, `properties_not_simple_registered_states`, `repeated_property_not_compacted`: a property or degree requirement fails. |
| Event/spatial/relation | `joint_activity_not_registered`, `event_has_focusing_or_modal_modifier`, `event_modifier_not_simple_together`, `event_participant_is_event`, `participant_governor_not_predicate`, `spatial_carrier_not_registered`, `spatial_carrier_not_exclusive_simple`, `possessor_not_nominal`, `location_not_nominal`, `landmark_governor_not_nominal`, `part_not_nominal`, `possessed_head_not_simple_nominal`, `unknown_domain`, `multiple_domains`: the relation family fails its role or endpoint checks. |
| Physical-property relation | `physical_property_event_role_not_registered`, `physical_property_event_has_scope_modifier`, `physical_property_not_simple_registered_state`, `spatial_landmark_not_simple_property_carrier`: physical property/event/carrier eligibility fails. |
| Head-specific lexical | `local_lexical_head_not_registered`, `local_modifier_not_registered_leaf`, `local_compound_head_not_registered`, `local_domain_not_nominal`, `local_owner_not_nominal`, `local_participant_role_not_registered`, `local_participant_has_scope_metadata`, `local_relation_target_not_nominal`, `local_relation_source_not_nominal`: the local lexical registry or endpoint restrictions fail. |
| Local property/material | `domain_possessor_not_simple_registered_predicate`, `property_relation_not_simple_registered_nodes`, `material_not_literal_leaf_or_carrier_not_simple`, `material_modifier_not_literal_physical_leaves`, `brand_degree_requires_simple_new_property`: the named local pattern fails. |
| Rendering or overlap | `surface_does_not_preserve_modified_endpoint`: re-rendering drops or duplicates the modified endpoint. `overlapping_candidate`: an earlier accepted candidate already consumed a component. |

A local merge wholly inside an unresolved content region may be accepted
while its surrounding boundary remains unresolved. Boundary detection restricts
merges; it does not supply modal or reported-speech semantics. See
[Unresolved content boundaries](#unresolved-content-boundaries) for the exact
detectors and their limits.

### Executed merge examples

The outputs below were produced by `translate_with_audit` and
`formula_ast_to_string` from the current package. All AMRs are self-contained
synthetic examples; no sentence parser or neural model is used. Atom IDs are
shown as emitted, including gaps after consumed atoms are removed.

#### Degree property

```text
(g / good-02 :ARG1 (b / boy) :degree (v / very))

formula: x3
x3: boy is very good
merge: attribute_degree, [x1, x2] -> x3
```

The audit also records one
`brand_degree_requires_simple_new_property` rejection for another template
matching the same role pair. The accepted degree merge is unaffected.

#### Participant modifier

```text
(r / run-01 :ARG0 (b / boy :mod (t / tall)))

formula: x3
x3: tall boy run
merge: participant_leaf_modifier, [x1, x2] -> x3
```

#### Shared participant and property

```text
(b / boy :ARG1-of (y / young-01) :ARG0-of (r / run-01))

formula: x3
x3: young boy run
merge: physical_property_relation, [x1, x2] -> x3
```

The two normalized edges share the boy as target. This is a registered
shared-target merge, not same-event role composition.

#### Spatial path

```text
(c / car :location (b / behind :op1 (h / house)))

formula: x3
x3: car behind house
merge: spatial_path, [x1, x2] -> x3
```

#### Head-specific compound

```text
(b / bench :mod (p / park) :location (g / garden))

formula: x3
x3: park bench is at garden
merge: local_lexical_located_entity_modifier, [x1, x2] -> x3
```

The ordinary modifier candidate is rejected with
`modifier_not_physical_leaf`; the head-specific `park/bench` entry is accepted.

#### Material and modifier

```text
(b / bench :mod (r / red) :consist-of (w / wood))

formula: x3
x3: red bench is made of wood
merge: local_material_modifier, [x1, x2] -> x3
```

#### Negative conjunction: no merge

```text
(b / boy :mod (t / tall) :mod (y / young) :polarity -)

formula: ~((x1 & x2))
x1: tall boy
x2: young boy
merges: []
rejected_templates: {"not_unique_same_positive_conjunction": 2}
```

The formula retains the shared negation and both unsigned atom surfaces.

#### Accepted local merge with an unresolved content boundary

```text
(w / want-01
   :ARG0 (b / boy)
   :ARG1 (r / run-01 :ARG0 b :mod (t / together)))

formula: (x3 & x4)
x3: boy want run
x4: boy run together
merge: event_simple_modifier, [x1, x2] -> x4
limitation: unresolved_nonfactive_content (governor w, content r)
```

The event modifier is compacted within the running content. The audit still
marks the wanting/running boundary as unresolved; this output must not be read
as a complete logical analysis of wanting.

## Diagnostics and failures

`translate_with_audit` returns the following fields. The `frame` is identical
to the result of `translate` for the same input.

| Field | Contents |
| --- | --- |
| `frame` | The final public atoms and formula AST. |
| `repairs` | Applied rule events, each with `rule` and `node`, sorted by those fields. This includes ordinary construction rules as well as surface repairs. |
| `warnings` | Unresolved construction or rendering cases, each with `rule` and `node`, sorted by those fields. |
| `limitations` | Detected nonfactual, reporting, and property-content boundaries, with their graph context. |
| `merges` | Accepted ordinary merge records: rule, component identities, graph provenance, resulting atom, and Boolean macro definition. Initial same-event triples are not entries in this list. |
| `rejected_templates` | Counts indexed by ordinary-merge rejection reason. These count rejected candidate checks, not failed input graphs or unique semantic problems. |
| `quantity_negations` | Accepted complete-proposition quantity-negation plans. |
| `quantity_negation_rejected` | Counts indexed by quantity-negation rejection reason. |
| `quantity_negation_rejected_details` | Individual rejected quantity candidates with their reason and available graph/atom context. |
| `diagnostic_provenance` | Internal atom records from before ordinary merging, including owners, canonical keys, component dyads, source records, and projection information where applicable. |

Rule events are stored as unique `(rule, node)` pairs: their number is not a
count of every application at that node. Rejection counts can include
overlapping candidates and checks reached before a later rule succeeds.

`diagnostic_provenance` is not the final atom inventory. It may retain atoms
subsequently consumed by a merge, while a new merged atom is described in
`merges` and `frame`. Use the merge's component IDs to trace it back to the
earlier records. Use `frame["atoms"]` to enumerate the actual output.

Warnings and limitations do not automatically make translation fail. For
example, a desired or reported event can produce a structurally valid frame
while its nonfactual status remains unresolved. An empty diagnostic category
is not a proof that all possible semantic limitations have been detected.

### Rejected inputs and contract errors

| Error | Trigger |
| --- | --- |
| `TypeError` | The public input is not a string or contains only whitespace. |
| PENMAN decoding errors | Invalid PENMAN syntax encountered by `penman.decode`. |
| `AMRTripleConversionError` / `AMRTripleValidationError` | Unsupported graph traversal or construction, including duplicate instance definitions, unresolvable scope cycles, or inconsistent internal records. These are `ValueError` subclasses defined in `primitives.py`. |
| `PropBankRoleIndexError` | Missing, changed, or invalid pinned role resources. This is a `ValueError` subclass defined in `propbank.py`. A missing lexical entry in a valid resource instead uses the documented role fallback. |
| `TranslatorContractError` | The public frame cannot satisfy its schema, atom identity, formula shape, or atom/formula closure requirements. This exported exception is a `RuntimeError` subclass. |
| `RuntimeError` from the compiler's assertions | A scope atom appears, an ordinary merge violates the two-dyad/three-node contract, or accepted ordinary merges reuse a component atom or source graph record. |

For example, a direct self-condition is rejected:

```lisp
(g / go-01 :condition g)
```

```text
AMRTripleConversionError: scope traversal encountered a graph cycle: g -> g
```

There is no successful empty-frame fallback for such an error. Catch failures
at the caller if processing a collection of independent AMRs.

### What validation establishes

`validate_frame(frame)` checks the public field sets; unique, non-empty atom
IDs and surfaces; supported expression type/kind pairs and JSON serialization;
formula operators and arities; absence of Boolean constants; and equality
between formula leaf IDs and the atom inventory. The compiler separately checks
the ordinary-merge invariants above.

The validator does not establish natural-language equivalence, verify the
source AMR against a sentence, or prove that a verbalization expresses every
semantic distinction in its structured atom. Its expression checks validate
type/kind and serialization, rather than a complete schema for every
template-specific payload. The boundary diagnostics identify the supported
checks described in this reference.

## Implementation map

| Source | Responsibility |
| --- | --- |
| [compiler.py](../amr_translator/compiler.py) | Public translation sequence and audit assembly. |
| [graph.py](../amr_translator/graph.py) | Graph ordering and ownership layout. |
| [atoms.py](../amr_translator/atoms.py) | Record partition, descriptors, base atoms, and recursive construction. |
| [builder.py](../amr_translator/builder.py) | Conditions, shared-node handling, quantity surfaces, and scoped graph rules. |
| [polarity.py](../amr_translator/polarity.py) | Participant-polarity projection. |
| [quantity_negation.py](../amr_translator/quantity_negation.py) | Guarded quantity-negation plans and formula rewrites. |
| [boundaries.py](../amr_translator/boundaries.py), [reporting.py](../amr_translator/reporting.py) | Boundary detection and candidate scope checks. |
| [templates.py](../amr_translator/templates.py) | Registered ordinary merges, candidate selection, and macro expansion. |
| [propbank.py](../amr_translator/propbank.py), [role_templates.py](../amr_translator/role_templates.py) | Pinned role lookup, role functions, lexical templates, and inflection. |
| [metadata.py](../amr_translator/metadata.py), [verbalization.py](../amr_translator/verbalization.py), [surfaces.py](../amr_translator/surfaces.py) | Metadata rendering, atom verbalization, and local surface rules. |
| [primitives.py](../amr_translator/primitives.py), [formula.py](../amr_translator/formula.py) | Formula constructors, traversal helpers, and constant normalization. |
| [frame.py](../amr_translator/frame.py) | Canonical keys, public frame projection, text helpers, and validation. |
| [tests](../tests/) | Executable regression cases and resource checks. |

For the accompanying paper and citation, see [the README](../README.md#citation).
