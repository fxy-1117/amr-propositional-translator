"""Core AMR record, surface, and Boolean-AST helpers."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple
import json
import re

from penman.models.amr import model as AMR_MODEL


_SENSE_RE = re.compile(r"-\d+$")

_OP_ROLE_RE = re.compile(r"^:op(\d+)$", re.IGNORECASE)

_SPACE_RE = re.compile(r"\s+")

class AMRTripleError(ValueError):
    """Base error for the isolated triple representation."""

class AMRTripleConversionError(AMRTripleError):
    """Raised when a decoded graph cannot be compiled into a finite AST."""

class AMRTripleValidationError(AMRTripleError):
    """Raised when an artifact violates the foundation stage contract."""

def _strip_sense(value: Any) -> str:
    return _SENSE_RE.sub("", str(value).strip())

def _literal_text(value: Any) -> str:
    raw = str(value).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            raw = str(json.loads(raw))
        except (TypeError, ValueError):
            raw = raw[1:-1]
    return _SPACE_RE.sub(" ", raw.replace("_", " ")).strip()

def _surface_label(value: Any) -> str:
    stripped = _literal_text(_strip_sense(value))
    # Some parser graphs contain a literal concept node ``(a / -)``.  It is
    # unusual but structurally valid; preserving it is preferable to either
    # deleting it or reinterpreting it as a repaired polarity annotation.
    if stripped == "-":
        return stripped
    return stripped.replace("-", " ")

def _canonical_label(value: Any) -> str:
    return _SPACE_RE.sub(" ", _surface_label(value).casefold()).strip()

def _role_name(role: Any) -> str:
    return str(role).strip().lstrip(":")

def _true_ast() -> Dict[str, Any]:
    return {"op": "true"}

def _false_ast() -> Dict[str, Any]:
    return {"op": "false"}

def _atom_ast(atom_id: str) -> Dict[str, Any]:
    return {"op": "atom", "id": str(atom_id)}

def _ast_key(node: Mapping[str, Any]) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"))

def _combine_ast(op: str, parts: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    if op not in {"and", "or"}:
        raise ValueError("op must be 'and' or 'or'")
    flattened: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for raw_part in parts:
        part = dict(raw_part)
        part_op = part.get("op")
        if op == "and" and part_op == "false":
            return _false_ast()
        if op == "or" and part_op == "true":
            return _true_ast()
        if (op == "and" and part_op == "true") or (
            op == "or" and part_op == "false"
        ):
            continue
        candidates = part.get("args", []) if part_op == op else [part]
        for candidate in candidates:
            item = dict(candidate)
            key = _ast_key(item)
            if key not in seen:
                seen.add(key)
                flattened.append(item)
    if not flattened:
        return _true_ast() if op == "and" else _false_ast()
    if len(flattened) == 1:
        return flattened[0]
    return {"op": op, "args": flattened}

def _not_ast(body: Mapping[str, Any]) -> Dict[str, Any]:
    if body.get("op") == "not":
        return dict(body.get("arg", _true_ast()))
    return {"op": "not", "arg": dict(body)}

def formula_ast_to_string(node: Mapping[str, Any]) -> str:
    """Render the formula with the syntax consumed by the frozen solver."""

    op = node.get("op")
    if op == "true":
        return "True"
    if op == "false":
        return "False"
    if op == "atom":
        return str(node["id"])
    if op == "not":
        return "~({})".format(formula_ast_to_string(node["arg"]))
    if op in {"and", "or"}:
        token = " & " if op == "and" else " | "
        return "({})".format(
            token.join(formula_ast_to_string(arg) for arg in node.get("args", []))
        )
    if op == "implies":
        left = formula_ast_to_string(node["antecedent"])
        right = formula_ast_to_string(node["consequent"])
        return "(({}) >> ({}))".format(left, right)
    raise AMRTripleValidationError(
        "Unsupported formula AST operator: {!r}".format(op)
    )

def _formula_ids(node: Mapping[str, Any]) -> Set[str]:
    op = node.get("op")
    if op == "atom":
        return {str(node.get("id", ""))}
    if op in {"true", "false"}:
        return set()
    if op == "not":
        return _formula_ids(node.get("arg", {}))
    if op in {"and", "or"}:
        result: Set[str] = set()
        for arg in node.get("args", []):
            result.update(_formula_ids(arg))
        return result
    if op == "implies":
        return _formula_ids(node.get("antecedent", {})) | _formula_ids(
            node.get("consequent", {})
        )
    raise AMRTripleValidationError(
        "Unsupported formula AST operator: {!r}".format(op)
    )

def _ordered_role_items(items: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    def key(item: Mapping[str, Any]) -> Tuple[int, str]:
        match = _OP_ROLE_RE.match(str(item.get("role", "")))
        return (int(match.group(1)) if match else 10**9, str(item.get("id", "")))

    return sorted(items, key=key)


_SNT_ROLE_RE = re.compile(r"^:snt([1-9][0-9]*)$", re.IGNORECASE)
_ANCHOR_ROLES = tuple(":arg{}".format(index) for index in range(5))

def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )

def _canonical_key(kind: str, payload: Mapping[str, Any]) -> str:
    return "{}-v1.2:{}".format(kind, _canonical_json(payload))

def _role_key(role: Any) -> str:
    return str(role or "").strip().lstrip(":").casefold()

def _contains_not_true(node: Mapping[str, Any]) -> bool:
    op = node.get("op")
    if op == "not":
        arg = node.get("arg", {})
        return bool(isinstance(arg, Mapping) and arg.get("op") == "true") or (
            isinstance(arg, Mapping) and _contains_not_true(arg)
        )
    if op in {"and", "or"}:
        return any(
            isinstance(arg, Mapping) and _contains_not_true(arg)
            for arg in node.get("args", [])
        )
    if op == "implies":
        return any(
            isinstance(arg, Mapping) and _contains_not_true(arg)
            for arg in (node.get("antecedent", {}), node.get("consequent", {}))
        )
    return False

def _ordered_unique(values):
    result = []
    seen = set()
    for value in values:
        item = str(value)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

def _require_unique_instance_nodes(graph: Any) -> None:
    """Reject malformed graphs that define one AMR variable twice."""

    seen: Set[str] = set()
    duplicates: Set[str] = set()
    for instance in graph.instances():
        node = str(instance.source)
        if node in seen:
            duplicates.add(node)
        else:
            seen.add(node)
    if duplicates:
        raise AMRTripleConversionError(
            "duplicate concept instance definitions for AMR variables: {}"
            .format(sorted(duplicates))
        )

def _node_value(item: Mapping[str, Any], *fields: str) -> str:
    """Return the first nonempty node-valued field."""

    for field in fields:
        value = item.get(field)
        if value is not None and str(value):
            return str(value)
    return ""
