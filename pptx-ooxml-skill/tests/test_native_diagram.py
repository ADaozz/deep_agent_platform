"""PR02: native diagrams bind every connector to the nodes it means, with relation-derived arrows."""
import importlib.util
import io
from pathlib import Path
import shutil

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'office_pptx.py'
spec = importlib.util.spec_from_file_location('ppt_diagram_engine', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from design import design_quality  # noqa: E402  (path set up by the engine)

ARCHITECTURE = dict(id='arch', geometry=dict(x=.06, y=.22, w=.6, h=.68), direction='TB', preset='layered',
    nodes=[dict(id='web', rank=0, label='Web 交互层', detail='任务下达、过程可视、产物预览'),
           dict(id='agent', rank=1, label='Agent 执行层', detail='意图理解、规划、调度与重试'),
           dict(id='tool', rank=2, label='Tool / Skill 能力层', detail='文档、表格、演示、检索'),
           dict(id='sandbox', rank=2, label='Sandbox 执行环境', detail='隔离运行、资源限额'),
           dict(id='artifact', rank=3, label='Artifact 产物与版本层', detail='落盘、快照、审查记录与回滚')],
    edges=[dict(id='request', source='web', target='agent', relation='calls'),
           dict(id='use-tool', source='agent', target='tool', relation='calls'),
           dict(id='run', source='tool', target='sandbox', relation='flows_to', label='执行'),
           dict(id='persist', source='sandbox', target='artifact', relation='data_flow')])


def op(store, kind, sid='s1', **kwargs):
    state = store.status()
    return store.apply_one(dict(presentation_id='deck', operation_id=f'op-{state["revision"] + 1}', base_revision=state['revision'],
        slide_id=sid, operation=kind, **kwargs))


def slide(store, sid='s1'):
    return next(item for item in store.status()['manifest']['slides'] if item['slide_id'] == sid)


@pytest.fixture
def deck(tmp_path):
    store = m.PresentationStore(tmp_path / 'deck.pptx')
    store.init('deck', profile='tech_dark')
    op(store, 'slide.add', layout='title_body')
    op(store, 'element.add_text', element=dict(id='title', anchor='title', text='五层结构，让每一次执行都有边界'))
    return store


def edges(view):
    return {e['id']: e for e in view['elements'] if e.get('diagram_role') == 'edge'}


def test_every_edge_is_bound_to_its_declared_nodes_with_relation_arrows(deck):
    op(deck, 'diagram.add', diagram=ARCHITECTURE)
    view = slide(deck)
    found = edges(view)
    assert set(found) == {'arch.request', 'arch.use-tool', 'arch.run', 'arch.persist'}
    for edge in found.values():
        connector = edge['connector']
        assert connector['start']['element_id'] == edge['source_element']
        assert connector['finish']['element_id'] == edge['target_element']
        assert connector['arrow_end'] and not connector['arrow_start']
    issues = design_quality.connector_issues(view['elements'])
    assert issues == [], issues
    # The stored route starts and ends on the node boundaries, not in empty space.
    nodes = {e['node_id']: e['geometry'] for e in view['elements'] if e.get('diagram_role') == 'node'}
    begin = found['arch.request']['route'][0]
    web = nodes['web']
    assert abs(begin[1] - (web['y'] + web['h'])) < .002
    assert any(e.get('diagram_role') == 'edge_label' and e['text'] == '执行' for e in view['elements'])


def test_relations_decide_arrows_and_dashes(deck):
    spec = dict(id='flow', geometry=dict(x=.06, y=.25, w=.88, h=.4), direction='LR', preset='flow',
        nodes=[dict(id='a', label='需求', rank=0), dict(id='b', label='设计', rank=1), dict(id='c', label='评审', rank=2)],
        edges=[dict(source='a', target='b', relation='flows_to'), dict(source='b', target='c', relation='bidirectional'),
               dict(source='c', target='a', relation='returns')])
    with pytest.raises(ValueError, match='cannot be drawn'):
        op(deck, 'diagram.add', diagram=spec)
    spec['edges'] = spec['edges'][:2]
    op(deck, 'diagram.add', diagram=spec)
    found = edges(slide(deck))
    both = found['flow.e-b-c']['connector']
    assert both['arrow_start'] and both['arrow_end']


@pytest.mark.parametrize('change,message', [
    (dict(edges=[dict(source='web', target='agent')]), 'needs a relation'),
    (dict(edges=[dict(source='web', target='ghost', relation='calls')]), 'two declared nodes'),
    (dict(edges=[dict(source='web', target='web', relation='calls')]), 'itself'),
    (dict(edges=[dict(source='web', target='agent', relation='calls'), dict(source='agent', target='web', relation='returns')]), 'parallel connectors'),
    (dict(edges=[dict(source='web', target='agent', relation='calls', points=[[0, 0], [1, 1]])]), 'coordinates are not accepted'),
    (dict(route=[[0, 0]]), 'computed, never supplied'),
    (dict(edges=[dict(source='web', target='agent', relation='teleports')]), 'Unknown edge relation'),
])
def test_invalid_relations_are_rejected(deck, change, message):
    spec = dict(ARCHITECTURE, **change)
    with pytest.raises(ValueError, match=message):
        op(deck, 'diagram.add', diagram=spec)


def test_router_refuses_edges_that_cross_nodes_or_text(deck):
    blocked = dict(ARCHITECTURE, nodes=[dict(node, rank={'web': 0, 'agent': 1, 'tool': 2, 'sandbox': 3, 'artifact': 4}[node['id']]) for node in ARCHITECTURE['nodes']],
        edges=[dict(source='agent', target='sandbox', relation='calls'), dict(source='agent', target='tool', relation='calls'),
               dict(source='tool', target='sandbox', relation='flows_to')])
    with pytest.raises(ValueError, match='crossing node tool'):
        op(deck, 'diagram.add', diagram=blocked)
    with pytest.raises(ValueError, match='cycle'):
        op(deck, 'diagram.add', diagram=dict(ARCHITECTURE, nodes=[{k: v for k, v in n.items() if k != 'rank'} for n in ARCHITECTURE['nodes']],
            edges=[dict(source='web', target='agent', relation='calls'), dict(source='agent', target='tool', relation='calls'),
                   dict(source='tool', target='web', relation='flows_to', id='loop')]))
    op(deck, 'element.add_text', element=dict(id='floating', geometry=dict(x=.06, y=.4, w=.6, h=.05), text='挡在连线上的文字'))
    with pytest.raises(ValueError, match='floating'):
        op(deck, 'diagram.add', diagram=ARCHITECTURE)


def test_update_preserves_ids_and_refuses_silent_relation_loss(deck):
    op(deck, 'diagram.add', diagram=ARCHITECTURE)
    with pytest.raises(ValueError, match='without an endpoint'):
        op(deck, 'diagram.update', diagram=dict(id='arch', remove_nodes=['artifact']))
    op(deck, 'diagram.update', diagram=dict(id='arch', update_nodes=[dict(id='web', label='Web 工作台')], add_edges=[dict(id='review', source='tool', target='artifact', relation='data_flow')]))
    view = slide(deck)
    labels = {e['id']: e.get('label') for e in view['elements'] if e.get('diagram_role') == 'node'}
    assert labels['arch.web'] == 'Web 工作台' and set(labels) == {f'arch.{n}' for n in ('web', 'agent', 'tool', 'sandbox', 'artifact')}
    assert 'arch.review' in edges(view)
    op(deck, 'diagram.update', diagram=dict(id='arch', remove_edges=['persist', 'review'], remove_nodes=['artifact']))
    assert 'arch.artifact' not in {e['id'] for e in slide(deck)['elements']}
    with pytest.raises(ValueError, match='diagram.update'):
        op(deck, 'element.remove', element=dict(id='arch.run'))


def test_moving_a_node_reroutes_its_connectors(deck):
    op(deck, 'diagram.add', diagram=ARCHITECTURE)
    before = edges(slide(deck))['arch.persist']['route']
    op(deck, 'element.adjust', element=dict(id='arch.artifact', dy_in=.1))
    view = slide(deck)
    after = edges(view)['arch.persist']
    assert after['route'] != before and after['connector']['finish']['element_id'] == 'arch.artifact'
    assert design_quality.connector_issues(view['elements']) == []
    with pytest.raises(ValueError, match='follow their nodes'):
        op(deck, 'element.adjust', element=dict(id='arch.persist', dx=.01))


def test_profile_change_keeps_bindings_and_node_styles(deck):
    op(deck, 'diagram.add', diagram=ARCHITECTURE)
    op(deck, 'element.set_style', element=dict(id='arch.agent', style=dict(fill='accent_soft')))
    op(deck, 'presentation.set_profile', sid=None, profile='corporate_blue')
    view = slide(deck)
    agent = next(e for e in view['elements'] if e['id'] == 'arch.agent')
    assert agent['style_overrides'] == {'fill': 'accent_soft'} and agent['fill'] == 'D7E6FA'
    assert design_quality.connector_issues(view['elements']) == []


def test_sample_page_four_defects_are_detected(deck):
    """Regression for ai_agent_enterprise_workbench.pptx page 4: decorative green stubs between cards."""
    op(deck, 'element.add_shape', element=dict(id='p4-web-card', shape='rounded_rectangle', layer='background', geometry=dict(x=.07, y=.216, w=.6, h=.096)))
    op(deck, 'element.add_shape', element=dict(id='p4-agent-card', shape='rounded_rectangle', layer='background', geometry=dict(x=.07, y=.336, w=.6, h=.096)))
    op(deck, 'element.add_shape', element=dict(id='p4-tool-card', shape='rounded_rectangle', layer='background', geometry=dict(x=.07, y=.453, w=.279, h=.207)))
    op(deck, 'element.add_shape', element=dict(id='p4-sandbox-card', shape='rounded_rectangle', layer='background', geometry=dict(x=.383, y=.453, w=.287, h=.207)))
    op(deck, 'element.add_shape', element=dict(id='p4-w2a', shape='line', fill_token='accent', layer='decoration', geometry=dict(x=.3697, y=.312, w=.003, h=.024)))
    op(deck, 'element.add_shape', element=dict(id='p4-t2s', shape='arrow', fill_token='accent', layer='decoration', geometry=dict(x=.3525, y=.5333, w=.0263, h=.0293)))
    op(deck, 'element.add_shape', element=dict(id='p4-stub', shape='line', fill_token='accent', layer='decoration', geometry=dict(x=.8, y=.85, w=.02, h=.001)))
    issues = {(i['type'], i['element_id']) for i in design_quality.connector_issues(slide(deck)['elements'])}
    assert ('connector_unbound', 'p4-w2a') in issues
    assert ('arrow_without_relation', 'p4-t2s') in issues
    assert ('connector_meaningless_short', 'p4-stub') in issues


@pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice Impress required')
def test_diagram_page_passes_formal_checks(deck):
    op(deck, 'diagram.add', diagram=ARCHITECTURE)
    result = deck.check_slide('s1', '五个节点、四条关系与标签已逐条核对')
    review = result['review']
    assert review['automatic_passed'], review['layout_issues']
    assert review['visual_integrity']['status'] == 'passed'
    prs = m.Presentation(io.BytesIO(deck.path.read_bytes()))
    kinds = {m.renderer.metadata(shape).get('diagram_role') for shape in prs.slides[0].shapes}
    assert {'node', 'edge', 'edge_label'} <= kinds
