"""Licensed project Word template: clone sections, recolor, real Writer pagination."""
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location('docx_biz_' + name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


atomic = load('atomic_docx')
sample = load('sample_template')
office = load('office_docx')
baseline = load('render_baseline')
GRAY = {'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'}


def apply(store, **operation):
    state = store.status()
    return store.apply_one(dict(document_id='report', operation_id=f'op-{state["revision"] + 1}',
        base_revision=state['revision'], operation=operation))


def maybe_checkpoint(store):
    if store.status()['gate'] != 'open':
        result = store.checkpoint()
        assert result['gate'] in ('open', 'visual_pending'), result
        if result['gate'] != 'open':
            for page in store.status()['pages']:
                if page['status'] != 'passed' and page['checks']['automatic_passed']:
                    store.visual_review(store.status()['revision'], page['page'], True, 'vision_model', '版式检查通过')


def part_text(path, name):
    with zipfile.ZipFile(path) as package:
        return package.read(name).decode()


def build_report(tmp_path, checkpoint=True):
    sample.build(tmp_path)
    store = atomic.DocumentStore(tmp_path / 'report.docx')
    store.init('report', tmp_path / 'template.docx')
    manifest = store.status()['template_manifest']
    background = next(item['block_id'] for item in manifest['sections'] if item['text'] == '项目背景')
    long_section = next(item['block_id'] for item in manifest['sections'] if item['text'] == '篇幅')
    table = manifest['tables'][0]['block_id']
    apply(store, type='slot.fill', tag='project_title', text='正式项目报告')
    paragraphs = ['模板章节沿用标题样式、正文样式和页眉页脚，由排版引擎重新分页，而不是把每一页画成图片。'] * 2
    long_paragraphs = ['模板章节沿用标题样式、正文样式和页眉页脚，由排版引擎重新分页，而不是把每一页画成图片。' * 12] * 8
    headings = ('实施范围', '里程碑', '组织分工', '验收标准')
    for index, heading in enumerate(headings):
        if checkpoint:
            maybe_checkpoint(store)
        apply(store, type='section.clone', source_block_id=background, block_id=f'sec-{index}',
            heading=heading, paragraphs=paragraphs)
    if checkpoint:
        maybe_checkpoint(store)
    apply(store, type='block.clone', source_block_id=table, block_id='tbl-next',
        rows=[['事项', '状态'], ['范围', '已复制'], ['数据', '新增一行']])
    if checkpoint:
        maybe_checkpoint(store)
    apply(store, type='section.clone', source_block_id=long_section, block_id='sec-long',
        heading='详细说明', paragraphs=long_paragraphs)
    colored = apply(store, type='document.recolor', palette=GRAY)
    apply(store, type='part.replace_text', part='header', old='内部项目报告', new='正式项目报告')
    return store, colored


def test_business_docx_reuses_styles_tables_and_header(tmp_path, monkeypatch):
    def render(docx, output, dpi=125):
        import pymupdf as fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'checkpoint placeholder')
        doc.save(output / 'preview.pdf')
        page.get_pixmap().save(output / 'page-001.png')
        doc.close()
    monkeypatch.setattr(atomic.o, 'run_render', render)
    store, colored = build_report(tmp_path)
    document = part_text(store.path, 'word/document.xml')
    styles = part_text(store.path, 'word/styles.xml')
    assert '正式项目报告' in document and '实施范围' in document and '详细说明' in document
    assert 'Heading1' in styles and 'Normal' in styles
    assert '新增一行' in document
    assert '正式项目报告' in part_text(store.path, 'word/header1.xml')
    assert '机密' in part_text(store.path, 'word/footer1.xml')
    assert colored['recolor_complete'] is False
    assert any(item['color'] == 'FF00AA' for item in colored['unhandled'])
    assert '707780' in styles
    atomic.o.validate_parts(atomic.o.read_parts(store.path))['ok']


@pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice Writer required')
def test_business_docx_libreoffice_evidence(tmp_path, monkeypatch):
    def render(docx, output, dpi=125):
        import pymupdf as fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'checkpoint placeholder')
        doc.save(output / 'preview.pdf')
        page.get_pixmap().save(output / 'page-001.png')
        doc.close()
    monkeypatch.setattr(atomic.o, 'run_render', render)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    sample.build(tmp_path / 'source')
    before_pdf = baseline.render_pdf(tmp_path / 'source' / 'template.docx', tmp_path / 'before')
    baseline.save_page(before_pdf, evidence / 'before.png')
    store, colored = build_report(tmp_path / 'work')
    filled_pdf = baseline.render_pdf(store.path, tmp_path / 'filled')
    pages = baseline.save_page(filled_pdf, evidence / 'filled.png')
    assert pages >= 8
    baseline.save_page(filled_pdf, evidence / 'recolored.png')
    page_files = []
    import pymupdf
    with pymupdf.open(filled_pdf) as document:
        for index in range(min(len(document), 12)):
            dest = evidence / f'delivery-{index + 1:02d}.png'
            baseline.save_page(filled_pdf, dest, index=index)
            page_files.append(dest)
    _contact_sheet(page_files, evidence / 'final-sheet.png')
    baseline.comparison(evidence / 'before.png', evidence / 'recolored.png', evidence / 'comparison.png')
    report = dict(
        renderer='LibreOffice Writer', source='templates/business-report',
        pages=pages, recolor_complete=colored.get('recolor_complete'),
        header='正式项目报告' in part_text(store.path, 'word/header1.xml'),
        images={name: dict(bytes=(evidence / name).stat().st_size)
            for name in ('before.png', 'filled.png', 'recolored.png', 'comparison.png', 'final-sheet.png')},
        note='Pagination comes from a LibreOffice PDF export. Page count is not manufactured with breaks.')
    (evidence / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert report['header'] is True
    assert (evidence / 'final-sheet.png').stat().st_size > 1000


def _contact_sheet(pages, dest, columns=4):
    from PIL import Image, ImageDraw
    images = [Image.open(path).convert('RGB') for path in pages]
    width = 200
    thumbs = [image.resize((width, round(image.height * width / image.width))) for image in images]
    height = max(thumb.height for thumb in thumbs)
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * width + 24, rows * (height + 24) + 24), 'white')
    draw = ImageDraw.Draw(sheet)
    for index, thumb in enumerate(thumbs):
        row, column = divmod(index, columns)
        x, y = 12 + column * width, 12 + row * (height + 24)
        draw.text((x, y), f'{index + 1:02d}', fill='black')
        sheet.paste(thumb, (x, y + 14))
    sheet.save(dest)
