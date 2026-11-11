"""Native Word charts: c:chart plus an embedded workbook. Never a picture or a shape stack."""
import io
import posixpath
import re
from lxml import etree

C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
RELS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
CHART_REL = R + '/chart'
PACKAGE_REL = R + '/package'
SUPPORTED = {'column', 'bar', 'line', 'area', 'pie', 'doughnut', 'scatter', 'bubble'}
ALIASES = {'donut': 'doughnut', 'column_clustered': 'column', 'bar_clustered': 'bar'}


def canonical(kind):
    kind = (kind or '').lower().replace('-', '_')
    return ALIASES.get(kind, kind)


def _q(ns, name):
    return '{%s}%s' % (ns, name)


def _el(parent, ns, name, **attrs):
    node = etree.SubElement(parent, _q(ns, name))
    for key, value in attrs.items():
        node.set(key if ':' not in key else _q(R if key.startswith('r:') else ns, key.split(':', 1)[-1]), str(value))
    return node


def _val(parent, ns, name, value):
    return _el(parent, ns, name, val=value)


def workbook(spec):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Sheet1'
    kind = canonical(spec['type'])
    series = spec['series']
    if kind in ('scatter', 'bubble'):
        sheet['A1'] = 'X'
        for index, item in enumerate(series, 1):
            column = index + 1
            sheet.cell(1, column, item['name'])
            points = item['values']
            for row, point in enumerate(points, 2):
                if kind == 'scatter':
                    sheet.cell(row, 1, point[0])
                    sheet.cell(row, column, point[1])
                else:
                    sheet.cell(row, 1, point[0])
                    sheet.cell(row, column, point[1])
                    sheet.cell(row, column + len(series), point[2])
        if kind == 'bubble':
            for index, item in enumerate(series):
                sheet.cell(1, 1 + len(series) + 1 + index, item['name'] + ' size')
    else:
        categories = spec.get('categories') or []
        for row, category in enumerate(categories, 2):
            sheet.cell(row, 1, category)
        for index, item in enumerate(series, 2):
            sheet.cell(1, index, item['name'])
            for row, value in enumerate(item['values'], 2):
                sheet.cell(row, index, value)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def _cache_str(parent, values):
    cache = _el(parent, C, 'strCache')
    _val(cache, C, 'ptCount', len(values))
    for index, value in enumerate(values):
        point = _el(cache, C, 'pt', idx=index)
        _el(point, C, 'v').text = str(value)
    return cache


def _cache_num(parent, values):
    cache = _el(parent, C, 'numCache')
    _el(cache, C, 'formatCode').text = 'General'
    _val(cache, C, 'ptCount', len(values))
    for index, value in enumerate(values):
        point = _el(cache, C, 'pt', idx=index)
        _el(point, C, 'v').text = str(value)
    return cache


def _ref(parent, tag, formula, values, numeric):
    holder = _el(parent, C, tag)
    ref = _el(holder, C, 'numRef' if numeric else 'strRef')
    _el(ref, C, 'f').text = formula
    (_cache_num if numeric else _cache_str)(ref, values)


# Explicit colours: documents without a theme part give theme-derived series no fill.
PALETTE = ('2F5597', 'C55A11', '548235', 'BF9000', '7030A0', '2E75B6', 'C00000', '00B0A0')


def _fill(parent, color, line_only=False):
    shape = _el(parent, C, 'spPr')
    if line_only:
        line = _el(shape, A, 'ln', w=22225)
        _val(_el(line, A, 'solidFill'), A, 'srgbClr', color)
    else:
        _val(_el(shape, A, 'solidFill'), A, 'srgbClr', color)
    return shape


def _series(parent, index, item, formula_value, categories, cat_formula, kind, colors=PALETTE):
    ser = _el(parent, C, 'ser')
    _val(ser, C, 'idx', index)
    _val(ser, C, 'order', index)
    _el(_el(ser, C, 'tx'), C, 'v').text = str(item['name'])
    _fill(ser, colors[index % len(colors)], line_only=kind in ('line', 'scatter'))
    if kind in ('pie', 'doughnut'):
        for point in range(len(categories)):
            data_point = _el(ser, C, 'dPt')
            _val(data_point, C, 'idx', point)
            _fill(data_point, colors[point % len(colors)])
    if kind in ('scatter', 'bubble'):
        xs = [point[0] for point in item['values']]
        ys = [point[1] for point in item['values']]
        _ref(ser, 'xVal', f'Sheet1!$A$2:$A${len(xs)+1}', xs, True)
        column = chr(ord('B') + index)
        _ref(ser, 'yVal', f'Sheet1!${column}$2:${column}${len(ys)+1}', ys, True)
        if kind == 'bubble':
            sizes = [point[2] for point in item['values']]
            size_column = chr(ord('B') + len(categories) + index)
            _ref(ser, 'bubbleSize', f'Sheet1!${size_column}$2:${size_column}${len(sizes)+1}', sizes, True)
    else:
        _ref(ser, 'cat', cat_formula, categories, False)
        _ref(ser, 'val', formula_value, item['values'], True)
    if kind in ('line', 'scatter'):
        # Office smooths lines unless told otherwise, which misrepresents measured data.
        _val(ser, C, 'smooth', 0)
    return ser


def _axes(plot, kind):
    if kind in ('pie', 'doughnut'):
        return
    cat = _el(plot, C, 'catAx' if kind != 'bar' else 'catAx')
    _val(cat, C, 'axId', 1)
    _val(_el(cat, C, 'scaling'), C, 'orientation', 'minMax')
    _val(cat, C, 'delete', 0)
    _val(cat, C, 'axPos', 'l' if kind == 'bar' else 'b')
    _val(cat, C, 'crossAx', 2)
    val = _el(plot, C, 'valAx')
    _val(val, C, 'axId', 2)
    _val(_el(val, C, 'scaling'), C, 'orientation', 'minMax')
    _val(val, C, 'delete', 0)
    _val(val, C, 'axPos', 'b' if kind == 'bar' else 'l')
    _val(val, C, 'crossAx', 1)


def chart_xml(spec):
    kind = canonical(spec['type'])
    if kind not in SUPPORTED:
        raise ValueError('Unsupported chart type: ' + str(spec.get('type')))
    series = spec.get('series') or []
    if not series or len(series) > 8:
        raise ValueError('A chart needs 1 to 8 series')
    categories = list(spec.get('categories') or [])
    if kind not in ('scatter', 'bubble'):
        if not categories or any(len(item['values']) != len(categories) for item in series):
            raise ValueError('Each series must match the category count')
    else:
        width = 3 if kind == 'bubble' else 2
        if any(not item['values'] or any(not isinstance(point, (list, tuple)) or len(point) != width for point in item['values']) for item in series):
            raise ValueError('Scatter points are [x, y]; bubble points are [x, y, size]')
        categories = series
    root = etree.Element(_q(C, 'chartSpace'), nsmap={'c': C, 'a': A, 'r': R})
    chart = _el(root, C, 'chart')
    if spec.get('title'):
        title = _el(chart, C, 'title')
        text = _el(_el(_el(title, C, 'tx'), C, 'rich'), A, 'p')
        run = _el(text, A, 'r')
        _el(run, A, 't').text = str(spec['title'])[:120]
    plot = _el(chart, C, 'plotArea')
    _el(plot, C, 'layout')
    tag = {'column': 'barChart', 'bar': 'barChart', 'line': 'lineChart', 'area': 'areaChart',
        'pie': 'pieChart', 'doughnut': 'doughnutChart', 'scatter': 'scatterChart', 'bubble': 'bubbleChart'}[kind]
    body = _el(plot, C, tag)
    colors = spec.get('colors') or PALETTE
    if not isinstance(colors, (list, tuple)) or any(not isinstance(c, str) or not re.fullmatch(r'[0-9A-Fa-f]{6}', c) for c in colors):
        raise ValueError('Chart colors must be RRGGBB strings')
    if kind in ('pie', 'doughnut'):
        _val(body, C, 'varyColors', 1)
    if kind in ('column', 'bar'):
        _val(body, C, 'barDir', 'bar' if kind == 'bar' else 'col')
        _val(body, C, 'grouping', 'clustered')
    elif kind in ('line', 'area'):
        _val(body, C, 'grouping', 'standard')
    elif kind == 'scatter':
        _val(body, C, 'scatterStyle', 'lineMarker')
    elif kind == 'bubble':
        _val(body, C, 'bubbleScale', 100)
    last = len(categories) if kind not in ('scatter', 'bubble') else len(series[0]['values'])
    cat_formula = f'Sheet1!$A$2:$A${last+1}'
    for index, item in enumerate(series):
        column = chr(ord('B') + index)
        _series(body, index, item, f'Sheet1!${column}$2:${column}${last+1}', categories, cat_formula, kind, [c.upper() for c in colors])
    if kind not in ('pie', 'doughnut', 'scatter', 'bubble'):
        _val(body, C, 'axId', 1)
        _val(body, C, 'axId', 2)
    if kind == 'doughnut':
        _val(body, C, 'holeSize', 50)
    _axes(plot, kind)
    legend = _el(chart, C, 'legend')
    _val(legend, C, 'legendPos', 'b')
    _val(chart, C, 'plotVisOnly', 1)
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _next_id(parts, prefix):
    index = 1
    while f'word/{prefix}{index}.xml' in parts or f'word/embeddings/chart-data-{index}.xlsx' in parts:
        index += 1
    return index


def _content_default(parts, office, extension, content_type):
    root = office.parse_xml(parts[office.CT_FILE])
    if not any(node.get('Extension') == extension for node in root):
        office.etree.SubElement(root, office.q(office.CT, 'Default'), Extension=extension, ContentType=content_type)
        parts[office.CT_FILE] = office.xml_bytes(root)


def _override(parts, office, name, content_type):
    root = office.parse_xml(parts[office.CT_FILE])
    part = '/' + name
    if not any(node.get('PartName') == part for node in root):
        office.etree.SubElement(root, office.q(office.CT, 'Override'), PartName=part, ContentType=content_type)
        parts[office.CT_FILE] = office.xml_bytes(root)


def paragraph(parts, spec, office, root):
    """Add one chart part with its embedded workbook; return the w:p that shows it.

    The caller places the paragraph in ``root`` (the parsed main document part),
    so this function never writes word/document.xml itself.
    """
    kind = canonical(spec.get('type'))
    if kind not in SUPPORTED:
        raise ValueError('Unsupported native chart type: ' + str(spec.get('type')))
    if not isinstance(spec.get('series'), list) or not 1 <= len(spec['series']) <= 12:
        raise ValueError('A chart needs 1-12 series')
    index = _next_id(parts, 'charts/chart')
    chart_name = f'word/charts/chart{index}.xml'
    book_name = f'word/embeddings/chart-data-{index}.xlsx'
    parts[chart_name] = chart_xml(dict(spec, type=kind))
    parts[book_name] = workbook(dict(spec, type=kind))
    rels_name = f'word/charts/_rels/chart{index}.xml.rels'
    rels = etree.Element(_q(RELS, 'Relationships'), nsmap={None: RELS})
    etree.SubElement(rels, _q(RELS, 'Relationship'), Id='rId1', Type=PACKAGE_REL, Target='../embeddings/' + posixpath.basename(book_name))
    parts[rels_name] = etree.tostring(rels, xml_declaration=True, encoding='UTF-8', standalone=True)
    _content_default(parts, office, 'xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    _override(parts, office, chart_name, 'application/vnd.openxmlformats-officedocument.drawingml.chart+xml')
    document_rels = office.parse_xml(parts[office.DOC_RELS])
    used = {node.get('Id') for node in document_rels}
    serial = index
    while 'rIdChart%d' % serial in used:
        serial += 1
    rid = 'rIdChart%d' % serial
    office.etree.SubElement(document_rels, office.q(office.RELS, 'Relationship'), Id=rid, Type=CHART_REL, Target=f'charts/chart{index}.xml')
    parts[office.DOC_RELS] = office.xml_bytes(document_rels)
    width = int(float(spec.get('width_cm', 14)) * 360000)
    height = int(float(spec.get('height_cm', 8)) * 360000)
    if not 2 * 360000 <= width <= 17 * 360000 or not 2 * 360000 <= height <= 20 * 360000:
        raise ValueError('Chart size must be 2-17 cm wide and 2-20 cm high')
    docpr = max([int(node.get('id', 0)) for node in root.iter(office.q(WP, 'docPr'))] or [0]) + 1
    node = office.etree.Element(office.q(W, 'p'))
    run = office.etree.SubElement(node, office.q(W, 'r'))
    drawing = office.etree.SubElement(run, office.q(W, 'drawing'))
    inline = office.etree.SubElement(drawing, office.q(WP, 'inline'))
    office.etree.SubElement(inline, office.q(WP, 'extent'), cx=str(width), cy=str(height))
    office.etree.SubElement(inline, office.q(WP, 'docPr'), id=str(docpr), name='Chart %d' % index)
    graphic = office.etree.SubElement(inline, office.q(A, 'graphic'))
    data = office.etree.SubElement(graphic, office.q(A, 'graphicData'), uri=C)
    chart = office.etree.SubElement(data, _q(C, 'chart'))
    chart.set(office.q(R, 'id'), rid)
    return node


def inventory(parts):
    charts = []
    for name in sorted(parts):
        if not name.startswith('word/charts/chart') or not name.endswith('.xml') or '/_rels/' in name:
            continue
        rels = name.replace('word/charts/', 'word/charts/_rels/') + '.rels'
        workbook_ok = False
        if rels in parts:
            root = etree.fromstring(parts[rels])
            for rel in root:
                target = rel.get('Target', '')
                path = posixpath.normpath(posixpath.join('word/charts', target))
                workbook_ok = path in parts and parts[path][:2] == b'PK'
        charts.append(dict(part=name, workbook=workbook_ok, native=workbook_ok))
    return charts


def verify_workbook(data, spec):
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(data), data_only=False)
    sheet = book.active
    kind = canonical(spec['type'])
    if kind in ('scatter', 'bubble'):
        point = spec['series'][0]['values'][0]
        return sheet['A2'].value == point[0] and sheet['B2'].value == point[1]
    return sheet['A2'].value == spec['categories'][0] and sheet['B2'].value == spec['series'][0]['values'][0]


def audit(parts, plan):
    found = inventory(parts)
    native = [item for item in found if item['native']]
    result = dict(native_charts=len(native), fallback_charts=0, all_native=False, inventory=found, audited=False)
    if not plan:
        result['note'] = 'No plan; inventory only'
        return result
    requested = 0
    fallbacks = []
    for section in plan.get('sections') or []:
        requested += int(section.get('statistical_charts') or 0)
        fallbacks.extend(section.get('chart_fallbacks') or [])
    problems = []
    for item in fallbacks:
        kind = canonical(item.get('type'))
        if kind in SUPPORTED:
            problems.append('Supported chart type cannot be declared as a fallback: ' + kind)
        elif not item.get('reason'):
            problems.append('A fallback chart requires a reason')
    fallback_count = len(fallbacks)
    if native and len(native) + fallback_count < requested:
        problems.append('Native charts plus declared fallbacks are fewer than statistical_charts')
    if requested and not native and not fallbacks:
        problems.append('statistical_charts were requested but the package has no native chart')
    result.update(fallback_charts=fallback_count, requested=requested, problems=problems,
        all_native=not problems and requested > 0 and fallback_count == 0 and len(native) >= requested,
        audited=not problems)
    return result
