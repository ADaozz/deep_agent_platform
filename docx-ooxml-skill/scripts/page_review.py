"""Conservative layout checks over an actual LibreOffice PDF, never visual QA.

Heuristics can detect geometry and text placement, not visual correctness.
Every new page therefore retains visual_review=pending until actual inspection.
"""
import hashlib
import json
import re
import shutil
import subprocess


def normalized(text):
    return re.sub(r'\s+', '', text)


def analyze_pages(parts, pdf, engine, final=False):
    o = engine
    root = o.parse_xml(parts[o.MAIN])
    ns = o.NS
    sections = root.findall('.//' + o.q(o.W, 'sectPr'))
    margin_abnormal = False
    for section in sections:
        size = section.find(o.q(o.W, 'pgSz'))
        margins = section.find(o.q(o.W, 'pgMar'))
        if size is None or margins is None:
            margin_abnormal = True
            continue
        width, height = (int(size.get(o.q(o.W, k), 0)) for k in ('w', 'h'))
        left, right, top, bottom = (int(margins.get(o.q(o.W, k), 0)) for k in ('left', 'right', 'top', 'bottom'))
        if min(left, right, top, bottom) < 180 or left + right >= width or top + bottom >= height:
            margin_abnormal = True
    texts = [normalized(p.get_text()) for p in pdf]
    headings = [o.ptext(p) for p in root.iter(o.q(o.W, 'p'))
        if p.xpath('./w:pPr/w:pStyle[starts-with(@w:val,"Heading") or starts-with(@w:val,"heading")]', namespaces=ns)]
    document_text = normalized(o.ptext(root))
    split_pages = set()
    uncertain_tables = 0
    for table in root.iter(o.q(o.W, 'tbl')):
        # A bounded table spanning pages needs a deliberate repair or review.
        samples = [normalized(o.ptext(p)) for p in table.iter(o.q(o.W, 'p')) if len(normalized(o.ptext(p))) >= 3 and document_text.count(normalized(o.ptext(p))) == 1]
        if not samples:
            uncertain_tables += 1
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
            if font.casefold() not in [f.strip() for f in matched.casefold().split(',')]:
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
        complete = final or i < len(pdf) - 1
        orphan = complete and bool(lines and any(normalized(h) and lines[-1].endswith(normalized(h)) for h in headings))
        checks = dict(ooxml_valid=True, render_success=True, content_overflow=overflow,
            table_split=i in split_pages, heading_orphan=orphan, margins_abnormal=margin_abnormal,
            fonts_missing=fonts_missing, visual_review='pending', warnings=['Table text cannot be uniquely mapped; inspect actual table layout'] if uncertain_tables else [])
        checks['automatic_passed'] = not (overflow or i in split_pages or orphan or margin_abnormal or fonts_missing)
        # Render fingerprint invalidates previous visual reviews after any reflow,
        # including images, font substitution, headers, and page renumbering.
        fingerprint = hashlib.sha256(page.get_pixmap(alpha=False).samples).hexdigest()
        complete = final or i < len(pdf) - 1
        pages.append(dict(page=i + 1, complete=complete, fingerprint=fingerprint, checks=checks,
            image='page-%03d.png' % (i + 1), status='failed' if not checks['automatic_passed'] else 'pending' if complete else 'writing'))
    return pages


def review_pages(docx, output, engine, final=False, render=True):
    import pymupdf as fitz
    if render:
        engine.run_render(docx, output)
    parts = engine.read_parts(docx)
    validation = engine.validate_parts(parts)
    if not validation['ok']:
        raise ValueError('Structural validation failed')
    with fitz.open(output / 'preview.pdf') as pdf:
        pages = analyze_pages(parts, pdf, engine, final)
    (output / 'checks.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2))
    return pages
