"""Ordinary boundaries rules."""
import re


def metadata_scope_issue(builder, roots, allowed_negative_nodes=(), *, metadata_only=True):
    """Check term metadata recursively, or all descendants for a whole scope.

    Ordinary templates inspect their own nodes and metadata subtrees, not
    unrelated event arguments. Once inside metadata, every descendant counts:
    a quantity's nested degree or mode must not disappear into a positive term.
    Only an explicitly allowed node's single literal minus is permitted.
    """
    allowed = frozenset(allowed_negative_nodes)
    agenda, seen = [(n, False) for n in roots], set()
    while agenda:
        node, in_metadata = agenda.pop()
        state = (node, in_metadata)
        if state in seen or node not in builder.concepts:
            continue
        seen.add(state)
        attrs = builder.attrs_by_source.get(node, [])
        edges = builder.outgoing.get(node, [])
        if any(r['role'].casefold() == ':mode' for r in attrs + edges):
            return 'mode_deferred'
        polarities = [r for r in attrs + edges if r['role'].casefold() == ':polarity']
        if polarities:
            literal_minus = (len(polarities) == 1 and str(polarities[0]['target']) == '-'
                             and not polarities[0].get('target_is_node'))
            if not literal_minus:
                return 'unsupported_polarity_deferred'
            if node not in allowed:
                return 'nested_polarity_deferred'
        for edge in edges:
            structural = edge['id'] in builder.structural_ids
            if not metadata_only or in_metadata or structural:
                agenda.append((edge['target'], in_metadata or structural))
    return None


def pure_positive_root_context_issue(builder, governor):
    """Accept a root scope or an unqualified op-only root AND branch."""
    parents = builder.incoming.get(governor, [])
    if not parents:
        return None if governor == builder.top else 'outer_scope_deferred'
    if len(parents) != 1:
        return 'outer_scope_deferred'
    edge = parents[0]
    parent = edge['source']
    if (parent != builder.top or builder.concepts.get(parent) != 'and'
            or not re.fullmatch(r':op[1-9]\d*', edge['role'].casefold())
            or builder.attrs_by_source.get(parent)
            or any(not re.fullmatch(r':op[1-9]\d*', e['role'].casefold())
                   for e in builder.outgoing.get(parent, []))):
        return 'outer_scope_deferred'
    return None


def _edge_key(record):
    return (record.get('source_node', record.get('source')),
            ':' + record['role'].casefold().lstrip(':'),
            record.get('target_node', record.get('target')))


def _reachable_edges(builder, start, blocked, records):
    """Follow AMR relations and declared inverse tree children, not all parents.

    Reentrancy alone must not replay an unrelated incoming event. Edge rather
    than entity membership distinguishes an inside property from an outside
    event even when both point to the same entity.
    """
    agenda, seen, reached = [start], set(), set()
    while agenda:
        node = agenda.pop()
        if node in seen:
            continue
        seen.add(node)
        for edge in builder.outgoing.get(node, []):
            if edge['id'] == blocked:
                continue
            reached.add(_edge_key(edge))
            if edge['target'] in builder.concepts:
                agenda.append(edge['target'])
        for child, edge_id in builder.inverse_tree_children.get(node, []):
            if edge_id == blocked:
                continue
            edge = records.get(edge_id)
            if edge:
                reached.add(_edge_key(edge))
                agenda.append(child)
    return reached


def merge_scope_issue(builder, records, boundaries):
    """Reject a pair that spans different unresolved content contexts."""
    if not boundaries:
        return None
    cache = getattr(builder, '_merge_scope_contexts', None)
    if cache is None:
        cache = builder._merge_scope_contexts = {}
    raw = {edge['id']: edge for rows in builder.outgoing.values() for edge in rows}
    keys = [_edge_key(record) for record in records]
    for boundary in boundaries:
        key = (boundary['edge'], boundary['content'])
        if key not in cache:
            context_factory = getattr(builder, 'merge_edge_contexts', None)
            if context_factory is None:
                inside = _reachable_edges(builder, boundary['content'], boundary['edge'], raw)
                outside = _reachable_edges(builder, builder.top, boundary['edge'], raw)
            else:
                inside, outside = context_factory(boundary)
            cache[key] = (inside, outside)
        inside, outside = cache[key]
        boundary_edge = raw.get(boundary['edge'])
        if boundary_edge and _edge_key(boundary_edge) in keys:
            return 'cross_unresolved_scope'
        ownership = [(edge in inside, edge in outside) for edge in keys]
        if any(context[0] for context in ownership) and len(set(ownership)) > 1:
            return 'cross_unresolved_scope'
    return None
