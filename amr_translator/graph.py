"""Deterministic graph layout and scope-reference views."""
from collections import defaultdict
from copy import copy, deepcopy
import heapq
import re

from penman.layout import Push

from .scope_boundaries import REPORTING, SCOPES


def graph_owned_layout(graph):
    concepts = {t.source: t.target for t in graph.instances()}
    nodes = set(concepts)
    edges = list(graph.edges())
    attrs = defaultdict(list)
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for t in graph.attributes():
        attrs[t.source].append((t.role, str(t.target)))
    for t in edges:
        outgoing[t.source].append(t)
        incoming[t.target].append(t)

    def rank(signatures):
        order = {v: i for i, v in enumerate(sorted(set(signatures.values())))}
        return {n: order[s] for n, s in signatures.items()}

    colors = rank({n: (concepts[n], n == graph.top, tuple(sorted(attrs[n]))) for n in nodes})
    for _ in nodes:
        refined = rank({n: (colors[n], tuple(sorted((t.role, colors[t.target]) for t in outgoing[n])),
                            tuple(sorted((t.role, colors[t.source]) for t in incoming[n]))) for n in nodes})
        if len(set(refined.values())) == len(set(colors.values())):
            colors = refined
            break
        colors = refined

    def role_key(role):
        match = re.fullmatch(r':(snt|op|ARG)(\d+)', role)
        if match:
            return {'snt': 0, 'op': 1, 'ARG': 3}[match[1]], int(match[2]), ''
        return (2 if role == ':condition' else 4), 0, role

    def edge_key(t, inverse=False):
        target = t.source if inverse else t.target
        return role_key(t.role), colors[target], concepts[target]

    def event(node):
        return bool(re.search(r'-\d\d$', concepts[node]))

    def explicit(edge):
        return bool((concepts[edge.source] in ('and', 'or', 'multi-sentence') and
                     re.fullmatch(r':(?:op|snt)[1-9]\d*', edge.role)) or
                    edge.role == ':condition' or
                    (edge.role == ':purpose' and event(edge.target)) or
                    (event(edge.source) and event(edge.target) and
                     re.fullmatch(r':ARG[1-9]\d*', edge.role)))

    reserved = {t.target for t in edges if explicit(t)} | {graph.top}
    object_nominals = {t.target for t in edges if event(t.source) and not event(t.target)
                       and re.fullmatch(r':ARG[1-9]\d*', t.role)
                       and (t.source in reserved or any(e.role == ':ARG0' for e in outgoing[t.source]))}
    operand_roots = {t.target for t in edges
                     if concepts[t.source] in ('and', 'or', 'multi-sentence')
                     and re.fullmatch(r':(?:op|snt)[1-9]\d*', t.role)}
    boundaries = {graph.top}
    for t in edges:
        if ((concepts[t.source] in ('and', 'or', 'multi-sentence') and
             re.fullmatch(r':(?:op|snt)[1-9]\d*', t.role)) or t.role == ':condition'):
            boundaries.add(t.target)
            boundaries.add(t.source)
        if concepts[t.source] in SCOPES and t.role == ':ARG1':
            boundaries.update((t.source, t.target))
    boundaries.update(n for n, values in attrs.items() if (':polarity', '-') in values)
    contexts = defaultdict(set)
    for boundary in boundaries:
        agenda, seen = [boundary], set()
        while agenda:
            node = agenda.pop()
            if node in seen or (node != boundary and node in boundaries):
                continue
            seen.add(node)
            contexts[node].add(boundary)
            agenda.extend(t.target for t in outgoing[node]
                          if not (t.role == ':ARG0' and event(t.target)))
    owned_objects = {t.target for t in edges if t.source in reserved | boundaries
                     and bool(contexts[t.source])
                     and event(t.source) and not event(t.target)
                     and re.fullmatch(r':ARG[1-9]\d*', t.role)
                     and concepts[t.target] not in ('and','or','multi-sentence','name')}
    negative_nodes = {n for n, values in attrs.items() if (':polarity', '-') in values}
    reporters = {t.target for t in edges if concepts[t.source] in REPORTING
                 and t.role == ':ARG0'}

    # Explicit statements/content are not introduced through a shared actor.
    # A predicate that describes an object attaches at that object in preference
    # to its subject. Event-valued ARG0 is a reference, not a content boundary.
    best = {graph.top: (0, 0, 0, 0, ())}
    parents = {}
    queue = [(0, 0, 0, 0, (), graph.top)]
    while queue:
        scope_steps, actor_steps, inverse_steps, length, path, node = heapq.heappop(queue)
        if best[node] != (scope_steps, actor_steps, inverse_steps, length, path):
            continue
        for inverse, rows in ((False, outgoing[node]), (True, incoming[node])):
            for t in sorted(rows, key=lambda t: edge_key(t, inverse)):
                target = t.source if inverse else t.target
                if target == node or target == graph.top:
                    continue
                reference = not inverse and t.role == ':ARG0' and event(target)
                secondary = inverse and t.role == ':ARG2' and any(
                    edge.role == ':ARG1' for edge in outgoing[target])
                actor_reference = (inverse and t.role == ':ARG0') or reference or secondary
                if (inverse and concepts[target] in ('have-rel-role-91', 'have-org-role-91')
                        and any(e.role == ':ARG0' and concepts[e.target] not in ('and', 'or')
                                for e in outgoing[target])):
                    roles = {e.role:e.target for e in outgoing[target]}
                    pronouns = {'i','you','he','she','it','we','they'}
                    focus = ':ARG0'
                    if (concepts.get(roles.get(':ARG0')) in pronouns
                            and ':ARG1' in roles and concepts[roles[':ARG1']] not in pronouns
                            and not event(roles[':ARG1'])):
                        # "your family": the pronoun refers back to you; it
                        # does not introduce family membership inside "if you
                        # die" when the family belongs to the main clause.
                        focus = ':ARG1'
                    actor_reference = t.role != focus
                # A reified modal can itself be reachable only through its
                # content. Penalize an alternate head entrance, never remove
                # the graph edge and disconnect the whole conditional clause.
                shared_entrance = bool(inverse and (len(contexts[node]) > 1 or
                    (contexts[target] and contexts[node] and contexts[target].isdisjoint(contexts[node]))))
                # In "image above them", :op1 points to the reference object;
                # it must not introduce "above" from an independently shared
                # "them" and bypass the image's OR/negative content boundary.
                lexical_op_reference = (inverse and re.fullmatch(r':op[1-9]\d*', t.role)
                                        and concepts[target] not in ('and', 'or', 'multi-sentence'))
                explicit_nominals = {edge.target for edge in outgoing[target]
                                     if edge.target in operand_roots and not event(edge.target)
                                     and concepts[edge.target] not in ('and', 'or', 'multi-sentence')}
                if concepts[target] == 'have-degree-91':
                    explicit_nominals.update(e.target for e in outgoing[target]
                                             if e.role == ':ARG2' and e.target in operand_roots)
                non_operand_entrance = inverse and bool(explicit_nominals) and node not in explicit_nominals
                scoped_object_reference = (inverse and target not in boundaries
                    and node not in explicit_nominals
                    and concepts[target] not in ('have-rel-role-91','have-org-role-91')
                    and any(
                        e.target != node and e.target in owned_objects
                        and concepts[e.target] not in ('and', 'or', 'multi-sentence', 'name')
                        and len(contexts[e.target]) == 1 and graph.top not in contexts[e.target]
                        and (len(contexts[node]) > 1 or contexts[node].isdisjoint(contexts[e.target]))
                        for e in outgoing[target]))
                # An independent predicate of the reporting agent is not a
                # description of a participant inside its reported content.
                # An explicitly owned content object still takes precedence.
                reporter_subject = {e.target for e in outgoing[target]
                                    if e.role == ':ARG0' and e.target in reporters}
                if inverse and target not in reserved and reporter_subject and not any(
                        e.target in owned_objects for e in outgoing[target] if e.role != ':ARG0'):
                    actor_reference = node not in reporter_subject
                negative_path = node in negative_nodes
                ancestor = node
                visited = set()
                while ancestor in parents and ancestor not in visited:
                    visited.add(ancestor)
                    ancestor = parents[ancestor][0]
                    negative_path |= ancestor in negative_nodes
                # Shared nominal arguments with a positive occurrence are
                # introduced there. Their independent descriptions must not
                # become true only within another clause's negation.
                negative_shared_nominal = (not inverse and negative_path and not event(target)
                    and bool(contexts[target] - negative_nodes)
                    and bool(contexts[target] & negative_nodes))
                if inverse and node == graph.top and not event(node):
                    # A nominal graph root can itself assert several relative
                    # predicates. A referenced object in one of their OR
                    # branches must not acquire those independent predicates.
                    actor_reference = shared_entrance = non_operand_entrance = False
                    scoped_object_reference = False
                key = (scope_steps + int(shared_entrance) + int(non_operand_entrance)
                       + int(bool(lexical_op_reference)) + int(scoped_object_reference)
                       + int(negative_shared_nominal), actor_steps + int(actor_reference)
                       + 2 * int((target in reserved and (inverse or not explicit(t)))
                                 or (inverse and target in object_nominals)),
                       inverse_steps + int(inverse), length + 1,
                       (*path, (int(inverse), *edge_key(t, inverse))))
                if target not in best or key < best[target]:
                    best[target] = key
                    parents[target] = (node, t)
                    heapq.heappush(queue, (*key, target))
    result = deepcopy(graph)
    # Stable record/metadata order also prevents repeated attributes from
    # becoming an accidental first/last-value decision after layout changes.
    result.triples = sorted(result.triples, key=lambda t: (
        colors[t[0]], role_key(t[1]),
        (0, colors[t[2]]) if t[2] in nodes and t[1] != ':instance' else (1, str(t[2]))))
    result.epidata = {t: [] for t in result.triples}
    for node, (_parent, triple) in parents.items():
        result.epidata[triple] = [Push(node)]
    return result


def scope_reference_view(builder, root):
    """Separate internal references and independently owned external clauses.

    Filter inverse descriptions for merge-boundary checks without changing
    the graph. Descriptions not owned by this context remain visible.
    """
    condition_ids = {e['id'] for e in builder._condition_edges(root)}
    agenda, seen, owned = [root], set(), set()
    while agenda:
        node = agenda.pop()
        if node in seen:
            continue
        seen.add(node)
        for edge in builder.outgoing.get(node, []):
            if node == root and edge['id'] in condition_ids:
                continue
            owned.add(edge['id'])
            agenda.append(edge['target'])
    # A shared bare participant can also be the subject of another explicit
    # statement. That statement is retained outside this scope, not swallowed
    # as a description of the participant. Require an explicit Boolean parent.
    contexts, current, visited = [], root, set()
    while current not in visited:
        visited.add(current)
        parents = builder.incoming.get(current, [])
        if len(parents) != 1:
            break
        parent = parents[0]['source']
        if builder.concepts.get(parent) not in ('and', 'or', 'multi-sentence'):
            break
        regions = builder.scope_branch_regions(parent)
        if current not in regions:
            break
        contexts.append((current, regions))
        current = parent

    def elsewhere(child):
        return any(child not in regions[active] and
                   any(child in nodes for target, nodes in regions.items() if target != active)
                   for active, regions in contexts)

    view = copy(builder)
    view.inverse_tree_children = {
        node: [(child, eid) for child, eid in children if eid not in owned and not elsewhere(child)]
        for node, children in builder.inverse_tree_children.items()
    }
    return view
