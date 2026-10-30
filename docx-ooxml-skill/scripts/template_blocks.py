"""Copy template blocks and fill them without assembling new WordprocessingML.

Word is a flow, so this clones paragraphs, tables and content controls with
their styles. It does not invent a page break to manufacture a page count.
"""
import copy
import posixpath
import re

ID = re.compile(r'^[\w.-]{1,80}$')


def apply(parts, op, o, block_attr):
    kind = op.get('type')
    if kind == 'block.clone':
        return clone_block(parts, op, o, block_attr)
    if kind == 'section.clone':
        return clone_section(parts, op, o, block_attr)
    if kind == 'slot.fill':
        return fill_slot(parts, op, o, block_attr)
    if kind == 'part.replace_text':
        return replace_part_text(parts, op, o)
    raise ValueError('Unsupported template block operation')


def clone_block(parts, op, o, block_attr):
    root = o.parse_xml(parts[o.MAIN])
    source = _one(root, op['source_block_id'], o, block_attr)
    if source.tag not in (o.q(o.W, 'p'), o.q(o.W, 'tbl')) or source.getparent() is not root.find(o.q(o.W, 'body')):
        raise ValueError('block.clone copies one top-level paragraph or table')
    clone = copy.deepcopy(source)
    if clone.tag == o.q(o.W, 'tbl') and op.get('rows') is not None:
        _replace_table_rows(clone, op['rows'], o)
    ids = _retarget(root, [clone], _prefix(op), o, block_attr)
    _insert(root, [clone], op.get('after_block_id'), o, block_attr, fallback=source)
    parts[o.MAIN] = o.xml_bytes(root)
    return parts, ids, None


def clone_section(parts, op, o, block_attr):
    root = o.parse_xml(parts[o.MAIN])
    body = root.find(o.q(o.W, 'body'))
    source = _one(root, op['source_block_id'], o, block_attr)
    nodes = _section_nodes(body, source, o)
    clones = [copy.deepcopy(node) for node in nodes]
    if op.get('heading') is not None:
        _set_paragraph_text(clones[0], op['heading'], o)
    paragraphs = [node for node in clones[1:] if node.tag == o.q(o.W, 'p')]
    if op.get('paragraphs') is not None:
        texts = op['paragraphs']
        if not isinstance(texts, list) or len(texts) != len(paragraphs):
            raise ValueError('section.clone paragraphs must match the template section body')
        for paragraph, text in zip(paragraphs, texts):
            _bounded_text(text)
            _set_paragraph_text(paragraph, text, o)
    if op.get('tables') is not None:
        tables = [node for node in clones if node.tag == o.q(o.W, 'tbl')]
        matrices = op['tables']
        if not isinstance(matrices, list) or len(matrices) != len(tables):
            raise ValueError('section.clone tables must match the template section')
        for table, rows in zip(tables, matrices):
            _replace_table_rows(table, rows, o)
    ids = _retarget(root, clones, _prefix(op), o, block_attr)
    _insert(root, clones, op.get('after_block_id'), o, block_attr, fallback=nodes[-1])
    parts[o.MAIN] = o.xml_bytes(root)
    return parts, ids, dict(cloned_blocks=len(clones))


def fill_slot(parts, op, o, block_attr):
    text = op.get('text')
    _bounded_text(text)
    root = o.parse_xml(parts[o.MAIN])
    if op.get('tag'):
        controls = []
        for sdt in root.iter(o.q(o.W, 'sdt')):
            tag = sdt.find('.//' + o.q(o.W, 'tag'))
            if tag is not None and tag.get(o.q(o.W, 'val')) == op['tag']:
                controls.append(sdt)
        if len(controls) != 1:
            raise ValueError('slot.fill requires one content control with that tag')
        paragraph = controls[0].find('.//' + o.q(o.W, 'p'))
        if paragraph is None:
            raise ValueError('Content control has no paragraph to preserve')
        _set_paragraph_text(paragraph, text, o)
        changed = [paragraph.get(block_attr) or op['tag']]
    else:
        paragraph = _one(root, op.get('block_id'), o, block_attr)
        if paragraph.tag != o.q(o.W, 'p'):
            raise ValueError('slot.fill targets a paragraph or a content-control tag')
        _set_paragraph_text(paragraph, text, o)
        changed = [op['block_id']]
    parts[o.MAIN] = o.xml_bytes(root)
    return parts, changed, None


def replace_part_text(parts, op, o):
    which = op.get('part')
    if which not in ('header', 'footer'):
        raise ValueError('part.replace_text targets header or footer')
    name = _section_part(parts, o, which)
    root = o.parse_xml(parts[name])
    hits = []
    for paragraph in root.iter(o.q(o.W, 'p')):
        if op['old'] and op['old'] in o.ptext(paragraph):
            hits.append(paragraph)
    if len(hits) != 1 or o.ptext(hits[0]).count(op['old']) != 1:
        raise ValueError('part.replace_text requires one occurrence in the ' + which)
    _bounded_text(op.get('new'))
    o.replace_text_in_paragraph(hits[0], op['old'], op['new'])
    parts[name] = o.xml_bytes(root)
    return parts, [which], None


def _section_nodes(body, source, o):
    if source.tag != o.q(o.W, 'p') or source.getparent() is not body:
        raise ValueError('section.clone starts at a top-level heading')
    level = _heading_level(source, o)
    if level is None:
        raise ValueError('section.clone source must use a Heading style')
    children = list(body)
    start = children.index(source)
    selected = [source]
    for node in children[start + 1:]:
        if node.tag == o.q(o.W, 'sectPr'):
            break
        if node.tag == o.q(o.W, 'p'):
            nxt = _heading_level(node, o)
            if nxt is not None and nxt <= level:
                break
        if node.tag not in (o.q(o.W, 'p'), o.q(o.W, 'tbl')):
            break
        selected.append(node)
    return selected


def _heading_level(paragraph, o):
    style = paragraph.find('./' + o.q(o.W, 'pPr') + '/' + o.q(o.W, 'pStyle'))
    if style is None:
        return None
    value = style.get(o.q(o.W, 'val')) or ''
    match = re.search(r'(\d+)$', value)
    if value.lower().startswith('heading'):
        return int(match.group(1)) if match else 1
    return None


def _replace_table_rows(table, rows, o):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10:
        raise ValueError('A cloned table stays within 1-10 rows')
    existing = [row for row in table if row.tag == o.q(o.W, 'tr')]
    if not existing:
        raise ValueError('Template table has no row to copy')
    width = len([cell for cell in existing[0] if cell.tag == o.q(o.W, 'tc')])
    if any(not isinstance(row, list) or len(row) != width for row in rows):
        raise ValueError('Cloned table rows must keep the template column count')
    if any(not isinstance(text, str) or len(text) > 300 for row in rows for text in row):
        raise ValueError('Table cells must be strings of at most 300 characters')
    prototype = existing[1] if len(existing) > 1 else existing[0]
    for row in existing[1:]:
        table.remove(row)
    _write_row(existing[0], rows[0], o)
    for data in rows[1:]:
        row = copy.deepcopy(prototype)
        _write_row(row, data, o)
        table.append(row)


def _write_row(row, values, o):
    cells = [cell for cell in row if cell.tag == o.q(o.W, 'tc')]
    for cell, text in zip(cells, values):
        paragraph = cell.find('.//' + o.q(o.W, 'p'))
        if paragraph is None:
            paragraph = o.etree.SubElement(cell, o.q(o.W, 'p'))
        _set_paragraph_text(paragraph, text, o)


def _set_paragraph_text(paragraph, text, o):
    _bounded_text(text)
    runs = [child for child in paragraph if child.tag == o.q(o.W, 'r') and child.find(o.q(o.W, 't')) is not None]
    if not runs:
        run = o.etree.SubElement(paragraph, o.q(o.W, 'r'))
        node = o.etree.SubElement(run, o.q(o.W, 't'))
        node.text = text
        return
    node = runs[0].find(o.q(o.W, 't'))
    node.text = text
    if text[:1].isspace() or text[-1:].isspace():
        node.set(o.q(o.XMLNS, 'space'), 'preserve')
    for extra in runs[1:]:
        paragraph.remove(extra)


def _retarget(root, nodes, prefix, o, block_attr):
    used = {node.get(block_attr) for node in root.iter() if node.get(block_attr)}
    assigned = []
    sequence = 0
    for node in nodes:
        for element in node.iter():
            if not element.get(block_attr):
                continue
            sequence += 1
            candidate = prefix if sequence == 1 else f'{prefix}-{sequence:02d}'
            while candidate in used:
                sequence += 1
                candidate = f'{prefix}-{sequence:02d}'
            if not ID.fullmatch(candidate):
                raise ValueError('Invalid cloned block id')
            element.set(block_attr, candidate)
            used.add(candidate)
            assigned.append(candidate)
    if not assigned:
        raise ValueError('Cloned block has no stable id')
    return assigned


def _insert(root, nodes, after_id, o, block_attr, fallback=None):
    body = root.find(o.q(o.W, 'body'))
    if after_id:
        anchor = _one(root, after_id, o, block_attr)
    elif fallback is not None:
        anchor = fallback
    else:
        raise ValueError('Insertion requires a top-level anchor')
    if anchor.getparent() is not body:
        raise ValueError('Insertion anchor must be one top-level block')
    index = list(body).index(anchor) + 1
    for offset, node in enumerate(nodes):
        body.insert(index + offset, node)


def _one(root, block_id, o, block_attr):
    if not isinstance(block_id, str) or not ID.fullmatch(block_id):
        raise ValueError('Invalid stable identifier')
    matches = [node for node in root.iter() if node.get(block_attr) == block_id]
    if len(matches) != 1:
        raise ValueError('Unknown or ambiguous block_id')
    return matches[0]


def _prefix(op):
    prefix = op.get('block_id')
    if not isinstance(prefix, str) or not ID.fullmatch(prefix):
        raise ValueError('Invalid stable identifier')
    return prefix


def _bounded_text(text):
    if not isinstance(text, str) or len(text) > 3000 or '\n' in text or '\r' in text:
        raise ValueError('One paragraph, maximum 3000 characters')


def _section_part(parts, o, which):
    root = o.parse_xml(parts[o.MAIN])
    tag = 'headerReference' if which == 'header' else 'footerReference'
    ref = root.find('.//' + o.q(o.W, tag))
    if ref is None:
        raise ValueError('Section has no ' + which)
    rel_id = ref.get(o.q(o.R, 'id'))
    rels = o.parse_xml(parts[o.DOC_RELS])
    target = next((rel.get('Target') for rel in rels if rel.get('Id') == rel_id), None)
    if not target:
        raise ValueError('Missing ' + which + ' relationship')
    path = posixpath.normpath(posixpath.join('word', target))
    if path not in parts:
        raise ValueError('Missing ' + which + ' part')
    return path
