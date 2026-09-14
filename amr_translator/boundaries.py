"""Ordinary boundaries rules."""


def metadata_scope_issue(builder, roots):
    """Check term metadata recursively for scope that cannot be merged.

    Ordinary templates inspect their own nodes and metadata subtrees, not
    unrelated event arguments. Once inside metadata, every descendant counts:
    a quantity's nested degree or mode must not disappear into a positive term.
    """
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
            return 'nested_polarity_deferred'
        for edge in edges:
            structural = edge['id'] in builder.structural_ids
            if in_metadata or structural:
                agenda.append((edge['target'], in_metadata or structural))
    return None


def _edge_key(record):
    return (record.get('source_node', record.get('source')),
            ':' + record['role'].casefold().lstrip(':'),
            record.get('target_node', record.get('target')))


def merge_scope_issue(builder, records, boundaries):
    """Reject a pair that spans different unresolved content contexts."""
    if not boundaries:
        return None
    raw = {edge['id']: edge for rows in builder.outgoing.values() for edge in rows}
    keys = [_edge_key(record) for record in records]
    for boundary in boundaries:
        inside, outside = builder.merge_edge_contexts(boundary)
        boundary_edge = raw.get(boundary['edge'])
        if boundary_edge and _edge_key(boundary_edge) in keys:
            return 'cross_unresolved_scope'
        ownership = [(edge in inside, edge in outside) for edge in keys]
        if any(context[0] for context in ownership) and len(set(ownership)) > 1:
            return 'cross_unresolved_scope'
    return None
