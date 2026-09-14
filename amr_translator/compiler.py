"""Fixed conversion pipeline and serializable translation diagnostics."""
from collections import Counter

import penman

from . import frame as frames, primitives
from .builder import GraphOwnedBuilder, nonfactive_boundaries, property_merge_boundaries
from .quantity_negation import COMPARATORS, plan_quantity_negations, apply_quantity_negations
from .reporting import reporting_boundaries
from .surfaces import repair_surfaces
from .templates import merge_registered_templates


def _render(builder):
    internal = frames._apply_role_surfaces(builder.build())
    repair_surfaces(builder, internal)
    return internal, frames.finalize_frame(internal)


def translate(raw_amr):
    """Return ``formula_ast`` and ``atoms`` for a nonempty raw AMR string."""
    return translate_with_audit(raw_amr)['frame']


def translate_with_audit(raw_amr):
    """Translate raw AMR and return the frame with rule decisions and provenance."""
    if not isinstance(raw_amr, str) or not raw_amr.strip():
        raise TypeError('raw_amr must be a non-empty str')
    graph = penman.decode(raw_amr, model=primitives.AMR_MODEL)
    primitives._require_unique_instance_nodes(graph)
    builder = GraphOwnedBuilder(graph)
    internal, repaired = _render(builder)
    quantity_negations, quantity_rejected = plan_quantity_negations(builder, internal, repaired)
    numeric = [row for row in quantity_negations
               if builder.concepts[row['node']].casefold() in COMPARATORS]
    if numeric:
        # Re-render accepted numeric metadata only if atom identities, unrelated
        # surfaces, and the Boolean formula are unchanged.
        candidate_builder = GraphOwnedBuilder(
            graph, quantity_surface_nodes=[row['node'] for row in numeric])
        candidate_internal, candidate_frame = _render(candidate_builder)
        candidate_plan, candidate_rejected = plan_quantity_negations(
            candidate_builder, candidate_internal, candidate_frame)
        old_atoms = {a['id']: a for a in repaired['atoms']}
        new_atoms = {a['id']: a for a in candidate_frame['atoms']}
        numeric_ids = {row['atom_id'] for row in numeric}
        stable = (candidate_frame['formula_ast'] == repaired['formula_ast']
                  and old_atoms.keys() == new_atoms.keys()
                  and {(r['node'], r['atom_id']) for r in candidate_plan}
                      == {(r['node'], r['atom_id']) for r in quantity_negations}
                  and all(old_atoms[key]['expression'] == new_atoms[key]['expression']
                          and (key in numeric_ids or old_atoms[key] == new_atoms[key])
                          for key in old_atoms)
                  and all(row['positive_quantity'] in new_atoms[row['atom_id']]['verbalization']
                          for row in numeric))
        if stable:
            builder, internal, repaired = candidate_builder, candidate_internal, candidate_frame
            quantity_negations, quantity_rejected = candidate_plan, candidate_rejected
        else:
            numeric_nodes = {row['node'] for row in numeric}
            quantity_negations = [row for row in quantity_negations if row['node'] not in numeric_nodes]
            quantity_rejected.extend(dict(row, reason='numeric_render_not_isolated') for row in numeric)
    quantity_counts = apply_quantity_negations(builder, internal, quantity_negations, quantity_rejected)
    repaired = frames.finalize_frame(internal)
    limitations = nonfactive_boundaries(builder)
    reporting_limits = reporting_boundaries(builder)
    property_limits = property_merge_boundaries(builder, limitations + reporting_limits)
    frame, merges, rejected = merge_registered_templates(
        builder, internal, repaired, limitations + reporting_limits + property_limits)
    limitations += reporting_limits + property_limits
    if any(a['expression'].get('scope_type') for a in frame['atoms']):
        raise RuntimeError('Unexpected nonfactual scope atom')
    consumed_atoms, consumed_edges = [], []
    for merge in merges:
        if (len(set(merge['nodes'])) != 3 or len(merge['edges']) != 2
                or len(merge['component_atom_ids']) != 2
                or len(merge['component_dyad_ids']) != 2):
            raise RuntimeError('Ordinary merge violates the two-dyad/three-node contract')
        consumed_atoms.extend(merge['component_atom_ids'])
        consumed_edges.extend(merge['source_graph_record_ids'])
    if any(n != 1 for n in Counter(consumed_atoms).values()) or any(
            n != 1 for n in Counter(consumed_edges).values()):
        raise RuntimeError('Ordinary merge reuses a component')
    # Normalize only after all rules and merges have finished using raw surfaces.
    for atom in frame['atoms']:
        atom['verbalization'] = frames.normalize_exact_match_surface(atom['verbalization'])
    for merge in merges:
        merge['surface'] = frames.normalize_exact_match_surface(merge['surface'])
    frames.validate_frame(frame)
    return {
        'frame': frame,
        'repairs': [dict(rule=r, node=n) for r, n in sorted(builder.events)],
        'warnings': [dict(rule=r, node=n) for r, n in sorted(builder.warnings)],
        'limitations': limitations,
        'merges': merges,
        'rejected_templates': rejected,
        'quantity_negations': quantity_negations,
        'quantity_negation_rejected': quantity_counts,
        'quantity_negation_rejected_details': quantity_rejected,
        'diagnostic_provenance': internal['atoms'],
    }
