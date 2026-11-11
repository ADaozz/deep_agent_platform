"""Native academic blocks for atomic DOCX edits: equations, charts, captions,
cross-references and a table of contents.

Numbers and cross-reference texts are Word fields (SEQ, REF, PAGEREF, TOC) whose
cached results are recomputed after every operation, so the preview is correct
and Word can still update them itself (settings.xml asks it to on open).
"""
import importlib.util
from pathlib import Path
import re

M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
SEQ_INSTR = re.compile(r'^\s*SEQ\s+(\S+)')
REF_INSTR = re.compile(r'^\s*(?:REF|PAGEREF)\s+(\S+)')
LABEL = re.compile(r'^[^\s{}\\"]{1,10}$')
SETTINGS = 'word/settings.xml'
# Private markers live in the block namespace, which mark_blocks declares mc:Ignorable.
BLOCK_NS = 'urn:deep-agent:docx:blocks:v1'
TOC_LEVELS = '{%s}toc-levels' % BLOCK_NS
TOC_TITLE = '{%s}toc-title' % BLOCK_NS

STYLE_XML = {
    'Heading3': '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="220" w:after="100"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>',
    'Caption': '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="60" w:after="200"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr></w:style>',
    'TOCHeading': '<w:style w:type="paragraph" w:styleId="TOCHeading"><w:name w:val="TOC Heading"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>',
    'TOC1': '<w:style w:type="paragraph" w:styleId="TOC1"><w:name w:val="toc 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/></w:rPr></w:style>',
    'TOC2': '<w:style w:type="paragraph" w:styleId="TOC2"><w:name w:val="toc 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:spacing w:after="60"/><w:ind w:left="440"/></w:pPr></w:style>',
    'TOC3': '<w:style w:type="paragraph" w:styleId="TOC3"><w:name w:val="toc 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:spacing w:after="60"/><w:ind w:left="880"/></w:pPr></w:style>',
}


def _sibling(name):
    spec = importlib.util.spec_from_file_location('docx_skill_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_styles(parts, o, names):
    """Add the given built-in style definitions when the document lacks them."""
    styles = o.parse_xml(parts['word/styles.xml'])
    present = {node.get(o.q(o.W, 'styleId')) for node in styles.iter(o.q(o.W, 'style'))}
    added = False
    for name in names:
        if name not in present:
            styles.append(o.etree.fromstring(STYLE_XML[name].replace('<w:style ', f'<w:style xmlns:w="{o.W}" ', 1)))
            added = True
    if added:
        parts['word/styles.xml'] = o.xml_bytes(styles)


def ensure_update_fields(parts, o):
    """Ask Word to refresh fields (TOC page numbers, SEQ, REF) when the file is opened."""
    if SETTINGS in parts:
        settings = o.parse_xml(parts[SETTINGS])
    else:
        settings = o.etree.Element(o.q(o.W, 'settings'), nsmap={'w': o.W})
        rels = o.parse_xml(parts[o.DOC_RELS])
        used = {node.get('Id') for node in rels}
        rid = next('rIdSettings%d' % i for i in range(1, 1000) if 'rIdSettings%d' % i not in used)
        o.etree.SubElement(rels, o.q(o.RELS, 'Relationship'), Id=rid,
            Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings', Target='settings.xml')
        parts[o.DOC_RELS] = o.xml_bytes(rels)
        types = o.parse_xml(parts[o.CT_FILE])
        if not any(node.get('PartName') == '/' + SETTINGS for node in types):
            o.etree.SubElement(types, o.q(o.CT, 'Override'), PartName='/' + SETTINGS,
                ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml')
            parts[o.CT_FILE] = o.xml_bytes(types)
    if settings.find(o.q(o.W, 'updateFields')) is None:
        o.etree.SubElement(settings, o.q(o.W, 'updateFields')).set(o.q(o.W, 'val'), 'true')
    parts[SETTINGS] = o.xml_bytes(settings)


def _text_width(root, o):
    section = root.find('.//' + o.q(o.W, 'sectPr'))
    size = section.find(o.q(o.W, 'pgSz')) if section is not None else None
    margin = section.find(o.q(o.W, 'pgMar')) if section is not None else None
    width = int(size.get(o.q(o.W, 'w'), 11906)) if size is not None else 11906
    left = int(margin.get(o.q(o.W, 'left'), 1440)) if margin is not None else 1440
    right = int(margin.get(o.q(o.W, 'right'), 1440)) if margin is not None else 1440
    return max(2000, width - left - right)


def _run(o, text):
    run = o.etree.Element(o.q(o.W, 'r'))
    node = o.etree.SubElement(run, o.q(o.W, 't'))
    node.text = text
    node.set(o.q(o.XMLNS, 'space'), 'preserve')
    return run


def _field(o, instr, cached):
    field = o.etree.Element(o.q(o.W, 'fldSimple'))
    field.set(o.q(o.W, 'instr'), f' {instr} ')
    field.append(_run(o, cached))
    return field


def bookmark_name(identity, prefix='_Ref_'):
    # Word bookmark names: letters, digits and underscores, at most 40 characters.
    return (prefix + re.sub(r'[^0-9A-Za-z_]', '_', identity))[:40]


def _bookmarks(root, o):
    return {node.get(o.q(o.W, 'name')): node for node in root.iter(o.q(o.W, 'bookmarkStart'))}


def _wrap_bookmark(o, root, name, nodes):
    if name in _bookmarks(root, o):
        raise ValueError('Duplicate cross-reference id: ' + name)
    serial = str(max([int(n.get(o.q(o.W, 'id'), 0)) for n in root.iter(o.q(o.W, 'bookmarkStart'))] or [0]) + 1)
    start = o.etree.Element(o.q(o.W, 'bookmarkStart'))
    start.set(o.q(o.W, 'id'), serial)
    start.set(o.q(o.W, 'name'), name)
    end = o.etree.Element(o.q(o.W, 'bookmarkEnd'))
    end.set(o.q(o.W, 'id'), serial)
    return [start, *nodes, end]


def caption(parts, root, op, o):
    label, text = op.get('label', '图'), op.get('text', '')
    if not isinstance(label, str) or not LABEL.fullmatch(label):
        raise ValueError('Caption label must be 1-10 characters without spaces, such as 图 or 表')
    if not isinstance(text, str) or len(text) > 300 or '\n' in text:
        raise ValueError('Caption text must be one line of at most 300 characters')
    ensure_styles(parts, o, ['Caption'])
    ensure_update_fields(parts, o)
    node = o.etree.Element(o.q(o.W, 'p'))
    prop = o.etree.SubElement(node, o.q(o.W, 'pPr'))
    o.etree.SubElement(prop, o.q(o.W, 'pStyle')).set(o.q(o.W, 'val'), 'Caption')
    if op.get('keep_with_next'):
        o.etree.SubElement(prop, o.q(o.W, 'keepNext'))
    numbered = [_run(o, label + ' '), _field(o, f'SEQ {label} \\* ARABIC', '1')]
    if op.get('ref_id'):
        numbered = _wrap_bookmark(o, root, bookmark_name(op['ref_id']), numbered)
    node.extend(numbered)
    if text:
        node.append(_run(o, '  ' + text))
    return node


def _merge_plain_runs(omml, o):
    """Join adjacent upright (m:sty p) runs, e.g. \\mathrm{softmax}, into one run.

    The converter emits one run per letter; LibreOffice then shows them as spaced
    italic identifiers. Italic variables are left as separate runs.
    """
    run_tag, text_tag = '{%s}r' % M, '{%s}t' % M
    for parent in list(omml.iter()):
        previous = None
        for child in list(parent):
            plain = child.tag == run_tag and child.find('{%s}rPr/{%s}sty[@{%s}val="p"]' % (M, M, M)) is not None
            if plain and previous is not None:
                same = [o.etree.tostring(n) for n in child if n.tag != text_tag] == [o.etree.tostring(n) for n in previous if n.tag != text_tag]
                if same and child.find(text_tag) is not None and previous.find(text_tag) is not None:
                    previous.find(text_tag).text = (previous.find(text_tag).text or '') + (child.find(text_tag).text or '')
                    parent.remove(child)
                    continue
            previous = child if plain else None


def equation(parts, root, op, o):
    math = _sibling('native_math')
    compiled = math.compile_omml(op['latex'], op.get('font_size', 12), op.get('color', '1A1A1A'))
    _merge_plain_runs(compiled['omml'], o)
    node = o.etree.Element(o.q(o.W, 'p'))
    prop = o.etree.SubElement(node, o.q(o.W, 'pPr'))
    if not op.get('numbered'):
        align = op.get('align', 'center')
        if align not in ('left', 'center', 'right'):
            raise ValueError('Equation align must be left, center or right')
        o.etree.SubElement(prop, o.q(o.W, 'jc')).set(o.q(o.W, 'val'), align)
        wrapper = o.etree.SubElement(node, '{%s}oMathPara' % M)
        wrapper.append(compiled['omml'])
        return node
    # Numbered display equation: centred formula and a right-aligned "(n)" via tab stops.
    ensure_update_fields(parts, o)
    width = _text_width(root, o)
    tabs = o.etree.SubElement(prop, o.q(o.W, 'tabs'))
    for kind, position in (('center', width // 2), ('right', width)):
        tab = o.etree.SubElement(tabs, o.q(o.W, 'tab'))
        tab.set(o.q(o.W, 'val'), kind)
        tab.set(o.q(o.W, 'pos'), str(position))
    tab_run = lambda: o.etree.SubElement(o.etree.Element(o.q(o.W, 'r')), o.q(o.W, 'tab')).getparent()
    node.append(tab_run())
    node.append(compiled['omml'])
    node.append(tab_run())
    number = [_run(o, '('), _field(o, 'SEQ Equation \\* ARABIC', '1'), _run(o, ')')]
    if op.get('ref_id'):
        number = _wrap_bookmark(o, root, bookmark_name(op['ref_id']), number)
    node.extend(number)
    return node


def chart(parts, root, op, o):
    spec = op.get('chart')
    if not isinstance(spec, dict):
        raise ValueError('insert_chart requires a chart object')
    node = _sibling('native_charts').paragraph(parts, spec, o, root)
    prop = o.etree.Element(o.q(o.W, 'pPr'))
    o.etree.SubElement(prop, o.q(o.W, 'keepNext'))
    o.etree.SubElement(prop, o.q(o.W, 'jc')).set(o.q(o.W, 'val'), 'center')
    node.insert(0, prop)
    return node


def _heading_levels(parts, o, deepest):
    """styleId -> outline level (1-based) for heading styles, by name or outlineLvl."""
    styles = o.parse_xml(parts['word/styles.xml'])
    levels = {}
    for style in styles.iter(o.q(o.W, 'style')):
        name = style.find(o.q(o.W, 'name'))
        outline = style.find('.//' + o.q(o.W, 'outlineLvl'))
        level = None
        found = re.fullmatch(r'heading (\d)', (name.get(o.q(o.W, 'val')) if name is not None else '').lower())
        if found:
            level = int(found.group(1))
        elif outline is not None:
            level = int(outline.get(o.q(o.W, 'val'), 9)) + 1
        if level and level <= deepest:
            levels[style.get(o.q(o.W, 'styleId'))] = level
    return levels


def _toc_entries(parts, root, o, deepest, container):
    """Bookmark every heading and return one TOC paragraph per heading."""
    for name, start in list(_bookmarks(root, o).items()):
        if name and name.startswith('_Toc_'):
            serial = start.get(o.q(o.W, 'id'))
            for end in root.iter(o.q(o.W, 'bookmarkEnd')):
                if end.get(o.q(o.W, 'id')) == serial:
                    end.getparent().remove(end)
            start.getparent().remove(start)
    levels = _heading_levels(parts, o, deepest)
    width = _text_width(root, o)
    entries = []
    for paragraph in root.iter(o.q(o.W, 'p')):
        if container is not None and container in paragraph.iterancestors():
            continue
        style = paragraph.find('./' + o.q(o.W, 'pPr') + '/' + o.q(o.W, 'pStyle'))
        level = levels.get(style.get(o.q(o.W, 'val'))) if style is not None else None
        text = o.ptext(paragraph).strip()
        if not level or not text:
            continue
        name = f'_Toc_{len(entries) + 1:04d}'
        runs = [child for child in paragraph if child.tag != o.q(o.W, 'pPr')]
        for child in runs:
            paragraph.remove(child)
        paragraph.extend(_wrap_bookmark(o, root, name, runs))
        entry = o.etree.Element(o.q(o.W, 'p'))
        prop = o.etree.SubElement(entry, o.q(o.W, 'pPr'))
        o.etree.SubElement(prop, o.q(o.W, 'pStyle')).set(o.q(o.W, 'val'), f'TOC{min(level, 3)}')
        tab = o.etree.SubElement(o.etree.SubElement(prop, o.q(o.W, 'tabs')), o.q(o.W, 'tab'))
        tab.set(o.q(o.W, 'val'), 'right')
        tab.set(o.q(o.W, 'leader'), 'dot')
        tab.set(o.q(o.W, 'pos'), str(width))
        link = o.etree.SubElement(entry, o.q(o.W, 'hyperlink'))
        link.set(o.q(o.W, 'anchor'), name)
        link.append(_run(o, text))
        tab_run = o.etree.SubElement(link, o.q(o.W, 'r'))
        o.etree.SubElement(tab_run, o.q(o.W, 'tab'))
        link.append(_field(o, f'PAGEREF {name} \\h', ''))
        entries.append(entry)
    return entries


def _fill_toc(parts, root, o, sdt):
    content = sdt.find(o.q(o.W, 'sdtContent'))
    deepest = int(sdt.get(TOC_LEVELS, '3'))
    title = sdt.get(TOC_TITLE, '')
    for child in list(content):
        content.remove(child)
    if title:
        heading = o.etree.SubElement(content, o.q(o.W, 'p'))
        prop = o.etree.SubElement(heading, o.q(o.W, 'pPr'))
        o.etree.SubElement(prop, o.q(o.W, 'pStyle')).set(o.q(o.W, 'val'), 'TOCHeading')
        heading.append(_run(o, title))
    entries = _toc_entries(parts, root, o, deepest, sdt) or [o.etree.Element(o.q(o.W, 'p'))]
    if len(entries) == 1 and not len(entries[0]):
        entries[0].append(_run(o, '（添加标题后自动生成目录）'))
    begin, instr, separate, end = (o.etree.Element(o.q(o.W, 'r')) for _ in range(4))
    o.etree.SubElement(begin, o.q(o.W, 'fldChar')).set(o.q(o.W, 'fldCharType'), 'begin')
    text = o.etree.SubElement(instr, o.q(o.W, 'instrText'))
    text.text = f' TOC \\o "1-{deepest}" \\h \\z \\u '
    text.set(o.q(o.XMLNS, 'space'), 'preserve')
    o.etree.SubElement(separate, o.q(o.W, 'fldChar')).set(o.q(o.W, 'fldCharType'), 'separate')
    o.etree.SubElement(end, o.q(o.W, 'fldChar')).set(o.q(o.W, 'fldCharType'), 'end')
    first, last = entries[0], entries[-1]
    anchor = first.find(o.q(o.W, 'pPr'))
    position = 1 if anchor is not None else 0
    for offset, run in enumerate((begin, instr, separate)):
        first.insert(position + offset, run)
    last.append(end)
    content.extend(entries)
    return len(entries) if entries[0].find('.//' + o.q(o.W, 'hyperlink')) is not None else 0


def toc(parts, root, op, o):
    levels = op.get('levels', 3)
    if type(levels) is not int or not 1 <= levels <= 3:
        raise ValueError('TOC levels must be 1, 2 or 3')
    title = op.get('title', '目录')
    if not isinstance(title, str) or len(title) > 40 or '\n' in title:
        raise ValueError('TOC title must be one line of at most 40 characters')
    ensure_styles(parts, o, ['TOCHeading', 'TOC1', 'TOC2', 'TOC3'])
    ensure_update_fields(parts, o)
    sdt = o.etree.Element(o.q(o.W, 'sdt'))
    prop = o.etree.SubElement(sdt, o.q(o.W, 'sdtPr'))
    gallery = o.etree.SubElement(o.etree.SubElement(prop, o.q(o.W, 'docPartObj')), o.q(o.W, 'docPartGallery'))
    gallery.set(o.q(o.W, 'val'), 'Table of Contents')
    o.etree.SubElement(prop.find(o.q(o.W, 'docPartObj')), o.q(o.W, 'docPartUnique'))
    o.etree.SubElement(sdt, o.q(o.W, 'sdtContent'))
    sdt.set(TOC_LEVELS, str(levels))
    sdt.set(TOC_TITLE, title)
    return sdt


def update_toc(parts, root, target, o):
    if target.tag != o.q(o.W, 'sdt') or target.get(TOC_LEVELS) is None:
        raise ValueError('update_toc target must be a table of contents block')
    return _fill_toc(parts, root, o, target)


def refresh_fields(parts, root, o):
    """Rebuild TOCs, renumber SEQ fields, refresh REF caches. Returns unresolved reference ids."""
    for sdt in list(root.iter(o.q(o.W, 'sdt'))):
        if sdt.get(TOC_LEVELS) is not None:
            _fill_toc(parts, root, o, sdt)
    counters = {}
    for field in root.iter(o.q(o.W, 'fldSimple')):
        found = SEQ_INSTR.match(field.get(o.q(o.W, 'instr'), ''))
        if found:
            counters[found.group(1)] = counters.get(found.group(1), 0) + 1
            _set_cached(field, o, str(counters[found.group(1)]))
    texts = {}
    for name, start in _bookmarks(root, o).items():
        if not name or not name.startswith('_Ref_'):
            continue
        serial, collected = start.get(o.q(o.W, 'id')), []
        for node in start.itersiblings():
            if node.tag == o.q(o.W, 'bookmarkEnd') and node.get(o.q(o.W, 'id')) == serial:
                break
            collected.extend(t.text or '' for t in node.iter(o.q(o.W, 't')))
        texts[name] = ''.join(collected).strip()
    unresolved, known = [], set(_bookmarks(root, o))
    for field in root.iter(o.q(o.W, 'fldSimple')):
        instr = field.get(o.q(o.W, 'instr'), '')
        found = REF_INSTR.match(instr)
        if not found or found.group(1).startswith('_Toc_'):
            continue
        name, page = found.group(1), instr.strip().startswith('PAGEREF')
        if name not in known:
            _set_cached(field, o, '?' if page else '??')
            unresolved.append(name)
        elif not page:
            _set_cached(field, o, texts.get(name, ''))
        # A page number depends on layout: Word and LibreOffice compute it when they paginate.
    return sorted(set(unresolved))


STANDARD_PREFIXES = {
    'm': M,
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
    'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
}


def normalize_namespaces(root, o):
    """Declare the conventional Office prefixes on the root instead of generated ns0/ns1."""
    o.etree.cleanup_namespaces(root, top_nsmap=STANDARD_PREFIXES)


def _set_cached(field, o, value):
    runs = field.findall(o.q(o.W, 'r'))
    for run in runs[1:]:
        field.remove(run)
    if not runs:
        field.append(_run(o, value))
        return
    texts = runs[0].findall(o.q(o.W, 't'))
    if not texts:
        texts = [o.etree.SubElement(runs[0], o.q(o.W, 't'))]
    for extra in texts[1:]:
        runs[0].remove(extra)
    texts[0].text = value
