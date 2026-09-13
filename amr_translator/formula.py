"""Boolean constant normalization helpers."""
from __future__ import annotations

from typing import Any, Dict, Mapping
import copy

from .primitives import AMRTripleValidationError


def _formula_constant_count(node: Mapping[str, Any]) -> int:
    op = str(node.get("op", ""))
    if op in {"true", "false"}:
        return 1
    if op == "atom":
        return 0
    if op == "not":
        return _formula_constant_count(node.get("arg", {}))
    if op in {"and", "or"}:
        return sum(
            _formula_constant_count(arg)
            for arg in node.get("args", [])
        )
    if op == "implies":
        return _formula_constant_count(
            node.get("antecedent", {})
        ) + _formula_constant_count(node.get("consequent", {}))
    raise AMRTripleValidationError(
        "hard-formula parent formula has unsupported operator {!r}".format(op)
    )

def _normalize_solver_constants(
    node: Mapping[str, Any]
) -> Dict[str, Any]:
    """Remove Boolean constants using only truth-preserving identities."""

    op = str(node.get("op", ""))
    if op in {"true", "false", "atom"}:
        return copy.deepcopy(dict(node))
    if op == "not":
        arg = _normalize_solver_constants(node.get("arg", {}))
        if arg.get("op") == "true":
            return {"op": "false"}
        if arg.get("op") == "false":
            return {"op": "true"}
        return {"op": "not", "arg": arg}
    if op in {"and", "or"}:
        args = [
            _normalize_solver_constants(arg)
            for arg in node.get("args", [])
        ]
        if op == "and":
            if any(arg.get("op") == "false" for arg in args):
                return {"op": "false"}
            args = [arg for arg in args if arg.get("op") != "true"]
            identity = {"op": "true"}
        else:
            if any(arg.get("op") == "true" for arg in args):
                return {"op": "true"}
            args = [arg for arg in args if arg.get("op") != "false"]
            identity = {"op": "false"}
        if not args:
            return identity
        if len(args) == 1:
            return args[0]
        return {"op": op, "args": args}
    if op == "implies":
        antecedent = _normalize_solver_constants(
            node.get("antecedent", {})
        )
        consequent = _normalize_solver_constants(
            node.get("consequent", {})
        )
        if antecedent.get("op") == "true":
            return consequent
        if antecedent.get("op") == "false":
            return {"op": "true"}
        if consequent.get("op") == "true":
            return {"op": "true"}
        if consequent.get("op") == "false":
            return _normalize_solver_constants(
                {"op": "not", "arg": antecedent}
            )
        return {
            "op": "implies",
            "antecedent": antecedent,
            "consequent": consequent,
        }
    raise AMRTripleValidationError(
        "hard-formula parent formula has unsupported operator {!r}".format(op)
    )
