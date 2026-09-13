"""Pinned PropBank numbered-role lookup for parser-owned AMR predicates.

Only a predicate concept such as ``walk-01`` and a parser role such as
``ARG2`` are accepted.  Source sentence text is never an input.  Missing or
ambiguous metadata is represented explicitly so callers can conservatively
retain their prior surface instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple
import hashlib
import json
import re

from .primitives import _role_key


INDEX_SCHEMA = "propbank-numbered-role-index-v1"

INDEX_RELEASE = "v3.4.0"

INDEX_COMMIT = "4087fa9ab5c40907c34ff91a56acc2cab1670145"

INDEX_SUPPLEMENTAL_COMMIT = "c66e0ccf28b53f00051b187db83e937b5bee2e32"

INDEX_SUPPLEMENTAL_SHA256 = (
    "9645673f4ec60c2caa1daa3430f6efa4c21985474e2a66fefc77e124204a6cb0"
)

INDEX_SHA256 = "59f3e380bbead1e75e60bfedfb868c941e53d8e2c63b7ce3341342a8ec47dc7d"

INDEX_ROLESET_COUNT = 11304

INDEX_ROLE_COUNT = 28810

INDEX_AMBIGUOUS_ROLE_COUNT = 1

RESOURCE_DIR = Path(__file__).resolve().parent / "resources" / "propbank"

INDEX_PATH = RESOURCE_DIR / "roleset_index.json"

_CONCEPT_SENSE_RE = re.compile(r"^(?P<lemma>.+)-(?P<sense>\d{2,3})$")

_NUMBERED_ROLE_RE = re.compile(r"^arg(?P<number>\d+)$", re.IGNORECASE)

class PropBankRoleIndexError(ValueError):
    """Raised when the pinned role resource is absent or has drifted."""

@dataclass(frozen=True)

class PropBankRoleResolution:
    predicate_concept: str
    role: str
    status: str
    roleset_id: Optional[str] = None
    roleset_name: str = ""
    function: str = ""
    description: str = ""

    @property
    def exact(self) -> bool:
        return self.status == "exact"

    def as_metadata(self) -> Dict[str, Any]:
        return {
            "resource": "PropBank Frames",
            "resource_release": INDEX_RELEASE,
            "resource_commit": INDEX_COMMIT,
            "resource_supplemental_commit": INDEX_SUPPLEMENTAL_COMMIT,
            "resource_supplemental_sha256": INDEX_SUPPLEMENTAL_SHA256,
            "resource_index_sha256": INDEX_SHA256,
            "predicate_concept": self.predicate_concept,
            "role": self.role,
            "status": self.status,
            "roleset_id": self.roleset_id,
            "roleset_name": self.roleset_name,
            "function": self.function,
            "description": self.description,
        }

def concept_roleset_candidates(concept: Any) -> Tuple[str, ...]:
    normalized = str(concept or "").strip().casefold()
    match = _CONCEPT_SENSE_RE.fullmatch(normalized)
    if not match:
        return ()
    lemma = match.group("lemma")
    sense = match.group("sense")
    candidates = ["{}.{}".format(lemma, sense)]
    underscored = "{}.{}".format(lemma.replace("-", "_"), sense)
    if underscored not in candidates:
        candidates.append(underscored)
    return tuple(candidates)

@lru_cache(maxsize=1)

def load_propbank_role_index() -> Mapping[str, Any]:
    try:
        raw = INDEX_PATH.read_bytes()
    except OSError as exc:
        raise PropBankRoleIndexError(
            "Pinned PropBank role index is unavailable: {}".format(INDEX_PATH)
        ) from exc
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != INDEX_SHA256:
        raise PropBankRoleIndexError(
            "Pinned PropBank role index hash mismatch: expected={}, actual={}".format(
                INDEX_SHA256, actual_sha256
            )
        )
    payload = json.loads(raw.decode("utf-8"))
    expected = {
        "schema": INDEX_SCHEMA,
        "source_release": INDEX_RELEASE,
        "source_commit": INDEX_COMMIT,
        "supplemental_commit": INDEX_SUPPLEMENTAL_COMMIT,
        "supplemental_sha256": INDEX_SUPPLEMENTAL_SHA256,
        "roleset_count": INDEX_ROLESET_COUNT,
        "role_count": INDEX_ROLE_COUNT,
        "ambiguous_role_count": INDEX_AMBIGUOUS_ROLE_COUNT,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise PropBankRoleIndexError(
                "Pinned PropBank role index {} mismatch".format(field)
            )
    rolesets = payload.get("rolesets")
    if not isinstance(rolesets, dict) or len(rolesets) != INDEX_ROLESET_COUNT:
        raise PropBankRoleIndexError("Pinned PropBank roleset map is invalid")
    return payload

def resolve_numbered_role(
    predicate_concept: Any,
    role: Any,
) -> PropBankRoleResolution:
    concept = str(predicate_concept or "").strip()
    role_key = _role_key(role)
    if not _NUMBERED_ROLE_RE.fullmatch(role_key):
        return PropBankRoleResolution(concept, role_key, "not-numbered-role")
    candidates = concept_roleset_candidates(concept)
    if not candidates:
        return PropBankRoleResolution(concept, role_key, "not-sense-predicate")
    rolesets = load_propbank_role_index()["rolesets"]
    roleset_id = next((item for item in candidates if item in rolesets), None)
    if roleset_id is None:
        return PropBankRoleResolution(concept, role_key, "missing-roleset")
    roleset = rolesets[roleset_id]
    role_entry = roleset.get("roles", {}).get(role_key)
    if not isinstance(role_entry, Mapping):
        return PropBankRoleResolution(
            concept,
            role_key,
            "missing-role",
            roleset_id=roleset_id,
            roleset_name=str(roleset.get("name", "")),
        )
    role_candidates = role_entry.get("candidates", [])
    if not isinstance(role_candidates, list) or len(role_candidates) != 1:
        return PropBankRoleResolution(
            concept,
            role_key,
            "ambiguous-role",
            roleset_id=roleset_id,
            roleset_name=str(roleset.get("name", "")),
        )
    selected = role_candidates[0]
    return PropBankRoleResolution(
        concept,
        role_key,
        "exact",
        roleset_id=roleset_id,
        roleset_name=str(roleset.get("name", "")),
        function=str(selected.get("function", "")).strip().upper(),
        description=str(selected.get("description", "")).strip(),
    )

_RESOURCE_HASHES = {
    "AMR-UMR-91-rolesets.xml": (
        "9645673f4ec60c2caa1daa3430f6efa4c21985474e2a66fefc77e124204a6cb0"
    ),
    "LICENSE": (
        "1fdd47d0526982b4e71694ca4faf1de06ef8957faec92e3d023a26d187ac9cd3"
    ),
    "README.md": (
        "e43e421ecbf8901a9f4b5357b0882235ca12d36dd0d3d3133633a3c282b71e53"
    ),
    "roleset_index.json": (
        "59f3e380bbead1e75e60bfedfb868c941e53d8e2c63b7ce3341342a8ec47dc7d"
    ),
}

@lru_cache(maxsize=1)

def verify_resources() -> Dict[str, str]:
    """Verify the bundled PropBank inventory and its provenance files."""
    resources = {}
    for name, expected in sorted(_RESOURCE_HASHES.items()):
        path = RESOURCE_DIR / name
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise PropBankRoleIndexError("Pinned translator resource is missing: %s" % path) from exc
        if actual != expected:
            raise PropBankRoleIndexError("Pinned translator resource hash mismatch: %s" % path)
        resources[name] = actual
    load_propbank_role_index()
    return resources
