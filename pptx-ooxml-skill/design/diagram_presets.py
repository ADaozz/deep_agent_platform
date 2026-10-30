"""Relation semantics and layout presets for native flow / architecture diagrams.

A relation decides the arrowheads and stroke of an edge; the model chooses the
relation, never the arrow. Presets only hold spacing and token choices.
"""

RELATIONS = {
    'flows_to': dict(arrow='end', dash='solid', directed=True, back=False, description='sequence or hand-off from source to target'),
    'calls': dict(arrow='end', dash='solid', directed=True, back=False, description='source invokes target'),
    'data_flow': dict(arrow='end', dash='solid', directed=True, back=False, description='data moves from source to target'),
    'depends_on': dict(arrow='end', dash='dash', directed=True, back=False, description='source requires target'),
    'returns': dict(arrow='end', dash='dash', directed=True, back=True, description='result returns from source to an earlier target'),
    'bidirectional': dict(arrow='both', dash='solid', directed=False, back=False, description='both nodes exchange in both directions'),
    'association': dict(arrow='none', dash='dot', directed=False, back=False, description='related without direction'),
}

PRESETS = {
    'flow': dict(direction='LR', description='left-to-right process; uniform nodes, straight or elbow connectors',
        node_shape='rounded_rectangle', node_fill='card', node_line='none', emphasis_fill='accent',
        label_token='body_small', detail_token='caption', connector_token='muted', connector_width_pt=1.5,
        rank_gap_in=.45, node_gap_in=.3, min_node_w_in=1.1, max_node_w_in=2.6, min_node_h_in=.6, max_node_h_in=1.3,
        uniform=True),
    'layered': dict(direction='TB', description='top-to-bottom layers; wide nodes per rank, vertical connectors',
        node_shape='rounded_rectangle', node_fill='card', node_line='none', emphasis_fill='accent',
        label_token='body_small', detail_token='caption', connector_token='muted', connector_width_pt=1.5,
        rank_gap_in=.3, node_gap_in=.35, min_node_w_in=1.4, max_node_w_in=9.0, min_node_h_in=.48, max_node_h_in=1.0,
        uniform=False),
    'tree': dict(direction='TB', description='top-down hierarchy; uniform nodes, elbow connectors from parents',
        node_shape='rounded_rectangle', node_fill='card', node_line='none', emphasis_fill='accent',
        label_token='body_small', detail_token='caption', connector_token='muted', connector_width_pt=1.25,
        rank_gap_in=.4, node_gap_in=.25, min_node_w_in=1.1, max_node_w_in=2.8, min_node_h_in=.5, max_node_h_in=1.0,
        uniform=True),
}

LIMITS = dict(nodes=14, edges=24, label_chars=40, detail_chars=60, edge_label_chars=12, ranks=6, per_rank=6)


def relation(name):
    if name not in RELATIONS:
        raise ValueError(f'Unknown edge relation {name!r}; use one of: ' + ', '.join(sorted(RELATIONS)))
    return RELATIONS[name]


def preset(name):
    if name not in PRESETS:
        raise ValueError(f'Unknown diagram preset {name!r}; use one of: ' + ', '.join(sorted(PRESETS)))
    return PRESETS[name]


def catalog():
    return dict(relations={key: dict(value) for key, value in RELATIONS.items()},
        presets={key: dict(direction=value['direction'], description=value['description']) for key, value in PRESETS.items()},
        limits=dict(LIMITS))
