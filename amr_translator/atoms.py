"""Build ordered records, atoms, and Boolean structure from a decoded AMR."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import copy

from penman import constant
from penman.layout import Push

from . import primitives, metadata as metadata_rules, polarity as polarity_rules
from .frame import TranslatorContractError
from .primitives import (_node_value, _SNT_ROLE_RE, _ANCHOR_ROLES, _ordered_unique, _canonical_json, _canonical_key, _contains_not_true)


class FlatTripleBuilder:
    """Compile a decoded AMR graph into atoms and a Boolean formula."""

    def __init__(self, graph: Any) -> None:
        self.concepts: Dict[str, str] = {}
        for instance in graph.instances():
            node = str(instance.source)
            self.concepts[node] = str(instance.target)
        if not self.concepts or graph.top is None:
            raise primitives.AMRTripleConversionError("AMR graph has no rooted concept nodes")
        self.top = str(graph.top)
        if self.top not in self.concepts:
            raise primitives.AMRTripleConversionError("AMR top does not identify a concept node")

        self.edges: List[Dict[str, Any]] = []
        self.attributes: List[Dict[str, Any]] = []
        self.outgoing: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.incoming: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.attrs_by_source: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for index, edge in enumerate(graph.edges(), 1):
            record = {
                "id": "e{}".format(index),
                "source": str(edge.source),
                "role": str(edge.role),
                "target": str(edge.target),
                "target_is_node": True,
                "record_type": "edge",
            }
            self.edges.append(record)
            self.outgoing[record["source"]].append(record)
            self.incoming[record["target"]].append(record)
        for index, attribute in enumerate(graph.attributes(), 1):
            record = {
                "id": "a{}".format(index),
                "source": str(attribute.source),
                "role": str(attribute.role),
                "target": str(attribute.target),
                "target_is_node": False,
                "record_type": "attribute",
            }
            self.attributes.append(record)
            self.attrs_by_source[record["source"]].append(record)

        self.structural_ids: Set[str] = set()
        self._mark_structural_records()
        self.unary_records: List[Dict[str, Any]] = []
        self.dyadic_records: List[Dict[str, Any]] = []
        self.dyad_by_graph_record: Dict[str, Dict[str, Any]] = {}
        self.edge_by_id: Dict[str, Dict[str, Any]] = {
            str(edge["id"]): edge for edge in self.edges
        }
        self.endpoint_variants_cache: Dict[
            str, List[Tuple[str, Mapping[str, Any], Optional[str], List[str]]]
        ] = {}
        self.atom_specs: List[Dict[str, Any]] = []
        self.consumed_ids: Set[str] = set()
        self.nodes_used_as_terms: Set[str] = set()
        self.reentrant_semantic_edge_ids: Set[str] = set()

        raw_attributes = list(graph.attributes())
        if len(raw_attributes) != len(self.attributes):
            raise primitives.AMRTripleConversionError(
                "attribute inventory changed during decode"
            )
        enriched: Dict[str, Dict[str, Any]] = {}
        for record, attribute in zip(self.attributes, raw_attributes):
            raw = str(attribute.target)
            try:
                value_type = constant.type(raw).name.casefold()
                value = constant.evaluate(raw)
            except Exception as exc:
                raise primitives.AMRTripleConversionError(
                    "literal type recovery failed for {}: {}".format(
                        raw, exc
                    )
                ) from exc
            if not isinstance(value, (str, int, float, bool, type(None))):
                value = str(value)
            record.update(
                {
                    "target_raw": raw,
                    "target_value": value,
                    "target_value_type": value_type,
                }
            )
            enriched[str(record["id"])] = record

        for record in self.metadata_records_raw:
            source = enriched.get(str(record["id"]))
            if source is not None:
                record.update(
                    {
                        "target_raw": source["target_raw"],
                        "target_value": source["target_value"],
                        "target_value_type": source["target_value_type"],
                    }
                )

        self.duplicate_group_by_record_id: Dict[
            str, Tuple[str, ...]
        ] = {}
        self._index_duplicate_semantic_occurrences()

        self.inverse_tree_children: Dict[
            str, List[Tuple[str, str]]
        ] = defaultdict(list)
        for record, edge in zip(self.edges, graph.edges()):
            record_id = str(record["id"])
            if record_id in self.structural_ids:
                continue
            pushed = {
                str(item.variable)
                for item in graph.epidata.get(edge, [])
                if isinstance(item, Push)
            }
            source = str(record["source"])
            target = str(record["target"])
            if source in pushed and source != target:
                self.inverse_tree_children[target].append(
                    (source, record_id)
                )

        self.participant_polarity_bindings: List[Dict[str, Any]] = []
        self.projected_negative_participant_nodes: Set[str] = set()
        self._participant_polarity_prepared = False
        self._temporarily_suppressed_formula_nodes: Set[str] = set()
        self._branch_reachability_cache: Dict[
            Tuple[str, str, Tuple[str, ...]], Set[str]
        ] = {}

    # Separate connective, condition, name/wiki, polarity, and metadata records
    # from semantic records before any atom is emitted.
    def _mark_structural_records(self) -> None:
        for edge in self.edges:
            source_concept = primitives._canonical_label(self.concepts.get(edge["source"], ""))
            target_concept = primitives._canonical_label(self.concepts.get(edge["target"], ""))
            role = edge["role"].casefold()
            if role == ":condition":
                self.structural_ids.add(edge["id"])
            elif role == ":name" and target_concept == "name":
                self.structural_ids.add(edge["id"])
            elif source_concept in {"and", "or"} and primitives._OP_ROLE_RE.match(role):
                self.structural_ids.add(edge["id"])
        for attribute in self.attributes:
            source_concept = primitives._canonical_label(
                self.concepts.get(attribute["source"], "")
            )
            role = attribute["role"].casefold()
            if role in {":polarity", ":wiki"}:
                self.structural_ids.add(attribute["id"])
            elif source_concept == "name" and primitives._OP_ROLE_RE.match(role):
                self.structural_ids.add(attribute["id"])

        self.metadata_ids: Set[str] = set()
        self.metadata_records_raw: List[Dict[str, Any]] = []
        self.metadata_by_source: Dict[
            str, List[Dict[str, Any]]
        ] = defaultdict(list)
        self.metadata_term_nodes: Set[str] = set()
        for record in [*self.edges, *self.attributes]:
            category = self._metadata_category(record)
            if category is None:
                continue
            item = dict(record)
            item["metadata_category"] = category
            self.metadata_ids.add(str(record["id"]))
            self.structural_ids.add(str(record["id"]))
            self.metadata_records_raw.append(item)
            self.metadata_by_source[str(record["source"])].append(item)
            if record.get("target_is_node") is True:
                self.metadata_term_nodes.add(str(record["target"]))

        agenda = list(self.metadata_term_nodes)
        visited: Set[str] = set()
        while agenda:
            node = str(agenda.pop())
            if node in visited:
                continue
            visited.add(node)
            for record in [
                *self.outgoing.get(node, []),
                *self.attrs_by_source.get(node, []),
            ]:
                record_id = str(record["id"])
                if record_id not in self.metadata_ids:
                    item = dict(record)
                    item["metadata_category"] = "metadata-subtree"
                    self.metadata_ids.add(record_id)
                    self.metadata_records_raw.append(item)
                    self.metadata_by_source[node].append(item)
                self.structural_ids.add(record_id)
                if record.get("target_is_node") is True:
                    target = str(record["target"])
                    self.metadata_term_nodes.add(target)
                    agenda.append(target)

        for edge in self.edges:
            source = str(edge["source"])
            concept = primitives._canonical_label(
                self.concepts.get(source, "")
            )
            if concept == "multi sentence" and _SNT_ROLE_RE.fullmatch(
                str(edge["role"])
            ):
                self.structural_ids.add(str(edge["id"]))

        multi_sentence_nodes = {
            node
            for node, concept in self.concepts.items()
            if primitives._canonical_label(concept)
            == "multi sentence"
        }
        envelope_nodes: Set[str] = set()
        sentence_chain_nodes = set(multi_sentence_nodes)
        changed = True
        while changed:
            changed = False
            for edge in self.edges:
                source = str(edge["source"])
                target = str(edge["target"])
                role = str(edge["role"]).casefold()
                if source not in sentence_chain_nodes:
                    continue
                if role != ":rel" and not _SNT_ROLE_RE.fullmatch(role):
                    continue
                self.structural_ids.add(str(edge["id"]))
                if target not in sentence_chain_nodes:
                    sentence_chain_nodes.add(target)
                    envelope_nodes.add(target)
                    changed = True
        self.multi_sentence_envelope_nodes = envelope_nodes

    def _name_surface(self, node: str) -> Optional[str]:
        name_edges = [
            edge
            for edge in self.outgoing.get(node, [])
            if edge["role"].casefold() == ":name"
            and primitives._canonical_label(self.concepts.get(edge["target"], "")) == "name"
        ]
        if not name_edges:
            return None
        name_node = name_edges[0]["target"]
        op_attrs = [
            attr
            for attr in self.attrs_by_source.get(name_node, [])
            if primitives._OP_ROLE_RE.match(attr["role"])
        ]
        values = [primitives._literal_text(item["target"]) for item in primitives._ordered_role_items(op_attrs)]
        surface = primitives._SPACE_RE.sub(" ", " ".join(value for value in values if value)).strip()
        return surface or None


    def node_canonical(self, node: str) -> str:
        return primitives._canonical_label(self.node_surface(node))

    def _raw_has_negative_polarity(self, node: str) -> bool:
        return any(
            attr["role"].casefold() == ":polarity"
            and primitives._literal_text(attr["target"]) == "-"
            for attr in self.attrs_by_source.get(node, [])
        )

    def _semantic_edges(self, node: str) -> List[Dict[str, Any]]:
        return [
            edge
            for edge in self.outgoing.get(node, [])
            if edge["id"] not in self.structural_ids
        ]


    def _render_dyad(self, source: str, role: str, target: str) -> str:
        pieces = (target, source) if role.casefold() == "arg0" else (source, target)
        return primitives._SPACE_RE.sub(" ", " ".join(piece for piece in pieces if piece)).strip()

    def _add_spec(self, spec: Mapping[str, Any]) -> None:
        self.atom_specs.append(dict(spec))

    # triple templates: two compatible role occurrences
    # around one predicate become one subject-predicate-object atom while
    # retaining every contributing graph-record identifier.
    def _triple_spec(
        self,
        *,
        owner: str,
        subject_node: str,
        predicate_node: str,
        object_node: str,
        join_signature: str,
        component_edges: Sequence[Mapping[str, Any]],
        component_dyads: Optional[Sequence[Mapping[str, Any]]] = None,
        extra_source_ids: Sequence[str] = (),
        coordination_root_node: Optional[str] = None,
        coordination_path: Sequence[str] = (),
        coordination_source_edge_id: Optional[str] = None,
        formula_owner_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        subject = self.node_surface(subject_node)
        predicate = self.node_surface(predicate_node)
        obj = self.node_surface(object_node)
        records = (
            list(component_dyads)
            if component_dyads is not None
            else [self.dyad_by_graph_record[item["id"]] for item in component_edges]
        )
        components = [str(item["id"]) for item in records]
        self.nodes_used_as_terms.update(
            {subject_node, predicate_node, object_node}
        )
        spec = {
            "_owner": formula_owner_node or owner,
            "kind": "triple",
            "arity": 3,
            "terms": [subject, predicate, obj],
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "join_signature": join_signature,
            "composition": "same-event",
            "event_occurrence_id": predicate_node,
            "governor_node": owner,
            "outer_node": None,
            "component_dyad_ids": components,
            "source_graph_record_ids": [
                *[str(item["id"]) for item in component_edges],
                *[str(item) for item in extra_source_ids],
            ],
            "base_surface_text": primitives._SPACE_RE.sub(
                " ", "{} {} {}".format(subject, predicate, obj)
            ).strip(),
        }
        if coordination_root_node is not None:
            if coordination_source_edge_id is None:
                raise primitives.AMRTripleConversionError(
                    "coordinated triple has no projected source edge"
                )
            spec["coordination_root_node"] = coordination_root_node
            spec["coordination_path"] = [str(item) for item in coordination_path]
            spec["coordination_source_edge_id"] = str(
                coordination_source_edge_id
            )
        subject_node = str(subject_node)
        predicate_node = str(predicate_node)
        object_node = str(object_node)
        payload = {
            "kind": "triple",
            "join_signature": str(join_signature).casefold(),
            "subject": self._node_identity(subject_node),
            "predicate": self._node_identity(predicate_node),
            "object": self._node_identity(object_node),
            "subject_object_coreference": (
                "same" if subject_node == object_node else "distinct"
            ),
        }
        spec.update(
            {
                "canonical_payload": payload,
                "canonical_key": _canonical_key(
                    "triple", payload
                ),
                "subject_node": subject_node,
                "predicate_node": predicate_node,
                "object_node": object_node,
            }
        )
        return spec

    def _is_structural_connective(self, node: str) -> bool:
        return bool(node in self.concepts and self._connective_op_edges(node))

    def _connective_leaf_surfaces(
        self, node: str, stack: Tuple[str, ...] = ()
    ) -> List[str]:
        if node in stack:
            raise primitives.AMRTripleConversionError(
                "connective summary encountered a scope cycle: {}".format(
                    " -> ".join([*stack, node])
                )
            )
        op_edges = self._connective_op_edges(node)
        if not op_edges:
            return [self.node_surface(node)]
        surfaces: List[str] = []
        for edge in op_edges:
            surfaces.extend(
                self._connective_leaf_surfaces(
                    str(edge["target"]), (*stack, node)
                )
            )
        return surfaces

    def _endpoint_variants(
        self, role_edge: Mapping[str, Any]
    ) -> List[Tuple[str, Mapping[str, Any], Optional[str], List[str]]]:
        edge_id = str(role_edge["id"])
        cached = self.endpoint_variants_cache.get(edge_id)
        if cached is not None:
            return cached
        target = str(role_edge["target"])
        variants: List[Tuple[str, Mapping[str, Any], Optional[str], List[str]]] = []

        def walk(
            node: str,
            root: Optional[str],
            branch_path: Sequence[Mapping[str, Any]],
            stack: Tuple[str, ...],
        ) -> None:
            if node in stack:
                raise primitives.AMRTripleConversionError(
                    "connective projection encountered a scope cycle: {}".format(
                        " -> ".join([*stack, node])
                    )
                )
            op_edges = self._connective_op_edges(node)
            if op_edges:
                actual_root = root or node
                for branch in op_edges:
                    walk(
                        str(branch["target"]),
                        actual_root,
                        [*branch_path, branch],
                        (*stack, node),
                    )
                return
            if not branch_path:
                dyad = self.dyad_by_graph_record[str(role_edge["id"])]
            else:
                dyad = self._projected_dyad(role_edge, node, branch_path)
            variants.append(
                (
                    node,
                    dyad,
                    root,
                    [str(branch["id"]) for branch in branch_path],
                )
            )

        walk(target, None, [], ())
        self.endpoint_variants_cache[edge_id] = variants
        return variants


    # active-atom rules: a semantic record that is not
    # consumed by a composed triple remains a dyadic atom with its parser
    # endpoints, role, provenance, and canonical identity unchanged.
    def _active_dyad_spec(
        self,
        record: Mapping[str, Any],
        *,
        owner: str,
        coordination_root_node: Optional[str] = None,
        coordination_path: Sequence[str] = (),
        formula_owner_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        spec: Dict[str, Any] = {
            "_owner": formula_owner_node or owner,
            "kind": "dyadic",
            "arity": 2,
            "terms": list(record["terms"]),
            "predicate": record["role"],
            "role": record["role"],
            "governor_node": owner,
            "component_dyad_ids": [record["id"]],
            "source_graph_record_ids": list(
                record.get("graph_record_ids", [record["graph_record_id"]])
            ),
            "canonical_payload": copy.deepcopy(
                record["canonical_payload"]
            ),
            "canonical_key": record["canonical_key"],
            "base_surface_text": record["base_surface_text"],
        }
        if coordination_root_node is not None:
            spec["coordination_root_node"] = coordination_root_node
            spec["coordination_path"] = [str(item) for item in coordination_path]
        if record.get("reference_mode_projection") is True:
            spec.update(
                {
                    "metadata_category": "mode",
                    "reference_mode_projection": True,
                    "source_concept": record.get("source_concept", ""),
                }
            )
        return spec

    def _opaque_connective_source_spec(
        self, record: Mapping[str, Any]
    ) -> Dict[str, Any]:
        source_node = str(record["source_node"])
        source_surface = " ".join(self._connective_leaf_surfaces(source_node))
        target_node = record.get("target_node")
        if target_node is not None and self._is_structural_connective(str(target_node)):
            target_surface = " ".join(
                self._connective_leaf_surfaces(str(target_node))
            )
        else:
            target_surface = str(record["terms"][1])
        role = str(record["role"])
        surface = self._render_dyad(source_surface, role, target_surface)
        target_identity: Mapping[str, Any]
        if target_node is not None:
            target_identity = {
                "node": self._node_identity(str(target_node))
            }
        else:
            target_identity = {
                "literal": (
                    primitives._canonical_label(
                        record.get("terms", ["", ""])[1]
                    )
                )
            }
        payload = {
            "kind": "opaque",
            "role": role.casefold(),
            "source": self._connective_identity(source_node),
            "target": target_identity,
        }
        return {
            "_owner": source_node,
            "kind": "opaque",
            "arity": 2,
            "terms": [source_surface, target_surface],
            "predicate": role,
            "role": role,
            "opaque_reason": "semantic-edge-from-connective-source",
            "component_dyad_ids": [record["id"]],
            "source_graph_record_ids": [record["graph_record_id"]],
            "canonical_payload": payload,
            "canonical_key": _canonical_key(
                "opaque", payload
            ),
            "base_surface_text": surface,
        }

    def _condition_edges(self, node: str) -> List[Dict[str, Any]]:
        return [
            edge
            for edge in self.outgoing.get(node, [])
            if edge["role"].casefold() == ":condition"
        ]


    # Polarity rules: use a signed surface only for a literal
    # negative occurrence outside structural scope.  Scoped negation stays
    # in the formula AST and never creates a second atom identity.
    @staticmethod
    def _annotate_atom_surfaces(
        atoms: Sequence[Dict[str, Any]], formula: Mapping[str, Any]
    ) -> None:
        contexts: Dict[str, List[Tuple[bool, bool]]] = defaultdict(list)

        def walk(node: Mapping[str, Any], parity: bool, scoped: bool) -> None:
            op = node.get("op")
            if op == "atom":
                contexts[str(node["id"])].append((parity, scoped))
                return
            if op in {"true", "false"}:
                return
            if op == "not":
                walk(node["arg"], not parity, scoped)
                return
            if op in {"and", "or"}:
                child_scoped = scoped or parity
                for arg in node.get("args", []):
                    walk(arg, parity, child_scoped)
                return
            if op == "implies":
                walk(node["antecedent"], parity, scoped)
                walk(node["consequent"], parity, scoped)
                return
            raise primitives.AMRTripleValidationError(
                "Unsupported formula AST operator: {!r}".format(op)
            )

        walk(formula, False, False)
        for atom in atoms:
            atom_contexts = contexts.get(str(atom["id"]), [])
            literal_negative = bool(atom_contexts) and all(
                parity and not scoped for parity, scoped in atom_contexts
            )
            scope_qualified = any(scoped for _parity, scoped in atom_contexts)
            atom["polarity"] = "negative" if literal_negative else "positive"
            atom["scope_qualified"] = scope_qualified
            if literal_negative:
                atom["signed_surface_text"] = "not {}".format(
                    atom["base_surface_text"]
                )
            atom["nli_surface_text"] = atom["signed_surface_text"]
            atom["surface_text"] = atom["signed_surface_text"]

    def build(self) -> Dict[str, Any]:
        """Build internal atoms, their source records, and the formula."""

        self._build_unary_records()
        self._build_dyadic_records()
        self._compose_same_event_triples()
        self._add_fallback_dyads()
        self._add_required_unaries()
        atoms = self._finalize_atoms()
        if not atoms:
            raise primitives.AMRTripleConversionError(
                "conversion produced no active propositions"
            )
        formula_ast = self._build_formula(atoms)
        self._annotate_atom_surfaces(atoms, formula_ast)
        return {
            "dyadic_records": self.dyadic_records,
            "atoms": atoms,
            "formula_ast": formula_ast,
        }

    def _metadata_category(self, record: Mapping[str, Any]) -> Optional[str]:
        source = str(record["source"])
        role = str(record["role"]).casefold()
        source_concept = str(self.concepts.get(source, "")).casefold()
        target_concept = str(self.concepts.get(str(record.get("target")), "")).casefold()

        if role == ":name" and target_concept == "name":
            return "name"
        if source_concept == "name" and primitives._OP_ROLE_RE.match(role):
            return "name-part"
        if role == ":wiki":
            return "wiki"
        if role in metadata_rules._METADATA_ALWAYS_ROLES:
            return "mode" if role == ":mode" else "list-index"
        if role == ":quant":
            return "quantity"
        if role in {":unit", ":scale"} and (
            source_concept.endswith("-quantity") or role == ":unit"
        ):
            return "quantity"
        if source_concept == "date-entity" and role in metadata_rules._DATE_ROLES:
            return "date"
        if source_concept in {
            "ordinal-entity",
            "percentage-entity",
            "score-entity",
            "value-interval",
        } and role in metadata_rules._ORDINAL_ROLES:
            return "value"
        return None

    def _raw_node_surface(self, node: str) -> str:
        name = self._name_surface(node)
        if name:
            return name
        return primitives._surface_label(self.concepts.get(node, node))

    def _metadata_value_surface(self, record: Mapping[str, Any]) -> str:
        if record.get("target_is_node") is True:
            return self._raw_node_surface(str(record["target"]))
        return primitives._literal_text(record.get("target", ""))

    def _metadata_values(self, node: str) -> Dict[str, List[str]]:
        values: Dict[str, List[str]] = defaultdict(list)
        for record in self.metadata_by_source.get(node, []):
            role = primitives._role_name(record["role"]).casefold()
            values[role].append(self._metadata_value_surface(record))
        return values

    def node_surface(self, node: str) -> str:
        name = self._name_surface(node)
        if name:
            return name
        base = primitives._surface_label(self.concepts.get(node, node))
        metadata = self._metadata_values(node)
        concept = str(self.concepts.get(node, "")).casefold()

        if concept == "date-entity":
            year = next(iter(metadata.get("year", [])), "")
            month = next(iter(metadata.get("month", [])), "")
            day = next(iter(metadata.get("day", [])), "")
            if year or month or day:
                pieces = []
                for index, value in enumerate((year, month, day)):
                    if not value:
                        continue
                    if index > 0 and metadata_rules._INTEGER_RE.fullmatch(value):
                        value = value.zfill(2)
                    pieces.append(value)
                return "-".join(pieces)
            date_parts = [
                value
                for role in sorted(metadata)
                if role in {item.lstrip(":") for item in metadata_rules._DATE_ROLES}
                for value in metadata[role]
            ]
            if date_parts:
                return " ".join(date_parts)

        quant = next(iter(metadata.get("quant", [])), "")
        unit = next(iter(metadata.get("unit", [])), "")
        if concept.endswith("-quantity"):
            quantity_kind = (
                base[: -len(" quantity")].strip()
                if base.endswith(" quantity")
                else base
            )
            pieces = [item for item in (quant, unit or quantity_kind) if item]
            if pieces:
                if quant and quant != "1" and len(pieces) > 1:
                    pieces[-1] = metadata_rules._simple_plural(pieces[-1])
                return " ".join(pieces)
        if quant:
            rendered = base if quant == "1" else metadata_rules._simple_plural(base)
            return "{} {}".format(quant, rendered).strip()

        if concept == "ordinal-entity":
            value = next(iter(metadata.get("value", [])), "")
            if value:
                return "ordinal {}".format(value)
        return base


    def _node_identity(self, node: str) -> Dict[str, Any]:
        metadata = []
        for record in self.metadata_by_source.get(node, []):
            category = str(record.get("metadata_category", ""))
            if category in {"name", "name-part", "wiki"}:
                continue
            metadata.append(
                {
                    "role": str(record["role"]).casefold(),
                    "value": self._metadata_identity_value(record),
                }
            )
        metadata.sort(key=_canonical_json)
        return {
            "concept": str(self.concepts.get(node, node)).casefold(),
            "name": primitives._canonical_label(self._name_surface(node) or ""),
            "metadata": metadata,
        }


    def _dyad_payload(
        self,
        record: Mapping[str, Any],
        *,
        target_node: Optional[str] = None,
    ) -> Dict[str, Any]:
        source = str(record["source"])
        actual_target = target_node if target_node is not None else str(record["target"])
        if target_node is not None or record.get("target_is_node") is True:
            target_identity: Mapping[str, Any] = {
                "node": self._node_identity(actual_target)
            }
            coreference = "same" if source == actual_target else "distinct"
        else:
            target_identity = self._target_identity(record)
            coreference = "literal"
        return {
            "kind": "dyadic",
            "role": str(record["role"]).casefold(),
            "source": self._node_identity(source),
            "target": target_identity,
            "coreference": coreference,
        }

    def _bare_unary_surface(self, node: str) -> str:
        surface = self.node_surface(node)
        if metadata_rules._SENSE_RE.search(str(self.concepts.get(node, ""))):
            return "{} occurs".format(surface)
        quant = next(iter(self._metadata_values(node).get("quant", [])), "")
        verb = "exist" if quant and quant != "1" else "exists"
        return "{} {}".format(surface, verb)


    @staticmethod
    def _literal_identity(record: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "raw": str(record.get("target_raw", record.get("target", ""))),
            "value": record.get("target_value", record.get("target", "")),
            "value_type": str(record.get("target_value_type", "symbol")),
        }

    def _metadata_tree_identity(
        self, node: str, stack: Tuple[str, ...] = ()
    ) -> Mapping[str, Any]:
        if node in stack:
            return {
                "concept": str(self.concepts.get(node, node)).casefold(),
                "cycle_ref": True,
            }
        records = []
        for record in self.metadata_by_source.get(node, []):
            if record.get("target_is_node") is True:
                value: Mapping[str, Any] = {
                    "node": self._metadata_tree_identity(
                        str(record["target"]), (*stack, node)
                    )
                }
            else:
                value = {"literal": self._literal_identity(record)}
            records.append(
                {
                    "role": str(record["role"]).casefold(),
                    "value": value,
                }
            )
        records.sort(key=_canonical_json)
        return {
            "concept": str(self.concepts.get(node, node)).casefold(),
            "name": primitives._canonical_label(self._name_surface(node) or ""),
            "metadata": records,
        }

    def _metadata_identity_value(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        if record.get("target_is_node") is True:
            return {
                "node": self._metadata_tree_identity(str(record["target"]))
            }
        return {"literal": self._literal_identity(record)}

    def _target_identity(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        if record.get("target_is_node") is True:
            return {"node": self._node_identity(str(record["target"]))}
        return {"literal": self._literal_identity(record)}

    def _build_unary_records(self) -> None:
        for index, node in enumerate(self.concepts, 1):
            surface = self.node_surface(node)
            payload = {
                "kind": "unary",
                "node": self._node_identity(str(node)),
            }
            self.unary_records.append(
                {
                    "id": "u{}".format(index),
                    "node": node,
                    "concept": self.concepts[node],
                    "canonical_payload": payload,
                    "canonical_concept": (
                        _canonical_key(
                            "unary", payload
                        )
                    ),
                    "base_surface_text": surface,
                    "signed_surface_text": surface,
                    "nli_surface_text": surface,
                }
            )

    def _projected_dyad(
        self,
        role_edge: Mapping[str, Any],
        target_node: str,
        branch_path: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        source_node = str(role_edge["source"])
        target_node = str(target_node)
        source_surface = self.node_surface(source_node)
        target_surface = self.node_surface(target_node)
        role = primitives._role_name(role_edge["role"])
        surface = self._render_dyad(
            source_surface, role, target_surface
        )
        payload = self._dyad_payload(
            role_edge, target_node=target_node
        )
        record = {
            "id": "d{}".format(len(self.dyadic_records) + 1),
            "graph_record_id": str(role_edge["id"]),
            "graph_record_ids": [
                str(role_edge["id"]),
                *[str(branch["id"]) for branch in branch_path],
            ],
            "record_type": "projected-edge",
            "projected": True,
            "source_node": source_node,
            "target_node": target_node,
            "source_concept": self.concepts.get(source_node, ""),
            "target_concept": self.concepts.get(target_node, ""),
            "role": role,
            "terms": [source_surface, target_surface],
            "canonical_payload": payload,
            "canonical_key": _canonical_key(
                "dyad", payload
            ),
            "base_surface_text": surface,
            "signed_surface_text": surface,
            "nli_surface_text": surface,
        }
        self.dyadic_records.append(record)
        return record

    def _core_edges(self, node: str) -> List[Dict[str, Any]]:
        # The base composition routine pairs ARG0 with every other edge
        # returned here.  Keeping the whitelist explicit prevents accidental
        # cross-scope or metadata composition.
        return [
            edge
            for edge in self._semantic_edges(node)
            if str(edge["role"]).casefold() in metadata_rules._COMPOSITION_ROLES
        ]

    def _nonassertive_scope_nodes(self) -> Set[str]:
        roots = []
        for node in self.concepts:
            modes = {
                self._metadata_value_surface(record).casefold()
                for record in self.metadata_by_source.get(node, [])
                if str(record.get("metadata_category")) == "mode"
            }
            if modes & metadata_rules._NONASSERTIVE_MODES:
                roots.append(node)

        scoped: Set[str] = set()
        agenda = list(roots)
        while agenda:
            node = str(agenda.pop())
            if node in scoped:
                continue
            scoped.add(node)
            for edge in self.outgoing.get(node, []):
                target = str(edge["target"])
                if target in self.concepts and target not in scoped:
                    agenda.append(target)
        return scoped

    def _finalize_atoms(self) -> List[Dict[str, Any]]:
        self.atom_specs.sort(
            key=lambda spec: (
                str(spec.get("canonical_key", "")),
                str(spec.get("kind", "")),
                str(spec.get("_owner", "")),
                _canonical_json(spec.get("terms", [])),
                str(spec.get("join_signature", "")),
            )
        )
        nonassertive = self._nonassertive_scope_nodes()
        atoms: List[Dict[str, Any]] = []
        for index, spec in enumerate(self.atom_specs, 1):
            atom = {key: value for key, value in spec.items() if not key.startswith("_")}
            owner = str(spec["_owner"])
            atom["id"] = "x{}".format(index)
            atom["owner_node"] = owner
            atom["polarity"] = "positive"
            atom["signed_surface_text"] = atom["base_surface_text"]
            atom["nli_surface_text"] = atom["base_surface_text"]
            atom["surface_text"] = atom["base_surface_text"]
            atom["linkable"] = bool(atom["nli_surface_text"])
            atom["scope_qualified"] = False
            scope_candidates = {
                owner,
                str(atom.get("governor_node", "")),
                str(atom.get("event_occurrence_id", "")),
            }
            atom["exact_match_eligible"] = (
                atom.get("kind") != "opaque"
                and not bool(scope_candidates & nonassertive)
            )
            atoms.append(atom)
        return atoms


    # Formula construction: compile all graph
    # roots, recover any disconnected active component, apply participant
    # polarity locally, and verify that every emitted atom occurs.
    def _build_formula(
        self, atoms: Sequence[Mapping[str, Any]]
    ) -> Dict[str, Any]:
        owner_atoms: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for atom in atoms:
            owner_atoms[str(atom["owner_node"])].append(atom)
        roots = self._formula_roots()
        parts = [self._compile_node(root, owner_atoms, ()) for root in roots]
        formula = primitives._combine_ast("and", parts)
        all_ids = {str(atom["id"]) for atom in atoms}
        atom_by_id = {str(atom["id"]): atom for atom in atoms}
        compiled_roots = set(roots)
        while True:
            referenced = primitives._formula_ids(formula)
            missing = sorted(all_ids - referenced)
            if not missing:
                break
            candidates = sorted(
                {
                    str(atom_by_id[atom_id]["owner_node"])
                    for atom_id in missing
                    if str(atom_by_id[atom_id]["owner_node"]) not in compiled_roots
                },
                key=lambda node: (
                    _canonical_json(self._node_identity(node)),
                    str(node),
                ),
            )
            if not candidates:
                raise primitives.AMRTripleConversionError(
                    "formula traversal omitted active atoms after component replay: {}".format(
                        ", ".join(missing)
                    )
                )
            before = len(referenced)
            for owner in candidates:
                parts.append(self._compile_node(owner, owner_atoms, ()))
                compiled_roots.add(owner)
            formula = primitives._combine_ast("and", parts)
            if len(primitives._formula_ids(formula)) <= before:
                raise primitives.AMRTripleConversionError(
                    "formula component replay made no progress"
                )
        if _contains_not_true(formula):
            raise primitives.AMRTripleConversionError("formula contains an empty negated owner")
        projected_ids = {
            str(atom["id"])
            for atom in atoms
            if atom.get("participant_polarity_projected") is True
        }
        before_contexts = (
            polarity_rules._formula_occurrence_contexts(
                formula
            )
        )
        after = (
            polarity_rules._apply_local_participant_polarity(
                formula, projected_ids
            )
        )
        after_contexts = (
            polarity_rules._formula_occurrence_contexts(
                after
            )
        )

        all_ids = set(before_contexts) | set(after_contexts)
        for atom_id in all_ids:
            before_paths = [
                context["structural_path"]
                for context in before_contexts.get(atom_id, [])
            ]
            after_paths = [
                context["structural_path"]
                for context in after_contexts.get(atom_id, [])
            ]
            if before_paths != after_paths:
                raise primitives.AMRTripleConversionError(
                    "participant-polarity projection moved atom {} between "
                    "formula branches".format(atom_id)
                )

        atoms_by_projection = {
            str(atom.get("participant_polarity_projection_id", "")): atom
            for atom in atoms
            if atom.get("participant_polarity_projection_id")
        }
        for binding in self.participant_polarity_bindings:
            projection_id = str(binding["projection_id"])
            atom = atoms_by_projection.get(projection_id)
            if atom is None:
                raise primitives.AMRTripleConversionError(
                    "participant-polarity projection {} has no finalized "
                    "atom".format(projection_id)
                )
            atom_id = str(atom["id"])
            before_items = before_contexts.get(atom_id, [])
            after_items = after_contexts.get(atom_id, [])
            if not before_items or len(before_items) != len(after_items):
                raise primitives.AMRTripleConversionError(
                    "participant-polarity projection {} has inconsistent "
                    "formula occurrences".format(projection_id)
                )
            after_parities = [
                bool(item["not_parity"]) for item in after_items
            ]
            if not all(after_parities):
                raise primitives.AMRTripleConversionError(
                    "participant-polarity projection {} left a positive "
                    "occurrence".format(projection_id)
                )

        if _contains_not_true(after):
            raise primitives.AMRTripleConversionError(
                "formula contains NOT(True) after participant projection"
            )
        return after

    def _semantic_occurrence_key(self, record: Mapping[str, Any]) -> str:
        target: Mapping[str, Any]
        if record.get("target_is_node") is True:
            target = {"node": str(record["target"])}
        else:
            target = {"literal": self._literal_identity(record)}
        return _canonical_json(
            {
                "record_type": str(record["record_type"]),
                "source": str(record["source"]),
                "role": str(record["role"]).casefold(),
                "target": target,
            }
        )

    def _index_duplicate_semantic_occurrences(self) -> None:
        grouped: Dict[str, List[str]] = defaultdict(list)
        for record in [*self.edges, *self.attributes]:
            record_id = str(record["id"])
            if record_id in self.structural_ids:
                continue
            grouped[self._semantic_occurrence_key(record)].append(record_id)

        for occurrence_ids in grouped.values():
            if len(occurrence_ids) < 2:
                continue
            group = tuple(occurrence_ids)
            for record_id in group:
                self.duplicate_group_by_record_id[record_id] = group
            duplicate_ids = list(group[1:])
            self.consumed_ids.update(duplicate_ids)

    def _connective_identity(
        self, node: str, stack: Tuple[str, ...] = ()
    ) -> Mapping[str, Any]:
        node_kind = self.node_canonical(node)
        if node in stack:
            if node_kind == "multi sentence":
                raise primitives.AMRTripleConversionError(
                    "multi-sentence identity encountered a scope cycle"
                )
            raise primitives.AMRTripleConversionError(
                "connective identity encountered a cycle"
            )
        op_edges = self._connective_op_edges(node)
        if not op_edges:
            return {"node": self._node_identity(node)}
        return {
            "op": "and" if node_kind == "multi sentence" else node_kind,
            "args": [
                self._connective_identity(str(edge["target"]), (*stack, node))
                for edge in op_edges
            ],
        }

    def _attach_duplicate_provenance(self) -> None:
        for spec in self.atom_specs:
            original_sources = [
                str(item) for item in spec.get("source_graph_record_ids", [])
            ]
            expanded_sources: List[str] = []
            duplicate_sources: List[str] = []
            for record_id in original_sources:
                group = self.duplicate_group_by_record_id.get(
                    record_id, (record_id,)
                )
                expanded_sources.extend(group)
                duplicate_sources.extend(
                    item for item in group if item != record_id
                )
            spec["source_graph_record_ids"] = _ordered_unique(
                expanded_sources
            )
            if duplicate_sources:
                spec["duplicate_component_edge_ids"] = _ordered_unique(
                    duplicate_sources
                )


    def _concept_canonical(self, node: str) -> str:
        return primitives._canonical_label(
            self.concepts.get(node, node)
        )

    def _connective_op_edges(self, node: str) -> List[Mapping[str, Any]]:
        concept = self._concept_canonical(node)
        is_multi_sentence = concept == "multi sentence"
        is_envelope = node in self.multi_sentence_envelope_nodes
        if not is_multi_sentence and not is_envelope:
            if self.node_canonical(node) not in {"and", "or"}:
                return []
            return primitives._ordered_role_items(
                [
                    edge
                    for edge in self.outgoing.get(node, [])
                    if edge["id"] in self.structural_ids
                    and primitives._OP_ROLE_RE.match(
                        edge["role"]
                    )
                ]
            )

        def order(edge: Mapping[str, Any]) -> Tuple[int, str]:
            role = str(edge.get("role", "")).casefold()
            match = _SNT_ROLE_RE.fullmatch(role)
            return (
                0 if role == ":rel" else (
                    int(match.group(1)) if match else 10**9
                ),
                str(edge.get("id", "")),
            )

        return sorted(
            [
                edge
                for edge in self.outgoing.get(node, [])
                if str(edge["id"]) in self.structural_ids
                and (
                    str(edge["role"]).casefold() == ":rel"
                    or _SNT_ROLE_RE.fullmatch(str(edge["role"]))
                )
            ],
            key=order,
        )

    def _connective_scope_reaches(self, start: str, goal: str) -> bool:
        """Return whether structural connective branches reach ``goal``."""

        agenda = [start]
        visited: Set[str] = set()
        while agenda:
            node = str(agenda.pop())
            if node == goal:
                return True
            if node in visited:
                continue
            visited.add(node)
            agenda.extend(
                str(edge["target"])
                for edge in self._connective_op_edges(node)
                if str(edge["target"]) not in visited
            )
        return False

    def _formula_reachable_from_top(self) -> Set[str]:
        reached: Set[str] = set()
        agenda = [self.top]
        while agenda:
            node = str(agenda.pop())
            if node in reached:
                continue
            reached.add(node)
            for edge in self.outgoing.get(node, []):
                target = str(edge["target"])
                if target in self.concepts and target not in reached:
                    agenda.append(target)
            for child, _edge_id in self.inverse_tree_children.get(node, []):
                if child not in reached:
                    agenda.append(child)
        return reached

    def _formula_roots(self) -> List[str]:
        incoming = {
            str(edge["target"])
            for edge in self.edges
            if str(edge["target"]) in self.concepts
        }
        roots: Set[str] = {
            node for node in self.concepts if node not in incoming
        }
        if not roots:
            roots.add(self.top)

        reachable = self._formula_reachable_from_top()
        reachable_roots = roots & reachable
        if reachable_roots:
            roots.difference_update(reachable_roots)
            roots.add(self.top)
        return sorted(
            roots,
            key=lambda node: (
                0 if node == self.top else 1,
                _canonical_json(
                    self._node_identity(node)
                ),
                str(node),
            ),
        )

    # recursive formula definition: local atoms conjoin
    # with semantic descendants, explicit and/or branches keep their
    # operator, multi-sentence branches conjoin, conditions imply the
    # owner body, and node polarity negates that completed body.
    def _compile_node_core(
        self,
        node: str,
        owner_atoms: Mapping[str, Sequence[Mapping[str, Any]]],
        stack: Tuple[str, ...],
    ) -> Dict[str, Any]:
        if node in stack:
            cycle = " -> ".join([*stack, node])
            raise primitives.AMRTripleConversionError(
                "scope traversal encountered a graph cycle: {}".format(cycle)
            )
        next_stack = (*stack, node)
        parts: List[Mapping[str, Any]] = [
            primitives._atom_ast(str(atom["id"]))
            for atom in owner_atoms.get(node, [])
        ]
        concept = self._concept_canonical(node)
        condition_edges = self._condition_edges(node)
        condition_ids = {str(edge["id"]) for edge in condition_edges}
        op_edges = self._connective_op_edges(node)
        op_ids = {str(edge["id"]) for edge in op_edges}
        connective_op = (
            "and"
            if concept == "multi sentence"
            or node in self.multi_sentence_envelope_nodes
            else concept
        )

        if connective_op in {"and", "or"} and op_edges:
            branch_formulas = []
            for edge in op_edges:
                target = str(edge["target"])
                if target in next_stack:
                    if self._connective_scope_reaches(target, node):
                        cycle = " -> ".join([*next_stack, target])
                        raise primitives.AMRTripleConversionError(
                            "connective scope cycle: {}".format(cycle)
                        )
                    # The branch may already be open through an ordinary
                    # semantic edge. Its propositions are already present;
                    # revisiting them here would duplicate that subtree.
                    self.reentrant_semantic_edge_ids.add(str(edge["id"]))
                    continue
                branch_formulas.append(
                    self._compile_node(target, owner_atoms, next_stack)
                )
            parts.append(
                primitives._combine_ast(
                    connective_op, branch_formulas
                )
            )

        for edge in self.outgoing.get(node, []):
            edge_id = str(edge["id"])
            if edge_id in condition_ids or edge_id in op_ids:
                continue
            if edge_id in self.structural_ids:
                continue
            target = str(edge["target"])
            if target not in self.concepts:
                continue
            if target in next_stack:
                self.reentrant_semantic_edge_ids.add(edge_id)
                continue
            child = self._compile_node(target, owner_atoms, next_stack)
            if child.get("op") != "true":
                parts.append(child)

        for child_node, edge_id in self.inverse_tree_children.get(node, []):
            if edge_id in condition_ids or edge_id in op_ids:
                continue
            if child_node in next_stack:
                self.reentrant_semantic_edge_ids.add(edge_id)
                continue
            child = self._compile_node(child_node, owner_atoms, next_stack)
            if child.get("op") != "true":
                parts.append(child)

        body = primitives._combine_ast("and", parts)
        if self._has_negative_polarity(node):
            body = primitives._not_ast(body)
        if condition_edges:
            antecedent = primitives._combine_ast(
                "and",
                [
                    self._compile_condition_target(
                        node, edge, owner_atoms, next_stack
                    )
                    for edge in condition_edges
                ],
            )
            body = {
                "op": "implies",
                "antecedent": antecedent,
                "consequent": body,
            }
        return body

    def _compile_condition_target(
        self,
        owner_node: str,
        edge: Mapping[str, Any],
        owner_atoms: Mapping[str, Sequence[Mapping[str, Any]]],
        stack: Tuple[str, ...],
    ) -> Dict[str, Any]:
        target = str(edge["target"])
        edge_id = str(edge["id"])
        if target == owner_node:
            cycle = " -> ".join([*stack, target])
            raise primitives.AMRTripleConversionError(
                "scope traversal encountered a graph cycle: {}".format(cycle)
            )
        if target not in stack:
            return self._compile_node(target, owner_atoms, stack)

        # The parser can point a condition back to an already open ancestor.
        # Replaying that ancestor would recurse through the whole subtree.  Its
        # local active proposition is the finite antecedent represented by the
        # reentrancy; no graph content is inferred or repaired here.
        self.reentrant_semantic_edge_ids.add(edge_id)
        local = primitives._combine_ast(
            "and",
            [
                primitives._atom_ast(str(atom["id"]))
                for atom in owner_atoms.get(target, [])
            ],
        )
        if local.get("op") == "true":
            cycle = " -> ".join([*stack, target])
            raise primitives.AMRTripleConversionError(
                "scope cycle has no local proposition: {}".format(cycle)
            )
        return local

    # record partition: convert every non-structural edge or
    # attribute into a canonical dyadic record.  Mode metadata is projected
    # only when it has a supported lexical/event endpoint.
    def _build_dyadic_records(self) -> None:
        semantic_records = [
            item
            for item in [*self.edges, *self.attributes]
            if item["id"] not in self.structural_ids
        ]
        for index, item in enumerate(semantic_records, 1):
            source_surface = self.node_surface(item["source"])
            target_surface = (
                self.node_surface(item["target"])
                if item["target_is_node"]
                else primitives._literal_text(item["target"])
            )
            role = primitives._role_name(item["role"])
            surface = self._render_dyad(
                source_surface, role, target_surface
            )
            payload = self._dyad_payload(item)
            record = {
                "id": "d{}".format(index),
                "graph_record_id": item["id"],
                "record_type": item["record_type"],
                "source_node": item["source"],
                "target_node": (
                    item["target"] if item["target_is_node"] else None
                ),
                "source_concept": self.concepts.get(item["source"], ""),
                "target_concept": (
                    self.concepts.get(item["target"], "")
                    if item["target_is_node"]
                    else item["target"]
                ),
                "role": role,
                "terms": [source_surface, target_surface],
                "canonical_payload": payload,
                "canonical_key": (
                    _canonical_key(
                        "dyad", payload
                    )
                ),
                "base_surface_text": surface,
                "signed_surface_text": surface,
                "nli_surface_text": surface,
            }
            self.dyadic_records.append(record)
            self.dyad_by_graph_record[item["id"]] = record

        grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for record in self.metadata_records_raw:
            if str(record.get("metadata_category", "")) != "mode":
                continue
            grouped[self._semantic_occurrence_key(record)].append(record)

        for records in grouped.values():
            primary = records[0]
            source_node = str(primary["source"])
            target_node = (
                str(primary["target"])
                if primary.get("target_is_node") is True
                else None
            )
            source_surface = self.node_surface(source_node)
            target_surface = self._metadata_value_surface(primary)
            payload = self._dyad_payload(primary)
            occurrence_ids = [str(record["id"]) for record in records]
            item = {
                "id": "d{}".format(len(self.dyadic_records) + 1),
                "graph_record_id": occurrence_ids[0],
                "graph_record_ids": occurrence_ids,
                "record_type": str(primary["record_type"]),
                "source_node": source_node,
                "target_node": target_node,
                "source_concept": self.concepts.get(source_node, ""),
                "target_concept": (
                    self.concepts.get(target_node, "")
                    if target_node is not None
                    else primary.get("target", "")
                ),
                "role": "mode",
                "terms": [source_surface, target_surface],
                "canonical_payload": payload,
                "canonical_key": _canonical_key(
                    "dyad", payload
                ),
                "base_surface_text": "{} {}".format(
                    source_surface, target_surface
                ).strip(),
                "signed_surface_text": "{} {}".format(
                    source_surface, target_surface
                ).strip(),
                "nli_surface_text": "{} {}".format(
                    source_surface, target_surface
                ).strip(),
                "metadata_category": "mode",
                "reference_mode_projection": True,
            }
            self.dyadic_records.append(item)
            for record_id in occurrence_ids:
                self.dyad_by_graph_record[record_id] = item
            if len(occurrence_ids) > 1:
                self.consumed_ids.update(occurrence_ids[1:])

    def _add_fallback_dyads(self) -> None:
        # ``:mode`` is clause-force metadata. Preserve its active dyad only
        # when its source is a lexical/event node. A mode
        # attached to a structural connective (for example, an exclamation
        # whose AMR root is ``and``) has no parser-faithful dyadic endpoint:
        # distributing it over the conjuncts would be semantic repair, while
        # the generic connective fallback would incorrectly turn metadata
        # into an opaque semantic atom.  Keep the record in metadata and
        # suppress only that unsupported active projection.
        inactive = [
            record
            for record in self.dyadic_records
            if record.get("reference_mode_projection") is True
            and self._is_structural_connective(str(record["source_node"]))
        ]
        original: Optional[List[Dict[str, Any]]] = None
        if inactive:
            inactive_ids = {id(record) for record in inactive}
            original = self.dyadic_records
            self.dyadic_records = [
                record
                for record in original
                if id(record) not in inactive_ids
            ]
        try:
            for record in list(self.dyadic_records):
                if record.get("projected") is True:
                    continue
                graph_record_id = record["graph_record_id"]
                if graph_record_id in self.consumed_ids:
                    continue
                owner = str(record["source_node"])
                if self._is_structural_connective(owner):
                    self._add_spec(
                        self._opaque_connective_source_spec(record)
                    )
                    continue
                edge = self.edge_by_id.get(str(graph_record_id))
                if edge is not None and self._is_structural_connective(
                    str(edge["target"])
                ):
                    variants = self._endpoint_variants(edge)
                    for target_node, projected, root, path in variants:
                        if root is None:
                            raise primitives.AMRTripleConversionError(
                                "connective endpoint projection lost its "
                                "parser root"
                            )
                        self.nodes_used_as_terms.update(
                            {owner, target_node}
                        )
                        self._add_spec(
                            self._active_dyad_spec(
                                projected,
                                owner=owner,
                                coordination_root_node=root,
                                coordination_path=path,
                                formula_owner_node=target_node,
                            )
                        )
                    self.consumed_ids.add(graph_record_id)
                    continue
                self._add_spec(
                    self._active_dyad_spec(record, owner=owner)
                )
        finally:
            if original is not None:
                self.dyadic_records = original

    # triple templates: pair the first available core anchor
    # role with each other core role on the same event.  Coordinated
    # endpoints are projected branch-locally rather than merged globally.
    def _compose_same_event_triples(self) -> None:
        seen: Set[Tuple[str, str, str, str]] = set()
        for event in self.concepts:
            if self._is_structural_connective(event):
                continue
            composable = self._core_edges(event)
            roles_present = {
                str(edge["role"]).casefold() for edge in composable
            }
            anchor_role = next(
                (role for role in _ANCHOR_ROLES if role in roles_present),
                None,
            )
            if anchor_role is None:
                continue
            anchor_edges = [
                edge
                for edge in composable
                if str(edge["role"]).casefold() == anchor_role
            ]
            other_edges = [
                edge
                for edge in composable
                if str(edge["role"]).casefold() != anchor_role
            ]
            for anchor in anchor_edges:
                for other in other_edges:
                    signature = "{}+{}".format(
                        primitives._role_name(
                            anchor["role"]
                        ).upper(),
                        primitives._role_name(
                            other["role"]
                        ).upper(),
                    )
                    subject_variants = self._endpoint_variants(anchor)
                    object_variants = self._endpoint_variants(other)
                    subject_groups = {
                        group
                        for _node, _dyad, group, _path in subject_variants
                        if group is not None
                    }
                    object_groups = {
                        group
                        for _node, _dyad, group, _path in object_variants
                        if group is not None
                    }
                    if subject_groups and object_groups:
                        continue
                    for (
                        subject_node,
                        subject_dyad,
                        subject_group,
                        subject_path,
                    ) in subject_variants:
                        for (
                            object_node,
                            object_dyad,
                            object_group,
                            object_path,
                        ) in object_variants:
                            key = (event, subject_node, object_node, signature)
                            if key in seen:
                                continue
                            seen.add(key)
                            group = subject_group or object_group
                            coordination_path = subject_path or object_path
                            coordination_edge_id = (
                                anchor["id"]
                                if subject_group is not None
                                else other["id"]
                            )
                            self._add_spec(
                                self._triple_spec(
                                    owner=event,
                                    subject_node=subject_node,
                                    predicate_node=event,
                                    object_node=object_node,
                                    join_signature=signature,
                                    component_edges=[anchor, other],
                                    component_dyads=[subject_dyad, object_dyad],
                                    extra_source_ids=[
                                        *subject_path,
                                        *object_path,
                                    ],
                                    coordination_root_node=group,
                                    coordination_path=coordination_path,
                                    coordination_source_edge_id=(
                                        coordination_edge_id
                                    ),
                                    formula_owner_node=(
                                        subject_node
                                        if subject_group is not None
                                        else (
                                            object_node
                                            if object_group is not None
                                            else None
                                        )
                                    ),
                                )
                            )
                            self.consumed_ids.update(
                                {str(anchor["id"]), str(other["id"])}
                            )

    def _spec_participant_nodes(self, spec: Mapping[str, Any]) -> List[str]:
        kind = str(spec.get("kind", ""))
        candidates: List[str] = []
        if kind == "triple":
            candidates.extend(
                [
                    str(spec.get("subject_node", "")),
                    str(spec.get("object_node", "")),
                ]
            )
        elif kind == "dyadic":
            dyads = {
                str(record["id"]): record for record in self.dyadic_records
            }
            for dyad_id in spec.get("component_dyad_ids", []):
                record = dyads.get(str(dyad_id))
                if record is None:
                    continue
                target = str(record.get("target_node", ""))
                if target in self.concepts:
                    candidates.append(target)
        else:
            # Unary atoms have no relation participant.  Opaque atoms stay
            # conservative because their endpoint contract is unsupported.
            return []

        predicate_nodes = {
            str(spec.get("predicate_node", "")),
            str(spec.get("governor_node", "")),
            str(spec.get("event_occurrence_id", "")),
        }
        ordered: List[str] = []
        seen: Set[str] = set()
        for node in candidates:
            if (
                node
                and node in self.concepts
                and node not in predicate_nodes
                and node not in seen
            ):
                seen.add(node)
                ordered.append(node)
        return ordered

    # participant-polarity rule: mark relation atoms whose
    # subject or object is explicitly negated so the later formula pass can
    # negate that occurrence without moving it between branches.
    def _prepare_participant_polarity(self) -> None:
        if self._participant_polarity_prepared:
            return
        self._participant_polarity_prepared = True
        seen_projection_ids: Set[str] = set()
        raw_has_negative_polarity = self._raw_has_negative_polarity

        for spec in self.atom_specs:
            negative_nodes = [
                node
                for node in self._spec_participant_nodes(spec)
                if raw_has_negative_polarity(node)
            ]
            if not negative_nodes:
                continue

            projection_id = polarity_rules._stable_projection_id(spec, negative_nodes)
            if projection_id in seen_projection_ids:
                raise primitives.AMRTripleConversionError(
                    "participant-polarity projection id collision: {}".format(
                        projection_id
                    )
                )
            seen_projection_ids.add(projection_id)
            owner = str(spec["_owner"])
            spec.update(
                {
                    "participant_polarity_projection_id": projection_id,
                    "participant_polarity_source_nodes": list(negative_nodes),
                    "polarity_source_nodes": list(negative_nodes),
                    "semantic_owner_node": owner,
                    "formula_owner_node": owner,
                    "participant_polarity_projected": True,
                }
            )
            self.projected_negative_participant_nodes.update(negative_nodes)
            self.participant_polarity_bindings.append(
                {
                    "projection_id": projection_id,
                    "kind": str(spec.get("kind", "")),
                    "canonical_key": str(spec.get("canonical_key", "")),
                    "semantic_owner_node": owner,
                    "formula_owner_node": owner,
                    "negative_participant_nodes": list(negative_nodes),
                    "negative_participant_count": len(negative_nodes),
                    "source_graph_record_ids": list(
                        map(str, spec.get("source_graph_record_ids", []))
                    ),
                    "owner_preserved": True,
                    "head_polarity_projected": False,
                }
            )

    def _has_negative_polarity(self, node: str) -> bool:
        if str(node) in self._temporarily_suppressed_formula_nodes:
            return False
        return self._raw_has_negative_polarity(node)

    # active-atom filtering: add a unary only when the node
    # is not already represented as a term or owner, except for the narrow
    # negative-carrier cases needed to preserve an otherwise empty scope.
    def _add_required_unaries(self) -> None:
        self._attach_duplicate_provenance()
        self._prepare_participant_polarity()
        owners = {str(spec["_owner"]) for spec in self.atom_specs}
        name_nodes = {
            str(edge["target"])
            for edge in self.edges
            if str(edge["role"]).casefold() == ":name"
            and str(edge["id"]) in self.structural_ids
        }
        owner_atoms = self._active_formula_probe()
        for unary in self.unary_records:
            node = str(unary["node"])
            if (
                node in name_nodes
                or node in self.metadata_term_nodes
                or self._concept_canonical(node)
                in {"and", "or", "multi sentence", "name"}
                or node in owners
            ):
                continue

            negative_carrier = self._has_negative_polarity(node)
            projected_contextual_carrier = False
            if (
                negative_carrier
                and node in self.projected_negative_participant_nodes
            ):
                if self._inverse_parent_context_needs_carrier(
                    node, owner_atoms
                ):
                    projected_contextual_carrier = True
                else:
                    continue

            if (
                negative_carrier
                and not projected_contextual_carrier
                and self._negative_scope_has_active_atoms(node, owner_atoms)
                and not self._inverse_parent_context_needs_carrier(
                    node, owner_atoms
                )
            ):
                continue
            if not negative_carrier and node in self.nodes_used_as_terms:
                continue

            semantic_incident = [
                edge
                for edge in [
                    *self.outgoing.get(node, []),
                    *self.incoming.get(node, []),
                ]
                if str(edge["id"]) not in self.structural_ids
            ]
            if semantic_incident and not negative_carrier:
                continue

            surface = self._bare_unary_surface(node)
            payload = copy.deepcopy(unary["canonical_payload"])
            self._add_spec(
                {
                    "_owner": node,
                    "kind": "unary",
                    "arity": 1,
                    "terms": [self.node_surface(node)],
                    "predicate": self.node_surface(node),
                    "component_dyad_ids": [],
                    "source_graph_record_ids": [],
                    "canonical_payload": payload,
                    "canonical_key": _canonical_key(
                        "unary", payload
                    ),
                    "base_surface_text": surface,
                }
            )

    def _active_formula_probe(
        self,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        owner_atoms: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for index, spec in enumerate(self.atom_specs, 1):
            owner = str(spec["_owner"])
            owner_atoms[owner].append(
                {
                    "id": "probe{}".format(index),
                    "owner_node": owner,
                    "event_occurrence_id": spec.get(
                        "event_occurrence_id"
                    ),
                    "governor_node": spec.get("governor_node"),
                    "predicate_node": spec.get("predicate_node"),
                    "source_node": spec.get("source_node"),
                }
            )
        return owner_atoms

    def _formula_event_nodes(self) -> Set[str]:
        result: Set[str] = set()
        for spec in self.atom_specs:
            node = _node_value(
                spec,
                "event_occurrence_id",
                "governor_node",
                "predicate_node",
                "source_node",
            )
            if node in self.concepts:
                result.add(node)
        return result

    def _propositional_scopes(
        self, path: Tuple[str, ...]
    ) -> List[Tuple[str, str, Tuple[str, ...]]]:
        event_nodes = self._formula_event_nodes()
        scopes: List[Tuple[str, str, Tuple[str, ...]]] = []
        for index, connective in enumerate(path[:-1]):
            # Multi-sentence nodes order discourse units; a later sentence may
            # intentionally re-enter an earlier event. Branch isolation is only
            # valid for explicit propositional conjunctions/disjunctions.
            if self._concept_canonical(connective) not in {"and", "or"}:
                continue
            targets = tuple(
                str(edge["target"])
                for edge in self._connective_op_edges(connective)
            )
            if len(targets) < 2:
                continue
            active = str(path[index + 1])
            event_targets = tuple(
                target for target in targets if target in event_nodes
            )
            if active in event_targets and len(event_targets) >= 2:
                scopes.append((str(connective), active, targets))
        return scopes

    def _branch_reachable_nodes(
        self, connective: str, active: str, targets: Tuple[str, ...]
    ) -> Set[str]:
        key = (str(connective), str(active), tuple(targets))
        cached = self._branch_reachability_cache.get(key)
        if cached is not None:
            return cached
        blocked = (set(targets) - {str(active)}) | {str(connective)}
        reached: Set[str] = set()
        agenda = [str(active)]
        while agenda:
            node = agenda.pop()
            if node in reached or node in blocked:
                continue
            reached.add(node)
            for edge in self.outgoing.get(node, []):
                target = str(edge["target"])
                if target in self.concepts and target not in reached:
                    agenda.append(target)
            for child, _edge_id in self.inverse_tree_children.get(node, []):
                child = str(child)
                if child in self.concepts and child not in reached:
                    agenda.append(child)
        self._branch_reachability_cache[key] = reached
        return reached

    def _atom_branch_anchor(self, atom: Mapping[str, Any]) -> str:
        return _node_value(
            atom,
            "event_occurrence_id",
            "governor_node",
            "predicate_node",
            "source_node",
            "semantic_owner_node",
            "formula_owner_node",
            "owner_node",
            "_owner",
        )

    def _atom_allowed_in_scopes(
        self,
        atom: Mapping[str, Any],
        scopes: Sequence[Tuple[str, str, Tuple[str, ...]]],
    ) -> bool:
        anchor = self._atom_branch_anchor(atom)
        if not anchor:
            return True
        for connective, active, targets in scopes:
            active_reachable = self._branch_reachable_nodes(
                connective, active, targets
            )
            sibling_reachable: Set[str] = set()
            for sibling in targets:
                if sibling == active:
                    continue
                sibling_reachable.update(
                    self._branch_reachable_nodes(
                        connective, sibling, targets
                    )
                )
            if anchor in sibling_reachable and anchor not in active_reachable:
                return False
        return True

    def _formula_traversal_children(self, node: str) -> List[str]:
        node = str(node)
        condition_ids = {
            str(edge["id"]) for edge in self._condition_edges(node)
        }
        op_edges = self._connective_op_edges(node)
        op_ids = {str(edge["id"]) for edge in op_edges}
        ordered: List[str] = []
        for edge in op_edges:
            target = str(edge["target"])
            if target in self.concepts:
                ordered.append(target)
        for edge in self.outgoing.get(node, []):
            edge_id = str(edge["id"])
            target = str(edge["target"])
            if (
                edge_id not in op_ids
                and edge_id not in self.structural_ids
                and target in self.concepts
            ):
                ordered.append(target)
        for child, edge_id in self.inverse_tree_children.get(node, []):
            child = str(child)
            edge_id = str(edge_id)
            if (
                edge_id not in condition_ids
                and edge_id not in op_ids
                and child in self.concepts
            ):
                ordered.append(child)
        return ordered

    def _formula_context_stacks(self, target: str) -> List[Tuple[str, ...]]:
        target = str(target)
        paths: List[Tuple[str, ...]] = []

        def visit(node: str, stack: Tuple[str, ...]) -> None:
            node = str(node)
            if node == target:
                paths.append(stack)
                return
            if node in stack:
                return
            next_stack = (*stack, node)
            for child in self._formula_traversal_children(node):
                visit(child, next_stack)

        for root in self._formula_roots():
            visit(str(root), ())
        unique: List[Tuple[str, ...]] = []
        seen: Set[Tuple[str, ...]] = set()
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique.append(path)
        return unique

    def _inverse_parent_context_needs_carrier(
        self,
        node: str,
        owner_atoms: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> bool:
        node = str(node)
        inverse_parents = {
            str(parent)
            for parent, children in self.inverse_tree_children.items()
            if any(
                str(child) == node
                for child, _edge_id in children
            )
        }
        if not inverse_parents:
            return False

        # First reproduce the immediate inverse-parent traversal. A node
        # that becomes empty there needs a local carrier even if compiling
        # it from the graph root appears non-empty.
        original_reentrant_ids = set(self.reentrant_semantic_edge_ids)
        try:
            for parent in sorted(inverse_parents):
                contextual = self._compile_node(
                    node, owner_atoms, (parent,)
                )
                if not primitives._formula_ids(
                    contextual
                ):
                    return True
        finally:
            self.reentrant_semantic_edge_ids.clear()
            self.reentrant_semantic_edge_ids.update(
                original_reentrant_ids
            )

        if node in self.projected_negative_participant_nodes:
            return False

        original_reentrant_ids = set(self.reentrant_semantic_edge_ids)
        try:
            for stack in self._formula_context_stacks(node):
                contextual = self._compile_node(
                    node, owner_atoms, tuple(stack)
                )
                if not primitives._formula_ids(contextual):
                    return True
        finally:
            self.reentrant_semantic_edge_ids.clear()
            self.reentrant_semantic_edge_ids.update(
                original_reentrant_ids
            )
        return False

    # Branch isolation: atoms owned by a predicate shared
    # across explicit and/or branches are rebound only to the branch whose
    # provenance contains the active occurrence.  Multi-sentence is not
    # treated as an exclusive branch.
    def _formula_view_with_exclusive_nested_anchors(
        self,
        node: str,
        owner_atoms: Mapping[str, Sequence[Mapping[str, Any]]],
        stack: Tuple[str, ...],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Keep an exclusively nested predicate in its active branch."""

        node = str(node)
        local_atoms = list(owner_atoms.get(node, []))
        if not local_atoms:
            return owner_atoms
        anchors = {
            self._atom_branch_anchor(atom)
            for atom in local_atoms
            if self._atom_branch_anchor(atom)
        }
        if len(anchors) <= 1:
            return owner_atoms
        active_anchor = next(
            (
                ancestor
                for ancestor in reversed(tuple(map(str, stack)))
                if ancestor in anchors
            ),
            "",
        )
        if not active_anchor:
            return owner_atoms
        scopes = self._propositional_scopes(
            (*tuple(map(str, stack)), node)
        )
        if not scopes:
            return owner_atoms

        rebound_atoms: List[Mapping[str, Any]] = []
        changed = False
        for atom in local_atoms:
            anchor = self._atom_branch_anchor(atom)
            if not anchor or anchor in {active_anchor, node}:
                rebound_atoms.append(atom)
                continue
            rebind = False
            for connective, active, targets in scopes:
                active_reachable = self._branch_reachable_nodes(
                    connective, active, targets
                )
                if active_anchor not in active_reachable:
                    continue
                sibling_reachable: Set[str] = set()
                for sibling in targets:
                    if sibling != active:
                        sibling_reachable.update(
                            self._branch_reachable_nodes(
                                connective, sibling, targets
                            )
                        )
                if (
                    anchor in active_reachable
                    and anchor not in sibling_reachable
                ):
                    rebind = True
                    break
            if not rebind:
                rebound_atoms.append(atom)
                continue

            formula_atom = dict(atom)
            formula_atom["event_occurrence_id"] = active_anchor
            rebound_atoms.append(formula_atom)
            changed = True

        if not changed:
            return owner_atoms
        scoped_owner_atoms = dict(owner_atoms)
        scoped_owner_atoms[node] = rebound_atoms
        return scoped_owner_atoms

    def _negative_scope_has_active_atoms(
        self,
        node: str,
        owner_atoms: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> bool:
        """Test the negative body, excluding a condition antecedent."""

        original_reentrant_ids = set(self.reentrant_semantic_edge_ids)
        try:
            formula = self._compile_node(str(node), owner_atoms, ())
        finally:
            self.reentrant_semantic_edge_ids.clear()
            self.reentrant_semantic_edge_ids.update(
                original_reentrant_ids
            )

        inspected = formula
        if (
            self._has_negative_polarity(str(node))
            and formula.get("op") == "implies"
        ):
            consequent = formula.get("consequent")
            if not isinstance(consequent, Mapping):
                raise TranslatorContractError(
                    "conditional negative owner lacks a consequent"
                )
            inspected = consequent
        return bool(primitives._formula_ids(inspected))

    # scope traversal: filter shared-owner atoms and inverse
    # edges to the active branch before recursion.  Structural cycles fail;
    # ordinary semantic reentrancies are recorded and not expanded twice.
    def _compile_node(
        self,
        node: str,
        owner_atoms: Mapping[str, Sequence[Mapping[str, Any]]],
        stack: Tuple[str, ...],
    ) -> Dict[str, Any]:
        node = str(node)
        stack = tuple(map(str, stack))
        owner_atoms = self._formula_view_with_exclusive_nested_anchors(
            node, owner_atoms, stack
        )
        local_atoms = list(owner_atoms.get(node, []))
        scopes = self._propositional_scopes((*stack, node))
        filtered_ids: Tuple[str, ...] = ()
        if scopes and local_atoms:
            filtered = [
                atom
                for atom in local_atoms
                if not self._atom_allowed_in_scopes(atom, scopes)
            ]
            if len(filtered) == len(local_atoms):
                filtered_ids = tuple(
                    sorted(
                        str(atom.get("id", ""))
                        for atom in filtered
                        if str(atom.get("id", ""))
                    )
                )

        scoped_owner_atoms = owner_atoms
        anchors = {
            self._atom_branch_anchor(atom)
            for atom in local_atoms
            if self._atom_branch_anchor(atom)
        }

        # A single-anchor owner is already occurrence-local.  For a shared
        # owner, the nearest proper ancestor whose identity occurs in the
        # atom provenance is the active event for this traversal.
        if len(anchors) > 1:
            active_anchor = ""
            for ancestor in reversed(tuple(map(str, stack))):
                if ancestor in anchors:
                    active_anchor = ancestor
                    break
            if active_anchor:
                retained: List[Mapping[str, Any]] = []
                for atom in local_atoms:
                    anchor = self._atom_branch_anchor(atom)
                    if not anchor or anchor in {active_anchor, node}:
                        retained.append(atom)
                        continue
                    continue
                if retained and len(retained) != len(local_atoms):
                    scoped_owner_atoms = dict(owner_atoms)
                    scoped_owner_atoms[node] = retained

        local_atoms = list(scoped_owner_atoms.get(node, []))
        if scopes and local_atoms:
            retained = [
                atom
                for atom in local_atoms
                if self._atom_allowed_in_scopes(atom, scopes)
            ]
            if len(retained) != len(local_atoms):
                scoped_owner_atoms = dict(scoped_owner_atoms)
                scoped_owner_atoms[node] = retained

        original_inverse = self.inverse_tree_children.get(node, [])
        retained_inverse = list(original_inverse)
        if scopes and original_inverse:
            retained_inverse = []
            for child, edge_id in original_inverse:
                blocked = False
                for _connective, active, targets in scopes:
                    if str(child) in set(targets) - {active}:
                        self.reentrant_semantic_edge_ids.add(str(edge_id))
                        blocked = True
                        break
                if not blocked:
                    retained_inverse.append(
                        (str(child), str(edge_id))
                    )

        inverse_changed = retained_inverse != list(original_inverse)
        if inverse_changed:
            self.inverse_tree_children[node] = retained_inverse

        suppress_empty_participant = (
            node in self.projected_negative_participant_nodes
            and not scoped_owner_atoms.get(node)
        )
        already_suppressed = (
            node in self._temporarily_suppressed_formula_nodes
        )
        if suppress_empty_participant:
            self._temporarily_suppressed_formula_nodes.add(node)
        try:
            result = self._compile_node_core(
                node, scoped_owner_atoms, stack
            )
        finally:
            if suppress_empty_participant and not already_suppressed:
                self._temporarily_suppressed_formula_nodes.remove(node)
            if inverse_changed:
                self.inverse_tree_children[node] = original_inverse

        arg = result.get("arg") if isinstance(result, Mapping) else None
        is_empty_negative = (
            result.get("op") == "not"
            and isinstance(arg, Mapping)
            and arg.get("op") == "true"
        )
        if (
            is_empty_negative
            and filtered_ids
            and self._has_negative_polarity(node)
        ):
            return {"op": "true"}
        return result
