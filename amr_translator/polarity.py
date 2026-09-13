"""Participant-local polarity and formula-occurrence helpers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple
import hashlib
import json

from .primitives import AMRTripleValidationError, AMRTripleConversionError


def _formula_occurrence_contexts(
    formula: Mapping[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    contexts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def walk(
        node: Mapping[str, Any], path: Tuple[str, ...], parity: bool
    ) -> None:
        op = str(node.get("op", ""))
        if op == "atom":
            contexts[str(node["id"])].append(
                {"structural_path": list(path), "not_parity": parity}
            )
            return
        if op in {"true", "false"}:
            return
        if op == "not":
            walk(node["arg"], path, not parity)
            return
        if op in {"and", "or"}:
            for index, arg in enumerate(node.get("args", [])):
                walk(arg, (*path, "{}[{}]".format(op, index)), parity)
            return
        if op == "implies":
            walk(
                node["antecedent"],
                (*path, "implies:antecedent"),
                parity,
            )
            walk(
                node["consequent"],
                (*path, "implies:consequent"),
                parity,
            )
            return
        raise AMRTripleValidationError(
            "Unsupported formula AST operator: {!r}".format(op)
        )

    walk(formula, (), False)
    return dict(contexts)

def _apply_local_participant_polarity(
    formula: Mapping[str, Any], projected_atom_ids: Set[str]
) -> Dict[str, Any]:
    """Wrap only positive leaf occurrences; never rewrite Boolean operators."""

    def project(node: Mapping[str, Any], parity: bool) -> Dict[str, Any]:
        op = str(node.get("op", ""))
        if op == "atom":
            atom = {"op": "atom", "id": str(node["id"])}
            if str(node["id"]) in projected_atom_ids and not parity:
                return {"op": "not", "arg": atom}
            return atom
        if op in {"true", "false"}:
            return {"op": op}
        if op == "not":
            return {"op": "not", "arg": project(node["arg"], not parity)}
        if op in {"and", "or"}:
            return {
                "op": op,
                "args": [project(arg, parity) for arg in node.get("args", [])],
            }
        if op == "implies":
            return {
                "op": "implies",
                "antecedent": project(node["antecedent"], parity),
                "consequent": project(node["consequent"], parity),
            }
        raise AMRTripleConversionError(
            "unsupported formula operator during participant-polarity "
            "projection: {!r}".format(op)
        )

    return project(formula, False)

def _stable_projection_id(
    spec: Mapping[str, Any], negative_nodes: Sequence[str]
) -> str:
    payload = {
        "kind": str(spec.get("kind", "")),
        "canonical_key": str(spec.get("canonical_key", "")),
        "owner": str(spec.get("_owner", "")),
        "subject_node": str(spec.get("subject_node", "")),
        "object_node": str(spec.get("object_node", "")),
        "negative_participant_nodes": sorted(map(str, negative_nodes)),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return "pp-{}".format(digest[:16])
