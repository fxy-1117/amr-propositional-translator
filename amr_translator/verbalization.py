"""Final parser-faithful atom surface realization."""
from __future__ import annotations

from typing import Any, Dict, Mapping

from . import primitives, role_templates
from .role_templates import _surface


_ADJUNCT_PREPOSITIONS = {
    "accompanier": "with",
    "beneficiary": "for",
    "cause": "because of",
    "destination": "to",
    "direction": "toward",
    "duration": "for",
    "extent": "by",
    "instrument": "using",
    "location": "at",
    "manner": "by",
    "medium": "via",
    "path": "along",
    "purpose": "for",
    "source": "from",
    "time": "during",
    "topic": "about",
}
_UNSAFE_WITH_RELATIONS = frozenset({"agent", "patient", "theme-active"})


def _correct_prp_metadata(item: Dict[str, Any]) -> bool:
    metadata = item.get("surface_role_metadata")
    if not isinstance(metadata, dict):
        return False
    if str(metadata.get("function", "")).upper() != "PRP":
        return False
    metadata["semantic_relation"] = "purpose"
    template_id = str(item.get("surface_template_id", ""))
    corrected = template_id.replace(":beneficiary:", ":purpose:")
    item["surface_template_id"] = corrected
    metadata["template_id"] = corrected
    return True

def _is_mode(item: Mapping[str, Any]) -> bool:
    return (
        str(item.get("role", "")).casefold() == "mode"
        and item.get("reference_mode_projection") is True
    )

def _mode_realization(item: Mapping[str, Any]) -> role_templates.SurfaceRealization:
    terms = list(item.get("terms", []))
    if len(terms) != 2:
        raise primitives.AMRTripleValidationError(
            "mode item {} has invalid terms".format(item.get("id"))
        )
    source, mode = map(_surface, terms)
    resolution = role_templates._fallback_resolution(
        item.get("source_concept", ""), "mode", "reference-mode-projection"
    )
    return role_templates.SurfaceRealization(
        _surface("{} has {} mode".format(source, mode)),
        "dyad:mode:reference-active",
        "clause-force",
        resolution,
    )

def _apply_realization(
    item: Dict[str, Any],
    realization: role_templates.SurfaceRealization,
    *,
    audit_surface: str,
) -> None:
    signed = (
        role_templates._signed_surface(item, realization)
        if "polarity" in item
        else realization.surface
    )
    fallback = _surface(
        item.get("surface_fallback_text", item.get("base_surface_text", ""))
    )
    item.update(
        {
            "surface_fallback_text": fallback,
            "base_surface_text": realization.surface,
            "signed_surface_text": signed,
            "nli_surface_text": signed,
            "surface_text": signed,
            "surface_template_id": realization.template_id,
            "surface_role_metadata": realization.metadata(),
            "audit_surface_text": audit_surface,
            "linkable": bool(signed),
        }
    )

def _triple_audit_surface(atom: Mapping[str, Any]) -> str:
    signature = str(atom.get("join_signature", "")).upper()
    if "+" in signature:
        anchor_role, object_role = signature.split("+", 1)
    else:
        anchor_role, object_role = "ARG?", "ARG?"
    return "In a {} event, {} fills {} and {} fills {}.".format(
        _surface(atom.get("predicate")),
        _surface(atom.get("subject")),
        anchor_role,
        _surface(atom.get("object")),
        object_role,
    )

def _signed_audit_surface(item: Mapping[str, Any], positive: str) -> str:
    if item.get("polarity") != "negative":
        return positive
    clause = positive.rstrip(".")
    return "It is not the case that {}.".format(
        clause[0].lower() + clause[1:]
    )

def _adjunct_realization(
    atom: Mapping[str, Any],
) -> role_templates.SurfaceRealization:
    signature = str(atom.get("join_signature", "")).casefold()
    role = signature.split("+", 1)[1] if "+" in signature else ""
    prep = _ADJUNCT_PREPOSITIONS.get(role)
    fallback = _surface(
        atom.get(
            "surface_fallback_text",
            atom.get("base_surface_text"),
        )
    )
    resolution = role_templates._fallback_resolution(
        atom.get("predicate", ""), role, "amr-adjunct"
    )
    if prep is None:
        return role_templates.SurfaceRealization(
            fallback,
            "triple:{}:v1-fallback".format(signature),
            role or "unresolved",
            resolution,
            fallback_used=True,
        )
    surface = _surface(
        "{} {} {} {}".format(
            atom.get("subject", ""),
            atom.get("predicate", ""),
            prep,
            atom.get("object", ""),
        )
    )
    return role_templates.SurfaceRealization(
        surface,
        "triple:{}:adjunct".format(signature),
        role,
        resolution,
    )

def _general_anchor_realization(
    atom: Mapping[str, Any],
    dyad_by_id: Mapping[str, Mapping[str, Any]],
) -> role_templates.SurfaceRealization:
    signature = str(atom.get("join_signature", "")).casefold()
    object_role = signature.split("+", 1)[1]
    predicate_concept, outer_concept = role_templates._triple_context(
        atom, dyad_by_id
    )
    fallback = _surface(
        atom.get("surface_fallback_text", atom.get("base_surface_text", ""))
    )
    realization = role_templates._realize_triple(
        str(atom.get("subject", "")),
        str(atom.get("predicate", "")),
        str(atom.get("object", "")),
        "arg0+{}".format(object_role),
        predicate_concept=predicate_concept,
        fallback_surface=fallback,
        outer_concept=outer_concept,
    )
    if (
        realization.relation in _UNSAFE_WITH_RELATIONS
        and " with " in realization.surface
    ):
        resolution = role_templates._fallback_resolution(
            atom.get("predicate", ""), signature, "v1-triple-fallback"
        )
        return role_templates.SurfaceRealization(
            fallback,
            "triple:{}:v1-fallback:{}".format(
                signature, realization.relation
            ),
            realization.relation,
            resolution,
            fallback_used=True,
        )
    template_id = realization.template_id.replace(
        "triple:arg0+", "triple:{}+".format(signature.split("+", 1)[0]), 1
    )
    return role_templates.SurfaceRealization(
        realization.surface,
        template_id,
        realization.relation,
        realization.resolution,
        fallback_used=realization.fallback_used,
    )

def _resurface_record(record: Dict[str, Any]) -> Dict[str, int]:
    if _is_mode(record):
        realization = _mode_realization(record)
        _apply_realization(
            record,
            realization,
            audit_surface=(
                "The parser graph records {} as {}.".format(
                    _surface(record.get("terms", ["", ""])[0]),
                    _surface(record.get("terms", ["", ""])[1]),
                )
            ),
        )
        return {"mode": 1, "purpose": 0}
    role_templates._resurface_record(record)
    record["audit_surface_text"] = record["nli_surface_text"]
    return {"mode": 0, "purpose": int(_correct_prp_metadata(record))}

def _resurface_atom(
    atom: Dict[str, Any], dyad_by_id: Mapping[str, Mapping[str, Any]]
) -> Dict[str, int]:
    counts = {
        "adjunct": 0,
        "non_arg0": 0,
        "mode": 0,
        "unsafe_with": 0,
        "purpose": 0,
    }
    if _is_mode(atom):
        realization = _mode_realization(atom)
        _apply_realization(
            atom,
            realization,
            audit_surface=_signed_audit_surface(
                atom,
                "The parser graph records {} as {}.".format(
                    _surface(atom.get("terms", ["", ""])[0]),
                    _surface(atom.get("terms", ["", ""])[1]),
                ),
            ),
        )
        counts["mode"] = 1
        return counts

    kind = str(atom.get("kind", ""))
    signature = str(atom.get("join_signature", "")).casefold()
    anchor_role = signature.split("+", 1)[0] if "+" in signature else ""
    object_role = signature.split("+", 1)[1] if "+" in signature else ""
    if kind == "triple" and object_role in _ADJUNCT_PREPOSITIONS:
        realization = _adjunct_realization(atom)
        _apply_realization(
            atom,
            realization,
            audit_surface=_signed_audit_surface(
                atom, _triple_audit_surface(atom)
            ),
        )
        counts["adjunct"] = 1
    elif kind == "triple" and anchor_role and anchor_role != "arg0":
        realization = _general_anchor_realization(atom, dyad_by_id)
        _apply_realization(
            atom,
            realization,
            audit_surface=_signed_audit_surface(
                atom, _triple_audit_surface(atom)
            ),
        )
        counts["non_arg0"] = 1
        counts["unsafe_with"] = int(
            realization.fallback_used
            and realization.relation in _UNSAFE_WITH_RELATIONS
        )
    else:
        fallback = _surface(
            atom.get(
                "surface_fallback_text",
                atom.get("base_surface_text", ""),
            )
        )
        atom["surface_fallback_text"] = fallback
        if kind == "unary":
            # Unary carrier surfaces are already complete; the generic
            # identity realizer would erase their ``occurs/exists`` verb.
            atom.update(
                {
                    "surface_template_id": (
                        "unary:root-safe:occur-exist"
                    ),
                    "surface_role_metadata": {
                        "status": "unary",
                        "semantic_relation": "identity",
                        "fallback_used": False,
                        "template_id": (
                            "unary:root-safe:occur-exist"
                        ),
                    },
                    "audit_surface_text": atom["nli_surface_text"],
                    "linkable": bool(atom["nli_surface_text"]),
                }
            )
        else:
            role_templates._resurface_atom(atom, dyad_by_id)
            if kind == "triple":
                metadata = atom.get("surface_role_metadata", {})
                relation = str(
                    metadata.get("semantic_relation", "")
                ).casefold()
                if (
                    relation in _UNSAFE_WITH_RELATIONS
                    and " with "
                    in str(atom.get("base_surface_text", ""))
                ):
                    resolution = role_templates._fallback_resolution(
                        atom.get("predicate", ""),
                        signature,
                        "v1-triple-fallback",
                    )
                    realization = role_templates.SurfaceRealization(
                        fallback,
                        "triple:{}:v1-fallback:{}".format(
                            signature, relation
                        ),
                        relation,
                        resolution,
                        fallback_used=True,
                    )
                    signed = role_templates._signed_surface(
                        atom, realization
                    )
                    atom.update(
                        {
                            "base_surface_text": fallback,
                            "signed_surface_text": signed,
                            "nli_surface_text": signed,
                            "surface_text": signed,
                            "surface_template_id": (
                                realization.template_id
                            ),
                            "surface_role_metadata": (
                                realization.metadata()
                            ),
                            "linkable": bool(signed),
                        }
                    )
                    counts["unsafe_with"] = 1
                atom["audit_surface_text"] = _signed_audit_surface(
                    atom, _triple_audit_surface(atom)
                )
            else:
                atom["audit_surface_text"] = atom[
                    "nli_surface_text"
                ]
    counts["purpose"] = int(_correct_prp_metadata(atom))
    return counts
