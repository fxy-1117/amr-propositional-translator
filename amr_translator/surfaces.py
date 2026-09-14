"""Deterministic atom verbalizations."""
from copy import deepcopy
import re

from . import verbalization

POSSESSIVE_PRONOUNS = {"i": "my", "you": "your", "he": "his", "she": "her",
                      "it": "its", "we": "our", "they": "their"}
OBJECT_PRONOUNS = {"i": "me", "he": "him", "she": "her", "we": "us", "they": "them"}
PLURAL_ONLY_NOUNS = frozenset(("clothes", "people", "shorts", "pants", "trousers",
                               "jeans", "glasses", "scissors"))
# Roleset-specific states, not a rule that turns every ARG1 into an adjective.
ADJECTIVE_STATES = {
    "important-01": "important", "easy-05": "easy", "hard-02": "hard",
    "long-03": "long", "fast-02": "fast", "funny-02": "funny", "well-09": "well",
    "black-04": "black", "white-03": "white", "red-02": "red", "green-02": "green",
    "yellow-02": "yellow", "blue-02": "blue", "brown-01": "brown", "pink-04": "pink",
    "gray-02": "gray", "small-02": "small", "large-02": "large",
    "young-01": "young", "old-02": "old",
    "quick-02": "quick", "intense-02": "intense", "wet-01": "wet",
}
# Kept separate: registering these surfaces must not expand the older merge
# registry or the set of accepted nonfactive scopes.
LOCAL_ADJECTIVE_STATES = {"empty-02": "empty"}


def adjective_state(builder, node):
    concept = builder.concepts.get(node, "")
    if concept in LOCAL_ADJECTIVE_STATES:
        edges = builder.outgoing.get(node, [])
        arguments = [e for e in edges if e['role'].casefold() == ':arg1']
        if (len(arguments) != 1 or any(e['role'].casefold() not in (':arg1', ':degree')
                                       for e in edges)):
            return None
        return LOCAL_ADJECTIVE_STATES[concept]
    return ADJECTIVE_STATES.get(concept)


def object_pronoun(text):
    return OBJECT_PRONOUNS.get(text.casefold(), text)


def compound_modifier_surface(builder, modifier, head):
    """Realize a registered NP modifier without renaming its AMR concept."""
    if (builder.concepts.get(modifier) == 'swim-01' and builder.concepts.get(head) == 'pool'
            and not builder.outgoing.get(modifier) and not builder.attrs_by_source.get(modifier)
            and any(e['role'].casefold() == ':mod' and e['target'] == modifier
                    for e in builder.outgoing.get(head, []))):
        return 'swimming'
    return builder.node_surface(modifier)


def pronoun_key(builder, node, text=None):
    """Only a bare pronoun concept, never an entity named like a pronoun."""
    concept = builder.concepts.get(node, "").casefold()
    if concept not in POSSESSIVE_PRONOUNS or any(
            edge['role'].casefold() == ':name' for edge in builder.outgoing.get(node, [])):
        return None
    surface = builder.node_surface(node) if text is None else text
    return concept if surface.casefold() == concept else None


def possessive(builder, node, head, *, owner_text=None):
    owner = builder.node_surface(node) if owner_text is None else owner_text
    key = pronoun_key(builder, node, owner)
    if key:
        return POSSESSIVE_PRONOUNS[key] + " " + head
    return owner + ("' " if owner.endswith("s") else "'s ") + head


def copula(builder, subject):
    text = builder.node_surface(subject).casefold()
    key = pronoun_key(builder, subject)
    if key == "i":
        return "am"
    quant = builder._metadata_values(subject).get("quant", [])
    if not builder.concepts[subject].endswith("-quantity") and (
            key in ("you", "we", "they") or text in PLURAL_ONLY_NOUNS
            or (quant and quant[0] != "1")):
        return "are"
    return "is"


def corrected_dyad(builder, record):
    source, target = record.get("source_node"), record.get("target_node")
    role = record.get("role", "").lower().lstrip(":")
    terms = record.get("terms", [])
    if len(terms) != 2:
        return None
    if role == "poss" and pronoun_key(builder, target, terms[1]):
        return possessive(builder, target, terms[0], owner_text=terms[1]), "possessive_pronoun_surface"
    if role == 'part' and source in builder.concepts and target in builder.concepts:
        owner = terms[0]
        if (pronoun_key(builder, source, owner) in OBJECT_PRONOUNS
                and owner.casefold() == builder.node_surface(source).casefold()):
            owner = object_pronoun(owner)
        agreement = copula(builder, target)
        quantities = [e for e in builder.outgoing.get(target, []) if e['role'].casefold() == ':quant']
        if len(quantities) == 1:
            quantity = quantities[0]['target']
            edges = builder.outgoing.get(quantity, [])
            attrs = builder.attrs_by_source.get(quantity, [])
            # A measured singular such as "1 mile of side" is not a plural
            # merely because its surface quantity differs from the string 1.
            # Keep this exception local to part, with no approximate/nested
            # quantity or polarity interpretation.
            if (builder.concepts.get(quantity, '').endswith('-quantity')
                    and len(edges) == 1 and edges[0]['role'].casefold() == ':unit'
                    and len(attrs) == 1 and attrs[0]['role'].casefold() == ':quant'
                    and not attrs[0].get('target_is_node') and str(attrs[0]['target']) == '1'):
                agreement = 'is'
        return ('{} {} part of {}'.format(terms[1], agreement, owner),
                'part_pronoun_agreement_surface')
    concept = builder.concepts.get(source, "")
    state = adjective_state(builder, source)
    if role == "arg1" and state and target in builder.concepts:
        return ("{} {} {}".format(terms[1], copula(builder, target), state),
                "adjectival_state_surface")
    if (role in ("arg1", "domain") and target in builder.concepts
            and builder._concept_canonical(target) not in ("and", "or", "multi sentence")):
        old = record.get("base_surface_text", "")
        prefix = terms[1] + " is "
        if old.startswith(prefix) and copula(builder, target) != "is":
            return terms[1] + " " + copula(builder, target) + " " + old[len(prefix):], "copular_agreement_surface"
    return None


def _set_surface(record, surface, rule):
    old = record["base_surface_text"]
    for key in ("base_surface_text", "signed_surface_text", "nli_surface_text",
                "surface_text", "audit_surface_text"):
        if key in record:
            if old in record[key]:
                record[key] = record[key].replace(old, surface)
            elif (record.get("polarity") == "negative"
                  and record[key] == re.sub(r"\b(is|are|am)\b", r"\1 not", old, count=1)):
                record[key] = re.sub(r"\b(is|are|am)\b", r"\1 not", surface, count=1)
    record["surface_template_id"] = "ordinary:" + rule
    metadata = dict(record.get("surface_role_metadata", {}))
    metadata.update(template_id="ordinary:" + rule, surface_repair=rule)
    record["surface_role_metadata"] = metadata


def repair_surfaces(builder, internal):
    records = {d["id"]: d for d in internal["dyadic_records"]}
    for d in records.values():
        fix = corrected_dyad(builder, d)
        if fix and d["base_surface_text"] != fix[0]:
            _set_surface(d, *fix)
            builder.events.add((fix[1], d["source_node"]))
    for a in internal["atoms"]:
        components = [records[k] for k in a.get("component_dyad_ids", []) if k in records]
        if a["kind"] == "dyadic" and len(components) == 1:
            # Records may already have been repaired; inspect the atom's own
            # old surface rather than incorrectly treating it as repaired too.
            d = dict(components[0], terms=a["terms"], base_surface_text=a['base_surface_text'])
            fix = corrected_dyad(builder, d)
            if fix and a["base_surface_text"] != fix[0]:
                _set_surface(a, *fix)
                builder.events.add((fix[1], d["source_node"]))
        # A base triple may already contain a possessive component. Only replace
        # an explicitly recorded pronoun possessor, never text in a name.
        if a["kind"] == "triple":
            for d in components:
                if d["role"].lower() != "poss" or len(d.get("terms", [])) != 2:
                    continue
                owner = d["terms"][1]
                if pronoun_key(builder, d.get('target_node'), owner):
                    old = a["base_surface_text"]
                    new = re.sub(r"(?<!\w)" + re.escape(owner) + "'s(?= )",
                                 POSSESSIVE_PRONOUNS[owner.casefold()], old)
                    if new != old:
                        _set_surface(a, new, "possessive_pronoun_surface")
                        builder.events.add(("possessive_pronoun_surface", d["source_node"]))


def render_dyad_with_term(builder, row, index, text):
    """Re-use the exact dyad role realizer after modifying one endpoint NP."""
    record = deepcopy(row["record"])
    record["terms"][index] = text
    verbalization._resurface_record(record)
    fix = corrected_dyad(builder, record)
    surface = fix[0] if fix else record["base_surface_text"]
    preserved_text = text
    if (index == 0 and record.get('role', '').casefold().lstrip(':') == 'part'
            and pronoun_key(builder, record.get('source_node'), text) in OBJECT_PRONOUNS
            and text.casefold() == builder.node_surface(record['source_node']).casefold()):
        preserved_text = object_pronoun(text)
    # Unknown role fallbacks may ignore the new term; do not approve that merge.
    return surface if surface.count(preserved_text) == 1 else None
