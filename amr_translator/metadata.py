"""Metadata, canonical-identity, and same-event composition helpers."""
from __future__ import annotations

import re


_DATE_ROLES = frozenset(
    {
        ":calendar",
        ":century",
        ":day",
        ":dayperiod",
        ":decade",
        ":era",
        ":month",
        ":quarter",
        ":season",
        ":timezone",
        ":weekday",
        ":year",
    }
)

_ORDINAL_ROLES = frozenset({":value", ":range"})

_METADATA_ALWAYS_ROLES = frozenset({":li", ":mode"})

_NONASSERTIVE_MODES = frozenset({"imperative", "interrogative", "expressive"})

_SENSE_RE = re.compile(r"-\d+$")

_INTEGER_RE = re.compile(r"^[+-]?\d+$")

def _simple_plural(value: str) -> str:
    words = str(value).split()
    if not words:
        return value
    irregular = {
        "child": "children",
        "man": "men",
        "mouse": "mice",
        "person": "people",
        "woman": "women",
    }
    word = words[-1]
    folded = word.casefold()
    if folded in irregular:
        replacement = irregular[folded]
    elif folded.endswith(("s", "x", "z", "ch", "sh")):
        replacement = word + "es"
    elif folded.endswith("y") and len(word) > 1 and folded[-2] not in "aeiou":
        replacement = word[:-1] + "ies"
    else:
        replacement = word + "s"
    words[-1] = replacement
    return " ".join(words)


_CORE_ROLES = frozenset(":arg{}".format(index) for index in range(5))

_ADJUNCT_ROLES = frozenset(
    {
        ":accompanier",
        ":beneficiary",
        ":cause",
        ":destination",
        ":direction",
        ":duration",
        ":extent",
        ":instrument",
        ":location",
        ":manner",
        ":medium",
        ":path",
        ":purpose",
        ":source",
        ":time",
        ":topic",
    }
)

_COMPOSITION_ROLES = _CORE_ROLES | _ADJUNCT_ROLES
