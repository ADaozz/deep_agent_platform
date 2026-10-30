"""Template sections, tables and theme colors stay inside the atomic DOCX protocol."""
import importlib.util
import shutil
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location('docx_' + name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


atomic = load('atomic_docx')
sample = load('sample_template')
importer = load('template_importer')


def apply(store, **operation):
    state = store.status()
    return store.apply_one(dict(document_id='report', operation_id=f'op-{state["revision"] + 1}',
        base_revision=state['revision'], operation=operation))


def part_text(path, name):
    with zipfile.ZipFile(path) as package:
        return package.read(name).decode()


def test_import_lists_sections_and_refuses_unlicensed_vendor(tmp_path):
    sample.build(tmp_path)
    manifest = importer.import_template(tmp_path / 'template.docx', 'business-report', license_note='external check')
    assert [item['text'] for item in manifest['sections']] == ['项目背景', '进度', '篇幅', '结尾']
    assert manifest['content_controls'][0]['tag'] == 'project_title'
    assert manifest['headers'] == ['word/header1.xml']
    with pytest.raises(ValueError, match='unlicensed'):
        importer.import_template(tmp_path / 'template.docx', output=Path(__file__).resolve().parents[1] / 'templates' / 'rejected' / 'manifest.json')


def test_section_table_fill_and_recolor_keep_structure(tmp_path):
    sample.build(tmp_path)
    store = atomic.DocumentStore(tmp_path / 'report.docx')
    store.init('report', tmp_path / 'template.docx')
    manifest = store.status()['template_manifest']
    source = manifest['sections'][0]['block_id']
    table = manifest['tables'][0]['block_id']
    filled = apply(store, type='slot.fill', tag='project_title', text='季度业务报告')
    assert store.apply_one(dict(document_id='report', operation_id=filled['operation_id'],
        base_revision=filled['revision'] - 1, operation=dict(type='slot.fill', tag='project_title', text='季度业务报告'))) == filled
    cloned = apply(store, type='section.clone', source_block_id=source, block_id='sec-next',
        heading='新增章节', paragraphs=['第一段沿用样式。', '第二段仍是模板段落。'])
    assert cloned['cloned_blocks'] == 3
    copied = apply(store, type='block.clone', source_block_id=table, block_id='tbl-next',
        rows=[['事项', '状态'], ['范围', '已复制'], ['数据', '新增一行']])
    assert 'tbl-next' in copied['changed_blocks']
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='checkpoint'):
        apply(store, type='section.clone', source_block_id=source, block_id='sec-bad', paragraphs=['only one'])
    assert store.path.read_bytes() == before
    colored = apply(store, type='document.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'})
    assert colored['recolor_complete'] is False
    assert any(item['color'] == 'FF00AA' for item in colored['unhandled'])
    apply(store, type='part.replace_text', part='header', old='内部项目报告', new='季度项目报告')
    document = part_text(store.path, 'word/document.xml')
    styles = part_text(store.path, 'word/styles.xml')
    assert '季度业务报告' in document and '新增章节' in document and '新增一行' in document and '项目背景' in document
    assert 'FF00AA' in document and document.count('F2F4F7') >= 4
    assert '707780' in styles and '1F4E79' not in styles
    assert '季度项目报告' in part_text(store.path, 'word/header1.xml')
    assert '机密' in part_text(store.path, 'word/footer1.xml')
    assert '季度项目报告' not in part_text(store.path, 'word/footer1.xml')
    atomic.o.validate_parts(atomic.o.read_parts(store.path))['ok']


@pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice Writer required')
def test_cloned_sections_reflow_to_at_least_eight_pages(tmp_path, monkeypatch):
    def render(docx, output, dpi=125):
        import pymupdf as fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'checkpoint placeholder')
        doc.save(output / 'preview.pdf')
        page.get_pixmap().save(output / 'page-001.png')
        doc.close()
    monkeypatch.setattr(atomic.o, 'run_render', render)
    sample.build(tmp_path)
    store = atomic.DocumentStore(tmp_path / 'report.docx')
    store.init('report', tmp_path / 'template.docx')
    source = next(item['block_id'] for item in store.status()['template_manifest']['sections'] if item['text'] == '篇幅')
    paragraphs = ['模板章节沿用标题样式、正文样式和页眉页脚，由排版引擎重新分页，而不是把每一页画成图片。' * 12] * 8
    for index in range(4):
        if store.status()['gate'] != 'open':
            assert store.checkpoint()['gate'] == 'open'
        apply(store, type='section.clone', source_block_id=source, block_id=f'long-{index}', heading=f'续篇 {index}', paragraphs=paragraphs)
    office = load('office_docx')
    output = tmp_path / 'render'
    manifest = office.run_render(store.path, output)
    assert manifest['page_count'] >= 8
