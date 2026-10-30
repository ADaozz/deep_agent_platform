"""Fixed regression sample: the 12-slide ai_agent_enterprise_workbench.pptx produced by the previous engine."""
import importlib.util
import io
from pathlib import Path
import shutil
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'office_pptx.py'
SAMPLE = Path(__file__).with_name('fixtures') / 'ai_agent_enterprise_workbench.pptx'
spec = importlib.util.spec_from_file_location('ppt_regression_engine', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from design import design_quality  # noqa: E402


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / 'ai_agent_enterprise_workbench.pptx'
    shutil.copy2(SAMPLE, path)
    store = m.PresentationStore(path)
    store.track_external('workbench')
    return store


def op(store, kind, sid, **kwargs):
    state = store.status()
    return store.apply_one(dict(presentation_id='workbench', operation_id=f'op-{state["revision"] + 1}', base_revision=state['revision'],
        slide_id=sid, operation=kind, **kwargs))


def connector_errors(store):
    result = {}
    for slide in store.status()['manifest']['slides']:
        errors = [(i['type'], i['element_id']) for i in design_quality.connector_issues(slide['elements']) if i['severity'] == 'error']
        if errors:
            result[slide['slide_id']] = errors
    return result


def test_sample_page_four_meaningless_connectors_are_the_only_connector_errors(sample):
    assert len(sample.status()['manifest']['slides']) == 12
    assert connector_errors(sample) == {'slide-004': [('connector_unbound', 'p4-w2a'), ('connector_unbound', 'p4-a2t'), ('connector_unbound', 'p4-s2r')]}


def test_local_repair_touches_only_page_four_and_is_reversible(sample):
    with zipfile.ZipFile(sample.path) as package:
        before = {name: package.read(name) for name in package.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml')}
    for element in ('p4-w2a', 'p4-a2t', 'p4-s2r', 'p4-t2s'):
        op(sample, 'element.remove', 'slide-004', element=dict(id=element))
    assert connector_errors(sample) == {}
    with zipfile.ZipFile(sample.path) as package:
        after = {name: package.read(name) for name in package.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml')}
    changed = sorted(name for name in before if before[name] != after.get(name))
    assert len(changed) == 1
    prs = m.Presentation(io.BytesIO(sample.path.read_bytes()))
    assert m.renderer.slide_identity(next(s for s in prs.slides if s.part.partname == '/' + changed[0])) == 'slide-004'
    m.validator.validate_package(sample.path.read_bytes())
    revision = sample.status()['revision']
    sample.restore(0, revision, 'restore-original')
    assert 'slide-004' in connector_errors(sample)


def test_page_four_rebuilt_as_a_native_diagram_has_bound_relations(sample):
    slide = next(s for s in sample.status()['manifest']['slides'] if s['slide_id'] == 'slide-004')
    for element in slide['elements']:
        if element['id'].startswith('p4-') and element['id'] not in ('p4-title', 'p4-side-bg', 'p4-side-title', 'p4-side-list', 'p4-side-note', 'p4-note', 'p4-foot'):
            op(sample, 'element.remove', 'slide-004', element=dict(id=element['id']))
    op(sample, 'diagram.add', 'slide-004', diagram=dict(id='arch', geometry=dict(x=.07, y=.215, w=.6, h=.575), preset='layered',
        nodes=[dict(id='web', rank=0, label='Web 交互层', detail='任务下达、过程可视、产物预览'), dict(id='agent', rank=1, label='Agent 执行层', detail='意图理解、规划、调度与重试'),
               dict(id='tool', rank=2, label='Tool / Skill 能力层', detail='文档、表格、演示、检索'), dict(id='sandbox', rank=2, label='Sandbox 执行环境', detail='隔离运行、资源限额'),
               dict(id='artifact', rank=3, label='Artifact 产物与版本层', detail='落盘、快照与回滚')],
        edges=[dict(source='web', target='agent', relation='calls'), dict(source='agent', target='tool', relation='calls'),
               dict(source='tool', target='sandbox', relation='flows_to'), dict(source='sandbox', target='artifact', relation='data_flow')]))
    assert connector_errors(sample) == {}
    slide = next(s for s in sample.status()['manifest']['slides'] if s['slide_id'] == 'slide-004')
    bound = {(e['connector']['start']['element_id'], e['connector']['finish']['element_id']) for e in slide['elements'] if e.get('diagram_role') == 'edge'}
    assert bound == {('arch.web', 'arch.agent'), ('arch.agent', 'arch.tool'), ('arch.tool', 'arch.sandbox'), ('arch.sandbox', 'arch.artifact')}
    if shutil.which('libreoffice'):
        review = sample.check_slide('slide-004', '五层节点与四条调用/数据关系逐条核对')['review']
        assert review['rendered'] and not [i for i in review['layout_issues'] if i['type'].startswith('connector')]
