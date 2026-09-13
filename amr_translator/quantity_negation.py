"""Ordinary quantity negation rules."""
from collections import Counter, defaultdict
from copy import deepcopy
import re

from . import primitives

F = primitives
QUANTIFIERS = frozenset("many much more less few little several lot enough all both far".split())
COMPARATORS = frozenset(("more-than", "less-than", "at-least", "at-most"))
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
# Only direct extensional assertions. An event-valued object of say/cause/want
# must not turn a negative quantity inside its content into NOT(the relation).
ASSERTIONS = frozenset((
    "use-01 run-01 walk-01 work-01 play-01 eat-01 drink-01 wear-01 carry-01 "
    "hold-01 have-03 exist-01 be-located-at-91 live-01 go-01 come-01 buy-01 "
    "sell-01 vote-01 attend-01 travel-01 spend-01 sleep-01 stand-01 sit-01 "
    "read-01 write-01 watch-01"
).split())
SPATIAL = frozenset("after before away ahead behind above below under over beyond near outside inside".split())


def _nodes(atom, records):
    nodes = {atom.get(k) for k in (
        "subject_node", "object_node", "predicate_node", "governor_node",
        "event_occurrence_id", "owner_node", "outer_node")}
    for key in atom.get("source_graph_record_ids", []):
        record = records.get(key, {})
        nodes.add(record.get("source"))
        if record.get("target_is_node"):
            nodes.add(record.get("target"))
    return nodes - {None, ""}


def _locations(ast):
    result = defaultdict(list)
    def visit(node, path=()):
        op = node["op"]
        if op == "atom":
            result[node["id"]].append(path)
        elif op in ("and", "or"):
            for child in node["args"]:
                visit(child, path + (op,))
        elif op == "not":
            visit(node["arg"], path + (op,))
        elif op == "implies":
            visit(node["antecedent"], path + (op,))
            visit(node["consequent"], path + (op,))
    visit(ast)
    return result


def _plain_context(builder, governor):
    """Only a root assertion or an explicitly independent AND/snt branch."""
    agenda, seen = [governor], set()
    while agenda:
        node = agenda.pop()
        if node in seen:
            return False
        seen.add(node)
        if any(a["role"].lower() == ":mode" for a in builder.attrs_by_source.get(node, [])):
            return False
        incoming = builder.incoming.get(node, [])
        if len(incoming) > 1:
            return False
        for edge in incoming:
            parent = edge["source"]
            if (builder.concepts.get(parent) not in ("and", "multi-sentence")
                    or not re.fullmatch(r":(?:op|snt)[1-9]\d*", edge["role"].lower())
                    or any(a["role"].lower() != ":wiki" for a in builder.attrs_by_source.get(parent, []))
                    or any(not re.fullmatch(r":(?:op|snt)[1-9]\d*", e["role"].lower())
                           for e in builder.outgoing.get(parent, []))):
                return False
            agenda.append(parent)
    return True


def plan_quantity_negations(builder, internal, frame):
    """Return accepted atom bindings and rejected raw-node reasons."""
    candidates = []
    for owner, records in builder.metadata_by_source.items():
        for record in records:
            node = record.get("target")
            if (record.get("target_is_node") and record["role"].lower() in (":quant", ":value")
                    and builder._raw_has_negative_polarity(node)):
                candidates.append({"node": node, "owner": owner, "role": record["role"].lower()})
    if not candidates:
        return [], []
    records = {r["id"]: r for values in (builder.outgoing, builder.attrs_by_source)
               for rows in values.values() for r in rows}
    active = {a["id"] for a in frame["atoms"]}
    atoms = {a["id"]: a for a in internal["atoms"] if a["id"] in active}
    node_sets = {key: _nodes(atom, records) for key, atom in atoms.items()}
    locations = _locations(frame["formula_ast"])
    accepted, rejected = [], []
    for candidate in candidates:
        row = dict(candidate)
        node, owner = row["node"], row["owner"]
        concept = builder.concepts[node].casefold()
        related = [key for key, nodes in node_sets.items() if owner in nodes]
        row["associated_atoms"] = related
        reason = None
        positive = None
        attrs = [a for a in builder.attrs_by_source.get(node, []) if a["role"].lower() != ":wiki"]
        operands = [a for a in attrs if a["role"].lower() != ":polarity"]
        polarity = [a for a in attrs if a["role"].lower() == ":polarity"]
        if len(builder.incoming.get(node, [])) != 1:
            reason = "shared_metadata_node"
        elif len(polarity) != 1 or str(polarity[0]["target"]) != "-":
            reason = "nonliteral_or_repeated_polarity"
        elif builder.outgoing.get(node):
            reason = "nested_quantity"
        elif concept in QUANTIFIERS and not operands:
            positive = builder._raw_node_surface(node)
        elif (concept in COMPARATORS and len(operands) == 1
              and operands[0]["role"].lower() == ":op1"
              and NUMBER.fullmatch(F._literal_text(operands[0]["target"]))):
            positive = concept.replace("-", " ") + " " + F._literal_text(operands[0]["target"])
        else:
            reason = "unsupported_quantity_form"
        if reason is None and len(related) != 1:
            reason = "not_single_complete_atom"
        if reason is None:
            key = related[0]
            atom = atoms[key]
            governor = atom.get("event_occurrence_id") or atom.get("governor_node") or atom["owner_node"]
            row.update(atom_id=key, governor=governor, positive_quantity=positive)
            if locations.get(key) is None or len(locations[key]) != 1 or any(x != "and" for x in locations[key][0]):
                reason = "existing_or_nested_formula_scope"
            elif sum(owner in node_sets[k] for k in atoms) != 1:
                reason = "shared_quantified_owner"
            elif any(other is not candidate and other["owner"] in node_sets[key] for other in candidates):
                reason = "multiple_negative_quantities"
            elif sum(r["role"].lower() == ":quant"
                     for n in node_sets[key] for r in builder.metadata_by_source.get(n, [])) != 1:
                # One positive and one negative quantifier also have a relative
                # scope. NOT(the whole relation) is not an automatic solution.
                reason = "multiple_quantifier_scopes"
            elif builder._raw_has_negative_polarity(owner):
                reason = "negative_quantified_owner"
            elif not _plain_context(builder, governor):
                reason = "embedded_or_shared_assertion"
            elif atom.get("kind") == "unary":
                if (governor != owner or re.search(r"-\d\d$", builder.concepts[owner])
                        or builder.concepts[owner] in ("and", "or", "multi-sentence", "amr-unknown")):
                    reason = "unsupported_unary_quantity"
            elif builder.concepts.get(governor) not in ASSERTIONS:
                reason = "predicate_scope_not_registered"
            elif any(k != key and (a.get("event_occurrence_id") or a.get("governor_node") or a["owner_node"]) == governor
                     for k, a in atoms.items()):
                reason = "multi_atom_event"
            if reason is None and builder.concepts.get(owner) in ("and", "or", "multi-sentence"):
                reason = "quantified_connective"
            if reason is None and concept == "far" and builder.concepts.get(owner) not in SPATIAL:
                reason = "distance_scope_not_registered"
            if reason is None and row["role"] != ":quant":
                reason = "value_scope_not_registered"
        if reason is not None:
            row["reason"] = reason
            rejected.append(row)
        else:
            accepted.append(row)
    return accepted, rejected


def apply_quantity_negations(builder, internal, accepted, rejected):
    ids = {row["atom_id"] for row in accepted}
    def visit(node):
        if node["op"] == "atom" and node["id"] in ids:
            return F._not_ast(deepcopy(node))
        result = deepcopy(node)
        if node["op"] in ("and", "or"):
            result["args"] = [visit(c) for c in node["args"]]
        elif node["op"] == "not":
            result["arg"] = visit(node["arg"])
        elif node["op"] == "implies":
            result["antecedent"] = visit(node["antecedent"])
            result["consequent"] = visit(node["consequent"])
        return result
    internal["formula_ast"] = visit(internal["formula_ast"])
    for row in accepted:
        builder.events.add(("quantity_metadata_negation", row["node"]))
        builder.warnings.discard(("unsupported_nested_quantity", row["node"]))
    for row in rejected:
        builder.warnings.add(("quantity_negation_deferred_" + row["reason"], row["node"]))
    return dict(Counter(row["reason"] for row in rejected))
