"""Render the project-owned report with LibreOffice Writer and save review images."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def load(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('docx_baseline_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_pdf(source, directory):
    source = Path(source).resolve()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lo-profile-') as profile:
        command = ['libreoffice', '-env:UserInstallation=' + Path(profile).as_uri(), '--headless',
            '--convert-to', 'pdf', '--outdir', str(directory), str(source)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stdout + completed.stderr)
    pdf = directory / (source.stem + '.pdf')
    if not pdf.is_file():
        raise RuntimeError('LibreOffice did not write a PDF')
    return pdf


def save_page(pdf, dest, index=0):
    with pymupdf.open(pdf) as document:
        pixmap = document[index].get_pixmap(matrix=pymupdf.Matrix(1.1, 1.1), alpha=False)
        pixmap.save(dest)
        return len(document)


def comparison(left, right, dest):
    first, second = Image.open(left).convert('RGB'), Image.open(right).convert('RGB')
    height = max(first.height, second.height)
    canvas = Image.new('RGB', (first.width + second.width + 24, height + 36), 'white')
    canvas.paste(first, (0, 36))
    canvas.paste(second, (first.width + 24, 36))
    ImageDraw.Draw(canvas).text((8, 8), 'before', fill='black')
    ImageDraw.Draw(canvas).text((first.width + 32, 8), 'recolored', fill='black')
    canvas.save(dest)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sheet(pages, dest, columns=4):
    images = [Image.open(path).convert('RGB') for path in pages]
    width = 200
    thumbs = [image.resize((width, round(image.height * width / image.width))) for image in images]
    height = max(thumb.height for thumb in thumbs)
    rows = (len(thumbs) + columns - 1) // columns
    canvas = Image.new('RGB', (columns * width + 24, rows * (height + 24) + 24), 'white')
    drawing = ImageDraw.Draw(canvas)
    for index, thumb in enumerate(thumbs):
        row, column = divmod(index, columns)
        x, y = 12 + column * width, 12 + row * (height + 24)
        drawing.text((x, y), f'{index + 1:02d}', fill='black')
        canvas.paste(thumb, (x, y + 14))
    canvas.save(dest)


def main():
    if shutil.which('libreoffice') is None:
        raise SystemExit('LibreOffice is required')
    atomic = load('atomic_docx')
    sample = load('sample_template')
    evidence = Path(__file__).resolve().parents[1] / 'tests' / 'evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sample.build(root)
        before_pdf = render_pdf(root / 'template.docx', root / 'before')
        save_page(before_pdf, evidence / 'before.png')
        store = atomic.DocumentStore(root / 'report.docx')
        store.init('report', root / 'template.docx')
        manifest = store.status()['template_manifest']
        source = next(item['block_id'] for item in manifest['sections'] if item['text'] == '项目背景')
        long_section = next(item['block_id'] for item in manifest['sections'] if item['text'] == '篇幅')
        state = store.status()
        store.apply_one(dict(document_id='report', operation_id='fill', base_revision=state['revision'],
            operation=dict(type='slot.fill', tag='project_title', text='正式项目报告')))
        state = store.status()
        store.apply_one(dict(document_id='report', operation_id='section', base_revision=state['revision'],
            operation=dict(type='section.clone', source_block_id=source, block_id='sec-new', heading='实施范围',
                paragraphs=['本章沿用模板标题和正文样式。', '表格与页眉仍来自原模板。'])))
        state = store.status()
        long_paragraphs = ['模板章节沿用标题样式、正文样式和页眉页脚，由排版引擎重新分页，而不是把每一页画成图片。' * 12] * 8
        store.apply_one(dict(document_id='report', operation_id='section-long', base_revision=state['revision'],
            operation=dict(type='section.clone', source_block_id=long_section, block_id='sec-long', heading='详细说明',
                paragraphs=long_paragraphs)))
        filled_pdf = render_pdf(store.path, root / 'filled')
        pages = save_page(filled_pdf, evidence / 'filled.png')
        state = store.status()
        result = store.apply_one(dict(document_id='report', operation_id='color', base_revision=state['revision'],
            operation=dict(type='document.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'})))
        colored_pdf = render_pdf(store.path, root / 'colored')
        save_page(colored_pdf, evidence / 'recolored.png')
        comparison(evidence / 'before.png', evidence / 'recolored.png', evidence / 'comparison.png')
        page_files = []
        thumbs = root / 'thumbs'
        thumbs.mkdir()
        with pymupdf.open(colored_pdf) as document:
            for index in range(len(document)):
                dest = thumbs / f'delivery-{index + 1:02d}.png'
                save_page(colored_pdf, dest, index=index)
                page_files.append(dest)
        _sheet(page_files, evidence / 'final-sheet.png')
        report = dict(renderer='LibreOffice Writer', source='templates/business-report', pages_after_fill=pages,
            recolor_complete=result.get('recolor_complete'),
            images={name: dict(sha256=sha(evidence / name), bytes=(evidence / name).stat().st_size)
                for name in ('before.png', 'filled.png', 'recolored.png', 'comparison.png', 'final-sheet.png')},
            note='Screenshots are LibreOffice PDF exports of the DOCX. Pagination is engine-driven, not inserted page breaks.')
        (evidence / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(evidence, 'pages', pages)


if __name__ == '__main__':
    main()
