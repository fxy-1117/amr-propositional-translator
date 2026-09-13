"""Registered two-edge, three-node atom merges."""
from collections import Counter, defaultdict
from copy import deepcopy
from itertools import combinations
import re

from . import primitives
from .boundaries import metadata_scope_issue, merge_scope_issue
from .surfaces import (ADJECTIVE_STATES, POSSESSIVE_PRONOUNS, copula,
                       possessive, render_dyad_with_term)

TEMPLATES = (
    {"id": "attribute_degree", "roles": (":arg1", ":degree"),
     "surface": "{subject} is {degree} {property}", "priority": 0},
    {"id": "domain_modifier", "roles": (":domain", ":mod"),
     "surface": "{subject} is {modifier} {type}", "priority": 1},
    {"id": "possessive_modifier", "roles": (":mod", ":poss"),
     "surface": "{possessor}'s {modifier} {entity}", "priority": 2},
    {"id": "entity_two_modifiers", "roles": (":mod", ":mod"),
     "surface": "{modifier1} {modifier2} {entity}", "priority": 3},
    {"id": "event_simple_modifier", "roles": (":arg0", ":mod"),
     "surface": "{participant} {event} {modifier}", "priority": 4},
    {"id": "event_simple_modifier", "roles": (":arg1", ":mod"),
     "surface": "{participant} {event} {modifier}", "priority": 4},
    {"id": "spatial_path", "roles": (":location", ":op1"),
     "shape": "directed_path", "surface": "{entity} {spatial_relation} {landmark}", "priority": 5},
    {"id": "domain_degree", "roles": (":degree", ":domain"),
     "surface": "{subject} is {degree} {property}", "priority": 6},
    {"id": "participant_leaf_modifier", "roles": (":arg0", ":mod"),
     "shape": "directed_path", "priority": 7},
    {"id": "participant_leaf_modifier", "roles": (":arg1", ":mod"),
     "shape": "directed_path", "priority": 7},
    {"id": "participant_possessor", "roles": (":arg1", ":poss"),
     "shape": "directed_path", "priority": 8},
    {"id": "shared_target_properties", "roles": (":arg1", ":arg1"),
     "shape": "shared_target", "priority": 9},
    {"id": "shared_target_properties", "roles": (":arg1", ":domain"),
     "shape": "shared_target", "priority": 9},
    {"id": "shared_target_properties", "roles": (":domain", ":domain"),
     "shape": "shared_target", "priority": 9},
    {"id": "located_entity_modifier", "roles": (":location", ":mod"), "priority": 10},
    {"id": "part_leaf_modifier", "roles": (":part", ":mod"),
     "shape": "directed_path", "priority": 11},
    {"id": "landmark_leaf_modifier", "roles": (":location", ":mod"),
     "shape": "directed_path", "priority": 12},
    {"id": "located_entity_possessor", "roles": (":location", ":poss"),
     "shape": "shared_source", "priority": 13},
    {"id": "physical_property_relation", "roles": (":arg0", ":arg1"),
     "shape": "shared_target", "priority": 14},
    {"id": "physical_property_relation", "roles": (":arg0", ":domain"),
     "shape": "shared_target", "priority": 14},
    {"id": "physical_property_relation", "roles": (":arg1", ":arg1"),
     "shape": "shared_target", "priority": 14},
    {"id": "physical_property_relation", "roles": (":arg1", ":domain"),
     "shape": "shared_target", "priority": 14},
    {"id": "physical_property_relation", "roles": (":domain", ":domain"),
     "shape": "shared_target", "priority": 14},
    {"id": "spatial_landmark_modifier", "roles": (":op1", ":mod"),
     "shape": "directed_path", "priority": 15},
    {"id": "spatial_landmark_property", "roles": (":arg1", ":op1"),
     "shape": "shared_target", "priority": 15},
    {"id": "spatial_landmark_property", "roles": (":domain", ":op1"),
     "shape": "shared_target", "priority": 15},
    {"id": "modified_whole_part", "roles": (":mod", ":part"),
     "shape": "shared_source", "priority": 16},
    {"id": "modified_possessor", "roles": (":poss", ":mod"),
     "shape": "directed_path", "priority": 17},
)
SPATIAL_CARRIERS = frozenset(
    "outside inside behind above below beside near under over across around next-to in-front-of "
    "along by beyond beneath through throughout among amongst".split())
# Deliberately not a blanket :mod rule. Joint participation is retained only
# on registered activity predicates; focusing and modal modifiers are excluded.
JOINT_ACTIVITIES = frozenset(("chat-01", "sit-01", "stand-01", "walk-01", "run-01",
                            "play-01", "sing-01", "dance-01", "work-01", "travel-01",
                            "eat-01", "talk-01", "live-01", "gather-01", "meet-03",
                            "party-01", "huddle-01", "converse-01", "march-01", "exercise-02", "skate-01"))
# Order within a category is lexical, so results do not depend on AMR edge order.
MODIFIER_GROUPS = (
    ("size", "small little big large huge tiny tall short long wide narrow giant massive thin thick"),
    ("age", "young old new ancient modern elderly adult teenage middle-aged"),
    ("shape", "round square rectangular circular triangular flat"),
    ("color", "black white red blue green yellow brown orange purple pink gray grey golden silver beige tan blond blonde striped spotted"),
    ("material", "wooden metal metallic plastic leather wool cotton stone brick glass jean denim cloth silk rubber"),
    ("sex", "male female"),
    ("classifying", "computer ski-01 heritage wedding sports sport office school kitchen garden dining "
                    "billboard camouflage polka forest dirt hockey soccer"),
)
MODIFIER_CLASS = {word: (rank, group) for rank, (group, words) in enumerate(MODIFIER_GROUPS)
                  for word in words.split()}
EXTRA_SIMPLE_MODIFIERS = frozenset("male female elderly adult teenage middle-aged".split())
BLOCKED_MODIFIERS = frozenset("fake former alleged possible potential only even just almost merely supposedly so-called".split())
DEGREES = frozenset("very really extremely quite slightly somewhat rather".split())
# Local degree realization only: do not rename arbitrary 'real'/'extreme'
# atoms or change a verbalization beneath an unmerged negative scope.
DEGREE_SURFACES = {word: word for word in DEGREES}
DEGREE_SURFACES.update({"extreme": "extremely", "real": "really"})
DEGREES = frozenset(DEGREE_SURFACES)
# These compounds are registered by head, not a general license to merge any
# noun modifier into any participant (e.g. fake gun is not an ordinary gun).
NOMINAL_COMPOUNDS = frozenset((
    ('billboard', 'sign'), ('camouflage', 'uniform'), ('polka', 'dot'),
    ('forest', 'path'), ('forest', 'area'), ('dirt', 'road'), ('dirt', 'path'),
    ('hockey', 'game'), ('soccer', 'game'),
))
PROPERTIES = frozenset((
    "good-02", "good", "bad-07", "bad", "popular-02", "popular", "lax",
    "happy-01", "happy", "sad-02", "sad", "tall", "short", "long", "big",
    "large", "small", "young", "old", "new", "fast", "slow", "hot", "cold",
    "warm", "loud", "quiet", "bright", "dark", "difficult", "easy", "expensive",
    "cheap", "important", "strong", "weak", "high", "low", "close", "wide", "deep",
    "lax-01", "nice-01", "high-02", "strong-02", "beautiful-02", "rough-04", "muddy-01",
))
PROPERTIES = PROPERTIES | frozenset(ADJECTIVE_STATES)
COUNTABLE_TYPES = frozenset("asset car desk house hotel girl creature boy man woman book city building company person amount".split())
# These properties are admitted only in the new positive-AND macros. This is
# not a blanket '-NN' -> adjective repair for all dyads or negative scopes.
PHYSICAL_PROPERTY_SURFACES = {
    concept: ADJECTIVE_STATES[concept] for concept in (
        "black-04", "white-03", "red-02", "green-02", "yellow-02", "blue-02",
        "brown-01", "pink-04", "gray-02", "small-02", "large-02", "young-01", "old-02")
}
PHYSICAL_PROPERTY_SURFACES.update({word: word for word in (
    "tall short big large small young old new black white red blue green yellow brown pink gray grey".split())})
PHYSICAL_PROPERTY_SURFACES["tan-01"] = "tan"
# Exact predicate/participant roles, rather than treating every roleset as an
# activity (e.g. short-07 and hard-04 are not licensed as generic events).
PROPERTY_EVENT_ROLES = frozenset(
    [(p, ":arg0") for p in (
        "race-02 run-01 run-02 walk-01 play-01 look-01 sleep-01 sit-01 stand-01 "
        "jump-01 jump-03 eat-01 dance-01 work-01 swim-01 fly-01 move-01").split()]
    + [(p, ":arg1") for p in (
        "carry-01 hold-01 wear-01 ride-01 drive-01 paint-01 kick-01 throw-01 "
        "pull-01 push-01 chase-01 eat-01 see-01 watch-01 fix-01 repair-01").split()])
RESTRICTIVE_MODIFIERS = BLOCKED_MODIFIERS | frozenset(
    "all each every both either neither this that these those another same other entire whole".split())
REMAINING_RULES = frozenset(("physical_property_relation", "spatial_landmark_modifier",
                           "spatial_landmark_property", "modified_whole_part", "modified_possessor"))

# New local families are deliberately scheduled after every existing rule.
# The lexical additions below do not mutate the original modifier registry.
LOCAL_WORD_HEADS = {
    "rocky": frozenset("ground terrain road path area mountain hill beach shore shoreline land surface".split()),
    "colorful": frozenset("shirt blouse dress clothes clothing costume umbrella painting bird flag toy building flower ball".split()),
    "topless": frozenset("man woman person boy girl people".split()),
}
LOCAL_COMPOUNDS = frozenset((("park", "bench"), ("sound", "equipment"),
    ("swim-01", "pool"), ("coffee", "kiosk"), ("sushi", "restaurant")))
LOCAL_MATERIALS = frozenset("wood rock stone marble metal steel iron brick concrete plastic glass".split())
LOCAL_PROPERTIES = dict(PHYSICAL_PROPERTY_SURFACES)
LOCAL_PROPERTIES.update({s: s for s in "long wide narrow thin thick deep cold hot warm dry wet".split()})
LOCAL_PROPERTIES.update({"wet-01": "wet", "long-03": "long", "rough-04": "rough", "muddy-01": "muddy"})
LOCAL_DOMAIN_PROPERTIES = (PROPERTIES | frozenset(LOCAL_PROPERTIES) | COUNTABLE_TYPES
    | frozenset("accurate intact female male alive dead ready empty full".split()))
_LOCAL_LEXICAL_BASES = frozenset(("domain_modifier", "possessive_modifier", "entity_two_modifiers",
    "participant_leaf_modifier", "located_entity_modifier", "part_leaf_modifier",
    "landmark_leaf_modifier", "spatial_landmark_modifier", "modified_whole_part", "modified_possessor"))
TEMPLATES += (
    {"id": "local_domain_possessor", "roles": (":domain", ":poss"), "shape": "directed_path", "priority": 20},
    *tuple({"id": "local_property_location", "roles": (role, ":location"), "shape": shape, "priority": 21}
           for role in (":arg1", ":domain") for shape in ("directed_path", "shared_target")),
    *tuple({"id": "local_property_part", "roles": (role, ":part"), "shape": shape, "priority": 22}
           for role in (":arg1", ":domain") for shape in ("directed_path", "shared_target")),
    {"id": "local_spatial_material", "roles": (":op1", ":consist-of"), "shape": "directed_path", "priority": 23},
    {"id": "local_material_modifier", "roles": (":consist-of", ":mod"), "priority": 24},
    {"id": "local_brand_new", "roles": (":arg1", ":degree"), "priority": 25},
    {"id": "local_brand_new", "roles": (":degree", ":domain"), "priority": 25},
) + tuple(dict(t, id="local_lexical_" + t["id"], base_rule=t["id"], priority=40 + t["priority"])
          for t in TEMPLATES if t["id"] in _LOCAL_LEXICAL_BASES)


def _local_nominal(builder, node, *, pronoun=False):
    if not _nominal(builder, node):
        return False
    if not pronoun and builder.concepts[node].casefold() in POSSESSIVE_PRONOUNS:
        return False
    blocked = RESTRICTIVE_MODIFIERS | frozenset("most some any no much many few several various".split())
    for edge in builder.outgoing.get(node, []):
        role = edge["role"].lower()
        if role.startswith(":arg") or role in (":quant", ":ord", ":ordinal", ":degree", ":mode", ":polarity", ":condition"):
            return False
        if role == ":mod" and builder.concepts[edge["target"]].casefold() in blocked:
            return False
    for attr in builder.attrs_by_source.get(node, []):
        role = attr["role"].lower()
        if role != ":wiki" and not (role == ":quant" and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", str(attr["target"]))):
            return False
    return True


def _local_leaf(builder, node):
    return (not builder.outgoing.get(node) and not builder.attrs_by_source.get(node)
            and len(builder.incoming.get(node, [])) == 1)


def _local_tree_scope_issue(builder, centre, pair, boundaries):
    """Reject ambiguous scope ownership through inverse/shared references.

    This supplements, rather than changes, the common normalized-edge guard.
    The endpoints exclusive to each relation must have the same raw content
    and disjunction context; the shared entity cannot establish ownership.
    """
    graph_guard = getattr(builder, 'local_merge_scope_issue', None)
    if graph_guard is not None:
        return graph_guard([row['record'] for row in pair], boundaries)
    endpoints = [next(n for n in (r["record"]["source_node"], r["target"]) if n != centre)
                 for r in pair]
    paths = []
    for endpoint in endpoints:
        path, seen = [], set()
        while endpoint not in seen:
            seen.add(endpoint)
            path.append(endpoint)
            if endpoint not in builder.tree_parents:
                break
            endpoint = builder.tree_parents[endpoint]
        paths.append(path)
    for boundary in boundaries:
        if (boundary["content"] in paths[0]) != (boundary["content"] in paths[1]):
            return "local_raw_tree_cross_content_scope"
    contexts = []
    for path in paths:
        context = set()
        for child, parent in zip(path, path[1:]):
            if builder.concepts.get(parent) == "or":
                context.add(("or", parent, child))
            if any(e["role"].lower() == ":condition" and e["target"] == child
                   for e in builder.outgoing.get(parent, [])):
                context.add(("condition", parent, child))
        contexts.append(context)
    return "local_raw_tree_cross_boolean_scope" if contexts[0] != contexts[1] else None


def _local_property(builder, row, *, domain=False):
    node = row["record"]["source_node"]
    return (row["role"] in ((":domain",) if domain else (":arg1", ":domain"))
            and builder.concepts[node] in (LOCAL_DOMAIN_PROPERTIES if domain else LOCAL_PROPERTIES)
            and len(builder.outgoing.get(node, [])) == 1 and not builder.attrs_by_source.get(node))


def _local_modifier_class(builder, node, head):
    word, noun = builder.concepts[node].casefold(), builder.concepts[head].casefold()
    if word in LOCAL_WORD_HEADS and noun in LOCAL_WORD_HEADS[word]:
        return (3 if word == "colorful" else 4, "physical")
    if (word, noun) in LOCAL_COMPOUNDS:
        return (7, "classifying")
    return MODIFIER_CLASS.get(word)


def _local_new_modifier(builder, node, head):
    word, noun = builder.concepts[node].casefold(), builder.concepts[head].casefold()
    return ((word in LOCAL_WORD_HEADS and noun in LOCAL_WORD_HEADS[word]) or (word, noun) in LOCAL_COMPOUNDS)


def _local_modified_head(builder, head, modifiers):
    from .surfaces import compound_modifier_surface
    ordered = sorted(modifiers, key=lambda n: (_local_modifier_class(builder, n, head)[0],
        builder.node_surface(n), n))
    words = [compound_modifier_surface(builder, n, head) for n in ordered]
    same_color = (len(ordered) == 2 and all(_local_modifier_class(builder, n, head)[0] == 3 for n in ordered))
    return _prefix_head(builder, head, (" and " if same_color else " ").join(words))


def _local_lexical_guard(builder, centre, pair, template):
    base = template["base_rule"]
    mods = [r["target"] for r in pair if r["role"] == ":mod"]
    if (not mods or not _local_nominal(builder, centre)
            or not any(_local_new_modifier(builder, n, centre) for n in mods)):
        return "local_lexical_head_not_registered"
    for node in mods:
        kind = _local_modifier_class(builder, node, centre)
        if not kind or not _local_leaf(builder, node):
            return "local_modifier_not_registered_leaf"
        compound = (builder.concepts[node].casefold(), builder.concepts[centre].casefold())
        if kind[1] == "classifying" and compound not in (LOCAL_COMPOUNDS | NOMINAL_COMPOUNDS):
            return "local_compound_head_not_registered"
    if len(mods) == 2:
        if builder.concepts[mods[0]] == builder.concepts[mods[1]]:
            return "repeated_modifier_not_compacted"
        if sum(_local_modifier_class(builder, n, centre)[1] == "classifying" for n in mods) > 1:
            return "multiple_classifying_modifiers"
    if base == "entity_two_modifiers":
        return None
    relation = next(r for r in pair if r["role"] != ":mod")
    source, target = relation["record"]["source_node"], relation["target"]
    if base == "domain_modifier":
        return None if _local_nominal(builder, target, pronoun=True) else "local_domain_not_nominal"
    if base == "possessive_modifier":
        return None if _local_nominal(builder, target, pronoun=True) else "local_owner_not_nominal"
    if base == "participant_leaf_modifier":
        from .surfaces import adjective_state
        safe_role = (builder.concepts[source], relation["role"]) in PROPERTY_EVENT_ROLES
        state = relation["role"] == ":arg1" and adjective_state(builder, source) is not None
        if not (safe_role or state):
            return "local_participant_role_not_registered"
        if builder.attrs_by_source.get(source) or any(
                e["role"].lower() == ":mod" and builder.concepts[e["target"]].casefold() in RESTRICTIVE_MODIFIERS
                for e in builder.outgoing.get(source, [])):
            return "local_participant_has_scope_metadata"
    elif base in ("located_entity_modifier", "modified_whole_part"):
        if not _local_nominal(builder, target, pronoun=True):
            return "local_relation_target_not_nominal"
    elif base in ("landmark_leaf_modifier", "part_leaf_modifier", "modified_possessor"):
        if not _local_nominal(builder, source, pronoun=True):
            return "local_relation_source_not_nominal"
    elif base == "spatial_landmark_modifier" and not _simple_carrier(builder, relation):
        return "spatial_carrier_not_exclusive_simple"
    return None


def _local_guard(builder, centre, pair, template):
    rule = template["id"]
    if rule.startswith("local_lexical_"):
        return _local_lexical_guard(builder, centre, pair, template)
    if rule == "local_brand_new":
        subject = next(r for r in pair if r["role"] in (":arg1", ":domain"))
        degree = next(r for r in pair if r["role"] == ":degree")
        if (builder.concepts[centre] not in ("new", "new-01")
                or builder.concepts[degree["target"]] != "brand" or not _local_leaf(builder, degree["target"])
                or len(builder.outgoing.get(centre, [])) != 2 or builder.attrs_by_source.get(centre)
                or not _local_nominal(builder, subject["target"], pronoun=True)):
            return "brand_degree_requires_simple_new_property"
        return None
    if not _local_nominal(builder, centre):
        return "not_simple_local_nominal"
    if rule == "local_domain_possessor":
        prop, owner = pair
        if not _local_property(builder, prop, domain=True) or not _local_nominal(builder, owner["target"], pronoun=True):
            return "domain_possessor_not_simple_registered_predicate"
    elif rule in ("local_property_location", "local_property_part"):
        prop, relation = pair
        other = relation["target"] if relation["record"]["source_node"] == centre else relation["record"]["source_node"]
        if not _local_property(builder, prop) or not _local_nominal(builder, other, pronoun=True):
            return "property_relation_not_simple_registered_nodes"
    elif rule == "local_spatial_material":
        carrier, material = pair
        if (not _simple_carrier(builder, carrier) or builder.concepts[material["target"]] not in LOCAL_MATERIALS
                or not _local_leaf(builder, material["target"])):
            return "material_not_literal_leaf_or_carrier_not_simple"
    elif rule == "local_material_modifier":
        material, modifier = pair
        if (builder.concepts[material["target"]] not in LOCAL_MATERIALS or not _local_leaf(builder, material["target"])
                or not _physical_leaf(builder, modifier["target"])):
            return "material_modifier_not_literal_physical_leaves"
    return None


def _local_surface(builder, centre, pair, template):
    rule = template["id"]
    if rule == "local_brand_new":
        subject = next(r["target"] for r in pair if r["role"] in (":arg1", ":domain"))
        return "{} {} brand new".format(builder.node_surface(subject), _copula(builder, subject))
    if rule == "local_domain_possessor":
        prop, owner = pair
        head = possessive(builder, owner["target"], builder.node_surface(centre))
        predicate = prop["record"]["source_node"]
        if builder.concepts[predicate] in COUNTABLE_TYPES:
            word = builder.node_surface(predicate)
            return "{} {} {} {}".format(head, _copula(builder, centre),
                "an" if word[:1].lower() in "aeiou" else "a", word)
        return render_dyad_with_term(builder, prop, 1, head)
    if rule in ("local_property_location", "local_property_part"):
        prop, relation = pair
        head = _prefix_head(builder, centre, LOCAL_PROPERTIES[builder.concepts[prop["record"]["source_node"]]])
        index = 0 if relation["record"]["source_node"] == centre else 1
        if rule == "local_property_location":
            return _local_location_surface(builder, relation, index, head)
        return render_dyad_with_term(builder, relation, index, head)
    if rule == "local_spatial_material":
        carrier, material = pair
        head = "{} made of {}".format(builder.node_surface(centre), builder.node_surface(material["target"]))
        return render_dyad_with_term(builder, carrier, 1, head)
    if rule == "local_material_modifier":
        material, modifier = pair
        return "{} {} made of {}".format(_modified_head(builder, centre, [modifier["target"]]),
            _copula(builder, centre), builder.node_surface(material["target"]))
    base = template["base_rule"]
    mods = [r["target"] for r in pair if r["role"] == ":mod"]
    head = _local_modified_head(builder, centre, mods)
    if base == "entity_two_modifiers":
        return head
    relation = next(r for r in pair if r["role"] != ":mod")
    index = 0 if relation["record"]["source_node"] == centre else 1
    if base in ("located_entity_modifier", "landmark_leaf_modifier"):
        return _local_location_surface(builder, relation, index, head)
    if (base == "domain_modifier" and builder.concepts[centre] in COUNTABLE_TYPES
            and not builder._metadata_values(centre).get("quant")):
        head = ("an " if head[:1].lower() in "aeiou" else "a ") + head
    return render_dyad_with_term(builder, relation, index, head)


def _local_location_surface(builder, relation, index, head):
    from .surfaces import object_pronoun
    source, target = relation["record"]["source_node"], relation["target"]
    subject = head if index == 0 else builder.node_surface(source)
    place = head if index == 1 else builder.node_surface(target)
    if (builder.concepts[target].casefold() in POSSESSIVE_PRONOUNS
            and place.casefold() == builder.node_surface(target).casefold()):
        place = object_pronoun(place)
    prep = "" if place in ("outdoors", "indoors", "here", "there", "outside", "inside") else "at "
    return "{} {} {}{}".format(subject, _copula(builder, source), prep, place)


def atom_locations(ast):
    locations = defaultdict(list)
    def walk(n, path=(), parent=None, under_negation=False):
        op = n["op"]
        if op == "atom":
            locations[n["id"]].append((parent, under_negation))
        elif op in ("and", "or"):
            for i, c in enumerate(n["args"]):
                walk(c, path + (i,), path if op == "and" else None, under_negation)
        elif op == "not":
            walk(n["arg"], path + ("not",), None, True)
        elif op == "implies":
            walk(n["antecedent"], path + ("if",), None, under_negation)
            walk(n["consequent"], path + ("then",), None, under_negation)
    walk(ast)
    return locations


def _ordinary_dyads(builder, internal, frame):
    records = {d["id"]: d for d in internal["dyadic_records"]}
    active = {a["id"] for a in frame["atoms"]}
    grouped = defaultdict(list)
    for a in internal["atoms"]:
        parts = a.get("component_dyad_ids", [])
        if a["id"] not in active or a["kind"] != "dyadic" or len(parts) != 1:
            continue
        # Projected carriers can have temporary dyad identifiers produced by
        # the base formula probes. They are out of scope before any record lookup.
        if (a.get("coordination_root_node") or a.get("participant_polarity_projected")
                or a.get("reference_mode_projection")):
            continue
        d = records[parts[0]]
        if (d.get("projected") or d.get("reference_mode_projection")
                or d.get("record_type") != "edge" or a.get("coordination_root_node")
                or a.get("participant_polarity_projected")):
            continue
        s, t = d.get("source_node"), d.get("target_node")
        if s == t or s not in builder.concepts or t not in builder.concepts:
            continue
        grouped[s].append({"atom": a, "record": d, "target": t,
                           "role": ":" + d["role"].lower().lstrip(":")})
    return grouped


def _simple_modifier(builder, node):
    concept = builder.concepts[node].casefold()
    if concept not in MODIFIER_CLASS:
        return False
    # Leaf modifiers only. ARG-bearing events or modifier-internal clauses are
    # not silently absorbed into a surface containing only their predicate.
    if builder.outgoing.get(node) or builder.attrs_by_source.get(node):
        return False
    return len(builder.incoming.get(node, [])) == 1


def _guard(builder, centre, left, right, template, locations, boundaries):
    nodes = {centre, left["target"], right["target"]}
    if len(nodes) != 3:
        return "not_three_distinct_nodes"
    la, lb = locations[left["atom"]["id"]], locations[right["atom"]["id"]]
    if not (len(la) == len(lb) == 1 and la == lb and la[0][0] is not None and not la[0][1]):
        return "not_unique_same_positive_conjunction"
    for n in nodes:
        if (builder._raw_has_negative_polarity(n) or builder._condition_edges(n)
                or builder._is_structural_connective(n) or n in builder.conditions):
            return "scope_boundary"
    if any(b["governor"] in nodes and b["content"] in nodes for b in boundaries):
        return "nonfactive_boundary"
    centre_concept = builder.concepts[centre].casefold()
    roles = {x["role"]: x["target"] for x in (left, right)}
    if template["id"] == "event_simple_modifier":
        if centre_concept not in JOINT_ACTIVITIES:
            return "joint_activity_not_registered"
        if any(e['role'].lower() == ':mod' and builder.concepts[e['target']].lower() in BLOCKED_MODIFIERS
               for e in builder.outgoing.get(centre, [])):
            return 'event_has_focusing_or_modal_modifier'
        mod = roles[":mod"]
        if (builder.concepts[mod] != "together" or builder.outgoing.get(mod)
                or builder.attrs_by_source.get(mod) or len(builder.incoming.get(mod, [])) != 1):
            return "event_modifier_not_simple_together"
        core = [r for r in roles if r.startswith(":arg")][0]
        participant = roles[core]
        if re.search(r"-\d\d$", builder.concepts[participant]):
            return "event_participant_is_event"
        return None
    if template["id"] in ("attribute_degree", "domain_degree"):
        subject_role = ":domain" if template["id"] == "domain_degree" else ":arg1"
        if centre_concept not in PROPERTIES:
            return "property_not_registered"
        if re.search(r"-\d\d$", builder.concepts[roles[subject_role]]):
            return "property_of_event_not_registered"
        degree = roles[":degree"]
        if (builder.concepts[degree].casefold() not in DEGREES
                or builder.outgoing.get(degree) or builder.attrs_by_source.get(degree)):
            return "degree_not_simple"
        if (builder.concepts[degree].casefold() in ("extreme", "real")
                and len(builder.incoming.get(degree, [])) != 1):
            return "new_degree_not_exclusive"
        if {e["role"].casefold() for e in builder.outgoing.get(centre, [])} != {subject_role, ":degree"}:
            return "additional_property_relations"
        if template["id"] == "domain_degree" and (
                len(builder.outgoing.get(centre, [])) != 2
                or len(builder.incoming.get(degree, [])) != 1):
            return "domain_degree_not_exclusive"
        return None
    if re.search(r"-\d\d$", centre_concept) or centre_concept in ("amr-unknown", "name"):
        return "not_nominal_centre"
    if any(e["role"].lower().startswith(":arg") for e in builder.outgoing.get(centre, [])):
        return "nominal_centre_has_argument_roles"
    if any(builder.concepts.get(e["target"], "").casefold() in BLOCKED_MODIFIERS
           for e in builder.outgoing.get(centre, []) if e["role"].lower() == ":mod"):
        return "nonintersective_or_focusing_modifier"
    mods = [x["target"] for x in (left, right) if x["role"] == ":mod"]
    if (any(builder.concepts[n].casefold() in EXTRA_SIMPLE_MODIFIERS for n in mods)
            and not _plain_local_nominal(builder, centre)):
        return "new_modifier_requires_simple_local_nominal"
    if any(not _simple_modifier(builder, n) for n in mods):
        return "modifier_not_registered_leaf"
    if any(builder.concepts[n].casefold() in {m for m, _head in NOMINAL_COMPOUNDS}
           and (builder.concepts[n].casefold(), centre_concept) not in NOMINAL_COMPOUNDS for n in mods):
        return "nominal_compound_head_not_registered"
    if len(mods) == 2 and builder.concepts[mods[0]] == builder.concepts[mods[1]]:
        return "repeated_modifier_not_compacted"
    if sum(MODIFIER_CLASS[builder.concepts[n].casefold()][1] == "classifying" for n in mods) > 1:
        return "multiple_classifying_modifiers"
    if template["id"] == "domain_modifier":
        subject = roles[":domain"]
        if builder.concepts[subject] in ("amr-unknown", "name"):
            return "unknown_domain"
        if sum(e["role"].casefold() == ":domain" for e in builder.outgoing[centre]) != 1:
            return "multiple_domains"
    return None


def _nominal(builder, node):
    return (not re.search(r"-\d\d$", builder.concepts[node])
            and builder.concepts[node] not in ("and", "or", "name", "amr-unknown", "multi-sentence"))


def _focus_free(builder, node):
    return not any(e["role"].lower() == ":mod" and builder.concepts[e["target"]] in BLOCKED_MODIFIERS
                   for e in builder.outgoing.get(node, []))


def _plain_local_nominal(builder, node):
    if not _nominal(builder, node):
        return False
    if builder.concepts[node].casefold() in (set(POSSESSIVE_PRONOUNS) | {
            "this", "that", "these", "those", "someone", "anyone", "everyone", "nobody"}):
        return False
    if any(e["role"].lower().startswith(":arg") or (
            e["role"].lower() == ":mod" and builder.concepts[e["target"]].casefold() in RESTRICTIVE_MODIFIERS)
           for e in builder.outgoing.get(node, [])):
        return False
    # Count metadata is retained by _prefix_head; generalized quantification
    # needs a scope rule, not one of these local physical-modifier templates.
    quantities = builder._metadata_values(node).get("quant", [])
    return all(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", q) for q in quantities)


def _physical_leaf(builder, node):
    return (_simple_modifier(builder, node)
            and MODIFIER_CLASS[builder.concepts[node].casefold()][1] != "classifying")


def _physical_property(builder, row):
    node = row["record"]["source_node"]
    return (row["role"] in (":arg1", ":domain")
            and builder.concepts[node] in PHYSICAL_PROPERTY_SURFACES
            and len(builder.outgoing.get(node, [])) == 1
            and not builder.attrs_by_source.get(node))


def _simple_carrier(builder, row):
    node = row["record"]["source_node"]
    return (row["role"] == ":op1" and builder.concepts[node] in SPATIAL_CARRIERS
            and len(builder.outgoing.get(node, [])) == 1
            and len(builder.incoming.get(node, [])) == 1
            and not builder.attrs_by_source.get(node))


def _remaining_guard(builder, centre, pair, template):
    if not _plain_local_nominal(builder, centre):
        return "not_simple_local_nominal"
    rule = template["id"]
    if rule == "physical_property_relation":
        properties = [r for r in pair if _physical_property(builder, r)]
        if len(properties) == 2:
            words = [PHYSICAL_PROPERTY_SURFACES[builder.concepts[r["record"]["source_node"]]] for r in properties]
            if words[0] == words[1]:
                return "repeated_property_not_compacted"
        elif len(properties) == 1:
            event = next(r for r in pair if r is not properties[0])
            node = event["record"]["source_node"]
            if (builder.concepts[node], event["role"]) not in PROPERTY_EVENT_ROLES:
                return "physical_property_event_role_not_registered"
            if (builder.attrs_by_source.get(node) or any(
                    e["role"].lower() == ":mod"
                    and builder.concepts[e["target"]].casefold() in RESTRICTIVE_MODIFIERS
                    for e in builder.outgoing.get(node, []))):
                return "physical_property_event_has_scope_modifier"
        else:
            return "physical_property_not_simple_registered_state"
    elif rule == "spatial_landmark_property":
        if (sum(_physical_property(builder, r) for r in pair) != 1
                or sum(_simple_carrier(builder, r) for r in pair) != 1):
            return "spatial_landmark_not_simple_property_carrier"
    else:
        modifier = next(r for r in pair if r["role"] == ":mod")
        if not _physical_leaf(builder, modifier["target"]):
            return "modifier_not_physical_leaf"
        relation = next(r for r in pair if r is not modifier)
        if rule == "spatial_landmark_modifier" and not _simple_carrier(builder, relation):
            return "spatial_carrier_not_exclusive_simple"
        if rule == "modified_whole_part" and not _nominal(builder, relation["target"]):
            return "part_not_nominal"
        if rule == "modified_possessor" and not _plain_local_nominal(builder, relation["record"]["source_node"]):
            return "possessed_head_not_simple_nominal"
    if _remaining_surface(builder, centre, pair, template) is None:
        return "surface_does_not_preserve_modified_endpoint"
    return None


def _prefix_head(builder, centre, prefix):
    head = builder.node_surface(centre)
    quantities = builder._metadata_values(centre).get("quant", [])
    if quantities and head.startswith(quantities[0] + " "):
        return quantities[0] + " " + prefix + " " + head[len(quantities[0]) + 1:]
    return prefix + " " + head


def _remaining_surface(builder, centre, pair, template):
    rule = template["id"]
    properties = [r for r in pair if _physical_property(builder, r)]
    if rule == "physical_property_relation" and len(properties) == 2:
        words = sorted(PHYSICAL_PROPERTY_SURFACES[builder.concepts[r["record"]["source_node"]]] for r in properties)
        return "{} {} {} and {}".format(builder.node_surface(centre), _copula(builder, centre), *words)
    if rule in ("physical_property_relation", "spatial_landmark_property"):
        if len(properties) != 1:
            return None
        property_row = properties[0]
        property_text = PHYSICAL_PROPERTY_SURFACES[builder.concepts[property_row["record"]["source_node"]]]
        head = _prefix_head(builder, centre, property_text)
        relation = next(r for r in pair if r is not property_row)
        return render_dyad_with_term(builder, relation, 1, head)
    modifier = next(r for r in pair if r["role"] == ":mod")
    relation = next(r for r in pair if r is not modifier)
    head = _modified_head(builder, centre, [modifier["target"]])
    # :part is directed whole -> part, so this template modifies term 0;
    # :poss and :op1 modify their target (the owner / spatial landmark).
    index = 0 if rule == "modified_whole_part" else 1
    return render_dyad_with_term(builder, relation, index, head)


def _extra_guard(builder, centre, pair, template, locations, boundaries):
    left, right = pair
    nodes = {e["record"]["source_node"] for e in pair} | {e["target"] for e in pair}
    if len(nodes) != 3:
        return "not_three_distinct_nodes"
    la, lb = locations[left["atom"]["id"]], locations[right["atom"]["id"]]
    if not (len(la) == len(lb) == 1 and la == lb and la[0][0] is not None and not la[0][1]):
        return "not_unique_same_positive_conjunction"
    if any(builder._raw_has_negative_polarity(n) or builder._condition_edges(n)
           or builder._is_structural_connective(n) or n in builder.conditions for n in nodes):
        return "scope_boundary"
    if any(b["governor"] in nodes and b["content"] in nodes for b in boundaries):
        return "nonfactive_boundary"
    if template["id"].startswith("local_"):
        reason = (_local_tree_scope_issue(builder, centre, pair, boundaries)
                  or _local_guard(builder, centre, pair, template))
        if reason is None and _local_surface(builder, centre, pair, template) is None:
            return "surface_does_not_preserve_modified_endpoint"
        return reason
    if not _nominal(builder, centre) or not _focus_free(builder, centre):
        return "not_unfocused_nominal_centre"
    rule = template["id"]
    if rule in REMAINING_RULES:
        return _remaining_guard(builder, centre, pair, template)
    if rule == "shared_target_properties":
        properties = [x["record"]["source_node"] for x in pair]
        if any(builder.concepts[n] not in PROPERTIES or len(builder.outgoing.get(n, [])) != 1
               or builder.attrs_by_source.get(n) for n in properties):
            return "properties_not_simple_registered_states"
        if builder.node_surface(properties[0]) == builder.node_surface(properties[1]):
            return "repeated_property_not_compacted"
        return None
    if rule in ("participant_possessor", "located_entity_possessor"):
        if not _nominal(builder, right["target"]):
            return "possessor_not_nominal"
    else:
        modifier = right["target"]
        if (builder.concepts[modifier].casefold() in EXTRA_SIMPLE_MODIFIERS
                and not _plain_local_nominal(builder, centre)):
            return "new_modifier_requires_simple_local_nominal"
        compound = ((builder.concepts[modifier].casefold(), builder.concepts[centre].casefold())
                    in NOMINAL_COMPOUNDS)
        if (not _simple_modifier(builder, modifier)
                or (MODIFIER_CLASS[builder.concepts[modifier].casefold()][1] == "classifying"
                    and not compound)):
            return "modifier_not_physical_leaf"
    if rule in ("participant_leaf_modifier", "participant_possessor"):
        if not re.search(r"-\d\d$", builder.concepts[left["record"]["source_node"]]):
            return "participant_governor_not_predicate"
    if rule in ("located_entity_modifier", "located_entity_possessor") and not _nominal(builder, left["target"]):
        return "location_not_nominal"
    if rule == "landmark_leaf_modifier" and not _nominal(builder, left['record']['source_node']):
        return "landmark_governor_not_nominal"
    if _extra_surface(builder, centre, pair, template) is None:
        return "surface_does_not_preserve_modified_endpoint"
    return None


def _extra_surface(builder, centre, pair, template):
    left, right = pair
    rule = template["id"]
    if rule.startswith("local_"):
        return _local_surface(builder, centre, pair, template)
    if rule in REMAINING_RULES:
        return _remaining_surface(builder, centre, pair, template)
    if rule == "shared_target_properties":
        props = [x["record"]["source_node"] for x in pair]
        props.sort(key=lambda n: (MODIFIER_CLASS.get(builder.node_surface(n), (99, ""))[0],
                                  builder.node_surface(n), n))
        # Preserve two predications explicitly, including two color properties.
        return "{} {} {} and {}".format(builder.node_surface(centre), _copula(builder, centre),
                                         *(builder.node_surface(n) for n in props))
    if rule in ("participant_possessor", "located_entity_possessor"):
        head = possessive(builder, right["target"], builder.node_surface(centre))
    else:
        head = _modified_head(builder, centre, [right["target"]])
    if rule in ("located_entity_modifier", "located_entity_possessor"):
        place = builder.node_surface(left["target"])
        prep = "" if place in ("outdoors", "indoors", "here", "there", "outside", "inside") else "at "
        return "{} {} {}{}".format(head, _copula(builder, centre), prep, place)
    if rule == "landmark_leaf_modifier":
        source = left['record']['source_node']
        return "{} {} at {}".format(builder.node_surface(source), _copula(builder, source), head)
    return render_dyad_with_term(builder, left, 1, head)


def _modified_head(builder, centre, modifiers):
    head = builder.node_surface(centre)
    same_class = (len(modifiers) == 2 and
                  MODIFIER_CLASS[builder.concepts[modifiers[0]].casefold()][0] ==
                  MODIFIER_CLASS[builder.concepts[modifiers[1]].casefold()][0])
    # Two color attributes remain explicit conjuncts, not an invented blended
    # color ("blue and purple", not the ambiguous compound "blue purple").
    prefix = (" and " if same_class else " ").join(builder.node_surface(n) for n in modifiers)
    quant = builder._metadata_values(centre).get("quant", [])
    if quant and head.startswith(quant[0] + " "):
        return quant[0] + " " + prefix + " " + head[len(quant[0]) + 1:]
    return prefix + " " + head


def _surface(builder, centre, left, right, template):
    roles = {x["role"]: x["target"] for x in (left, right)}
    if template["id"] == "event_simple_modifier":
        core = next(x for x in (left, right) if x["role"].startswith(":arg"))
        return core["atom"]["base_surface_text"] + " together"
    if template["id"] in ("attribute_degree", "domain_degree"):
        subject = roles[":domain" if template["id"] == "domain_degree" else ":arg1"]
        return "{} {} {} {}".format(builder.node_surface(subject), _copula(builder, subject),
                                     DEGREE_SURFACES[builder.concepts[roles[":degree"]].casefold()],
                                     builder.node_surface(centre))
    modifiers = [x["target"] for x in (left, right) if x["role"] == ":mod"]
    modifiers.sort(key=lambda n: (MODIFIER_CLASS[builder.concepts[n].casefold()][0],
                                  builder.node_surface(n), n))
    head = _modified_head(builder, centre, modifiers)
    if template["id"] == "domain_modifier":
        if (builder.concepts[centre] in COUNTABLE_TYPES
                and not builder._metadata_values(centre).get("quant")):
            head = ("an " if head[:1].lower() in "aeiou" else "a ") + head
        return "{} {} {}".format(builder.node_surface(roles[":domain"]),
                                  _copula(builder, roles[":domain"]), head)
    if template["id"] == "possessive_modifier":
        return possessive(builder, roles[":poss"], head)
    return head


def _copula(builder, subject):
    return copula(builder, subject)


def merge_registered_templates(builder, internal, frame, boundaries, *, extended=True):
    groups = _ordinary_dyads(builder, internal, frame)
    locations = atom_locations(frame["formula_ast"])
    candidates, rejected = [], Counter()
    for centre, rows in groups.items():
        for left, right in combinations(rows, 2):
            roles = tuple(sorted((left["role"], right["role"])))
            for template in TEMPLATES:
                if not extended and template["priority"] >= 4:
                    continue
                if template.get("shape") in ("directed_path", "shared_target") or template["priority"] >= 7:
                    continue
                if roles != template["roles"]:
                    continue
                reason = _guard(builder, centre, left, right, template, locations, boundaries)
                if reason:
                    rejected[reason] += 1
                    continue
                ordered = sorted((left, right), key=lambda x: (x["role"],
                    primitives._canonical_json(builder._node_identity(x["target"])), x["target"]))
                candidates.append((template, centre, ordered))
    path_template = next(t for t in TEMPLATES if t["id"] == "spatial_path")
    for source, rows in (groups.items() if extended else []):
        for left in rows:
            if left["role"] != ":location":
                continue
            carrier = left["target"]
            for right in groups.get(carrier, []):
                if right["role"] != ":op1":
                    continue
                target = right["target"]
                nodes = {source, carrier, target}
                la, lb = locations[left["atom"]["id"]], locations[right["atom"]["id"]]
                reason = None
                if len(nodes) != 3:
                    reason = "path_not_three_nodes"
                elif not (len(la) == len(lb) == 1 and la == lb and la[0][0] is not None and not la[0][1]):
                    reason = "path_not_same_positive_conjunction"
                elif builder.concepts[carrier] not in SPATIAL_CARRIERS:
                    reason = "spatial_carrier_not_registered"
                elif (len(builder.outgoing.get(carrier, [])) != 1
                      or len(builder.incoming.get(carrier, [])) != 1
                      or builder.attrs_by_source.get(carrier)):
                    reason = "spatial_carrier_not_exclusive_simple"
                elif any(builder._raw_has_negative_polarity(n) or builder._condition_edges(n)
                         or builder._is_structural_connective(n) or n in builder.conditions for n in nodes):
                    reason = "path_scope_boundary"
                elif any(b["governor"] in nodes and b["content"] in nodes for b in boundaries):
                    reason = "path_nonfactive_boundary"
                if reason:
                    rejected[reason] += 1
                else:
                    candidates.append((path_template, carrier, [left, right]))
    if extended:
        incoming = defaultdict(list)
        for source, rows in groups.items():
            for left in rows:
                incoming[left["target"]].append(left)
                for right in groups.get(left["target"], []):
                    for template in TEMPLATES:
                        if (template.get("shape") == "directed_path" and template["priority"] >= 7
                                and (left["role"], right["role"]) == template["roles"]):
                            reason = _extra_guard(builder, left["target"], [left, right], template, locations, boundaries)
                            if reason:
                                rejected[reason] += 1
                            else:
                                candidates.append((template, left["target"], [left, right]))
            for left, right in combinations(rows, 2):
                pair = sorted((left, right), key=lambda x: x["role"])
                for template in TEMPLATES:
                    if (template['priority'] >= 7 and template.get('shape') not in ('directed_path','shared_target')
                            and tuple(x["role"] for x in pair) == template["roles"]):
                        reason = _extra_guard(builder, source, pair, template, locations, boundaries)
                        if reason:
                            rejected[reason] += 1
                        else:
                            candidates.append((template, source, pair))
        for centre, rows in incoming.items():
            for left, right in combinations(rows, 2):
                pair = sorted((left, right), key=lambda x: (x["role"],
                    primitives._canonical_json(builder._node_identity(x["record"]["source_node"])), x["record"]["source_node"]))
                for template in TEMPLATES:
                    if template.get("shape") != "shared_target" or tuple(x["role"] for x in pair) != template["roles"]:
                        continue
                    reason = _extra_guard(builder, centre, pair, template, locations, boundaries)
                    if reason:
                        rejected[reason] += 1
                    else:
                        candidates.append((template, centre, pair))
    safe_candidates = []
    for candidate in candidates:
        records = [row["record"] for row in candidate[2]]
        nodes = {record["source_node"] for record in records} | {
            record["target_node"] for record in records}
        # Formula locations cannot expose an omitted metadata negation or a
        # content relation flattened into an outer AND through shared nodes.
        reason = (metadata_scope_issue(builder, nodes)
                  or merge_scope_issue(builder, records, boundaries))
        if reason:
            rejected[reason] += 1
        else:
            safe_candidates.append(candidate)
    candidates = safe_candidates
    legacy_key = lambda x: (x[0]["priority"],
        primitives._canonical_json(builder._node_identity(x[1])), x[1],
        tuple(primitives._canonical_json(builder._node_identity(y["target"])) for y in x[2]))
    candidates.sort(key=getattr(builder, 'merge_candidate_key', legacy_key))
    original = {a["id"]: a for a in frame["atoms"]}
    used, merges, new_atoms = set(), [], []
    next_number = 1 + max(int(a["id"][1:]) for a in frame["atoms"])
    for template, centre, pair in candidates:
        ids = [x["atom"]["id"] for x in pair]
        if used.intersection(ids):
            rejected["overlapping_candidate"] += 1
            continue
        new_id = "x{}".format(next_number)
        next_number += 1
        is_path = template.get("shape") == "directed_path"
        nodes = ([pair[0]["record"]["source_node"], centre, pair[1]["record"]["source_node"]]
                 if template.get("shape") == "shared_target" else
                 [pair[0]["record"]["source_node"], centre, pair[1]["target"]] if is_path
                 else [pair[0]["target"], centre, pair[1]["target"]])
        edges = [[x["record"]["source_node"], x["role"], x["target"]] for x in pair]
        surface = (_extra_surface(builder, centre, pair, template) if template["priority"] >= 7 else
                   "{} {} {}".format(*(builder.node_surface(n) for n in nodes)) if is_path
                   else _surface(builder, centre, pair[0], pair[1], template))
        expression = {
            "type": "triple-v1.2", "kind": "triple",
            "join_signature": "ordinary:" + template["id"],
            "subject": builder._node_identity(nodes[0]),
            "predicate": builder._node_identity(centre),
            "object": builder._node_identity(nodes[2]),
            "subject_object_coreference": "distinct",
            "components": [deepcopy(original[k]["expression"]) for k in ids],
        }
        new_atoms.append({"id": new_id, "expression": expression, "verbalization": surface})
        definition = {"op": "and", "args": [{"op": "atom", "id": k} for k in ids]}
        merges.append({"rule": template["id"], "atom_id": new_id,
                       "component_atom_ids": ids, "definition": definition,
                       "nodes": nodes, "edges": edges,
                       "component_dyad_ids": [x["record"]["id"] for x in pair],
                       "source_graph_record_ids": [x["record"]["graph_record_id"] for x in pair],
                       "surface": surface})
        used.update(ids)

    def rewrite(n):
        n = deepcopy(n)
        if n["op"] in ("and", "or"):
            n["args"] = [rewrite(c) for c in n["args"]]
            if n["op"] == "and":
                for m in merges:
                    local = {c["id"] for c in n["args"] if c["op"] == "atom"}
                    if set(m["component_atom_ids"]) <= local:
                        replacement, once = [], False
                        for c in n["args"]:
                            if c["op"] == "atom" and c["id"] in m["component_atom_ids"]:
                                if not once:
                                    replacement.append({"op": "atom", "id": m["atom_id"]})
                                    once = True
                            else:
                                replacement.append(c)
                        n["args"] = replacement
                if len(n["args"]) == 1:
                    return n["args"][0]
        elif n["op"] == "not":
            n["arg"] = rewrite(n["arg"])
        elif n["op"] == "implies":
            n["antecedent"] = rewrite(n["antecedent"])
            n["consequent"] = rewrite(n["consequent"])
        return n
    result = {"formula_ast": rewrite(frame["formula_ast"]),
              "atoms": [deepcopy(a) for a in frame["atoms"] if a["id"] not in used] + new_atoms}
    return result, merges, dict(rejected)


def expand_macros(ast, merges):
    definitions = {m["atom_id"]: m["definition"] for m in merges}
    def expand(n):
        if n["op"] == "atom":
            return deepcopy(definitions.get(n["id"], n))
        n = deepcopy(n)
        if n["op"] in ("and", "or"):
            n["args"] = [expand(c) for c in n["args"]]
        elif n["op"] == "not":
            n["arg"] = expand(n["arg"])
        elif n["op"] == "implies":
            n["antecedent"] = expand(n["antecedent"])
            n["consequent"] = expand(n["consequent"])
        return n
    return expand(ast)
