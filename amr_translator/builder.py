"""Graph ownership, formula scope, and numeric surface rules.

The builder adds these rules directly to the base atom builder. Formula-only
views leave emitted atom provenance unchanged.
"""
from collections import defaultdict
from copy import copy, deepcopy
import re

from penman.layout import Push

from . import boundaries, metadata as metadata_rules
from . import primitives as F
from .primitives import _NUMBER_RE
from .atoms import FlatTripleBuilder
from .frame import TranslatorContractError
from .graph import graph_owned_layout, scope_reference_view
from .scope_boundaries import EVENT, SCOPES


# These are boundary detectors, NOT assertions about the truth of their content.
NONFACTIVE_CONTENT = {
    "want-01": ":arg1", "believe-01": ":arg1", "think-01": ":arg1",
    "hope-01": ":arg1", "fear-01": ":arg1", "wish-01": ":arg1",
    "intend-01": ":arg1", "pretend-01": ":arg1", "plan-01": ":arg1",
}
# Modal boundaries are detected separately from attitude-governed branches.
MODAL_CONTENT = {"possible-01": ":arg1"}
NUMERIC_UNARY = {
    "more-than": "more than", "less-than": "less than",
    "at-least": "at least", "at-most": "at most",
    "approximately": "approximately", "about": "about",
    "almost": "almost", "nearly": "nearly",
}
MAGNITUDE_SCALES = frozenset(("hundred", "thousand", "million", "billion", "trillion"))
MEASURE_RELATIONS = frozenset("after before ago away apart ahead behind above below under over beyond along around near outside inside across".split())


def plural_unit(unit):
    return {"foot": "feet"}.get(unit, metadata_rules._simple_plural(unit))


def nonfactive_boundaries(builder):
    found = []
    for node, concept in builder.concepts.items():
        role = NONFACTIVE_CONTENT.get(concept.casefold()) or MODAL_CONTENT.get(concept.casefold())
        if not role:
            continue
        for edge in builder.outgoing.get(node, []):
            if edge["role"].casefold() != role:
                continue
            target = edge["target"]
            target_concept = builder.concepts.get(target, "")
            if (re.search(r"-\d\d$", target_concept)
                    or target_concept in ("and", "or")):
                found.append({"governor": node, "concept": concept,
                              "content": target, "edge": edge["id"],
                              "status": "unresolved_nonfactive_content"})
    return found


def property_merge_boundaries(builder, existing):
    """Detect alternate property clauses for atom-merge guards.

    AMR may express a property as bare red :domain car instead of red-02
    :ARG1 car. These boundaries keep that content separate from outside
    assertions during atom merging.
    """
    from .reporting import REPORTING
    from .templates import PROPERTIES, PHYSICAL_PROPERTY_SURFACES
    roles = dict(NONFACTIVE_CONTENT, **MODAL_CONTENT)
    roles.update({concept: ':arg1' for concept in REPORTING})
    known = {(row['governor'], row['content']) for row in existing}
    properties = PROPERTIES | frozenset(PHYSICAL_PROPERTY_SURFACES)
    found = []
    for governor, concept in builder.concepts.items():
        role = roles.get(concept.casefold())
        if role is None:
            continue
        for edge in builder.outgoing.get(governor, []):
            content = edge['target']
            if edge['role'].casefold() != role or (governor, content) in known:
                continue
            content_roles = {r['role'].casefold() for r in builder.outgoing.get(content, [])}
            if (':domain' in content_roles or
                    (builder.concepts[content].casefold() in properties and ':arg1' in content_roles)):
                found.append({'governor': governor, 'concept': concept,
                              'content': content, 'edge': edge['id'],
                              'status': 'unresolved_property_content', 'merge_guard_only': True})
    return found


class GraphOwnedBuilder(FlatTripleBuilder):
    """Build atoms and formulas using graph-defined scope ownership."""

    def __init__(self, graph, *, quantity_surface_nodes=()):
        graph = graph_owned_layout(graph)
        self._stable_colors = None
        self._graph_context_cache = {}
        self._semantic_branch_regions = {}
        self._explicit_branch_regions = {}
        self.quantity_surface_nodes = frozenset(quantity_surface_nodes)
        self.events = set()
        self.warnings = set()
        self.conditions = {}
        self.negative_relatives = {}
        self.tree_parents = {}
        for edge in graph.edges():
            for item in graph.epidata.get(edge, []):
                if isinstance(item, Push):
                    self.tree_parents[item.variable] = edge.target if item.variable == edge.source else edge.source
        self._formula_stage = False
        super().__init__(graph)
        for node in self.concepts:
            values = defaultdict(set)
            for row in self.outgoing[node] + self.attrs_by_source[node]:
                if row['role'] in (':quant', ':unit', ':scale', ':value', ':polarity'):
                    values[row['role']].add(row['target'])
            if any(len(v) > 1 for v in values.values()):
                self.warnings.add(('ambiguous_repeated_metadata_preserved', node))
        for edge in self.edges:
            if (edge['role'] == ':part' and
                    not EVENT.fullmatch(self.concepts[edge['source']]) and
                    not EVENT.fullmatch(self.concepts[edge['target']])):
                pair = (edge['source'], edge['id'])
                if pair not in self.inverse_tree_children[edge['target']]:
                    self.inverse_tree_children[edge['target']].append(pair)
        self._shared_or_properties = {}
        for root in self.concepts:
            if self.concepts[root] != 'or':
                continue
            targets = {e['target'] for e in self._connective_op_edges(root)}
            if len(targets) < 2:
                continue
            properties = {}
            for prop in {e['source'] for t in targets for e in self.incoming[t]}:
                edges = self.outgoing[prop]
                roles = {e['role'] for e in edges}
                if (self.concepts[prop] in SCOPES or self.incoming[prop]
                        or roles not in ({':ARG1'},{':ARG2'})
                        or {e['target'] for e in edges} != targets or len(edges) != len(targets)
                        or any(a['role'] not in (':polarity',':wiki') for a in self.attrs_by_source[prop])):
                    continue
                properties[prop] = {e['target']: e['id'] for e in edges}
                for edge in edges:
                    pair = (prop,edge['id'])
                    if pair not in self.inverse_tree_children[edge['target']]:
                        self.inverse_tree_children[edge['target']].append(pair)
            if properties:
                self._shared_or_properties[root] = (targets,properties)

    def _build_formula(self, atoms):
        # Closed operands below compile their own local minus. The inherited
        # participant pass enforces globally negative parity, which would add
        # another NOT under a negated OR. Exempt only these isolated records;
        # every other participant retains its projection and checks.
        owners = defaultdict(list)
        for atom in atoms:
            owners[atom['owner_node']].append(atom)
        local_ids = set()
        for root in self.concepts:
            ast = self._closed_entity_or(root, owners)
            if ast is not None:
                local_ids.update(F._formula_ids(ast))
        projected = {a['id'] for a in atoms if a['id'] in local_ids and
                     a.get('participant_polarity_projected')}
        if not projected:
            return self._build_scoped_formula(atoms)
        projection_ids = {a['participant_polarity_projection_id'] for a in atoms if a['id'] in projected}
        bindings = self.participant_polarity_bindings
        self.participant_polarity_bindings = [b for b in bindings if b['projection_id'] not in projection_ids]
        view = [dict(a, participant_polarity_projected=False) if a['id'] in projected else a for a in atoms]
        try:
            return self._build_scoped_formula(view)
        finally:
            self.participant_polarity_bindings = bindings

    def _build_scoped_formula(self, atoms):
        self._formula_stage = True
        original_envelopes = self.multi_sentence_envelope_nodes
        self.multi_sentence_envelope_nodes = set(original_envelopes)
        for node in sorted(original_envelopes):
            if self._concept_canonical(node) not in ("and", "or"):
                continue
            if self._raw_statement_cycle(node):
                self.warnings.add(("cyclic_sentence_connective_unrepaired", node))
            else:
                # Formula view only: changing this during atomization can
                # change participant projections and their original owners.
                self.multi_sentence_envelope_nodes.remove(node)
                self.events.add(("sentence_connective_preserved", node))
        try:
            return super()._build_formula(atoms)
        finally:
            self._formula_stage = False
            self.multi_sentence_envelope_nodes = original_envelopes

    def _raw_statement_cycle(self, root):
        agenda = [root]
        seen = set()
        while agenda:
            node = agenda.pop()
            if node in seen:
                continue
            seen.add(node)
            for edge in self.outgoing.get(node, []):
                if not re.fullmatch(r":(?:op[1-9]\d*|snt[1-9]\d*|rel)", edge["role"]):
                    continue
                if edge["target"] == root:
                    return True
                agenda.append(edge["target"])
        return False

    def _connective_op_edges(self, node):
        # Quantification can make node_surface(or) become "some ors". The
        # Boolean operator is identified by its raw concept, never that text.
        # This is formula-only: group-quantity atomization stays unchanged and
        # is disclosed as unresolved rather than distributing quantifiers.
        if self._formula_stage and self._concept_canonical(node) in ("and", "or"):
            if self.node_canonical(node) not in ("and", "or"):
                self.events.add(("raw_connective_operator", node))
                self.warnings.add(("qualified_connective_atomization_unchanged", node))
                return F._ordered_role_items([e for e in self.outgoing.get(node, [])
                    if e["id"] in self.structural_ids and F._OP_ROLE_RE.match(e["role"])])
        return super()._connective_op_edges(node)

    def _mark_structural_records(self):
        super()._mark_structural_records()
        for node, concept in self.concepts.items():
            if concept != "have-condition-91":
                continue
            edges = self.outgoing.get(node, [])
            args = {r: [e for e in edges if e["role"].casefold() == r]
                    for r in (":arg1", ":arg2")}
            unsupported_attrs = [a for a in self.attrs_by_source.get(node, [])
                                 if a["role"].casefold() != ":wiki"]
            # The AMR roleset defines ARG1 as main clause, ARG2 as condition.
            # Polarity, extra relations and self-reference require other rules.
            extras = [e for e in edges if e["role"].casefold() not in args]
            safe_extras = all(e["role"].casefold() == ":mod"
                              and self.concepts[e["target"]] in ("still", "likewise", "especially")
                              and not self.outgoing.get(e["target"])
                              and not self.attrs_by_source.get(e["target"])
                              and len(self.incoming.get(e["target"], [])) == 1 for e in extras)
            valid = (len(edges) <= 3 and safe_extras and not unsupported_attrs
                     and all(len(x) == 1 for x in args.values()))
            if valid:
                consequence = args[":arg1"][0]["target"]
                condition = args[":arg2"][0]["target"]
                valid = len({node, consequence, condition}) == 3
                if valid and (self._raw_reaches(condition, node) or self._raw_reaches(consequence, node)):
                    valid = False
            if not valid:
                self.warnings.add(("unsupported_reified_condition", node))
                continue
            self.conditions[node] = (condition, consequence)
            self.structural_ids.update(e["id"] for es in args.values() for e in es)
            self.events.add(("reified_condition", node))
            if extras:
                # Keep the modifier as its own recorded relation. We recover
                # the conditional direction, not a semantics for discourse focus.
                self.events.add(("modified_reified_condition", node))
                self.warnings.add(("condition_modifier_scope_uninterpreted", node))

    def _raw_reaches(self, start, goal):
        visited, agenda = set(), [start]
        while agenda:
            node = agenda.pop()
            if node == goal:
                return True
            if node in visited:
                continue
            visited.add(node)
            agenda.extend(e["target"] for e in self.outgoing.get(node, []))
        return False

    def _build_unary_records(self):
        super()._build_unary_records()
        # A recognized logical connective is not an additional proposition.
        if self.conditions:
            self.unary_records = [r for r in self.unary_records
                                  if r["node"] not in self.conditions]

    def _formula_traversal_children(self, node):
        if node in self.conditions:
            return list(self.conditions[node])
        return super()._formula_traversal_children(node)

    def _compile_condition_target(self, owner_node, edge, owner_atoms, stack):
        target = edge["target"]
        if target not in stack:
            # A condition may explicitly refer to a sibling clause. Branch
            # filtering must not erase that referenced antecedent merely
            # because we are currently compiling the other branch. Retain the
            # owner in the stack to block semantic back-edges, and the original
            # connective to keep unrelated siblings out of the antecedent.
            # Conditional cycles and chained references are not replayed here.
            for root, active, targets in self._propositional_scopes(tuple(stack)):
                if (target in set(targets) - {active}
                        and not self._condition_edges(target)
                        and not self._raw_reaches(target, owner_node)):
                    if self._concept_canonical(root) == "or":
                        # Replaying A inside a sibling alternative produces
                        # A OR (A -> B), a tautology. This cross-disjunct AMR
                        # condition is not covered by the conjunctive repair;
                        # preserve the existing traversal and report the limitation.
                        self.warnings.add(("sibling_disjunct_condition_unrepaired", owner_node))
                        continue
                    replay_stack = (owner_node,) + tuple(stack[:stack.index(root) + 1])
                    result = self._compile_node(target, owner_atoms, replay_stack)
                    if F._formula_ids(result):
                        self.events.add(("sibling_condition_reference", owner_node))
                        return result
        return super()._compile_condition_target(owner_node, edge, owner_atoms, stack)

    def _projection_is_in_declared_branch(self, atom, node, stack):
        root = atom.get("coordination_root_node")
        path = atom.get("coordination_path", [])
        if root not in stack or not path:
            return False
        branch_nodes = [root]
        for eid in path:
            edge = self.edge_by_id.get(str(eid))
            if (not edge or edge["source"] != branch_nodes[-1]
                    or not re.fullmatch(r":op[1-9]\d*", edge["role"])):
                return False
            branch_nodes.append(edge["target"])
        if branch_nodes[-1] != node:
            return False
        actual = list(stack) + [node]
        start = actual.index(root)
        return actual[start:] == branch_nodes

    def _compile_node(self, node, owner_atoms, stack):
        for i, root in enumerate(stack[:-1]):
            if self.concepts.get(root) != 'or':
                continue
            targets = {edge['target'] for edge in self._connective_op_edges(root)}
            if stack[i + 1] in targets and node in targets - {stack[i + 1]}:
                if not self._explicit_boolean_operand(node, stack):
                    return {'op': 'true'}
                self.events.add(('explicit_boolean_reference_preserved', node))
        # Select the existing edge-specific atom for a shared property.
        for i, root in enumerate(stack[:-1]):
            shared = getattr(self, '_shared_or_properties', {}).get(root)
            if not shared:
                continue
            targets, properties = shared
            active = stack[i + 1]
            if active in targets and node in properties:
                eid = properties[node][active]
                owner_atoms = dict(owner_atoms)
                owner_atoms[node] = [atom for atom in owner_atoms.get(node, [])
                                     if eid in atom.get('source_graph_record_ids', [])]
        original_inverse = self.inverse_tree_children.get(node, [])
        retained = [(child, eid) for child, eid in original_inverse
                    if not self._inverse_statement_conflict(child, (*stack, node))]
        if retained != original_inverse:
            self.events.add(("shared_entity_inverse_scope_isolation", node))
            self.inverse_tree_children[node] = retained
        try:
            relative = self.negative_relatives.get(node)
            if relative and relative[0] in stack:
                # Its signed property is emitted in each declared operand branch.
                return {"op": "true"}
            local = list(owner_atoms.get(node, []))
            if stack:
                # A shared participant can carry projections from several events.
                # Re-entering the participant is not an assertion of those events.
                # Their atoms belong in their governor's declared OP traversal.
                retained = [a for a in local if not a.get("coordination_root_node")
                            or self._atom_branch_anchor(a) in (*stack, node)
                            or self._projection_is_in_declared_branch(a, node, stack)]
                if len(retained) != len(local):
                    owner_atoms = dict(owner_atoms)
                    owner_atoms[node] = local = retained
                    self.events.add(("shared_participant_projection_isolation", node))
            anchors = {self._atom_branch_anchor(a) for a in local}
            active = next((n for n in reversed(stack) if n in anchors), "")
            additions = set()
            negative_ids = set()
            if active:
                for atom in local:
                    if not self._projection_is_in_declared_branch(atom, node, stack):
                        continue
                    root = atom["coordination_root_node"]
                    anchor = self._atom_branch_anchor(atom)
                    if self.negative_relatives.get(anchor) == (root, active):
                        additions.add(anchor)
                        negative_ids.add(atom["id"])
                        self.events.add(("negative_relative_branch_binding", root))
                    attached = {n for n, _eid in self.inverse_tree_children.get(root, [])}
                    # Only a positive relative predicate introduced directly on the
                    # coordination is shared with its active outer event. Another
                    # event merely referring to the same participants is NOT shared:
                    # e.g. NOT(trim(plants OR shrubs)) AND overgrow(plants OR shrubs).
                    if (anchor in attached and anchor not in (active, node)
                            and not self._raw_has_negative_polarity(anchor)
                            and not self._condition_edges(anchor)
                            and all(e["id"] in {eid for _n, eid in self.inverse_tree_children.get(anchor, [])}
                                    for e in self.incoming.get(anchor, []))
                            and self.concepts.get(anchor) not in NONFACTIVE_CONTENT):
                        additions.add(anchor)
                if additions:
                    replacements = []
                    for atom in local:
                        anchor = self._atom_branch_anchor(atom)
                        if anchor in additions or anchor in (active, node):
                            # Formula view only; the emitted provenance is unchanged.
                            atom = dict(atom)
                            atom["event_occurrence_id"] = node
                            replacements.append(atom)
                    owner_atoms = dict(owner_atoms)
                    owner_atoms[node] = replacements
                    self.events.add(("projected_branch_binding", node))
            result = super()._compile_node(node, owner_atoms, stack)
            if negative_ids:
                def signed(n):
                    if n["op"] == "atom" and n["id"] in negative_ids:
                        return F._not_ast(n)
                    # Copy each AST node once while rebuilding its children.
                    n = dict(n)
                    if n["op"] in ("and", "or"):
                        n["args"] = [signed(c) for c in n["args"]]
                    elif n["op"] == "not":
                        n["arg"] = signed(n["arg"])
                    elif n["op"] == "implies":
                        n["antecedent"] = signed(n["antecedent"])
                        n["consequent"] = signed(n["consequent"])
                    return n
                result = signed(result)
            return result
        finally:
            self.inverse_tree_children[node] = original_inverse

    def _compile_node_core(self, node, owner_atoms, stack):
        if node not in stack and self._formula_stage:
            result = self._closed_entity_or(node, owner_atoms)
            if result is not None:
                return result
        if node not in self.conditions:
            return super()._compile_node_core(node, owner_atoms, stack)
        if node in stack:
            raise TranslatorContractError("conditional scope cycle")
        condition, consequence = self.conditions[node]
        next_stack = (*stack, node)
        antecedent = self._compile_node(condition, owner_atoms, next_stack)
        consequent = self._compile_node(consequence, owner_atoms, next_stack)
        if not F._formula_ids(antecedent) or not F._formula_ids(consequent):
            raise TranslatorContractError("condition lacks a proposition")
        conditional = {"op": "implies", "antecedent": antecedent,
                       "consequent": consequent}
        return F._combine_ast("and", [conditional] + [F._atom_ast(a["id"])
                              for a in owner_atoms.get(node, [])])

    def _numeric_node_surface(self, node, stack=()):
        if node in stack or len(stack) >= 6:
            return None
        records = list(self.outgoing.get(node, [])) + list(self.attrs_by_source.get(node, []))
        records = [r for r in records if r["role"].casefold() != ":wiki"]
        # Only nodes already accepted by the complete-proposition scope guard.
        # Their negative sign is supplied by formula NOT, not by quantity text.
        if node in self.quantity_surface_nodes:
            records = [r for r in records if r["role"].casefold() != ":polarity"]
        concept = self.concepts.get(node, "").casefold()
        if concept.endswith("-quantity"):
            roles = {r["role"].casefold(): r for r in records}
            if len(roles) != len(records) or not {":quant", ":unit"} <= set(roles):
                return None
            if set(roles) - {":quant", ":unit", ":scale"}:
                return None
            quant, unit = roles[":quant"], roles[":unit"]
            value = (self._numeric_node_surface(quant["target"], (*stack, node))
                     if quant.get("target_is_node") else F._literal_text(quant["target"]))
            if not value or (not quant.get("target_is_node") and not _NUMBER_RE.fullmatch(value)):
                return None
            if unit.get("target_is_node"):
                u = unit["target"]
                if self.outgoing.get(u) or self.attrs_by_source.get(u):
                    return None
                unit_text = self._raw_node_surface(u)
            else:
                unit_text = F._literal_text(unit["target"])
            scale = ""
            if ":scale" in roles:
                r = roles[":scale"]
                scale = self.concepts.get(r["target"], "") if r.get("target_is_node") else F._literal_text(r["target"])
                if (scale not in MAGNITUDE_SCALES or (r.get("target_is_node")
                        and (self.outgoing.get(r["target"]) or self.attrs_by_source.get(r["target"])))):
                    return None
            if value != "1" or scale:
                unit_text = plural_unit(unit_text)
            self.events.add(("nested_quantity_unit_surface", node))
            return " ".join(x for x in (value, scale, unit_text) if x)
        by_role = {}
        for record in records:
            role = record["role"].casefold()
            if role in by_role:
                return None
            if record.get("target_is_node"):
                value = self._numeric_node_surface(record["target"], (*stack, node))
            else:
                value = F._literal_text(record["target"])
                if not _NUMBER_RE.fullmatch(value):
                    value = None
            if value is None:
                return None
            by_role[role] = value
        if concept in NUMERIC_UNARY and set(by_role) == {":op1"}:
            return NUMERIC_UNARY[concept] + " " + by_role[":op1"]
        if concept == "between" and set(by_role) == {":op1", ":op2"}:
            return "between {} and {}".format(by_role[":op1"], by_role[":op2"])
        if concept == "value-interval" and set(by_role) == {":op1", ":op2"}:
            return "from {} to {}".format(by_role[":op1"], by_role[":op2"])
        if concept == "or" and set(by_role) == {":op1", ":op2"}:
            return "{} or {}".format(by_role[":op1"], by_role[":op2"])
        if _NUMBER_RE.fullmatch(concept) and not records:
            return concept
        return None

    def _metadata_value_surface(self, record):
        if record.get("target_is_node"):
            node = str(record["target"])
            if record["role"].casefold() in (":quant", ":value"):
                value = self._numeric_node_surface(node)
                if value is not None:
                    self.events.add(("numeric_operand_surface", node))
                    return value
                if self.outgoing.get(node) or self.attrs_by_source.get(node):
                    self.warnings.add(("unsupported_nested_quantity", node))
        return super()._metadata_value_surface(record)

    def node_surface(self, node):
        surface = super().node_surface(node)
        concept = self.concepts.get(node, "")
        quant_records = [r for r in self.metadata_by_source.get(node, []) if r["role"].lower() == ":quant"]
        if (len(quant_records) == 1 and quant_records[0].get("target_is_node")
                and self.concepts.get(quant_records[0]["target"], "").endswith("-quantity")):
            measure = self._numeric_node_surface(quant_records[0]["target"])
            if (measure and concept not in MEASURE_RELATIONS and not concept.endswith("-quantity")
                    and not re.search(r"-\d\d$", concept)
                    and concept not in ("and", "or", "multi-sentence", "name", "amr-unknown")
                    and not self._name_surface(node)):
                # An explicit measurement is not a count of the head noun:
                # :quant (volume-quantity :quant 1 :unit cup) -> 1 cup of coffee.
                self.events.add(("measured_nominal_surface", node))
                return measure + " of " + self._raw_node_surface(node)
        if concept in MEASURE_RELATIONS:
            values = self._metadata_values(node).get("quant", [])
            if len(values) == 1:
                # Distances and durations modify a relation, not a plural noun.
                return values[0] + " " + self.concepts[node]
        if not self.concepts.get(node, "").endswith("-quantity"):
            return surface
        metadata = self._metadata_values(node)
        scales = metadata.get("scale", [])
        quantities, units = metadata.get("quant", []), metadata.get("unit", [])
        if not scales:
            if len(quantities) == len(units) == 1 and units[0] == "foot" and quantities[0] != "1":
                return quantities[0] + " feet"
            return surface
        if (len(scales) == len(quantities) == len(units) == 1
                and scales[0] in MAGNITUDE_SCALES):
            # A magnitude is expressed, not multiplied or rounded away. The
            # original structured metadata and unit identity remain intact.
            prefix = quantities[0] + " "
            if surface.startswith(prefix):
                self.events.add(("quantity_magnitude_surface", node))
                unit = plural_unit(units[0])
                return prefix + scales[0] + " " + unit
        self.warnings.add(("unsupported_quantity_scale", node))
        return surface

    def _add_required_unaries(self):
        # A leaf negative relative on a nominal coordination is already
        # represented by one projected property per operand. Do not invent a
        # separate "break occurs" carrier for its now-empty inverse traversal.
        for root, concept in self.concepts.items():
            if concept not in ("and", "or") or self.attrs_by_source.get(root):
                continue
            edges = self.outgoing.get(root, [])
            if len(edges) < 2 or any(not F._OP_ROLE_RE.fullmatch(e["role"]) for e in edges):
                continue
            operands = {e["target"] for e in edges}
            if len(operands) != len(edges) or any(
                    re.search(r"-\d\d$", self.concepts[n])
                    or self.concepts[n] in ("and", "or")
                    or self._raw_has_negative_polarity(n)
                    or self._condition_edges(n)
                    or len(self.incoming.get(n, [])) != 1 for n in operands):
                continue
            for anchor, _eid in self.inverse_tree_children.get(root, []):
                outgoing = self.outgoing.get(anchor, [])
                attrs = self.attrs_by_source.get(anchor, [])
                if (len(outgoing) != 1 or outgoing[0]["target"] != root
                        or outgoing[0]["role"].casefold() != ":arg1"
                        or self.incoming.get(anchor)
                        or not self._raw_has_negative_polarity(anchor)
                        or any(a["role"].casefold() not in (":polarity", ":wiki") for a in attrs)):
                    continue
                others = [e for e in self.incoming.get(root, []) if e["source"] != anchor]
                if (len(others) != 1 or others[0]["source"] == anchor
                        or others[0]["id"] in {eid for _n, eid in self.inverse_tree_children.get(root, [])}):
                    continue
                governor = others[0]["source"]
                outer = [a for a in self.atom_specs
                         if self._atom_branch_anchor(a) == governor
                         and a.get("coordination_root_node") == root]
                if {a["_owner"] for a in outer} != operands:
                    continue
                projected = [a for a in self.atom_specs if self._atom_branch_anchor(a) == anchor]
                if (len(projected) != len(operands)
                        or {a["_owner"] for a in projected} != operands
                        or any(a.get("coordination_root_node") != root
                               or a.get("kind") != "dyadic" for a in projected)):
                    continue
                self.negative_relatives[anchor] = (root, governor)
        original = self.unary_records
        self.unary_records = [r for r in original if r["node"] not in self.negative_relatives]
        try:
            super()._add_required_unaries()
        finally:
            self.unary_records = original
        records = {r['node']: r for r in self.unary_records}
        for root in self.concepts:
            if self.concepts[root] != 'or' or root in self.metadata_term_nodes:
                continue
            owners = defaultdict(list)
            for index, spec in enumerate(self.atom_specs):
                owners[spec['_owner']].append(dict(spec, id='carrier-probe-'+str(index)))
            if self._closed_entity_or(root, owners) is not None:
                continue
            for edge in self._connective_op_edges(root):
                node = edge['target']
                if (node not in records or node in self.metadata_term_nodes or
                        self.concepts[node] in ('and', 'or', 'multi-sentence', 'name')):
                    continue
                # Being an argument in another branch does not represent an
                # otherwise empty disjunct. Reuse the existing unary carrier;
                # do not erase the operand with OR(True, ...)/empty filtering.
                if F._formula_ids(self._compile_node(node, self._active_formula_probe(), (root,))):
                    continue
                unary = records[node]
                self._add_spec(dict(_owner=node, kind='unary', arity=1,
                    terms=[self.node_surface(node)], predicate=self.node_surface(node),
                    component_dyad_ids=[], source_graph_record_ids=[],
                    canonical_payload=deepcopy(unary['canonical_payload']),
                    canonical_key=unary['canonical_concept'], base_surface_text=self._bare_unary_surface(node)))
                self.events.add(('empty_or_operand_unary_carrier', node))

    def _explicit_boolean_operand(self, node, stack):
        if not stack:
            return False
        parent = stack[-1]
        # Use only condition structures already recognized by the compiler.
        # Arbitrary ARG1/ARG2 links do not become Boolean scope edges here.
        if node in getattr(self, 'conditions', {}).get(parent, ()):
            return True
        # Native cross-disjunct :condition remains an unsupported case.
        return (self.concepts.get(parent) in ('and', 'or', 'multi-sentence')
                and any(edge['target'] == node for edge in self._connective_op_edges(parent)))

    def _inverse_statement_conflict(self, child, stack):
        for i, root in enumerate(stack[:-1]):
            shared = getattr(self,'_shared_or_properties',{}).get(root)
            if shared and child in shared[1] and stack[i+1] == stack[-1] and stack[-1] in shared[0]:
                return False
        # The relation is owned by the described part, not by where the whole
        # happens to be introduced. Keep the existing narrow nominal exception.
        if (stack and not EVENT.fullmatch(self.concepts.get(child, ''))
                and any(e['source'] == child and e['target'] == stack[-1]
                        and e['role'] == ':part' for e in self.edges)):
            return False
        path, current = [], child
        while current not in path:
            path.append(current)
            if current not in self.tree_parents:
                break
            current = self.tree_parents[current]
        path.reverse()
        # Reentrancy does not assert a predicate from an exclusive or negative
        # context outside that context, nor capture another declared predicate
        # inside it. Forward event references keep the existing semantics.
        for index, owner in enumerate(path[:-1]):
            if self.concepts.get(owner) == 'or':
                operands = {e['target'] for e in self._connective_op_edges(owner)}
                if path[index+1] in operands and path[index+1] not in stack:
                    return True
            if self._raw_has_negative_polarity(owner) and owner not in stack:
                return True
        for owner in stack:
            if self._raw_has_negative_polarity(owner) and owner not in path:
                return True
            antecedents = {e['target'] for e in self._condition_edges(owner)}
            if owner in self.conditions:
                antecedents.add(self.conditions[owner][0])
            for condition in antecedents:
                if (condition in stack) != (condition in path):
                    # A bare property of a shared argument is not another
                    # explicit event. Keep it when either side mentions that
                    # same argument; this cannot replay a consequent clause.
                    edges = self.outgoing.get(child, [])
                    if (len(edges) == 1 and edges[0]['target'] == stack[-1]
                            and re.fullmatch(r':ARG[012]', edges[0]['role'])
                            and not self.incoming.get(child)
                            and self.concepts[child] not in SCOPES
                            and not self._raw_has_negative_polarity(child)):
                        continue
                    return True
        # Nominal descriptions belong to their described item, not always the
        # edge source: whole :part item, but item :poss owner. 'His eyes' must
        # not lose ownership because 'he' occurs in a negative sibling clause.
        concept = self.concepts.get(child, '')
        if not (EVENT.fullmatch(concept) or concept in ('and', 'or', 'multi-sentence')):
            edges = [e for e in self.outgoing.get(child, [])
                     if stack and e['target'] == stack[-1] and e['role'].lower() == ':poss']
            if len(edges) != 1:
                return self._declared_statement_conflict(child, stack)
        for i, root in enumerate(stack[:-1]):
            if self.concepts.get(root) not in ('and', 'or', 'multi-sentence'):
                continue
            regions = self.explicit_regions(root)
            active = stack[i + 1]
            if active not in regions:
                continue
            memberships = {target for target, nodes in regions.items() if child in nodes}
            if memberships:
                if active not in memberships:
                    self.events.add(('semantic_shared_event_branch_isolation', child))
                    return True
        # Check declared statement paths for conditional antecedents and
        # qualified contexts not resolved by OP/SNT membership.
        return self._declared_statement_conflict(child, stack)

    def _declared_statement_conflict(self, child, stack):
        # Inverse predicates introduced in another explicit statement branch
        # are not replayed just because that branch's entity is mentioned here.
        # An explicit forward reference to the event itself is still compiled.
        path = [child]
        while path[-1] in self.tree_parents and self.tree_parents[path[-1]] not in path:
            path.append(self.tree_parents[path[-1]])
        path.reverse()
        for i, root in enumerate(stack[:-1]):
            if self.concepts.get(root) not in ("and", "or", "multi-sentence"):
                continue
            targets = {e["target"] for e in self._connective_op_edges(root)}
            if root not in path[:-1] or stack[i + 1] not in targets:
                continue
            declared = path[path.index(root) + 1]
            if declared in targets and declared != stack[i + 1]:
                return True
        return False

    def scope_branch_regions(self, root):
        """Planning-only ownership of explicit branches and descriptions.

        Shared participants may be in several operands. An incoming predicate
        is not owned by a participant merely because PENMAN defines it there.
        Cyclic branches are left to the existing cycle/limitation handling.
        """
        if root in self._semantic_branch_regions:
            return self._semantic_branch_regions[root]
        # Also breaks recursive queries on cyclic connective graphs.
        self._semantic_branch_regions[root] = {}
        explicit = {e['target'] for n in self.concepts
                    if self.concepts[n] in ('and', 'or', 'multi-sentence')
                    for e in self._connective_op_edges(n)}

        def forward(start):
            agenda, seen = [start], set()
            while agenda:
                node = agenda.pop()
                if node in seen:
                    continue
                seen.add(node)
                agenda.extend(e['target'] for e in self.outgoing.get(node, []))
                if node != root and self.concepts.get(node) in ('and', 'or', 'multi-sentence'):
                    agenda.extend(n for nodes in self.scope_branch_regions(node).values() for n in nodes)
            return seen

        regions = {e['target']: forward(e['target']) for e in self._connective_op_edges(root)}
        if any(root in nodes for nodes in regions.values()):
            return {}
        # Follow an inverse description only from an exclusively owned node.
        # Shared participants and another explicit statement never license it.
        # Competing proposals or links into a different unique owner defer it.
        while True:
            memberships = defaultdict(set)
            for branch, nodes in regions.items():
                for node in nodes:
                    memberships[node].add(branch)
            proposals, closures = defaultdict(set), {}
            for node, owners in memberships.items():
                if len(owners) != 1:
                    continue
                for edge in self.incoming.get(node, []):
                    source = edge['source']
                    if (source in memberships or source == root or source in explicit
                            or edge['id'] in self.structural_ids):
                        continue
                    if source not in closures:
                        closures[source] = forward(source)
                    closure = closures[source]
                    if root in closure or any(len(memberships.get(n, ())) == 1 and
                                              memberships[n] != owners for n in closure):
                        continue
                    proposals[source].update(owners)
            changed = False
            for source, owners in proposals.items():
                if len(owners) == 1:
                    branch = next(iter(owners))
                    additions = closures[source] - regions[branch]
                    changed |= bool(additions)
                    regions[branch].update(additions)
            if not changed:
                break
        self._semantic_branch_regions[root] = regions
        return regions

    def explicit_regions(self, root):
        """Only forward AMR edges; do not propagate inverse descriptions."""
        if root not in self._explicit_branch_regions:
            regions = {}
            for edge in self._connective_op_edges(root):
                agenda, seen = [edge['target']], set()
                while agenda:
                    node = agenda.pop()
                    if node in seen:
                        continue
                    seen.add(node)
                    # Topic/time/condition references do not make their target
                    # an owned event of this operand. Follow explicit content
                    # roles and local nominal structure, not arbitrary reach.
                    for e in self.outgoing.get(node, []):
                        role = e['role'].lower()
                        if not re.fullmatch(r':(?:arg\d+|op[1-9]\d*|snt[1-9]\d*|poss|part|mod|domain|quant|name)', role):
                            continue
                        target = e['target']
                        concept = self.concepts.get(target, '')
                        if EVENT.fullmatch(concept) or concept in ('and', 'or', 'multi-sentence'):
                            # Referring to an event as an ordinary argument
                            # does not own it. Only explicit operands and
                            # registered scope-content edges license this
                            # additional event-isolation rule.
                            if not (re.fullmatch(r':(?:op[1-9]\d*|snt[1-9]\d*)', role) or
                                    (self.concepts.get(node) in SCOPES and role == ':arg1')):
                                continue
                        agenda.append(target)
                if root in seen:
                    regions = {}
                    break
                regions[edge['target']] = seen
            self._explicit_branch_regions[root] = regions
        return self._explicit_branch_regions[root]

    def _closed_entity_or(self, root, owner_atoms):
        """A closed OR whose operands denote one relation to the same entity.

        Only existing single-edge dyads are rebound. No relation is invented,
        deleted or merged; contexts with extra edges/metadata are left alone.
        """
        if self.concepts.get(root) != 'or' or self._condition_edges(root):
            return None
        ops = self._connective_op_edges(root)
        targets = {e['target'] for e in ops}
        if len(ops) < 2 or len(targets) != len(ops) or owner_atoms.get(root):
            return None
        if any(e not in ops for e in self.outgoing.get(root, [])):
            return None
        if any(a['role'].lower() not in (':polarity', ':wiki') for a in self.attrs_by_source.get(root, [])):
            return None
        relations = []
        for target in targets:
            if self._condition_edges(target) or self._is_structural_connective(target):
                return None
            if any(a['role'].lower() not in (':polarity', ':wiki') for a in self.attrs_by_source.get(target, [])):
                return None
            incident = [e for e in self.outgoing.get(target, []) + self.incoming.get(target, [])
                        if e['id'] not in self.structural_ids]
            if len(incident) != 1:
                return None
            e = incident[0]
            centre = e['target'] if e['source'] == target else e['source']
            if centre in targets or centre == root:
                return None
            relations.append((target, centre, e))
        centres = {c for _, c, _ in relations}
        if len(centres) != 1:
            return None
        centre = next(iter(centres))
        if (self._raw_has_negative_polarity(centre) or self.attrs_by_source.get(centre)
                or EVENT.fullmatch(self.concepts.get(centre, ''))
                or self._is_structural_connective(centre) or self._condition_edges(centre)):
            return None
        selected_edges = {e['id'] for _, _, e in relations}
        centre_edges = {e['id'] for e in self.outgoing.get(centre, []) + self.incoming.get(centre, [])}
        if centre_edges != selected_edges:
            return None
        # The explicit operand has only the OR parent and its one relation.
        if any(any(e['id'] not in selected_edges and e not in ops
                   for e in self.incoming.get(t, []) + self.outgoing.get(t, [])) for t in targets):
            return None
        all_atoms = [a for rows in owner_atoms.values() for a in rows]
        branches = {}
        for target, _, edge in relations:
            matches = [a for a in all_atoms if edge['id'] in a.get('source_graph_record_ids', [])]
            if (len(matches) != 1 or matches[0]['kind'] != 'dyadic'
                    or matches[0].get('coordination_root_node')
                    or matches[0].get('source_graph_record_ids') != [edge['id']]):
                return None
            if (matches[0].get('participant_polarity_projected') and
                    set(matches[0].get('participant_polarity_source_nodes', [])) != {target}):
                return None
            atom = F._atom_ast(matches[0]['id'])
            branches[target] = F._not_ast(atom) if self._raw_has_negative_polarity(target) else atom
        allowed_ids = {F._formula_ids(ast).pop() for ast in branches.values()}
        if any(a['id'] not in allowed_ids for n in targets | {centre} for a in owner_atoms.get(n, [])):
            return None
        result = F._combine_ast('or', [branches[e['target']] for e in ops])
        self.events.add(('closed_entity_or_edge_ownership', root))
        return F._not_ast(result) if self._raw_has_negative_polarity(root) else result

    def _all_inverse_descriptions(self):
        return {n: [(e['source'], e['id']) for e in self.incoming.get(n, [])
                    if e['id'] not in self.structural_ids and e['role'].lower() != ':condition']
                for n in self.concepts}

    def graph_scope_view(self, root):
        view = copy(self)
        view.inverse_tree_children = self._all_inverse_descriptions()
        return scope_reference_view(view, root)

    def _walk_edges(self, start, blocked, inverse):
        agenda, seen, edges = [start], set(), set()
        while agenda:
            node = agenda.pop()
            if node in seen:
                continue
            seen.add(node)
            for e in self.outgoing.get(node, []):
                if e['id'] != blocked:
                    edges.add(boundaries._edge_key(e))
                    agenda.append(e['target'])
            for child, eid in inverse.get(node, []):
                if eid != blocked:
                    edges.add(boundaries._edge_key(self.edge_by_id[eid]))
                    agenda.append(child)
        return seen, edges

    def merge_edge_contexts(self, boundary):
        key = (boundary['edge'], boundary['content'])
        if key not in self._graph_context_cache:
            view = self.graph_scope_view(boundary['governor'])
            _, inside = self._walk_edges(boundary['content'], boundary['edge'], view.inverse_tree_children)
            owned_nodes, _ = self._walk_edges(boundary['content'], boundary['edge'], {})
            # A shared subject does not independently assert its content event.
            inverse = {n: [(child, eid) for child, eid in rows
                           if child not in owned_nodes or not (
                               child == boundary['content'] or
                               EVENT.fullmatch(self.concepts.get(child, '')) or
                               self.edge_by_id[eid]['role'].lower() == ':domain')]
                       for n, rows in self._all_inverse_descriptions().items()}
            _, outside = self._walk_edges(self.top, boundary['edge'], inverse)
            self._graph_context_cache[key] = inside, outside
        return self._graph_context_cache[key]

    def local_merge_scope_issue(self, records, limits):
        reason = boundaries.merge_scope_issue(self, records, limits)
        if reason:
            return reason
        keys = [boundaries._edge_key(r) for r in records]
        # Keep the local rules' additional OR/condition guards graph-based too.
        for node in self.concepts:
            edges = self._connective_op_edges(node) if self.concepts[node] == 'or' else []
            edges = list(edges) + self._condition_edges(node)
            for edge in edges:
                boundary = {'governor': node, 'content': edge['target'], 'edge': edge['id']}
                inside, outside = self.merge_edge_contexts(boundary)
                contexts = [(k in inside, k in outside) for k in keys]
                if any(c[0] for c in contexts) and len(set(contexts)) != 1:
                    return 'local_graph_cross_boolean_scope'
        return None

    def _node_colors(self):
        if self._stable_colors is None:
            def ranks(signatures):
                order = {v: i for i, v in enumerate(sorted(set(signatures.values())))}
                return {n: order[v] for n, v in signatures.items()}
            colors = ranks({n: (F._canonical_json(self._node_identity(n)), n == self.top) for n in self.concepts})
            for _ in self.concepts:
                signatures = {n: (colors[n],
                    tuple(sorted((e['role'].lower(), colors[e['target']]) for e in self.outgoing.get(n, []))),
                    tuple(sorted((e['role'].lower(), colors[e['source']]) for e in self.incoming.get(n, []))))
                    for n in self.concepts}
                refined = ranks(signatures)
                if len(set(refined.values())) == len(set(colors.values())):
                    colors = refined
                    break
                colors = refined
            self._stable_colors = colors
        return self._stable_colors

    def merge_candidate_key(self, candidate):
        template, centre, pair = candidate
        colors = self._node_colors()
        def identity(n):
            return F._canonical_json(self._node_identity(n)), colors[n]
        return (template['priority'], identity(centre), template['id'],
                tuple((row['role'], identity(row['record']['source_node']), identity(row['target'])) for row in pair))
