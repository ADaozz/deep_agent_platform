"""Rich atomic DOCX content: formatted runs, inline math, complex tables,
floating and side-by-side images, and headers/footers with page numbers.

Inline content is either plain text with tokens or an explicit ``runs`` list:
  {{ref:id}}      REF field: caption/equation number or the bookmarked paragraph text
  {{pageref:id}}  PAGEREF field: page of the bookmarked block
  {{math:latex}}  inline Office Math (braces inside the LaTeX are matched)
  {{page}} {{pages}}  PAGE / NUMPAGES fields (headers and footers)
"""
import importlib.util
from pathlib import Path
import re

M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
HIGHLIGHTS = {'yellow', 'green', 'cyan', 'magenta', 'blue', 'red', 'darkBlue', 'darkCyan', 'darkGreen',
              'darkMagenta', 'darkRed', 'darkYellow', 'darkGray', 'lightGray', 'black'}
FORMAT_KEYS = {'bold', 'italic', 'underline', 'strike', 'size', 'color', 'highlight', 'font', 'spacing', 'vertical'}
HEX = re.compile(r'^[0-9A-Fa-f]{6}$')
ID = re.compile(r'^[\w.-]{1,80}$')


def _sibling(name):
    spec = importlib.util.spec_from_file_location('docx_skill_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _academic():
    return _sibling('academic_blocks')


# ---------------------------------------------------------------- inline runs

def run_properties(o, fmt):
    """w:rPr in schema order: rFonts b i strike color spacing sz szCs highlight u vertAlign."""
    unknown = set(fmt) - FORMAT_KEYS - {'text'}
    if unknown:
        raise ValueError('Unknown run format keys: ' + ', '.join(sorted(unknown)))
    prop = o.etree.Element(o.q(o.W, 'rPr'))

    def add(name, **attrs):
        node = o.etree.SubElement(prop, o.q(o.W, name))
        for key, value in attrs.items():
            node.set(o.q(o.W, key), str(value))
        return node

    if fmt.get('font') is not None:
        font = fmt['font']
        if not isinstance(font, str) or not 1 <= len(font) <= 40:
            raise ValueError('font must be a font family name of at most 40 characters')
        add('rFonts', ascii=font, hAnsi=font, eastAsia=font, cs=font)
    for key, tag in (('bold', 'b'), ('italic', 'i'), ('strike', 'strike')):
        if fmt.get(key):
            add(tag)
    if fmt.get('color') is not None:
        if not isinstance(fmt['color'], str) or not HEX.fullmatch(fmt['color']):
            raise ValueError('color must be RRGGBB')
        add('color', val=fmt['color'].upper())
    if fmt.get('spacing') is not None:
        if type(fmt['spacing']) not in (int, float) or not -5 <= fmt['spacing'] <= 20:
            raise ValueError('spacing (letter spacing) must be -5..20 pt')
        add('spacing', val=int(round(fmt['spacing'] * 20)))
    if fmt.get('size') is not None:
        if type(fmt['size']) not in (int, float) or not 6 <= fmt['size'] <= 72:
            raise ValueError('size must be 6..72 pt')
        add('sz', val=int(round(fmt['size'] * 2)))
        add('szCs', val=int(round(fmt['size'] * 2)))
    if fmt.get('highlight') is not None:
        if fmt['highlight'] not in HIGHLIGHTS:
            raise ValueError('highlight must be one of ' + ', '.join(sorted(HIGHLIGHTS)))
        add('highlight', val=fmt['highlight'])
    if fmt.get('underline'):
        add('u', val='single')
    if fmt.get('vertical') is not None:
        if fmt['vertical'] not in ('superscript', 'subscript'):
            raise ValueError('vertical must be superscript or subscript')
        add('vertAlign', val=fmt['vertical'])
    return prop if len(prop) else None


def _text_run(o, text, fmt=None):
    run = o.etree.Element(o.q(o.W, 'r'))
    prop = run_properties(o, fmt or {})
    if prop is not None:
        run.append(prop)
    node = o.etree.SubElement(run, o.q(o.W, 't'))
    node.text = text
    node.set(o.q(o.XMLNS, 'space'), 'preserve')
    return run


def _field(o, instr, cached, fmt=None):
    field = o.etree.Element(o.q(o.W, 'fldSimple'))
    field.set(o.q(o.W, 'instr'), f' {instr} ')
    field.append(_text_run(o, cached, fmt))
    return field


def inline_math(o, latex, size=None):
    academic = _academic()
    compiled = _sibling('native_math').compile_omml(latex, size or 11, '1A1A1A')
    academic._merge_plain_runs(compiled['omml'], o)
    return compiled['omml']


def _tokens(text):
    """Split text into ('text'|'ref'|'pageref'|'math'|'page'|'pages', value) with brace matching for math."""
    pieces, position, buffer = [], 0, []
    while position < len(text):
        if text.startswith('{{', position):
            if text.startswith('{{math:', position):
                end, depth = position + 7, 0
                while end < len(text):
                    char = text[end]
                    if char == '\\' and end + 1 < len(text):
                        end += 2
                        continue
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        if depth == 0 and text.startswith('}}', end):
                            break
                        depth -= 1
                    end += 1
                if end >= len(text):
                    raise ValueError('Unclosed {{math:...}} token')
                if buffer:
                    pieces.append(('text', ''.join(buffer))); buffer = []
                pieces.append(('math', text[position + 7:end]))
                position = end + 2
                continue
            match = re.match(r'\{\{(ref|pageref):([\w.-]{1,80})\}\}|\{\{(page|pages)\}\}', text[position:])
            if match:
                if buffer:
                    pieces.append(('text', ''.join(buffer))); buffer = []
                pieces.append((match.group(1), match.group(2)) if match.group(1) else (match.group(3), None))
                position += match.end()
                continue
        buffer.append(text[position])
        position += 1
    if buffer:
        pieces.append(('text', ''.join(buffer)))
    return pieces


def inline_nodes(o, text=None, runs=None, base=None, allow_page_fields=False):
    """Inline content for one paragraph from tokenized ``text`` or an explicit ``runs`` list."""
    academic = _academic()
    if (text is None) == (runs is None):
        raise ValueError('Give exactly one of text or runs')
    base = dict(base or {})
    items = [dict(base, text=text)] if runs is None else runs
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError('runs must be a list of 1-200 items')
    nodes, length = [], 0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Each run is an object')
        unknown = set(item) - FORMAT_KEYS - {'text', 'math', 'ref', 'show'}
        if unknown:
            raise ValueError('Unknown run keys: ' + ', '.join(sorted(unknown)))
        fmt = {k: v for k, v in {**base, **item}.items() if k in FORMAT_KEYS}
        if 'math' in item:
            nodes.append(inline_math(o, item['math'], fmt.get('size')))
            continue
        if 'ref' in item:
            if not isinstance(item['ref'], str) or not ID.fullmatch(item['ref']):
                raise ValueError('ref must be a stable id')
            name = academic.bookmark_name(item['ref'])
            page = item.get('show') == 'page'
            nodes.append(_field(o, f'{"PAGEREF" if page else "REF"} {name} \\h', '?' if page else '??', fmt))
            continue
        value = item.get('text')
        if not isinstance(value, str) or '\n' in value or '\r' in value:
            raise ValueError('Run text must be a string without newlines')
        length += len(value)
        for kind, payload in _tokens(value):
            if kind == 'text':
                nodes.append(_text_run(o, payload, fmt))
            elif kind == 'math':
                nodes.append(inline_math(o, payload, fmt.get('size')))
            elif kind in ('ref', 'pageref'):
                name = academic.bookmark_name(payload)
                nodes.append(_field(o, f'{"PAGEREF" if kind == "pageref" else "REF"} {name} \\h', '?' if kind == 'pageref' else '??', fmt))
            else:
                if not allow_page_fields:
                    raise ValueError('{{page}} and {{pages}} are only valid in headers and footers')
                nodes.append(_field(o, 'PAGE' if kind == 'page' else 'NUMPAGES', '1', fmt))
    if length > 3000:
        raise ValueError('One paragraph, maximum 3000 characters')
    return nodes


def bookmark_paragraph(o, root, paragraph, ref_id):
    """Bookmark the whole paragraph content so {{ref:id}} shows its text and {{pageref:id}} its page."""
    academic = _academic()
    content = [child for child in paragraph if child.tag != o.q(o.W, 'pPr')]
    for child in content:
        paragraph.remove(child)
    paragraph.extend(academic._wrap_bookmark(o, root, academic.bookmark_name(ref_id), content))


def format_text(root, target, op, o):
    """Apply run formatting to one occurrence of ``match`` that lies inside a single run."""
    if target.tag != o.q(o.W, 'p'):
        raise ValueError('format_text target must be a paragraph')
    match = op.get('match')
    if not isinstance(match, str) or not match:
        raise ValueError('format_text requires nonempty match text')
    fmt = {k: v for k, v in op.items() if k in FORMAT_KEYS}
    if not fmt:
        raise ValueError('format_text requires at least one format key')
    occurrence = op.get('occurrence', 1)
    hits = []
    for run in target.iter(o.q(o.W, 'r')):
        texts = run.findall(o.q(o.W, 't'))
        if run.getparent().tag == o.q(o.W, 'fldSimple') or len(texts) != 1:
            continue
        value, start = texts[0].text or '', 0
        while (index := value.find(match, start)) >= 0:
            hits.append((run, index))
            start = index + len(match)
    total = o.ptext(target).count(match)
    if not hits or type(occurrence) is not int or not 1 <= occurrence <= len(hits):
        raise ValueError(f'"{match}" occurrence {occurrence} is not inside a single run '
                         f'({len(hits)} formattable of {total} found); rewrite the paragraph with runs')
    run, index = hits[occurrence - 1]
    value = run.find(o.q(o.W, 't')).text
    old = run.find(o.q(o.W, 'rPr'))
    pieces = [(value[:index], None), (match, fmt), (value[index + len(match):], None)]
    parent, position = run.getparent(), run.getparent().index(run)
    parent.remove(run)
    for offset, (piece, extra) in enumerate(p for p in pieces if p[0]):
        new = _text_run(o, piece, extra)
        if old is not None:
            merged = o.etree.fromstring(o.etree.tostring(old))
            if extra:
                additions = run_properties(o, extra)
                names = {child.tag for child in additions}
                for child in list(merged):
                    if child.tag in names:
                        merged.remove(child)
                for child in additions:
                    merged.append(child)
                _sort_rpr(o, merged)
            existing = new.find(o.q(o.W, 'rPr'))
            if existing is not None:
                new.remove(existing)
            new.insert(0, merged)
        parent.insert(position + offset, new)


RPR_ORDER = ['rStyle', 'rFonts', 'b', 'bCs', 'i', 'iCs', 'caps', 'smallCaps', 'strike', 'dstrike', 'outline', 'shadow',
             'emboss', 'imprint', 'noProof', 'snapToGrid', 'vanish', 'webHidden', 'color', 'spacing', 'w', 'kern',
             'position', 'sz', 'szCs', 'highlight', 'u', 'effect', 'bdr', 'shd', 'fitText', 'vertAlign']


def _sort_rpr(o, prop):
    rank = {o.q(o.W, name): index for index, name in enumerate(RPR_ORDER)}
    children = sorted(prop, key=lambda child: rank.get(child.tag, len(RPR_ORDER)))
    for child in list(prop):
        prop.remove(child)
    prop.extend(children)


# ---------------------------------------------------------------- tables

BORDER_STYLES = ('grid', 'three_line', 'horizontal', 'none')
CELL_KEYS = {'text', 'runs', 'colspan', 'rowspan', 'align', 'valign', 'fill'} | FORMAT_KEYS


def _cell_spec(cell, columns):
    spec = {'text': cell} if isinstance(cell, str) else dict(cell) if isinstance(cell, dict) else None
    if spec is None:
        raise ValueError('A table cell is a string or an object')
    unknown = set(spec) - CELL_KEYS
    if unknown:
        raise ValueError('Unknown table cell keys: ' + ', '.join(sorted(unknown)))
    if 'text' not in spec and 'runs' not in spec:
        spec['text'] = ''
    for key, limit in (('colspan', columns), ('rowspan', 60)):
        value = spec.setdefault(key, 1)
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError(f'{key} must be 1..{limit}')
    if spec.get('align', 'left') not in ('left', 'center', 'right', 'both'):
        raise ValueError('Cell align must be left, center, right or both')
    if spec.get('valign', 'center') not in ('top', 'center', 'bottom'):
        raise ValueError('Cell valign must be top, center or bottom')
    if spec.get('fill') is not None and (not isinstance(spec['fill'], str) or not HEX.fullmatch(spec['fill'])):
        raise ValueError('Cell fill must be RRGGBB')
    if isinstance(spec.get('text'), str) and len(spec['text']) > 1000:
        raise ValueError('Cell text must be at most 1000 characters')
    return spec


def _layout(rows, columns):
    """HTML-style placement: a rowspan reserves the same columns in later rows."""
    reserved, layout = {}, []
    for r, row in enumerate(rows):
        if not isinstance(row, list) or not row and not any(key[0] == r for key in reserved):
            raise ValueError(f'Row {r} must be a list of cells')
        placed, c, queue = [], 0, list(row)

        def take_reserved():
            nonlocal c
            while (r, c) in reserved:
                origin = reserved.pop((r, c))
                placed.append(('continue', c, origin))
                c += origin['colspan']

        for cell in queue:
            take_reserved()
            spec = _cell_spec(cell, columns)
            if c + spec['colspan'] > columns:
                raise ValueError(f'Row {r} overflows {columns} columns')
            if r + spec['rowspan'] > len(rows):
                raise ValueError(f'rowspan in row {r} extends past the last row')
            placed.append(('cell', c, spec))
            for k in range(1, spec['rowspan']):
                reserved[(r + k, c)] = spec
            c += spec['colspan']
        take_reserved()
        if c != columns:
            raise ValueError(f'Row {r} covers {c} of {columns} columns')
        layout.append(placed)
    return layout


def _borders(o, parent, tag, edges, color='BCC4CC'):
    borders = o.etree.SubElement(parent, o.q(o.W, tag))
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        size = edges.get(edge)
        node = o.etree.SubElement(borders, o.q(o.W, edge))
        if size:
            node.set(o.q(o.W, 'val'), 'single')
            node.set(o.q(o.W, 'sz'), str(size))
            node.set(o.q(o.W, 'space'), '0')
            node.set(o.q(o.W, 'color'), color)
        else:
            node.set(o.q(o.W, 'val'), 'nil')
    return borders


TABLE_EDGES = {'grid': {e: 4 for e in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV')},
               'horizontal': {'top': 4, 'bottom': 4, 'insideH': 4}, 'three_line': {'top': 12, 'bottom': 12}, 'none': {}}


def _cell_paragraph(o, spec, align, base):
    paragraph = o.etree.Element(o.q(o.W, 'p'))
    prop = o.etree.SubElement(paragraph, o.q(o.W, 'pPr'))
    spacing = o.etree.SubElement(prop, o.q(o.W, 'spacing'))
    spacing.set(o.q(o.W, 'before'), '40')
    spacing.set(o.q(o.W, 'after'), '40')
    o.etree.SubElement(prop, o.q(o.W, 'jc')).set(o.q(o.W, 'val'), align)
    fmt = {**base, **{k: v for k, v in spec.items() if k in FORMAT_KEYS}}
    if spec.get('runs') is not None:
        paragraph.extend(inline_nodes(o, runs=spec['runs'], base=fmt))
    elif spec.get('text'):
        paragraph.extend(inline_nodes(o, text=spec['text'], base=fmt))
    return paragraph


def table(parts, root, op, o):
    columns, rows = op.get('columns'), op.get('rows')
    if not isinstance(columns, list) or not 1 <= len(columns) <= 12:
        raise ValueError('columns must list 1-12 column definitions')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 60:
        raise ValueError('rows must list 1-60 rows')
    academic = _academic()
    available = academic._text_width(root, o)
    widths = []
    for column in columns:
        column = column if isinstance(column, dict) else {}
        width = column.get('width_twips', available // len(columns))
        if type(width) is not int or not 300 <= width <= 15000:
            raise ValueError('width_twips must be 300..15000')
        if column.get('align', 'left') not in ('left', 'center', 'right', 'both'):
            raise ValueError('Column align must be left, center, right or both')
        widths.append(width)
    if sum(widths) > available + 10:
        raise ValueError(f'Column widths total {sum(widths)} twips, wider than the {available}-twip text area')
    header_rows = op.get('header_rows', 1)
    if type(header_rows) is not int or not 0 <= header_rows <= len(rows):
        raise ValueError('header_rows must be 0..row count')
    style = op.get('borders', 'three_line')
    if style not in BORDER_STYLES:
        raise ValueError('borders must be one of ' + ', '.join(BORDER_STYLES))
    base = {'size': op['font_size']} if op.get('font_size') is not None else {}
    layout = _layout(rows, len(columns))

    node = o.etree.Element(o.q(o.W, 'tbl'))
    prop = o.etree.SubElement(node, o.q(o.W, 'tblPr'))
    o.etree.SubElement(prop, o.q(o.W, 'tblW'), attrib={o.q(o.W, 'w'): str(sum(widths)), o.q(o.W, 'type'): 'dxa'})
    o.etree.SubElement(prop, o.q(o.W, 'jc')).set(o.q(o.W, 'val'), 'center')
    _borders(o, prop, 'tblBorders', TABLE_EDGES[style], '1A1A1A' if style == 'three_line' else 'BCC4CC')
    o.etree.SubElement(prop, o.q(o.W, 'tblLayout')).set(o.q(o.W, 'type'), 'fixed')
    grid = o.etree.SubElement(node, o.q(o.W, 'tblGrid'))
    for width in widths:
        o.etree.SubElement(grid, o.q(o.W, 'gridCol')).set(o.q(o.W, 'w'), str(width))
    for r, placed in enumerate(layout):
        tr = o.etree.SubElement(node, o.q(o.W, 'tr'))
        trp = o.etree.SubElement(tr, o.q(o.W, 'trPr'))
        o.etree.SubElement(trp, o.q(o.W, 'cantSplit'))
        if r < header_rows:
            o.etree.SubElement(trp, o.q(o.W, 'tblHeader'))
        for kind, c, spec in placed:
            tc = o.etree.SubElement(tr, o.q(o.W, 'tc'))
            tcp = o.etree.SubElement(tc, o.q(o.W, 'tcPr'))
            o.etree.SubElement(tcp, o.q(o.W, 'tcW'), attrib={o.q(o.W, 'w'): str(sum(widths[c:c + spec['colspan']])), o.q(o.W, 'type'): 'dxa'})
            if spec['colspan'] > 1:
                o.etree.SubElement(tcp, o.q(o.W, 'gridSpan')).set(o.q(o.W, 'val'), str(spec['colspan']))
            if spec['rowspan'] > 1 or kind == 'continue':
                merge = o.etree.SubElement(tcp, o.q(o.W, 'vMerge'))
                if kind == 'cell':
                    merge.set(o.q(o.W, 'val'), 'restart')
            if style == 'three_line' and r == header_rows - 1:
                _borders(o, tcp, 'tcBorders', {'bottom': 6}, '1A1A1A')
            if spec.get('fill'):
                o.etree.SubElement(tcp, o.q(o.W, 'shd'), attrib={o.q(o.W, 'val'): 'clear', o.q(o.W, 'color'): 'auto', o.q(o.W, 'fill'): spec['fill'].upper()})
            o.etree.SubElement(tcp, o.q(o.W, 'vAlign')).set(o.q(o.W, 'val'), spec.get('valign', 'center'))
            column_align = (columns[c] if isinstance(columns[c], dict) else {}).get('align', 'left')
            cell_base = dict(base, bold=True) if r < header_rows else base
            if kind == 'continue':
                tc.append(o.etree.Element(o.q(o.W, 'p')))
            else:
                tc.append(_cell_paragraph(o, spec, spec.get('align', 'center' if r < header_rows else column_align), cell_base))
    return node


def _grid_cells(o, table_node):
    """(row, start_column, tc, is_continuation) for every cell in the table."""
    for r, tr in enumerate(table_node.findall(o.q(o.W, 'tr'))):
        c = 0
        for tc in tr.findall(o.q(o.W, 'tc')):
            prop = tc.find(o.q(o.W, 'tcPr'))
            span = prop.find(o.q(o.W, 'gridSpan')) if prop is not None else None
            merge = prop.find(o.q(o.W, 'vMerge')) if prop is not None else None
            yield r, c, tc, merge is not None and merge.get(o.q(o.W, 'val')) != 'restart'
            c += int(span.get(o.q(o.W, 'val'))) if span is not None else 1


def set_cell(parts, root, target, op, o):
    if target.tag != o.q(o.W, 'tbl'):
        raise ValueError('set_table_cell target must be a table block')
    row, column = op.get('row'), op.get('column')
    if type(row) is not int or type(column) is not int:
        raise ValueError('row and column are 0-based integers on the logical grid')
    found = next(((tc, cont) for r, c, tc, cont in _grid_cells(o, target) if r == row and c == column), None)
    if found is None:
        raise ValueError(f'No cell starts at row {row}, column {column} (it may be covered by a column span)')
    tc, continuation = found
    if continuation:
        raise ValueError('That position is covered by a row span; edit the merged cell where it starts')
    spec = _cell_spec({k: v for k, v in op.items() if k in CELL_KEYS - {'colspan', 'rowspan'}}, 12)
    prop = tc.find(o.q(o.W, 'tcPr'))
    if op.get('fill'):
        shading = prop.find(o.q(o.W, 'shd'))
        if shading is None:
            shading = o.etree.Element(o.q(o.W, 'shd'))
            anchor = prop.find(o.q(o.W, 'vAlign'))
            prop.insert(prop.index(anchor) if anchor is not None else len(prop), shading)
        shading.set(o.q(o.W, 'val'), 'clear'); shading.set(o.q(o.W, 'color'), 'auto'); shading.set(o.q(o.W, 'fill'), op['fill'].upper())
    old = tc.find(o.q(o.W, 'p'))
    jc = old.find('./' + o.q(o.W, 'pPr') + '/' + o.q(o.W, 'jc')) if old is not None else None
    align = op.get('align') or (jc.get(o.q(o.W, 'val')) if jc is not None else 'left')
    if 'text' in op or 'runs' in op:
        for paragraph in tc.findall(o.q(o.W, 'p')):
            tc.remove(paragraph)
        tc.append(_cell_paragraph(o, spec, align, {}))
    elif op.get('align') and old is not None:
        _cell_align(o, old, align)


def _cell_align(o, paragraph, align):
    prop = paragraph.find(o.q(o.W, 'pPr'))
    if prop is None:
        prop = o.etree.Element(o.q(o.W, 'pPr')); paragraph.insert(0, prop)
    jc = prop.find(o.q(o.W, 'jc'))
    if jc is None:
        jc = o.etree.SubElement(prop, o.q(o.W, 'jc'))
    jc.set(o.q(o.W, 'val'), align)


# ---------------------------------------------------------------- images

def float_image(node, op, o):
    """Turn the inline picture into a square-wrapped anchor on the left or right of the column."""
    side = op.get('float')
    if side not in ('left', 'right'):
        raise ValueError('float must be left or right')
    wp = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
    inline = node.find('.//{%s}inline' % wp)
    gap = str(int(float(op.get('gap_cm', 0.3)) * 360000))
    anchor = o.etree.Element('{%s}anchor' % wp, attrib={
        'distT': '0', 'distB': '0', 'distL': gap, 'distR': gap, 'simplePos': '0', 'relativeHeight': '251658240',
        'behindDoc': '0', 'locked': '0', 'layoutInCell': '1', 'allowOverlap': '1'})
    o.etree.SubElement(anchor, '{%s}simplePos' % wp, x='0', y='0')
    horizontal = o.etree.SubElement(anchor, '{%s}positionH' % wp, relativeFrom='column')
    o.etree.SubElement(horizontal, '{%s}align' % wp).text = side
    vertical = o.etree.SubElement(anchor, '{%s}positionV' % wp, relativeFrom='paragraph')
    o.etree.SubElement(vertical, '{%s}posOffset' % wp).text = '0'
    anchor.append(inline.find('{%s}extent' % wp))
    o.etree.SubElement(anchor, '{%s}effectExtent' % wp, l='0', t='0', r='0', b='0')
    o.etree.SubElement(anchor, '{%s}wrapSquare' % wp, wrapText='bothSides')
    anchor.append(inline.find('{%s}docPr' % wp))
    o.etree.SubElement(anchor, '{%s}cNvGraphicFramePr' % wp)
    anchor.append(inline.find('{http://schemas.openxmlformats.org/drawingml/2006/main}graphic'))
    inline.getparent().replace(inline, anchor)
    return node


def image_row(parts, root, op, asset_root, o):
    """2-4 pictures side by side in a borderless table, with optional sub-captions below."""
    images = op.get('images')
    if not isinstance(images, list) or not 2 <= len(images) <= 4:
        raise ValueError('images must list 2-4 pictures')
    academic = _academic()
    width = academic._text_width(root, o)
    column = width // len(images)
    drawings, captions = [], []
    wp = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
    first_id = max([int(x.get('id', 0)) for x in root.iter('{%s}docPr' % wp)] or [0]) + 1
    for index, image in enumerate(images):
        if not isinstance(image, dict) or 'path' not in image:
            raise ValueError('Each image needs a path')
        size = image.get('width_cm', round(column / 567 - 0.4, 2))
        picture = o.add_image(parts, root, {'path': image['path'], 'width_cm': size, 'alt': image.get('alt', image.get('caption', 'image'))}, asset_root)
        picture.find('.//{%s}docPr' % wp).set('id', str(first_id + index))
        drawings.append(picture)
        caption = image.get('caption', '')
        if not isinstance(caption, str) or len(caption) > 200 or '\n' in caption:
            raise ValueError('Image caption must be one line of at most 200 characters')
        captions.append(caption)
    node = o.etree.Element(o.q(o.W, 'tbl'))
    prop = o.etree.SubElement(node, o.q(o.W, 'tblPr'))
    o.etree.SubElement(prop, o.q(o.W, 'tblW'), attrib={o.q(o.W, 'w'): str(column * len(images)), o.q(o.W, 'type'): 'dxa'})
    o.etree.SubElement(prop, o.q(o.W, 'jc')).set(o.q(o.W, 'val'), 'center')
    _borders(o, prop, 'tblBorders', {})
    o.etree.SubElement(prop, o.q(o.W, 'tblLayout')).set(o.q(o.W, 'type'), 'fixed')
    grid = o.etree.SubElement(node, o.q(o.W, 'tblGrid'))
    for _ in images:
        o.etree.SubElement(grid, o.q(o.W, 'gridCol')).set(o.q(o.W, 'w'), str(column))
    rows = [drawings] + ([captions] if any(captions) else [])
    for r, row in enumerate(rows):
        tr = o.etree.SubElement(node, o.q(o.W, 'tr'))
        o.etree.SubElement(o.etree.SubElement(tr, o.q(o.W, 'trPr')), o.q(o.W, 'cantSplit'))
        for item in row:
            tc = o.etree.SubElement(tr, o.q(o.W, 'tc'))
            tcp = o.etree.SubElement(tc, o.q(o.W, 'tcPr'))
            o.etree.SubElement(tcp, o.q(o.W, 'tcW'), attrib={o.q(o.W, 'w'): str(column), o.q(o.W, 'type'): 'dxa'})
            if r == 0:
                _cell_align(o, item, 'center')
                prop_p = item.find(o.q(o.W, 'pPr'))
                o.etree.SubElement(prop_p, o.q(o.W, 'keepNext'))
                tc.append(item)
            else:
                tc.append(_cell_paragraph(o, {'text': item}, 'center', {'size': 9}))
    return node


# ---------------------------------------------------------------- headers and footers

HF = {'header': ('hdr', 'headerReference', 'header', 'application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml'),
      'footer': ('ftr', 'footerReference', 'footer', 'application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml')}


def header_footer(parts, root, op, o):
    which = op.get('part')
    if which not in HF:
        raise ValueError('part must be header or footer')
    tag, reference, relation, content_type = HF[which]
    align = op.get('align', 'center')
    if align not in ('left', 'center', 'right'):
        raise ValueError('align must be left, center or right')
    body = root.find(o.q(o.W, 'body'))
    section = body.find(o.q(o.W, 'sectPr'))
    if section is None:
        section = o.etree.SubElement(body, o.q(o.W, 'sectPr'))
    rels = o.parse_xml(parts[o.DOC_RELS])
    existing = next((ref for ref in section.findall(o.q(o.W, reference)) if ref.get(o.q(o.W, 'type'), 'default') == 'default'), None)
    if existing is not None:
        target = next(rel.get('Target') for rel in rels if rel.get('Id') == existing.get(o.q(o.R, 'id')))
        name = 'word/' + target.lstrip('/').removeprefix('word/')
    else:
        index = next(i for i in range(1, 100) if f'word/{relation}{i}.xml' not in parts)
        name = f'word/{relation}{index}.xml'
        used = {rel.get('Id') for rel in rels}
        rid = next(f'rId{relation.title()}{i}' for i in range(1, 100) if f'rId{relation.title()}{i}' not in used)
        o.etree.SubElement(rels, o.q(o.RELS, 'Relationship'), Id=rid,
            Type=f'http://schemas.openxmlformats.org/officeDocument/2006/relationships/{relation}', Target=f'{relation}{index}.xml')
        parts[o.DOC_RELS] = o.xml_bytes(rels)
        types = o.parse_xml(parts[o.CT_FILE])
        o.etree.SubElement(types, o.q(o.CT, 'Override'), PartName='/' + name, ContentType=content_type)
        parts[o.CT_FILE] = o.xml_bytes(types)
        ref = o.etree.Element(o.q(o.W, reference))
        ref.set(o.q(o.W, 'type'), 'default')
        ref.set(o.q(o.R, 'id'), rid)
        # Header/footer references come first in sectPr.
        position = sum(1 for child in section if child.tag in (o.q(o.W, 'headerReference'), o.q(o.W, 'footerReference')))
        section.insert(position, ref)
    part = o.etree.Element(o.q(o.W, tag), nsmap={'w': o.W, 'r': o.R, 'm': M})
    paragraph = o.etree.SubElement(part, o.q(o.W, 'p'))
    prop = o.etree.SubElement(paragraph, o.q(o.W, 'pPr'))
    if op.get('border'):
        edge = o.etree.SubElement(o.etree.SubElement(prop, o.q(o.W, 'pBdr')), o.q(o.W, 'bottom' if which == 'header' else 'top'))
        for key, value in (('val', 'single'), ('sz', '6'), ('space', '1'), ('color', '808080')):
            edge.set(o.q(o.W, key), value)
    o.etree.SubElement(prop, o.q(o.W, 'jc')).set(o.q(o.W, 'val'), align)
    base = {'size': op['size']} if op.get('size') is not None else {'size': 9}
    paragraph.extend(inline_nodes(o, text=op.get('text'), runs=op.get('runs'), base=base, allow_page_fields=True))
    o.etree.cleanup_namespaces(part, top_nsmap=_academic().STANDARD_PREFIXES)
    parts[name] = o.xml_bytes(part)
