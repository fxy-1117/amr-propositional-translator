"""Translate raw PENMAN AMR into deterministic atoms and a Boolean formula."""
from .frame import (
    TranslatorContractError,
    canonical_atom_key,
    format_nli_prompt_surface,
    normalize_exact_match_surface,
    validate_frame,
)
from .compiler import translate, translate_with_audit
from .primitives import formula_ast_to_string
from .propbank import verify_resources

__all__ = [
    'translate', 'translate_with_audit', 'validate_frame', 'verify_resources',
    'canonical_atom_key', 'formula_ast_to_string', 'normalize_exact_match_surface',
    'format_nli_prompt_surface', 'TranslatorContractError',
]
