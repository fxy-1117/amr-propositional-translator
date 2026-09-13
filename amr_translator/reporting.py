"""Ordinary reporting rules."""
import re

from .boundaries import metadata_scope_issue, pure_positive_root_context_issue


REPORTING = {"say-01": "say"}
# Reuse the existing content realization only for direct event predicates.
# In particular, an inner report, attitude, cause or modal is not a leaf event.
DIRECT_CONTENT = frozenset((
    "fly-01", "run-01", "walk-01", "read-01", "eat-01", "sleep-01",
    "swim-01", "sing-01", "leave-01", "arrive-01", "come-01", "go-01",
    "sit-01", "stand-01", "write-01", "buy-01", "sell-01", "carry-01",
    "hold-01", "watch-01", "drink-01", "work-01", "play-01", "use-01",
    "speak-01", "help-01", "need-01",
))


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


def reporting_guard(builder, boundary):
    """Extra restrictions before applying the existing complete-region rule."""
    gov, content = boundary["governor"], boundary["content"]
    if builder.concepts[content] not in DIRECT_CONTENT:
        return "reporting_content_predicate_not_registered"
    roles = {":arg0", ":arg1", ":location", ":time"}
    if builder.concepts[content] == "speak-01":
        roles.add(":arg2")  # Addressee, rendered by the existing role template.
    if any(e["role"].lower() not in roles
           for e in builder.outgoing.get(content, [])):
        return "reporting_content_role_not_registered"
    # The complete predicate, rather than a partial role phrase, must be read
    # as the clause following 'that'. The shared scope renderer checks voice.
    if not any(e["role"].lower() == ":arg0"
               for e in builder.outgoing.get(content, [])):
        return "reporting_content_subject_not_registered"
    issue = pure_positive_root_context_issue(builder, gov)
    if issue:
        return "reporting_" + issue
    # Metadata can carry its own negation/mode. It must not silently enter a
    # positive term while only the content event's minus is verbalized.
    issue = metadata_scope_issue(builder, [gov], allowed_negative_nodes=(gov, content),
                                 metadata_only=False)
    if issue:
        return "reporting_" + issue
    return None
