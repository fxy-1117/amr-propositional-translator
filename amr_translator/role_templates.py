"""PropBank-aware dyad, triple, and fallback surface templates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional
import re

from . import primitives
from .propbank import PropBankRoleResolution, resolve_numbered_role
from .primitives import _role_key


_SPACE_RE = re.compile(r"\s+")

_OP_ROLE_RE = re.compile(r"^op\d+$", re.IGNORECASE)

_PREP_ROLE_RE = re.compile(r"^prep-(.+)$", re.IGNORECASE)

_CORE_ROLE_RE = re.compile(r"^arg\d+$", re.IGNORECASE)

_ACTIVE_ARG1_PREDICATES = frozenset(
    {
        "appear",
        "arrive",
        "bend",
        "come",
        "come out",
        "come up",
        "crouch",
        "die",
        "disappear",
        "emerge",
        "exist",
        "fall",
        "fly",
        "freeze",
        "get",
        "grow",
        "hang",
        "happen",
        "kneel",
        "lean",
        "lie",
        "occur",
        "remain",
        "rest",
        "ring",
        "rise",
        "shine",
        "sit",
        "sit down",
        "sit up",
        "sleep",
        "slide",
        "spin",
        "stand",
        "stand by",
        "stand up",
        "step",
        "swing",
        "wait",
    }
)

PROPERTY_PREDICATES = frozenset(
    {
        "afraid",
        "alive",
        "angry",
        "available",
        "bald",
        "bad",
        "bare",
        "beautiful",
        "big",
        "black",
        "blue",
        "bright",
        "brown",
        "busy",
        "clean",
        "clear",
        "closed",
        "cold",
        "cool",
        "dead",
        "dark",
        "different",
        "dirty",
        "dry",
        "empty",
        "fast",
        "friendly",
        "full",
        "good",
        "gray",
        "green",
        "grey",
        "happy",
        "high",
        "hot",
        "hurt",
        "important",
        "large",
        "late",
        "likely",
        "little",
        "light",
        "long",
        "near",
        "necessary",
        "new",
        "old",
        "open",
        "orange",
        "pale",
        "patient",
        "pink",
        "possible",
        "public",
        "purple",
        "quiet",
        "ready",
        "red",
        "sad",
        "safe",
        "same",
        "short",
        "similar",
        "small",
        "snowy",
        "superior",
        "tall",
        "tan",
        "thin",
        "upset",
        "warm",
        "well",
        "wet",
        "white",
        "yellow",
        "young",
    }
)

_DOUBLE_FINAL_CONSONANT = frozenset(
    {
        "admit",
        "beg",
        "bar",
        "chop",
        "clap",
        "commit",
        "drag",
        "drop",
        "fit",
        "gas",
        "grab",
        "hug",
        "jog",
        "knit",
        "nod",
        "plan",
        "pot",
        "prefer",
        "refer",
        "rub",
        "step",
        "stop",
        "strap",
        "strip",
        "tag",
        "thin",
        "trap",
        "wed",
        "wrap",
    }
)

_GERUND_DOUBLE_FINAL_CONSONANT = frozenset(
    set(_DOUBLE_FINAL_CONSONANT)
    | {"begin", "control", "get", "run", "sit", "swim"}
)

DYAD_ROLE_TEMPLATES: Dict[str, str] = {
    "purpose": "{source} is for {target}",
    "time": "{source} occurs at {target}",
    "direction": "{source} proceeds toward {target}",
    "domain": "{target} is {source}",
    "mod": "{target} {source}",
    "manner": "{source} occurs in manner {target}",
    "poss": "{target}'s {source}",
    "poss-of": "{source}'s {target}",
    "topic": "{source} is about {target}",
    "part": "{target} is part of {source}",
    "part-of": "{source} is part of {target}",
    # PENMAN canonicalizes ``whole :consist-of part`` to
    # ``part :consist whole``.
    "consist": "{target} consists of {source}",
    "consist-of": "{source} consists of {target}",
    "location": "{source} occurs at {target}",
    "location-of": "{target} occurs at {source}",
    "dayperiod": "{source} occurs during {target}",
    "destination": "{source} proceeds to {target}",
    "source": "{source} originates from {target}",
    "instrument": "{source} uses {target}",
    "accompanier": "{source} occurs with {target}",
    "beneficiary": "{source} is for {target}",
    "path": "{source} proceeds along {target}",
    "medium": "{source} occurs via {target}",
    "cause": "{source} is caused by {target}",
    "concession": "{source} occurs despite {target}",
    "duration": "{source} lasts for {target}",
    "degree": "{source} has degree {target}",
    "age": "{source} has age {target}",
    "frequency": "{source} has frequency {target}",
    "extent": "{source} has extent {target}",
    "example": "{source} has example {target}",
    "subevent": "{source} is part of {target}",
    "subevent-of": "{target} is part of {source}",
}

@dataclass(frozen=True)

class SurfaceRealization:
    surface: str
    template_id: str
    relation: str
    resolution: PropBankRoleResolution
    fallback_used: bool = False

    def metadata(self) -> Dict[str, Any]:
        payload = self.resolution.as_metadata()
        payload.update(
            {
                "semantic_relation": self.relation,
                "fallback_used": self.fallback_used,
                "template_id": self.template_id,
            }
        )
        return payload

def _surface(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip()).strip()

def _predicate_words(value: Any) -> str:
    concept = str(value or "").strip().casefold()
    concept = re.sub(r"-\d+$", "", concept)
    return _surface(concept.replace("-", " "))

def _fill(template: str, **values: str) -> str:
    return _surface(template.format(**values))

def _contains(description: str, *needles: str) -> bool:
    value = description.casefold()
    return any(needle in value for needle in needles)

def _past_participle(lemma: str) -> str:
    """Inflect a parser predicate without consulting source text or a model."""

    pieces = _surface(lemma).casefold().split()
    if not pieces:
        return "occurred"
    verb = pieces[0]
    irregular = {
        "bear": "borne",
        "be": "been",
        "bend": "bent",
        "begin": "begun",
        "blow": "blown",
        "break": "broken",
        "bring": "brought",
        "build": "built",
        "buy": "bought",
        "catch": "caught",
        "choose": "chosen",
        "cut": "cut",
        "do": "done",
        "drive": "driven",
        "draw": "drawn",
        "eat": "eaten",
        "feel": "felt",
        "find": "found",
        "fly": "flown",
        "freeze": "frozen",
        "get": "gotten",
        "give": "given",
        "go": "gone",
        "have": "had",
        "keep": "kept",
        "hide": "hidden",
        "hold": "held",
        "know": "known",
        "leave": "left",
        "lose": "lost",
        "make": "made",
        "meet": "met",
        "pay": "paid",
        "read": "read",
        "ride": "ridden",
        "ring": "rung",
        "rise": "risen",
        "run": "run",
        "say": "said",
        "see": "seen",
        "send": "sent",
        "sell": "sold",
        "set": "set",
        "shake": "shaken",
        "shoot": "shot",
        "show": "shown",
        "sling": "slung",
        "sit": "sat",
        "speak": "spoken",
        "stand": "stood",
        "steal": "stolen",
        "strew": "strewn",
        "strike": "struck",
        "string": "strung",
        "spread": "spread",
        "swim": "swum",
        "swing": "swung",
        "take": "taken",
        "teach": "taught",
        "tell": "told",
        "think": "thought",
        "throw": "thrown",
        "tear": "torn",
        "upset": "upset",
        "wear": "worn",
        "win": "won",
        "write": "written",
    }
    if verb in irregular:
        pieces[0] = irregular[verb]
    elif verb.endswith("e"):
        pieces[0] = verb + "d"
    elif len(verb) > 1 and verb.endswith("y") and verb[-2] not in "aeiou":
        pieces[0] = verb[:-1] + "ied"
    elif verb in _DOUBLE_FINAL_CONSONANT:
        pieces[0] = verb + verb[-1] + "ed"
    else:
        pieces[0] = verb + "ed"
    return " ".join(pieces)

def _present_participle(lemma: str) -> str:
    pieces = _surface(lemma).casefold().split()
    if not pieces:
        return ""
    verb = pieces[0]
    if verb == "be":
        pieces[0] = "being"
    elif verb.endswith("ie"):
        pieces[0] = verb[:-2] + "ying"
    elif verb.endswith("e") and not verb.endswith(("ee", "ye")):
        pieces[0] = verb[:-1] + "ing"
    elif verb in _GERUND_DOUBLE_FINAL_CONSONANT:
        pieces[0] = verb + verb[-1] + "ing"
    else:
        pieces[0] = verb + "ing"
    return " ".join(pieces)

def _prd_relation(resolution: PropBankRoleResolution) -> str:
    """Use the one role split shared by PropBank product/result frames."""

    return "patient" if resolution.role.casefold() == "arg1" else "result"

def _semantic_relation(resolution: PropBankRoleResolution) -> str:
    """Map official role metadata to a small transparent surface relation."""

    if not resolution.exact:
        return "unresolved"
    description = resolution.description.casefold()
    function = resolution.function.strip().upper()
    role = resolution.role.casefold()
    predicate = _predicate_words(resolution.predicate_concept)
    predicate_progressive = _present_participle(predicate)

    # The function tag fixes the broad class. Free-text descriptions only
    # refine prepositions within that class; they may never turn PAG into a
    # goal merely because the word "goal" happens to occur in its gloss.
    if function == "PAG":
        return "agent"
    if function == "CAU":
        return "cause"
    if function == "PPT":
        if role == "arg0":
            return "agent"
        if role == "arg1" and predicate in PROPERTY_PREDICATES:
            return "property"
        if _contains(
            description,
            "instrument",
            "equipment",
            "tool",
            "means of",
            "medium used",
        ):
            return "instrument"
        if _contains(
            description,
            "decoration",
            "covering",
            "clothing",
            "clothes",
        ):
            return "accompaniment"
        if _contains(description, "path", "route", "course", "trajectory"):
            return "path"
        if _contains(description, "source", "origin", "start point", " from"):
            return "source"
        if _contains(description, "location", "position", "place", "site"):
            return "location"
        if _contains(description, "topic", "subject matter"):
            return "topic"
        if _contains(description, "amount", "extent", "difference", "distance"):
            return "extent"
        if _contains(description, "attribute", "characteristic"):
            return "attribute"
        if role == "arg1" and predicate in _ACTIVE_ARG1_PREDICATES:
            return "theme-active"
        if predicate_progressive and predicate_progressive in description:
            return "theme-active"
        if _contains(
            description,
            "logical subject",
            "entity in motion",
            "thing arising",
            "thing appearing",
            "thing falling",
            "thing freezing",
            "thing leaning",
            "thing shining",
            "thing sliding",
            "thing spinning",
            "thing swinging",
            "thing trembling",
            "thing forming a curve",
            "swimmer",
            "jumper",
            "runner",
            "comer",
        ):
            return "theme-active"
        if _contains(
            description,
            "experiencer",
            "experiencing",
            "now-",
            "degrees (temperature",
            "positive state",
            "whose degree is emphasized",
            "what was fun",
        ):
            return "state"
        if predicate and description.startswith("{} entity".format(predicate)):
            return "state"
        return "patient"
    if function == "GOL":
        if _contains(description, "benefactive", "beneficiary", "ordered-for"):
            return "beneficiary"
        if _contains(
            description,
            "attribute",
            "characteristic",
            "resulting state",
        ):
            return "result"
        if _contains(description, "location", "position", "place", "site"):
            return "location"
        if _contains(
            description,
            "instrument",
            "equipment",
            "tool",
        ):
            return "instrument"
        return "goal"
    if function == "LOC":
        if _contains(description, "path", "route", "course", "trajectory"):
            return "path"
        if _contains(description, "source", "origin", "start point"):
            return "source"
        if _contains(description, "destination", "goal", "end point"):
            return "goal"
        return "location"
    if function == "DIR":
        if _contains(
            description,
            "entity left behind",
            "thing left behind",
            "thing milked",
            "thing that is clear",
        ):
            return "patient"
        if _contains(
            description,
            "source",
            "origin",
            "start point",
            "from",
            "seller",
            "giver",
            "former job",
            "place left",
            "location left",
            "original set",
        ):
            return "source"
        if _contains(description, "destination", "goal", "end point"):
            return "goal"
        if _contains(description, "path", "route", "course", "trajectory"):
            return "path"
        if _contains(description, "location", "position", "site"):
            return "location"
        if _contains(description, "benefactive", "beneficiary"):
            return "beneficiary"
        return "direction"
    if function == "SRC":
        return "source"
    if function == "MNR":
        if _contains(
            description,
            "instrument",
            "equipment",
            "tool",
            "means of",
            "medium used",
        ):
            return "instrument"
        if _contains(description, "decoration", "covering"):
            return "accompaniment"
        return "manner"
    if function == "COM":
        if _contains(
            description,
            "opponent",
            "against",
            "adversary",
            "competitor",
            "fighting",
            "fighter",
            "wrestler",
            "arguer",
        ):
            return "opponent"
        return "companion"
    if function == "PRD":
        return _prd_relation(resolution)
    if function == "VSP":
        return "unresolved"
    return {
        "EXT": "extent",
        "PRP": "purpose",
        "TMP": "time",
    }.get(function, "unresolved")

def _fallback_resolution(
    predicate_concept: Any,
    role: Any,
    status: str,
) -> PropBankRoleResolution:
    return PropBankRoleResolution(
        str(predicate_concept or "").strip(), _role_key(role), status
    )

def _render_numbered_dyad(
    source: str,
    role: Any,
    target: str,
    *,
    source_concept: Any,
    fallback_surface: str,
) -> SurfaceRealization:
    role_key = _role_key(role)
    resolution = resolve_numbered_role(source_concept, role_key)
    relation = _semantic_relation(resolution)
    templates = {
        "agent": "{target} {source}",
        "goal": "{source} is directed to {target}",
        "location": "{source} occurs at {target}",
        "path": "{source} occurs along {target}",
        "direction": "{source} proceeds toward {target}",
        "source": "{source} originates from {target}",
        "instrument": "{source} uses {target}",
        "manner": "{source} occurs by {target}",
        "companion": "{source} occurs with {target}",
        "opponent": "{source} occurs against {target}",
        "beneficiary": "{source} is for {target}",
        "purpose": "{source} is for {target}",
        "result": "{source} results in {target}",
        "extent": "{source} has extent {target}",
        "cause": "{source} is caused by {target}",
        "topic": "{source} is about {target}",
        "time": "{source} occurs during {target}",
        "accompaniment": "{source} occurs with {target}",
        "theme-active": "{target} {source}",
        "property": "{target} is {source}",
        "state": "{target} is in state {source}",
        "attribute": "{source} has attribute {target}",
    }
    if relation == "patient":
        surface = _fill(
            "{target} is {participle}",
            target=target,
            participle=_past_participle(source),
        )
        template = None
    else:
        template = templates.get(relation)
        surface = _fill(template, source=source, target=target) if template else ""
    if relation != "patient" and template is None:
        return SurfaceRealization(
            _surface(fallback_surface),
            "dyad:{}:fallback:{}".format(role_key, resolution.status),
            relation,
            resolution,
            fallback_used=True,
        )
    return SurfaceRealization(
        surface,
        "dyad:{}:{}:{}".format(
            role_key, relation, resolution.function.casefold() or "none"
        ),
        relation,
        resolution,
    )

def _render_non_numbered_dyad(
    source: str,
    role: Any,
    target: str,
    *,
    source_concept: Any,
    fallback_surface: str,
) -> SurfaceRealization:
    role_key = _role_key(role)
    template = DYAD_ROLE_TEMPLATES.get(role_key)
    template_id = "dyad:{}".format(role_key)
    prep_match = _PREP_ROLE_RE.fullmatch(role_key)
    if template is None and prep_match:
        prep = prep_match.group(1).replace("-", " ")
        template = "{source} " + prep + " {target}"
        template_id = "dyad:prep-*"
    if template is None and _OP_ROLE_RE.fullmatch(role_key):
        template = "{source} {target}"
        template_id = "dyad:op*"
    resolution = _fallback_resolution(source_concept, role_key, "amr-role")
    if template is None:
        return SurfaceRealization(
            _surface(fallback_surface),
            "dyad:fallback",
            "unresolved",
            resolution,
            fallback_used=True,
        )
    return SurfaceRealization(
        _fill(template, source=source, target=target),
        template_id,
        role_key,
        resolution,
    )

def _realize_dyad(
    source: str,
    role: Any,
    target: str,
    *,
    source_concept: Any = "",
    fallback_surface: Optional[str] = None,
) -> SurfaceRealization:
    fallback = _surface(
        fallback_surface if fallback_surface is not None else "{} {}".format(source, target)
    )
    if _CORE_ROLE_RE.fullmatch(_role_key(role)):
        return _render_numbered_dyad(
            source,
            role,
            target,
            source_concept=source_concept,
            fallback_surface=fallback,
        )
    return _render_non_numbered_dyad(
        source,
        role,
        target,
        source_concept=source_concept,
        fallback_surface=fallback,
    )

_TRIPLE_RELATION_TEMPLATES: Dict[str, str] = {
    "goal": "{subject} {predicate} to {object}",
    "location": "{subject} {predicate} at {object}",
    "path": "{subject} {predicate} along {object}",
    "direction": "{subject} {predicate} toward {object}",
    "source": "{subject} {predicate} from {object}",
    "instrument": "{subject} {predicate} using {object}",
    "manner": "{subject} {predicate} by {object}",
    "companion": "{subject} {predicate} with {object}",
    "opponent": "{subject} {predicate} against {object}",
    "beneficiary": "{subject} {predicate} for {object}",
    "purpose": "{subject} {predicate} for {object}",
    "result": "{subject} {predicate} {object}",
    "extent": "{subject} {predicate} by {object}",
    "cause": "{subject} {predicate} because of {object}",
    "topic": "{subject} {predicate} about {object}",
    "time": "{subject} {predicate} during {object}",
    "accompaniment": "{subject} {predicate} with {object}",
    # Secondary PPT/PAG arguments are conservatively realized as associated
    # participants rather than being forced into a direct-object position.
    "patient": "{subject} {predicate} with {object}",
    "agent": "{subject} {predicate} with {object}",
    "theme-active": "{subject} {predicate} with {object}",
    "property": "{subject} {predicate} as {object}",
    "state": "{subject} {predicate} as {object}",
    "attribute": "{subject} {predicate} as {object}",
}

def _realize_triple(
    subject: str,
    predicate: str,
    obj: str,
    join_signature: Any,
    *,
    predicate_concept: Any = "",
    fallback_surface: Optional[str] = None,
) -> SurfaceRealization:
    signature = str(join_signature or "").strip().casefold()
    fallback = _surface(
        fallback_surface
        if fallback_surface is not None
        else "{} {} {}".format(subject, predicate, obj)
    )
    if not signature.startswith("arg0+"):
        resolution = _fallback_resolution(
            predicate_concept, signature, "unsupported-join"
        )
        return SurfaceRealization(
            fallback,
            "triple:fallback:unsupported-join",
            "unresolved",
            resolution,
            fallback_used=True,
        )
    object_role = signature.split("+", 1)[1]
    resolution = resolve_numbered_role(predicate_concept, object_role)
    relation = _semantic_relation(resolution)
    if object_role == "arg1":
        return SurfaceRealization(
            _fill(
                "{subject} {predicate} {object}",
                subject=subject,
                predicate=predicate,
                object=obj,
            ),
            "triple:arg0+arg1:direct:{}".format(
                resolution.function.casefold() if resolution.exact else resolution.status
            ),
            relation,
            resolution,
            fallback_used=not resolution.exact,
        )
    template = _TRIPLE_RELATION_TEMPLATES.get(relation)
    if template is None:
        return SurfaceRealization(
            fallback,
            "triple:{}:fallback:{}".format(signature, resolution.status),
            relation,
            resolution,
            fallback_used=True,
        )
    return SurfaceRealization(
        _fill(
            template,
            subject=subject,
            predicate=predicate,
            object=obj,
        ),
        "triple:{}:{}:{}".format(
            signature, relation, resolution.function.casefold() or "none"
        ),
        relation,
        resolution,
    )

def _triple_predicate_concept(
    atom: Mapping[str, Any],
    dyad_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    records = [
        dyad_by_id[item]
        for item in atom.get("component_dyad_ids", [])
        if item in dyad_by_id
    ]
    event_id = str(atom.get("event_occurrence_id", ""))
    event_records = [
        record for record in records if str(record.get("source_node", "")) == event_id
    ]
    return str(next(
        (record.get("source_concept", "") for record in event_records
         if _role_key(record.get("role")) == "arg0"),
        event_records[0].get("source_concept", "") if event_records else "",
    ))

def _negative_triple_surface(
    atom: Mapping[str, Any],
    realization: SurfaceRealization,
) -> str:
    subject = _surface(atom.get("subject"))
    predicate = _surface(atom.get("predicate"))
    obj = _surface(atom.get("object"))
    positive_prefix = _surface("{} {}".format(subject, predicate))
    if realization.surface.startswith(positive_prefix):
        suffix = realization.surface[len(positive_prefix) :].strip()
        return _surface(
            "{} does not {}{}".format(
                subject,
                predicate,
                " {}".format(suffix) if suffix else "",
            )
        )
    return _surface("not {}".format(realization.surface))

def _negative_dyad_surface(
    atom: Mapping[str, Any],
    realization: SurfaceRealization,
) -> str:
    terms = [_surface(item) for item in atom.get("terms", [])]
    if len(terms) == 2:
        source, target = terms
        if realization.relation in {"agent", "theme-active"}:
            return _fill(
                "{target} does not {source}", target=target, source=source
            )
        if realization.relation == "patient":
            return _fill(
                "{target} is not {participle}",
                target=target,
                participle=_past_participle(source),
            )
    surface = realization.surface
    replacements = (
        (" is ", " is not "),
        (" occurs ", " does not occur "),
        (" proceeds ", " does not proceed "),
        (" originates ", " does not originate "),
        (" uses ", " does not use "),
        (" has ", " does not have "),
    )
    for old, new in replacements:
        if old in surface:
            return _surface(surface.replace(old, new, 1))
    return _surface("not {}".format(surface))

def _signed_surface(
    atom: Mapping[str, Any],
    realization: SurfaceRealization,
) -> str:
    if atom.get("polarity") != "negative":
        return realization.surface
    if atom.get("kind") == "triple":
        return _negative_triple_surface(atom, realization)
    if atom.get("kind") in {"dyadic", "opaque"}:
        return _negative_dyad_surface(atom, realization)
    return _surface("not {}".format(realization.surface))

def _record_realization(record: Mapping[str, Any]) -> SurfaceRealization:
    terms = list(record.get("terms", []))
    if len(terms) != 2:
        raise primitives.AMRTripleValidationError(
            "dyadic record {} has invalid terms".format(record.get("id"))
        )
    fallback = _surface(
        record.get("surface_fallback_text", record.get("base_surface_text", ""))
    )
    realization = _realize_dyad(
        str(terms[0]),
        record.get("role", ""),
        str(terms[1]),
        source_concept=record.get("source_concept", ""),
        fallback_surface=fallback,
    )
    return realization


def _atom_realization(
    atom: Mapping[str, Any],
    dyad_by_id: Mapping[str, Mapping[str, Any]],
) -> SurfaceRealization:
    kind = str(atom.get("kind", ""))
    fallback = _surface(
        atom.get("surface_fallback_text", atom.get("base_surface_text", ""))
    )
    if kind == "triple":
        predicate_concept = _triple_predicate_concept(atom, dyad_by_id)
        realization = _realize_triple(
            str(atom["subject"]),
            str(atom["predicate"]),
            str(atom["object"]),
            atom.get("join_signature", ""),
            predicate_concept=predicate_concept,
            fallback_surface=fallback,
        )
    elif kind in {"dyadic", "opaque"}:
        terms = list(atom.get("terms", []))
        if len(terms) != 2:
            raise primitives.AMRTripleValidationError(
                "{} atom {} has invalid terms".format(kind, atom.get("id"))
            )
        component = next(
            (
                dyad_by_id[item]
                for item in atom.get("component_dyad_ids", [])
                if item in dyad_by_id
            ),
            {},
        )
        source_concept = atom.get("source_concept") or component.get(
            "source_concept", ""
        )
        realization = _realize_dyad(
            str(terms[0]),
            atom.get("role", ""),
            str(terms[1]),
            source_concept=source_concept,
            fallback_surface=fallback,
        )
    else:
        raise primitives.AMRTripleValidationError(
            "unsupported atom kind {!r}".format(kind)
        )
    return realization
