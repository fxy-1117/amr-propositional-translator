"""Ordinary reporting rules."""
import re

from .scope_boundaries import REPORTING


def reporting_boundaries(builder):
    result = []
    for node, concept in builder.concepts.items():
        if concept not in REPORTING:
            continue
        for edge in builder.outgoing.get(node, []):
            if edge["role"].lower() != ":arg1":
                continue
            content = edge["target"]
            concept = builder.concepts[content]
            if re.search(r"-\d\d$", concept) or concept in ("and", "or"):
                result.append({"governor": node, "concept": builder.concepts[node],
                               "content": content, "edge": edge["id"],
                               "status": "unresolved_reporting_content"})
    return result
