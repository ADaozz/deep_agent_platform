"""Scripted Agent execute traces for template-first DOCX generation."""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
CLI = 'python /agent-skills/docx-ooxml-author/scripts/office_docx.py'


def load(name):
    spec = importlib.util.spec_from_file_location('docx_trace_' + name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


atomic = load('atomic_docx')
sample = load('sample_template')
importer = load('template_importer')


class ExecuteLog:
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


def apply(store, log, **operation):
    import json
    state = store.status()
    request = dict(document_id='report', operation_id=f'op-{state["revision"] + 1}',
        base_revision=state['revision'], operation=operation)
    command = f"{CLI} apply-one report.docx /dev/stdin --asset-root . <<'JSON'\n{json.dumps(request, ensure_ascii=False)}\nJSON"
    return log.run(command, lambda: store.apply_one(request))


def open_gate(store, monkeypatch):
    def render(docx, output, dpi=125):
        import pymupdf as fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'checkpoint placeholder')
        doc.save(output / 'preview.pdf')
        page.get_pixmap().save(output / 'page-001.png')
        doc.close()
    monkeypatch.setattr(atomic.o, 'run_render', render)
    if store.status()['gate'] != 'open':
        assert store.checkpoint()['gate'] == 'open'


def test_agent_trace_generates_docx_from_template(tmp_path, monkeypatch):
    log = ExecuteLog()
    sample.build(tmp_path)
    store = atomic.DocumentStore(tmp_path / 'report.docx')
    log.run(f'{CLI} import-template --template input.docx --template-id report --license-note "user file"',
        lambda: importer.import_template(tmp_path / 'template.docx', 'report', license_note='user file'))
    log.run(f'{CLI} init --document-id report --output report.docx --template input.docx',
        lambda: store.init('report', tmp_path / 'template.docx'))
    manifest = store.status()['template_manifest']
    source = next(item['block_id'] for item in manifest['sections'] if item['text'] == '项目背景')
    table = manifest['tables'][0]['block_id']
    apply(store, log, type='slot.fill', tag='project_title', text='季度业务报告')
    open_gate(store, monkeypatch)
    apply(store, log, type='section.clone', source_block_id=source, block_id='sec-next',
        heading='实施范围', paragraphs=['第一段沿用标题样式。', '第二段沿用正文样式。'])
    open_gate(store, monkeypatch)
    apply(store, log, type='block.clone', source_block_id=table, block_id='tbl-next',
        rows=[['事项', '状态'], ['范围', '已复制']])
    open_gate(store, monkeypatch)
    apply(store, log, type='document.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'})
    apply(store, log, type='part.replace_text', part='header', old='内部项目报告', new='季度项目报告')
    text = log.text()
    assert 'import-template' in text and '--template' in text
    assert 'section.clone' in text and 'block.clone' in text and 'slot.fill' in text
    assert 'document.recolor' in text
    assert 'append_paragraph' not in text and 'insert_block' not in text
    apply_calls = [item for item in log.calls if 'apply-one' in item['command']]
    assert len(apply_calls) == 5
    assert all(item['command'].count('apply-one') == 1 for item in apply_calls)
    before = store.path.read_bytes()
    revision = store.status()['revision']
    open_gate(store, monkeypatch)
    try:
        apply(store, log, type='section.clone', source_block_id=source, block_id='sec-bad', paragraphs=['only one'])
        raise AssertionError('expected rejection')
    except ValueError:
        log.calls.append(dict(command=f'{CLI} apply-one report.docx ../tmp/rejected.json',
            result=dict(status='rejected'), mutates=False))
    assert store.path.read_bytes() == before
    assert store.status()['revision'] == revision
