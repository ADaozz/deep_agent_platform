"""Render the project-owned consulting deck with LibreOffice and save review images.

The pictures are pixmaps of a real PPTX conversion. They are not illustrations.
"""
import importlib.util
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def load(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('baseline_' + name, path)
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


def save_page(pdf, dest, index=0, zoom=1.0):
    with pymupdf.open(pdf) as document:
        page = document[index]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pixmap.save(dest)
        return pixmap.width, pixmap.height


def comparison(left, right, dest):
    first, second = Image.open(left).convert('RGB'), Image.open(right).convert('RGB')
    height = max(first.height, second.height)
    canvas = Image.new('RGB', (first.width + second.width + 24, height + 36), 'white')
    canvas.paste(first, (0, 36))
    canvas.paste(second, (first.width + 24, 36))
    drawing = ImageDraw.Draw(canvas)
    drawing.text((8, 8), 'before', fill='black')
    drawing.text((first.width + 32, 8), 'recolored', fill='black')
    canvas.save(dest)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    if shutil.which('libreoffice') is None:
        raise SystemExit('LibreOffice is required')
    office = load('office_pptx')
    sample = load('sample_template')
    evidence = Path(__file__).resolve().parents[1] / 'tests' / 'evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        sample.build(root)
        store = office.PresentationStore(root / 'deck.pptx')
        store.init('deck', root / 'template.pptx', use='template_reference')
        before_pdf = render_pdf(root / 'template.pptx', root / 'before')
        save_page(before_pdf, evidence / 'before.png', index=3)
        state = store.status()
        store.apply_one(dict(presentation_id='deck', operation_id='fill', base_revision=state['revision'],
            slide_id='tpl-001', operation='slot.fill', slots={'title': '项目汇报', 'subtitle': '沿用企业模板'}))
        state = store.status()
        filled_pdf = render_pdf(store.path, root / 'filled')
        save_page(filled_pdf, evidence / 'filled.png', index=0)
        store.apply_one(dict(presentation_id='deck', operation_id='color', base_revision=state['revision'],
            operation='presentation.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'}))
        colored_pdf = render_pdf(store.path, root / 'colored')
        save_page(colored_pdf, evidence / 'recolored.png', index=3)
        comparison(evidence / 'before.png', evidence / 'recolored.png', evidence / 'comparison.png')
        report = dict(renderer='LibreOffice Impress', source='templates/consulting-brief',
            operations=['slot.fill cover', 'presentation.recolor gray'],
            images={name: dict(sha256=sha(evidence / name), bytes=(evidence / name).stat().st_size)
                for name in ('before.png', 'filled.png', 'recolored.png', 'comparison.png')},
            deck_sha256=sha(store.path), recolor_complete=False,
            note='before and recolored are the data slide (blue accent bar versus gray). filled is the cover after slot.fill. Logo on the cover stays the original navy. All images are LibreOffice PDF exports.')
        (evidence / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(evidence)


if __name__ == '__main__':
    main()
