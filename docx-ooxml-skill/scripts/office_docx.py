#!/usr/bin/env python3
"""Conservative OOXML DOCX authoring / rendering CLI. Python 3.10+.

This implements only a safe, deliberately bounded subset of WordprocessingML.
It edits existing packages in place at a part level without re-saving them
through a high-level library. It is not a full Office compatibility validator.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
RELS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PIC = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
XMLNS = 'http://www.w3.org/XML/1998/namespace'
NS = {'w': W, 'r': R, 'a': A, 'wp': WP}
MAIN = 'word/document.xml'
DOC_RELS = 'word/_rels/document.xml.rels'
CT_FILE = '[Content_Types].xml'
MAX_UNCOMPRESSED = 250 * 1024 * 1024
MAX_PART = 80 * 1024 * 1024


def q(ns: str, name: str) -> str:
    return '{%s}%s' % (ns, name)


def parse_xml(data: bytes):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('DTD/ENTITY forbidden in XML')
    return etree.fromstring(data, parser=etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=False,
        load_dtd=False, remove_blank_text=False))


def xml_bytes(node):
    return etree.tostring(node, encoding='UTF-8', xml_declaration=True)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_parts(file):
    parts = {}
    total = 0
    with zipfile.ZipFile(file) as z:
        names = set()
        for info in z.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if (name in names or name.startswith('/') or '\\' in name
                    or '..' in Path(name).parts or name.startswith('../')):
                raise ValueError('Unsafe or duplicate package entry: ' + name)
            if info.file_size > MAX_PART:
                raise ValueError('Part too large: ' + name)
            total += info.file_size
            if total > MAX_UNCOMPRESSED or len(names) > 9999:
                raise ValueError('Package exceeds safe limits')
            names.add(name)
            parts[name] = z.read(info)
    return parts


def save_parts(parts, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.docx', delete=False) as tmp:
        tmpname = tmp.name
    try:
        with zipfile.ZipFile(tmpname, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in parts.items():
                z.writestr(name, data)
        os.replace(tmpname, path)
    finally:
        if os.path.exists(tmpname):
            os.unlink(tmpname)


def default_package():
    # XML is intentionally compact. No external assets or macros.
    content_types = f'''<Types xmlns="{CT}">
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
      <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
    </Types>'''
    root_rels = f'''<Relationships xmlns="{RELS}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'''
    doc_rels = f'''<Relationships xmlns="{RELS}"><Relationship Id="rId1" Type="{R}/styles" Target="styles.xml"/></Relationships>'''
    document = f'''<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>
      <w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>
    </w:body></w:document>'''
    styles = f'''<w:styles xmlns:w="{W}">
      <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:eastAsia="Noto Sans CJK SC" w:ascii="Liberation Sans" w:hAnsi="Liberation Sans"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
      <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:style>
      <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="320"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>
      <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="160"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
      <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="260" w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="27"/></w:rPr></w:style>
    </w:styles>'''
    return {
        CT_FILE: content_types.encode(), '_rels/.rels': root_rels.encode(),
        MAIN: document.encode(), DOC_RELS: doc_rels.encode(),
        'word/styles.xml': styles.encode(),
    }


def ptext(p):
    return ''.join(n.text or '' for n in p.iter(q(W, 't')))


def body_blocks(root):
    body = root.find(q(W, 'body'))
    if body is None:
        raise ValueError('No w:body')
    blocks = [b for b in body if b.tag in (q(W, 'p'), q(W, 'tbl'))]
    return body, blocks


def require_style(parts, style):
    if not style:
        return
    if 'word/styles.xml' not in parts:
        raise ValueError('Document does not define styles')
    tree = parse_xml(parts['word/styles.xml'])
    found = tree.xpath('boolean(//w:style[@w:styleId=$style])', namespaces=NS, style=style)
    if not found:
        raise ValueError('Undefined style: ' + style)


def rels_path_for(source):
    dirname, basename = posixpath.split(source)
    return posixpath.join(dirname, '_rels', basename + '.rels')


def validate_parts(parts):
    errors, warnings = [], []
    for mandatory in (CT_FILE, '_rels/.rels', MAIN):
        if mandatory not in parts:
            errors.append('Missing mandatory part ' + mandatory)
    parsed = {}
    for name, data in parts.items():
        if name.endswith(('.xml', '.rels')):
            try:
                parsed[name] = parse_xml(data)
            except Exception as exc:
                errors.append('%s: %s' % (name, exc))
    if errors:
        return {'ok': False, 'errors': errors, 'warnings': warnings}
    if parsed[MAIN].tag != q(W, 'document'):
        errors.append('Main part is not w:document')
    try:
        body, blocks = body_blocks(parsed[MAIN])
        sections = [n for n in body if n.tag == q(W, 'sectPr')]
        if sections and body[-1].tag != q(W, 'sectPr'):
            errors.append('Final w:sectPr must be final body child')
        if not blocks:
            warnings.append('Document contains no paragraphs or tables')
    except ValueError as exc:
        errors.append(str(exc))
        blocks = []
    for name, root in parsed.items():
        if not name.endswith('.rels'):
            continue
        ids = set()
        # Source part is parent of _rels/name.xml.rels
        if name == '_rels/.rels':
            base = ''
        else:
            base = name.rsplit('/_rels/', 1)[0]
        for rel in root:
            rid = rel.get('Id')
            if not rid or rid in ids:
                errors.append('Missing/duplicate relationship Id: ' + name)
            ids.add(rid)
            target = rel.get('Target', '')
            if rel.get('TargetMode') == 'External':
                warnings.append('External relationship found in ' + name + ': ' + target)
                continue
            if not target or target.startswith('/'):
                errors.append('Unsupported relationship target: ' + target)
                continue
            target_path = posixpath.normpath(posixpath.join(base, target.split('#')[0]))
            if target_path.startswith('../') or target_path not in parts:
                errors.append('Dangling relationship %s -> %s' % (name, target_path))
    # Verify relationship references from each relevant XML part.
    for name, root in parsed.items():
        if name.endswith('.rels'):
            continue
        refs = set()
        for el in root.iter():
            for attr, value in el.attrib.items():
                if attr.startswith('{' + R + '}') and value.startswith('rId'):
                    refs.add(value)
        if not refs:
            continue
        rel_path = rels_path_for(name)
        if rel_path not in parsed:
            errors.append('Missing relationships part for ' + name)
            continue
        ids = {rel.get('Id') for rel in parsed[rel_path]}
        for rid in sorted(refs - ids):
            errors.append('Unresolved reference %s in %s' % (rid, name))
    ct_root = parsed[CT_FILE]
    defaults = {n.get('Extension', '').lower() for n in ct_root if n.tag == q(CT, 'Default')}
    overrides = {n.get('PartName', '').lstrip('/') for n in ct_root if n.tag == q(CT, 'Override')}
    for name in parts:
        if name == CT_FILE:
            continue
        ext = name.rsplit('.', 1)[-1].lower()
        if ext not in defaults and name not in overrides:
            errors.append('Missing content type for ' + name)
    return {
        'ok': not errors, 'errors': errors, 'warnings': warnings,
        'stats': {'body_blocks': len(blocks), 'package_parts': len(parts),
                  'paragraphs': len(parsed[MAIN].findall('.//' + q(W, 'p'))),
                  'tables': len(parsed[MAIN].findall('.//' + q(W, 'tbl')))},
    }


def inspect(parts, source):
    root = parse_xml(parts[MAIN])
    _, blocks = body_blocks(root)
    paragraphs = list(root.iter(q(W, 'p')))
    rows = []
    for i, p in enumerate(paragraphs):
        style = p.find('./' + q(W, 'pPr') + '/' + q(W, 'pStyle'))
        rows.append({'index': i, 'text': ptext(p)[:500], 'block_id': p.get('{urn:deep-agent:docx:blocks:v1}id'),
                     'style': style.get(q(W, 'val')) if style is not None else None,
                     'has_field': bool(p.xpath('.//w:fldChar|.//w:instrText', namespaces=NS)),
                     'has_drawing': bool(p.xpath('.//w:drawing|.//w:object', namespaces=NS))})
    styles = []
    if 'word/styles.xml' in parts:
        styles = [x.get(q(W, 'styleId')) for x in parse_xml(parts['word/styles.xml']).iter(q(W, 'style'))]
    return {'file': str(source), 'sha256': sha(source),
            'body_blocks': [{'index': i, 'type': 'paragraph' if b.tag == q(W, 'p') else 'table',
                             'text': ptext(b)[:500], 'block_id': b.get('{urn:deep-agent:docx:blocks:v1}id')} for i, b in enumerate(blocks)],
            'paragraphs': rows, 'styles': styles,
            'parts': sorted(parts.keys()), 'validation': validate_parts(parts)}


def paragraph(root, index):
    ps = list(root.iter(q(W, 'p')))
    if not 0 <= index < len(ps):
        raise ValueError('paragraph_index out of range: %s' % index)
    return ps[index]


def replace_text_in_paragraph(p, old, new):
    if not old:
        raise ValueError('replace_text requires nonempty old')
    if p.xpath('.//w:fldChar|.//w:instrText|.//w:drawing|.//w:object', namespaces=NS):
        raise ValueError('Refusing text replace in paragraph with fields/drawings')
    text_nodes = list(p.iter(q(W, 't')))
    s = ''.join(n.text or '' for n in text_nodes)
    if s.count(old) != 1:
        raise ValueError('Expected exactly one target occurrence; found %d' % s.count(old))
    start = s.index(old)
    end = start + len(old)
    offset = 0
    for n in text_nodes:
        s0 = n.text or ''
        a, b = offset, offset + len(s0)
        offset = b
        if b <= start or a >= end:
            continue
        if a <= start < b:
            suffix = s0[end-a:] if end <= b else ''
            n.text = s0[:start-a] + new + suffix
        elif a < end <= b:
            n.text = s0[end-a:]
        else:
            n.text = ''
        if n.text and (n.text.startswith(' ') or n.text.endswith(' ')):
            n.set(q(XMLNS, 'space'), 'preserve')


def fragment_nodes(raw, parts):
    if not isinstance(raw, list) or not raw:
        raise ValueError('insert_blocks requires nonempty blocks XML list')
    parsed = []
    for text in raw:
        node = parse_xml(text.encode('utf-8'))
        if node.tag not in (q(W, 'p'), q(W, 'tbl')):
            raise ValueError('Only <w:p> and <w:tbl> may be inserted as body blocks')
        for el in node.iter():
            if el.tag in (q(W, 'sectPr'), q(W, 'altChunk'), q(W, 'object'), q(W, 'drawing'),
                          q(W, 'fldChar'), q(W, 'instrText')):
                raise ValueError('Complex relationships/fields require dedicated operations')
            if any(attr.startswith('{' + R + '}') for attr in el.attrib):
                raise ValueError('Relationship attributes require dedicated operations')
        for pstyle in node.iter(q(W, 'pStyle')):
            require_style(parts, pstyle.get(q(W, 'val')))
        parsed.append(node)
    return parsed


def add_image(parts, root, op, asset_root):
    from PIL import Image
    relative = Path(op['path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Image path must be relative to asset_root')
    asset = (asset_root / relative).resolve()
    if not asset.is_relative_to(asset_root.resolve()) or not asset.is_file():
        raise ValueError('Image is not a safe file under asset_root')
    data = asset.read_bytes()
    if len(data) > 12 * 1024 * 1024:
        raise ValueError('Image too large')
    with Image.open(asset) as img:
        img.verify()
    with Image.open(asset) as img:
        if img.width > 10000 or img.height > 10000 or img.width * img.height > 40_000_000:
            raise ValueError('Image dimensions too large')
        fmt = img.format.upper()
        pxw, pxh = img.size
    if fmt not in ('PNG', 'JPEG'):
        raise ValueError('Only PNG/JPEG supported for image insertion')
    ext = 'png' if fmt == 'PNG' else 'jpeg'
    h = hashlib.sha256(data).hexdigest()[:16]
    media = 'word/media/agent_%s.%s' % (h, ext)
    if media in parts and parts[media] != data:
        raise ValueError('Media collision')
    parts[media] = data
    rel_root = parse_xml(parts.get(DOC_RELS, f'<Relationships xmlns="{RELS}"/>'.encode()))
    existing = [int(m.group(1)) for x in rel_root for m in [re.fullmatch(r'rId(\d+)', x.get('Id', ''))] if m]
    rid = 'rId%d' % (max(existing, default=0) + 1)
    el = etree.SubElement(rel_root, q(RELS, 'Relationship'))
    el.set('Id', rid)
    el.set('Type', R + '/image')
    el.set('Target', 'media/' + Path(media).name)
    parts[DOC_RELS] = xml_bytes(rel_root)
    ct_root = parse_xml(parts[CT_FILE])
    if not any(x.get('Extension') == ext for x in ct_root):
        el = etree.SubElement(ct_root, q(CT, 'Default'))
        el.set('Extension', ext)
        el.set('ContentType', 'image/png' if ext == 'png' else 'image/jpeg')
        parts[CT_FILE] = xml_bytes(ct_root)
    width = float(op.get('width_cm', min(pxw / 96 * 2.54, 15)))
    if not 0.5 <= width <= 30:
        raise ValueError('width_cm must be between 0.5 and 30')
    cx = int(width / 2.54 * 914400)
    cy = int(cx * pxh / pxw)
    next_docpr = max([int(x.get('id', 0)) for x in root.iter(q(WP, 'docPr'))] or [0]) + 1
    # Relationships, drawing IDs and dimensions are generated by the engine, not by AI.
    block = etree.fromstring(f'''<w:p xmlns:w="{W}" xmlns:r="{R}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="{PIC}">
      <w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{next_docpr}" name="Generated image" descr="{etree_escape(op.get('alt','Inserted image'))}"/>
        <a:graphic><a:graphicData uri="{PIC}"><pic:pic>
          <pic:nvPicPr><pic:cNvPr id="0" name="{Path(media).name}"/><pic:cNvPicPr/></pic:nvPicPr>
          <pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
          <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
        </pic:pic></a:graphicData></a:graphic>
      </wp:inline></w:drawing></w:r>
    </w:p>'''.encode())
    return block


def etree_escape(s):
    from xml.sax.saxutils import escape
    return escape(str(s), {'"': '&quot;'})


def insert_after(body, after, nodes):
    blocks = [b for b in body if b.tag in (q(W, 'p'), q(W, 'tbl'))]
    if not isinstance(after, int) or after < -1 or after >= len(blocks):
        raise ValueError('after_body_index must be between -1 and %d' % (len(blocks)-1))
    if after == -1:
        ix = next((i for i, e in enumerate(body) if e.tag in (q(W, 'p'), q(W, 'tbl'))), 0)
    else:
        ix = list(body).index(blocks[after]) + 1
    for node in nodes:
        body.insert(ix, node)
        ix += 1


def replace_sdt_text(root, tag, new):
    matches = []
    for sdt in root.iter(q(W, 'sdt')):
        prop = sdt.find(q(W, 'sdtPr'))
        if prop is None: continue
        tags = prop.findall(q(W, 'tag'))
        if any(t.get(q(W, 'val')) == tag for t in tags):
            matches.append(sdt)
    if len(matches) != 1:
        raise ValueError('SDT tag must be unique; matched %d' % len(matches))
    content = matches[0].find(q(W, 'sdtContent'))
    if content is None:
        raise ValueError('SDT is missing sdtContent')
    if content.xpath('.//w:drawing|.//w:fldChar|.//w:object|.//w:tbl', namespaces=NS):
        raise ValueError('Cannot replace complex SDT; use a specific structural editor')
    text_nodes = list(content.iter(q(W, 't')))
    if not text_nodes:
        raise ValueError('Empty text SDT; use a dedicated content-control insertion operation')
    text_nodes[0].text = new
    if new.startswith(' ') or new.endswith(' '):
        text_nodes[0].set(q(XMLNS, 'space'), 'preserve')
    for node in text_nodes[1:]:
        node.text = ''


def set_para_format(p, op):
    ppr = p.find(q(W, 'pPr'))
    if ppr is None:
        ppr = etree.Element(q(W, 'pPr'))
        p.insert(0, ppr)
    # Element ordering is part of the OOXML schema. Preserve its common ordering.
    rank = {'pStyle': 0, 'keepNext': 1, 'keepLines': 2, 'pageBreakBefore': 3,
            'spacing': 10, 'ind': 11, 'jc': 12, 'rPr': 50}
    def ensure(name):
        el = ppr.find(q(W, name))
        if el is not None: return el
        el = etree.Element(q(W, name))
        ix = next((i for i, c in enumerate(ppr)
                   if rank.get(etree.QName(c).localname, 40) > rank[name]), len(ppr))
        ppr.insert(ix, el)
        return el
    if 'alignment' in op:
        if op['alignment'] not in ('left', 'right', 'center', 'both', 'distribute'):
            raise ValueError('Unsupported alignment')
        ensure('jc').set(q(W, 'val'), op['alignment'])
    for inp, attr in [('space_before_twips','before'), ('space_after_twips','after'),
                      ('line_twips','line')]:
        if inp in op:
            v = int(op[inp])
            if not 0 <= v <= 20000: raise ValueError(inp + ' out of range')
            spacing = ensure('spacing')
            spacing.set(q(W, attr), str(v))
            if inp == 'line_twips': spacing.set(q(W, 'lineRule'), 'exact')
    for inp, name in [('keep_next', 'keepNext'),
                      ('keep_lines','keepLines'),('page_break_before','pageBreakBefore')]:
        if inp in op:
            ensure(name).set(q(W, 'val'), '1' if op[inp] else '0')


def apply_plan(parts, plan, asset_root):
    operations = plan.get('operations')
    if not isinstance(operations, list) or not operations:
        raise ValueError('Plan must have a nonempty operations list')
    root = parse_xml(parts[MAIN])
    body, _ = body_blocks(root)
    for i, op in enumerate(operations):
        kind = op.get('op')
        try:
            if kind == 'replace_text':
                p = paragraph(root, op['paragraph_index'])
                if op.get('expected_paragraph') is not None and ptext(p) != op['expected_paragraph']:
                    raise ValueError('Expected paragraph guard mismatch')
                replace_text_in_paragraph(p, op['old'], op['new'])
            elif kind == 'replace_sdt_text':
                replace_sdt_text(root, op['tag'], op['new'])
            elif kind == 'set_paragraph_format':
                p = paragraph(root, op['paragraph_index'])
                if op.get('expected_text') is not None and ptext(p) != op['expected_text']:
                    raise ValueError('Expected paragraph guard mismatch')
                set_para_format(p, op)
            elif kind == 'insert_blocks':
                nodes = fragment_nodes(op['blocks'], parts)
                insert_after(body, op['after_body_index'], nodes)
            elif kind == 'set_paragraph_style':
                p = paragraph(root, op['paragraph_index'])
                if op.get('expected_text') is not None and ptext(p) != op['expected_text']:
                    raise ValueError('Expected paragraph guard mismatch')
                style = op['style']
                require_style(parts, style)
                pPr = p.find(q(W, 'pPr'))
                if pPr is None:
                    pPr = etree.Element(q(W, 'pPr'))
                    p.insert(0, pPr)
                pStyle = pPr.find(q(W, 'pStyle'))
                if pStyle is None:
                    pStyle = etree.Element(q(W, 'pStyle'))
                    pPr.insert(0, pStyle)
                pStyle.set(q(W, 'val'), style)
            elif kind == 'insert_image':
                node = add_image(parts, root, op, asset_root)
                insert_after(body, op['after_body_index'], [node])
            elif kind == 'set_page':
                sect = body.find(q(W, 'sectPr'))
                if sect is None:
                    sect = etree.SubElement(body, q(W, 'sectPr'))
                sz = sect.find(q(W, 'pgSz'))
                if sz is None:
                    sz = etree.Element(q(W, 'pgSz'))
                    sect.insert(0, sz)
                mar = sect.find(q(W, 'pgMar'))
                if mar is None:
                    mar = etree.SubElement(sect, q(W, 'pgMar'))
                for name, target in [('width_twips', sz), ('height_twips', sz),
                                     ('top_twips', mar), ('bottom_twips', mar),
                                     ('left_twips', mar), ('right_twips', mar)]:
                    if name in op:
                        v = int(op[name])
                        if not 100 <= v <= 50000:
                            raise ValueError('Twip value out of range: ' + name)
                        key = {'width_twips': 'w', 'height_twips': 'h'}.get(name, name.removesuffix('_twips'))
                        target.set(q(W, key), str(v))
            else:
                raise ValueError('Unsupported operation %r' % kind)
        except Exception as exc:
            raise ValueError('operations[%d] (%s): %s' % (i, kind, exc)) from exc
    parts[MAIN] = xml_bytes(root)
    return parts


def json_print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def run_render(docx, output, dpi=125):
    # LibreOffice is a layout engine; a PDF must be generated from the interim DOCX.
    import pymupdf as fitz
    docx = Path(docx).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob('page-*.png'):
        old.unlink()
    pdf = output / 'preview.pdf'
    if pdf.exists(): pdf.unlink()
    with tempfile.TemporaryDirectory(prefix='lo-profile-') as td:
        cmd = ['libreoffice', '-env:UserInstallation=' + Path(td).as_uri(),
               '--headless', '--convert-to', 'pdf', '--outdir', str(output), str(docx)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('LibreOffice rendering timed out after 120 seconds') from exc
        except FileNotFoundError as exc:
            raise RuntimeError('LibreOffice not installed') from exc
        if p.returncode != 0:
            raise RuntimeError('LibreOffice conversion failed: %s %s' % (p.stdout, p.stderr))
    generated = output / (docx.stem + '.pdf')
    if not generated.exists() or not generated.stat().st_size:
        raise RuntimeError('LibreOffice did not create a PDF')
    generated.replace(pdf)
    with fitz.open(pdf) as doc:
        if not 0 < len(doc) <= 200:
            raise ValueError('Unexpected page count')
        page_text = []
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            pix.save(output / ('page-%03d.png' % i))
            page_text.append(len(page.get_text().strip()))
        pages = len(doc)
    manifest = {'input': str(docx), 'sha256': sha(docx), 'page_count': pages,
                'page_text_lengths': page_text,
                'images': ['page-%03d.png' % i for i in range(1, pages + 1)],
                'pdf': 'preview.pdf', 'dpi': dpi}
    (output / 'render.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def run_compare(before, after, output):
    from PIL import Image, ImageChops, ImageOps, ImageDraw
    before, after, output = Path(before), Path(after), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    a = sorted(before.glob('page-*.png'))
    b = sorted(after.glob('page-*.png'))
    n = max(len(a), len(b))
    diffs = []
    for i in range(n):
        imgs = []
        for source in (a, b):
            if i < len(source):
                with Image.open(source[i]) as im:
                    temp = im.convert('RGB')
                    temp.thumbnail((800, 1100))
                    imgs.append(temp.copy())
            else:
                imgs.append(Image.new('RGB', (800, 1100), 'white'))
        h = max(x.height for x in imgs)
        sheet = Image.new('RGB', (imgs[0].width + imgs[1].width + 24, h + 38), 'white')
        sheet.paste(imgs[0], (0, 38))
        sheet.paste(imgs[1], (imgs[0].width + 24, 38))
        ImageDraw.Draw(sheet).text((4, 8), 'SOURCE TEMPLATE                    CANDIDATE', fill='black')
        name = 'compare-%03d.png' % (i + 1)
        sheet.save(output / name)
        diffs.append(name)
    report = {'source_pages': len(a), 'candidate_pages': len(b),
              'compare_images': diffs,
              'note': 'Visual side-by-side only. Content changes invalidate whole-page pixel equality; inspect all pages manually.'}
    (output / 'compare.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    x = sub.add_parser('init'); x.add_argument('--output', required=True); x.add_argument('--template'); x.add_argument('--document-id'); x.add_argument('--mode', choices=['normal','strict'], default='normal')
    x = sub.add_parser('import-template'); x.add_argument('--template', required=True); x.add_argument('--template-id'); x.add_argument('--output'); x.add_argument('--license-note', default='')
    x = sub.add_parser('inspect'); x.add_argument('docx'); x.add_argument('--output')
    x = sub.add_parser('discover'); x.add_argument('--templates', required=True); x.add_argument('--query', required=True); x.add_argument('--limit', type=int, default=15)
    x = sub.add_parser('apply'); x.add_argument('docx'); x.add_argument('plan'); x.add_argument('--output', required=True); x.add_argument('--asset-root', default='.')
    x = sub.add_parser('validate'); x.add_argument('docx'); x.add_argument('--output')
    x = sub.add_parser('render'); x.add_argument('docx'); x.add_argument('--output-dir', required=True); x.add_argument('--dpi', type=int, default=125)
    x = sub.add_parser('compare'); x.add_argument('--before', required=True); x.add_argument('--after', required=True); x.add_argument('--output-dir', required=True)
    x = sub.add_parser('review-template'); x.add_argument('--render-dir', required=True); x.add_argument('--output', required=True)
    x = sub.add_parser('finalize'); x.add_argument('docx'); x.add_argument('--render-dir'); x.add_argument('--review'); x.add_argument('--output'); x.add_argument('--base-revision', type=int)
    x = sub.add_parser('apply-one'); x.add_argument('docx'); x.add_argument('request'); x.add_argument('--asset-root', default='.')
    x = sub.add_parser('status'); x.add_argument('docx')
    x = sub.add_parser('restore'); x.add_argument('docx'); x.add_argument('--revision', type=int, required=True); x.add_argument('--base-revision', type=int, required=True); x.add_argument('--operation-id', required=True)
    x = sub.add_parser('checkpoint'); x.add_argument('docx'); x.add_argument('--final', action='store_true')
    x = sub.add_parser('review-page'); x.add_argument('docx'); x.add_argument('--revision', type=int, required=True); x.add_argument('--page', type=int, required=True); x.add_argument('--reviewer', choices=['human','vision_model'], required=True); x.add_argument('--notes', required=True); x.add_argument('--passed', action='store_true')
    x = sub.add_parser('publish'); x.add_argument('docx'); x.add_argument('--base-revision', type=int, required=True)
    a = p.parse_args(argv)
    try:
        if a.command == 'import-template':
            import importlib.util
            spec = importlib.util.spec_from_file_location('docx_template_importer', Path(__file__).with_name('template_importer.py'))
            importer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(importer)
            json_print(importer.import_template(a.template, a.template_id, a.output, a.license_note))
            return 0
        if a.command in ('apply-one', 'status', 'restore', 'checkpoint', 'review-page', 'publish') or (a.command == 'init' and a.document_id) or (a.command == 'finalize' and a.base_revision is not None):
            from atomic_docx import DocumentStore
            store = DocumentStore(a.output if a.command == 'init' else a.docx)
            if a.command == 'init': result = store.init(a.document_id, a.template, a.mode)
            elif a.command == 'apply-one': result = store.apply_one(json.loads(Path(a.request).read_text(encoding='utf-8')), a.asset_root)
            elif a.command == 'restore': result = store.restore(a.revision, a.base_revision, a.operation_id)
            elif a.command == 'checkpoint': result = store.checkpoint(a.final)
            elif a.command == 'review-page': result = store.visual_review(a.revision, a.page, a.passed, a.reviewer, a.notes)
            elif a.command in ('publish', 'finalize'): result = store.finalize(a.base_revision)
            else: result = store.status()
            json_print(result)
            return 0
        if a.command == 'init':
            if a.template:
                data = read_parts(a.template)
                result = validate_parts(data)
                if not result['ok']:
                    raise ValueError('Template failed structural checks: ' + str(result['errors']))
            else:
                data = default_package()
            save_parts(data, a.output)
            json_print({'output': a.output, 'sha256': sha(a.output), 'validation': validate_parts(data)})
        elif a.command == 'discover':
            folder = Path(a.templates)
            if not folder.is_dir(): raise ValueError('No template directory')
            results = []
            query = re.sub(r'\s+', '', a.query.lower())
            keys = [k for k in re.split(r'[,，;；\s]+', a.query.lower()) if len(k) >= 2]
            for f in sorted(folder.glob('*.docx'))[:100]:
                try:
                    parts = read_parts(f)
                    root = parse_xml(parts[MAIN])
                    content = ''.join(x.text or '' for x in root.iter(q(W,'t')))[:8000]
                    filename = f.stem.lower()
                    score = sum(4 for key in keys if key in filename) + sum(1 for key in keys if key in content.lower())
                    if query and query in filename: score += 4
                    results.append({'path': str(f), 'name': f.name, 'score': score,
                                    'sample': content[:200], 'sha256': sha(f)})
                except Exception as exc:
                    results.append({'path': str(f), 'name': f.name, 'score': -1, 'error': str(exc)})
            results.sort(key=lambda x: x['score'], reverse=True)
            json_print({'query': a.query, 'candidates': results[:a.limit],
                        'note': 'Lexical heuristic only; inspect/preview and confirm relevance before selecting. Explicit user template takes precedence.'})
        elif a.command == 'inspect':
            result = inspect(read_parts(a.docx), a.docx)
            if a.output: Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            json_print(result)
        elif a.command == 'apply':
            if Path(a.docx).resolve() == Path(a.output).resolve():
                raise ValueError('Output must not overwrite its source')
            parts = read_parts(a.docx)
            plan = json.loads(Path(a.plan).read_text(encoding='utf-8'))
            if plan.get('source_sha256') != sha(a.docx):
                raise ValueError('Plan source_sha256 does not match input: re-inspect and re-plan')
            updated = apply_plan(parts, plan, Path(a.asset_root).resolve())
            result = validate_parts(updated)
            if not result['ok']:
                raise ValueError('Patched package failed validation: %s' % result['errors'])
            save_parts(updated, a.output)
            json_print({'output': a.output, 'sha256': sha(a.output), 'validation': result})
        elif a.command == 'validate':
            result = validate_parts(read_parts(a.docx))
            result['input_sha256'] = sha(a.docx)
            if a.output: Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            json_print(result)
            if not result['ok']:
                return 2
        elif a.command == 'render':
            json_print(run_render(a.docx, a.output_dir, a.dpi))
        elif a.command == 'compare':
            json_print(run_compare(a.before, a.after, a.output_dir))
        elif a.command == 'review-template':
            render = json.loads((Path(a.render_dir) / 'render.json').read_text(encoding='utf-8'))
            template = {'sha256': render['sha256'], 'page_count': render['page_count'],
                        'reviewed_pages': [], 'passed': False, 'issues': ['REVIEW_REQUIRED'],
                        'notes': 'Inspect every page PNG, compare with source when available, then fill this file truthfully.'}
            Path(a.output).write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding='utf-8')
            json_print(template)
        elif a.command == 'finalize':
            if not all((a.render_dir, a.review, a.output)):
                raise ValueError('Use --base-revision for atomic finalize, or --render-dir --review --output for legacy finalization')
            val = validate_parts(read_parts(a.docx))
            if not val['ok']:
                raise ValueError('Invalid candidate: %s' % val['errors'])
            render = json.loads((Path(a.render_dir) / 'render.json').read_text(encoding='utf-8'))
            review = json.loads(Path(a.review).read_text(encoding='utf-8'))
            current_hash = sha(a.docx)
            if render['sha256'] != current_hash or review['sha256'] != current_hash:
                raise ValueError('Candidate changed since rendering or review')
            n = render['page_count']
            if review.get('page_count') != n or sorted(set(review.get('reviewed_pages', []))) != list(range(1, n + 1)):
                raise ValueError('Every page must be reviewed')
            if review.get('passed') is not True or review.get('issues'):
                raise ValueError('Visual review did not pass')
            if any(not (Path(a.render_dir) / name).is_file() for name in render['images']):
                raise ValueError('Missing rendered page(s)')
            if Path(a.docx).resolve() == Path(a.output).resolve():
                raise ValueError('Final output must be separate from candidate')
            Path(a.output).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(a.docx, a.output)
            json_print({'final': a.output, 'sha256': sha(a.output), 'page_count': n, 'passed': True})
        return 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, KeyError, ImportError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
