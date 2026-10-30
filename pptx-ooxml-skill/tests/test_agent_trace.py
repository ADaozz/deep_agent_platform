"""Scripted Agent execute traces for template-first PPTX creation, editing and fallback."""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location('pptx_trace_' + name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


office = load('office_pptx')
sample = load('sample_template')
importer = load('template_importer')
CLI = 'python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py'


class ExecuteLog:
    """Stand-in for the platform execute tool: one command, one result."""

    def __init__(self):
        self.calls = []

    def run(self, command, fn):
        result = fn()
        self.calls.append(dict(command=command, result=result, mutates=_mutates(command)))
        return result

    def text(self):
        return '\n'.join(item['command'] for item in self.calls)


def _mutates(command):
    return any(token in command for token in (' init ', ' apply-one ', ' restore '))


def accept_all(store):
    with store.locked() as (connection, state):
        for slide in store.view(connection, state)['manifest']['slides']:
            state['reviews'][slide['slide_id']] = dict(
                automatic_passed=True, status='pending', visual_review='pending',
                revision=state['revision'], content_notes='fixture',
                fingerprint=f'fp-{slide["slide_id"]}-{state["revision"]}')
        store.save_state(connection, state)
        connection.commit()


def apply(store, log, kind, **kwargs):
    state = store.status()
    request = dict(presentation_id='deck', operation_id=f'op-{state["revision"] + 1}',
        base_revision=state['revision'], operation=kind, **kwargs)
    command = f"{CLI} apply-one deck.pptx --request /dev/stdin <<'JSON'\n{__import__('json').dumps(request, ensure_ascii=False)}\nJSON"
    return log.run(command, lambda: store.apply_one(request))


def test_agent_trace_creates_pptx_from_template(tmp_path):
    log = ExecuteLog()
    sample.build(tmp_path)
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    listed = log.run(f'{CLI} list-templates', importer.list_templates)
    assert any(item['template_id'] == 'consulting-brief' for item in listed)
    log.run(f'{CLI} import-template --template brand.pptx --template-id brand --license-note "user file"',
        lambda: importer.import_template(tmp_path / 'template.pptx', 'brand', license_note='user file'))
    log.run(f'{CLI} init deck.pptx --presentation-id deck --template brand.pptx --use template_reference',
        lambda: store.init('deck', tmp_path / 'template.pptx', use='template_reference'))
    matched = log.run(f'{CLI} match-template deck.pptx --role cover',
        lambda: importer.match(store.status(), 'cover'))
    source = matched['slides'][0]['slide_id']
    accept_all(store)
    apply(store, log, 'slide.clone', slide_id='page-1', source_slide_id=source)
    apply(store, log, 'slot.fill', slide_id='page-1', slots={'title': '项目汇报', 'subtitle': '模板优先'})
    accept_all(store)
    apply(store, log, 'slide.clone', slide_id='page-2', source_slide_id='tpl-004')
    apply(store, log, 'presentation.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'})
    apply(store, log, 'element.adjust', slide_id='page-1', element={'id': 'title', 'dx_in': 0.2})
    apply(store, log, 'presentation.discard_prototypes')
    text = log.text()
    assert 'list-templates' in text and 'import-template' in text
    assert '--use template_reference' in text
    assert 'slide.clone' in text and 'slot.fill' in text
    assert 'presentation.recolor' in text and 'element.adjust' in text
    assert 'presentation.discard_prototypes' in text
    assert 'slide.add' not in text and ' plan ' not in text
    mutating = [item for item in log.calls if item['mutates']]
    assert all('apply-one' in item['command'] or ' init ' in item['command'] for item in mutating)
    assert store.status()['prototype_slides'] == []
    accept_all(store)
    first = log.run(f'{CLI} request-deck-review deck.pptx --base-revision {store.status()["revision"]}',
        lambda: store.request_deck_review(store.status()['revision']))
    apply(store, log, 'slot.fill', slide_id='page-2', slots={'title': '修订数据'})
    assert store.record_deck_review(first['deck_review']['request_id'], dict(status='passed', summary='old')) == dict(applied=False, reason='stale')
    accept_all(store)
    log.run(f'{CLI} finalize deck.pptx --base-revision {store.status()["revision"]}',
        lambda: store.finalize(store.status()['revision']))
    assert store.status()['finalized'] is True
    assert [slide['slide_id'] for slide in store.status()['manifest']['slides']] == ['page-1', 'page-2']


def test_agent_trace_edits_existing_pptx(tmp_path):
    log = ExecuteLog()
    sample.build(tmp_path)
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    log.run(f'{CLI} init deck.pptx --presentation-id deck --template brand.pptx --use edit_existing',
        lambda: store.init('deck', tmp_path / 'template.pptx', use='edit_existing'))
    apply(store, log, 'slot.fill', slide_id='tpl-001', slots={'title': '修订封面', 'subtitle': '编辑已有文件'})
    apply(store, log, 'element.adjust', slide_id='tpl-002', element={'id': 'title', 'dy_in': 0.1})
    apply(store, log, 'presentation.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'})
    text = log.text()
    assert '--use edit_existing' in text
    assert 'presentation.discard_prototypes' not in text
    assert 'slide.add' not in text
    assert [slide['slide_id'] for slide in store.status()['manifest']['slides']][:4] == ['tpl-001', 'tpl-002', 'tpl-003', 'tpl-004']
    accept_all(store)
    log.run(f'{CLI} finalize deck.pptx --base-revision {store.status()["revision"]}',
        lambda: store.finalize(store.status()['revision']))


def test_agent_trace_falls_back_only_without_a_template(tmp_path):
    log = ExecuteLog()
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    listed = log.run(f'{CLI} list-templates', lambda: importer.list_templates(tmp_path / 'empty-templates'))
    assert listed == []
    log.run(f'{CLI} init deck.pptx --presentation-id deck', lambda: store.init('deck'))
    plan = log.run(f'{CLI} plan --request plan.json',
        lambda: office.plan_request(store, dict(intent='cover', content={'blocks': [{'type': 'text', 'chars': 40}]})))
    accept_all(store)
    apply(store, log, 'slide.add', slide_id='s1', layout=plan.get('layout') or 'title_body')
    text = log.text()
    assert 'slide.add' in text and 'slide.clone' not in text
    assert '--template' not in text


def test_execute_is_one_document_mutation(tmp_path):
    log = ExecuteLog()
    sample.build(tmp_path)
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    log.run(f'{CLI} init deck.pptx --presentation-id deck --template brand.pptx --use template_reference',
        lambda: store.init('deck', tmp_path / 'template.pptx', use='template_reference'))
    accept_all(store)
    first = apply(store, log, 'slide.clone', slide_id='page-1', source_slide_id='tpl-001')
    second = apply(store, log, 'slot.fill', slide_id='page-1', slots={'title': '一页一改', 'subtitle': '原子操作'})
    assert first['revision'] + 1 == second['revision']
    apply_calls = [item for item in log.calls if 'apply-one' in item['command']]
    assert len(apply_calls) == 2
    assert all(item['command'].count('apply-one') == 1 for item in apply_calls)
