"""Canonical atom identities, surface normalization, and validated output frames."""
from __future__ import annotations

from typing import Any, Dict, Mapping
import copy
import json

from . import primitives, formula as formula_ops, propbank, verbalization
from .primitives import formula_ast_to_string


AMR_TRIPLE_RELEASE_EXACT_MATCH_POLICY = "normalized-base-surface-text"

class TranslatorContractError(RuntimeError):
    """Raised when the fixed standalone contract cannot be satisfied."""

def _apply_role_surfaces(base: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply the parser-faithful, PropBank-aware verbalization rules."""

    propbank.load_propbank_role_index()
    frame = copy.deepcopy(dict(base))
    for record in frame["dyadic_records"]:
        verbalization._resurface_record(record)
    dyad_by_id = {
        str(record["id"]): record for record in frame["dyadic_records"]
    }
    for atom in frame["atoms"]:
        verbalization._resurface_atom(atom, dyad_by_id)
    return frame

_EXPRESSION_KIND_BY_TYPE = {
    "unary-v1.2": "unary",
    "dyad-v1.2": "dyadic",
    "triple-v1.2": "triple",
    "opaque-v1.2": "opaque",
}

def normalize_exact_match_surface(value: Any) -> str:
    """Return the translator-owned surface used for exact atom matching."""

    return str(value).strip().casefold()

def format_nli_prompt_surface(value: Any) -> str:
    """Return the translator-owned atom surface passed to the NLI adapter."""

    return f"{str(value).strip()}."

def _expression_from_canonical_key(value: Any) -> Dict[str, Any]:
    """Decode the translator's canonical atom identity."""

    atom_type, separator, payload_text = str(value).partition(":")
    if not separator or atom_type not in _EXPRESSION_KIND_BY_TYPE:
        raise TranslatorContractError("release atom has an invalid expression type")
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError) as exc:
        raise TranslatorContractError(
            "release atom has a malformed expression"
        ) from exc
    if not isinstance(payload, Mapping) or "type" in payload:
        raise TranslatorContractError("release atom has a malformed expression")
    return {"type": atom_type, **copy.deepcopy(dict(payload))}

def canonical_atom_key(expression: Mapping[str, Any]) -> str:
    """Serialize a structured atom expression into its canonical identity."""

    if not isinstance(expression, Mapping):
        raise TranslatorContractError("release atom expression must be a mapping")
    atom_type = str(expression.get("type", ""))
    expected_kind = _EXPRESSION_KIND_BY_TYPE.get(atom_type)
    if expected_kind is None or expression.get("kind") != expected_kind:
        raise TranslatorContractError("release atom has an invalid expression type")
    payload = {
        key: copy.deepcopy(value)
        for key, value in expression.items()
        if key != "type"
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise TranslatorContractError(
            "release atom expression is not JSON serializable"
        ) from exc
    return "{}:{}".format(atom_type, encoded)

def _public_frame(frame: Mapping[str, Any]) -> Dict[str, Any]:
    """Expose only the formula and atom information used by the methods."""

    return {
        "formula_ast": copy.deepcopy(frame["formula_ast"]),
        "atoms": [
            {
                "id": str(atom["id"]),
                "expression": _expression_from_canonical_key(
                    atom["canonical_key"]
                ),
                "verbalization": str(atom["base_surface_text"]).strip(),
            }
            for atom in frame["atoms"]
        ],
    }

def finalize_frame(
    internal: Mapping[str, Any],
) -> Dict[str, Any]:
    """Normalize the formula and retain only atoms that remain active."""

    frame = copy.deepcopy(dict(internal))
    formula = frame.get("formula_ast")
    if not isinstance(formula, Mapping):
        raise TranslatorContractError("translator did not produce a formula")
    normalized_formula = formula_ops._normalize_solver_constants(
        formula
    )
    active_ids = primitives._formula_ids(normalized_formula)
    original_atoms = list(frame.get("atoms", []))
    frame["atoms"] = [
        copy.deepcopy(atom)
        for atom in original_atoms
        if str(atom.get("id", "")) in active_ids
    ]
    frame["formula_ast"] = normalized_formula
    result = _public_frame(frame)
    validate_frame(result)
    return result

def validate_frame(frame: Mapping[str, Any]) -> None:
    """Validate the minimal formula-and-atoms translator output."""

    if not isinstance(frame, Mapping):
        raise TranslatorContractError("release frame must be a mapping")
    if set(frame) != {"formula_ast", "atoms"}:
        raise TranslatorContractError(
            "release frame must contain only formula_ast and atoms"
        )

    atoms = frame.get("atoms")
    formula = frame.get("formula_ast")
    if not isinstance(atoms, list) or not isinstance(formula, Mapping):
        raise TranslatorContractError("release frame lacks atoms or formula_ast")

    atom_ids = []
    for atom in atoms:
        if not isinstance(atom, Mapping):
            raise TranslatorContractError("release atom must be a mapping")
        if set(atom) != {"id", "expression", "verbalization"}:
            raise TranslatorContractError(
                "release atom must contain only id, expression, and verbalization"
            )
        atom_id = str(atom.get("id", ""))
        verbalization = str(atom.get("verbalization", "")).strip()
        expression = atom.get("expression")
        if not atom_id or not verbalization:
            raise TranslatorContractError("release atom lacks an id or surface")
        canonical_atom_key(expression)
        atom_ids.append(atom_id)

    if len(atom_ids) != len(set(atom_ids)):
        raise TranslatorContractError("release frame has duplicate atom ids")

    def validate_formula(node: Any) -> None:
        if not isinstance(node, Mapping):
            raise TranslatorContractError("release formula node must be a mapping")
        op = node.get("op")
        if op == "atom":
            if not str(node.get("id", "")):
                raise TranslatorContractError("release formula has an empty atom id")
            return
        if op == "not":
            validate_formula(node.get("arg"))
            return
        if op in {"and", "or"}:
            args = node.get("args")
            if not isinstance(args, list) or len(args) < 2:
                raise TranslatorContractError(
                    "release formula has a malformed Boolean connective"
                )
            for arg in args:
                validate_formula(arg)
            return
        if op == "implies":
            validate_formula(node.get("antecedent"))
            validate_formula(node.get("consequent"))
            return
        raise TranslatorContractError(
            "release formula has unsupported operator {!r}".format(op)
        )

    validate_formula(formula)
    if primitives._formula_ids(formula) != set(atom_ids):
        raise TranslatorContractError(
            "release formula and atom inventory differ"
        )
    if formula_ops._formula_constant_count(formula):
        raise TranslatorContractError(
            "release formula contains a Boolean constant"
        )
