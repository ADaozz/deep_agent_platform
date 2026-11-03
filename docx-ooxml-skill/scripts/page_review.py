"""Layout checks over a LibreOffice PDF. They do not decide visual quality.

A long table may cross pages. A table that asked to stay together and still
splits, overflow, a heading left at the bottom, missing fonts, or margins that
depart from the recorded plan are repair targets. Pending visual review is not
a failure.
"""
import hashlib
import json
import re
import shutil
import subprocess


def normalized(text):
    return re.sub(r'\s+', '', text)


def _kept_together(table, o):
    return any(row.find(o.q(o.W, 'trPr') + '/' + o.q(o.W, 'cantSplit')) is not None for row in table.findall(o.q(o.W, 'tr')))


def analyze_pages(parts, pdf, engine, expected_margins=None):
    o = engine
    root = o.parse_xml(parts[o.MAIN])
    ns = o.NS
    sections = root.findall('.//' + o.q(o.W, 'sectPr'))
    margin_abnormal = False
    margin_off_plan = False
    for section in sections:
        size = section.find(o.q(o.W, 'pgSz'))
        margins = section.find(o.q(o.W, 'pgMar'))
        if size is None or margins is None:
            margin_abnormal = True
            continue
        width, height = (int(size.get(o.q(o.W, k), 0)) for k in ('w', 'h'))
        sides = {k: int(margins.get(o.q(o.W, k), 0)) for k in ('left', 'right', 'top', 'bottom')}
        if min(sides.values()) < 180 or sides['left'] + sides['right'] >= width or sides['top'] + sides['bottom'] >= height:
            margin_abnormal = True
        if expected_margins and any(abs(sides[k] - expected_margins[k]) > 40 for k in sides):
            margin_off_plan = True
    texts = [normalized(p.get_text()) for p in pdf]
    headings = [o.ptext(p) for p in root.iter(o.q(o.W, 'p'))
        if p.xpath('./w:pPr/w:pStyle[starts-with(@w:val,"Heading") or starts-with(@w:val,"heading")]', namespaces=ns)]
    document_text = normalized(o.ptext(root))
    split_pages = set()
    for table in root.iter(o.q(o.W, 'tbl')):
        if not _kept_together(table, o):
            continue
        samples = [normalized(o.ptext(p)) for p in table.iter(o.q(o.W, 'p')) if len(normalized(o.ptext(p))) >= 3 and document_text.count(normalized(o.ptext(p))) == 1]
        hits = {i for i, text in enumerate(texts) if any(sample in text for sample in samples)}
        if len(hits) > 1:
            split_pages.update(hits)
    fonts_missing = []
    full_text = ''.join(texts)
    if re.search(r'[\u3400-\u9fff]', full_text):
        declared = set()
        for name in (o.MAIN, 'word/styles.xml'):
            if name in parts:
                declared.update(o.parse_xml(parts[name]).xpath('//w:rFonts/@w:eastAsia', namespaces=ns))
        for font in sorted(declared):
            if not shutil.which('fc-match'):
                fonts_missing.append('fontconfig unavailable: ' + font)
                continue
            matched = subprocess.run(['fc-match', '-f', '%{family}', font], capture_output=True, text=True, timeout=5).stdout
            if font.casefold() not in [item.strip() for item in matched.casefold().split(',')]:
                fonts_missing.append(font)
    pages = []
    for i, page in enumerate(pdf):
        rect = page.rect
        overflow = False
        for block in page.get_text('blocks'):
            x0, y0, x1, y1 = block[:4]
            if x0 < -1 or y0 < -1 or x1 > rect.width + 1 or y1 > rect.height + 1:
                overflow = True
        for image in page.get_image_info():
            x0, y0, x1, y1 = image['bbox']
            if x0 < -1 or y0 < -1 or x1 > rect.width + 1 or y1 > rect.height + 1:
                overflow = True
        lines = [normalized(line) for line in page.get_text().splitlines() if normalized(line)]
        orphan = bool(lines and any(normalized(h) and lines[-1].endswith(normalized(h)) for h in headings))
        checks = dict(ooxml_valid=True, render_success=True, content_overflow=overflow,
            kept_table_split=i in split_pages, heading_orphan=orphan, margins_abnormal=margin_abnormal,
            margins_off_plan=margin_off_plan, fonts_missing=fonts_missing)
        checks['automatic_passed'] = not (overflow or i in split_pages or orphan or margin_abnormal or margin_off_plan or fonts_missing)
        fingerprint = hashlib.sha256(page.get_pixmap(alpha=False).samples).hexdigest()
        pages.append(dict(page=i + 1, complete=True, fingerprint=fingerprint, checks=checks,
            image='page-%03d.png' % (i + 1), status='failed' if not checks['automatic_passed'] else 'checked'))
    return pages


def review_pages(docx, output, engine, expected_margins=None, render=True):
    import pymupdf as fitz
    if render:
        engine.run_render(docx, output)
    parts = engine.read_parts(docx)
    validation = engine.validate_parts(parts)
    if not validation['ok']:
        raise ValueError('Structural validation failed')
    with fitz.open(output / 'preview.pdf') as pdf:
        pages = analyze_pages(parts, pdf, engine, expected_margins)
    (output / 'checks.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2))
    return pages
