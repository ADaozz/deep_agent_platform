"""Native OOXML charts: category, XY, stock, surface, combination and ChartEx.

No chart is flattened into a picture. Each chart owns an embedded workbook.
Modern charts use Microsoft's Office 2016 ChartEx namespace; renderer support
is reported separately from the ability to author an editable chart.
"""
import copy
import io
import json
import math

from lxml import etree
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from pptx.chart.data import CategoryChartData, XyChartData, BubbleChartData
from pptx.enum.chart import XL_CHART_TYPE as CT
from pptx.opc.package import XmlPart, Part, PartFactory
from pptx.opc.packuri import PackURI
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn

C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
CX = 'http://schemas.microsoft.com/office/drawing/2014/chartex'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CX_REL = 'http://schemas.microsoft.com/office/2014/relationships/chartEx'
PACKAGE_REL = R + '/package'
XLSX_CT = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
CX_CT = 'application/vnd.ms-office.chartex+xml'
PartFactory.part_type_for[CX_CT] = XmlPart

# All 73 legacy PowerPoint chart enum variants, plus readable/backward aliases.
CHART_TYPES = {t.name.lower(): t for t in CT}
CHART_TYPES.update(bar=CT.COLUMN_CLUSTERED, column=CT.COLUMN_CLUSTERED,
                   line=CT.LINE_MARKERS, doughnut=CT.DOUGHNUT,
                   scatter=CT.XY_SCATTER, bubble=CT.BUBBLE, line_plain=CT.LINE)
MODERN = {'waterfall': 'waterfall', 'funnel': 'funnel',
          'histogram': 'clusteredColumn', 'pareto': 'clusteredColumn',
          'box_whisker': 'boxWhisker', 'treemap': 'treemap',
          'sunburst': 'sunburst', 'map': 'regionMap'}
ALIASES = {'bar_horizontal': 'bar_clustered', 'column_stacked_100_percent': 'column_stacked_100',
           'donut': 'doughnut', 'box_plot': 'box_whisker', 'region_map': 'map',
           'combo': 'combination'}
SUPPORTED = set(CHART_TYPES) | set(MODERN) | {'combination'} | set(ALIASES)
XY = {t.name.lower() for t in CT if t.name.startswith('XY_SCATTER')} | {'scatter'}
BUBBLE = {'bubble', 'bubble_three_d_effect'}
RADIAL = {'pie', 'pie_exploded', 'three_d_pie', 'three_d_pie_exploded',
          'pie_of_pie', 'bar_of_pie', 'doughnut', 'doughnut_exploded'}


def canonical(kind):
    kind = ALIASES.get(kind, kind)
    if kind not in SUPPORTED:
        raise ValueError('Unknown native chart type: ' + str(kind))
    return kind


def family(kind):
    kind = canonical(kind)
    if kind in XY: return 'xy'
    if kind in BUBBLE: return 'bubble'
    if kind in MODERN: return 'chartex'
    return 'category'


def _number(v):
    return type(v) in (int, float) and math.isfinite(v)


def validate(payload):
    if not isinstance(payload, dict): raise ValueError('chart must be an object')
    kind = canonical(payload.get('type', 'bar'))
    series = payload.get('series')
    if not isinstance(series, list) or not 1 <= len(series) <= 12:
        raise ValueError('Chart needs 1–12 named series')
    for s in series:
        if not isinstance(s, dict) or not isinstance(s.get('name'), str) or not s['name'].strip() or len(s['name']) > 160:
            raise ValueError('Every chart series needs a bounded nonempty name')
    if kind in XY | BUBBLE:
        for s in series:
            points = s.get('points')
            arity = 3 if kind in BUBBLE else 2
            if not isinstance(points, list) or not 1 <= len(points) <= 2000 or any(
                    not isinstance(p, (list, tuple)) or len(p) != arity or not all(_number(v) for v in p)
                    or (arity == 3 and p[2] < 0) for p in points):
                raise ValueError('XY points must be finite [x,y]; bubble points [x,y,size] with nonnegative size (1–2000 points)')
        return kind
    categories = payload.get('categories')
    if not isinstance(categories, list) or not 1 <= len(categories) <= 2000 or any(
            not isinstance(v, (str, list, tuple)) or (isinstance(v, str) and len(v) > 160)
            or (not isinstance(v, str) and (not 1 <= len(v) <= 8 or any(not isinstance(x, str) or len(x) > 160 for x in v)))
            for v in categories):
        raise ValueError('Chart needs 1–2000 bounded categories (hierarchy paths may have 1–8 levels)')
    for s in series:
        values = s.get('values')
        if not isinstance(values, list) or len(values) != len(categories) or not all(_number(v) for v in values):
            raise ValueError('Finite chart values must match categories')
    if kind in RADIAL - {'doughnut', 'doughnut_exploded'} and len(series) != 1:
        raise ValueError('A pie chart shows exactly one series')
    if kind in RADIAL | {'treemap', 'sunburst', 'funnel', 'pareto'}:
        if any(v < 0 for s in series for v in s['values']) or any(sum(s['values']) <= 0 for s in series):
            raise ValueError('This chart needs nonnegative values and a positive total')
    if kind in {'treemap', 'sunburst', 'funnel', 'map', 'waterfall', 'histogram', 'pareto'} and len(series) != 1:
        raise ValueError('This chart needs exactly one series')
    if kind in {'treemap', 'sunburst'} and any(not isinstance(v, (list, tuple)) for v in categories):
        raise ValueError('Hierarchy charts require category paths such as [region, product]')
    if any(not isinstance(v, str) for v in categories) and kind not in {'treemap', 'sunburst'}:
        raise ValueError('Hierarchy categories are only supported for treemap and sunburst')
    if kind.startswith('stock_'):
        expected = {'stock_hlc': 3, 'stock_ohlc': 4, 'stock_vhlc': 4, 'stock_vohlc': 5}[kind]
        if len(series) != expected: raise ValueError('Stock series order: [Volume,] [Open,] High, Low, Close')
        offset = int(kind in {'stock_vhlc', 'stock_vohlc'})
        ohlc = kind in {'stock_ohlc', 'stock_vohlc'}
        price = series[offset:]
        for i in range(len(categories)):
            high, low, close = [s['values'][i] for s in price[-3:]]
            if low > high or not low <= close <= high or (ohlc and not low <= price[0]['values'][i] <= high):
                raise ValueError('Stock prices require Low <= Open/Close <= High')
        if offset and any(v < 0 for v in series[0]['values']): raise ValueError('Stock volume must be nonnegative')
    if 'surface' in kind and (len(series) < 2 or len(categories) < 2):
        raise ValueError('Surface chart requires a grid with at least 2 rows and 2 columns')
    if kind == 'combination':
        if len(series) < 2: raise ValueError('Combination requires at least two series')
        allowed = {'column', 'bar', 'column_clustered', 'column_stacked', 'column_stacked_100', 'line', 'line_markers', 'area', 'area_stacked'}
        for i, s in enumerate(series):
            if s.get('type', 'column' if i == 0 else 'line') not in allowed:
                raise ValueError('Combination supports column, line and area series')
            if 'secondary_axis' in s and type(s['secondary_axis']) is not bool:
                raise ValueError('secondary_axis must be boolean')
        if all(s.get('secondary_axis', False) for s in series): raise ValueError('At least one series must use the primary axis')
    if kind in {'waterfall'}:
        totals = payload.get('totals', [])
        if not isinstance(totals, list) or any(type(i) is not int or not 0 <= i < len(categories) for i in totals):
            raise ValueError('waterfall totals must be zero-based category indices')
    if kind in {'histogram', 'pareto'}:
        for key in ('bin_count', 'bin_size'):
            if key in payload and (not _number(payload[key]) or payload[key] <= 0): raise ValueError('Binning must be positive')
        if 'bin_count' in payload and (type(payload['bin_count']) is not int or payload['bin_count'] > 200):
            raise ValueError('bin_count must be an integer in 1–200')
        if 'bin_count' in payload and 'bin_size' in payload: raise ValueError('Choose bin_count or bin_size')
    if kind == 'box_whisker' and len(categories) < 4: raise ValueError('Box plot needs at least four observations per group')
    return kind


def chart_data(payload):
    kind = validate(payload)
    if kind in XY | BUBBLE:
        data = BubbleChartData() if kind in BUBBLE else XyChartData()
        for item in payload['series']:
            series = data.add_series(item['name'])
            for point in item['points']: series.add_data_point(*point)
    else:
        data = CategoryChartData()
        data.categories = [' / '.join(v) if isinstance(v, (list, tuple)) else v for v in payload['categories']]
        for item in payload['series']: data.add_series(item['name'], item['values'])
    return data


def node(parent, tag, text=None, **attrs):
    ns, name = tag.split(':')
    e = etree.SubElement(parent, '{' + {'c': C, 'cx': CX, 'a': A}[ns] + '}' + name,
                         {k: str(v) for k, v in attrs.items()})
    if text is not None: e.text = str(text)
    return e


def _base_type(kind):
    if kind.startswith(('cone_', 'cylinder_', 'pyramid_')) or kind.startswith('three_d_'):
        if 'pie' in kind: return CT.PIE_EXPLODED if 'exploded' in kind else CT.PIE
        if 'line' in kind: return CT.LINE_MARKERS
        if 'area' in kind:
            return CT.AREA_STACKED_100 if '100' in kind else CT.AREA_STACKED if 'stacked' in kind else CT.AREA
        horizontal = '_bar_' in kind
        return getattr(CT, ('BAR_' if horizontal else 'COLUMN_') + ('STACKED_100' if '100' in kind else 'STACKED' if 'stacked' in kind else 'CLUSTERED'))
    if kind in {'pie_of_pie', 'bar_of_pie'}: return CT.PIE
    if kind.startswith('stock_'): return CT.LINE
    if 'surface' in kind: return CT.AREA
    return CHART_TYPES[kind]


def _strip(parent, names):
    for child in list(parent):
        if etree.QName(child).localname in names: parent.remove(child)


def _secondary_axes(plot_area, ids):
    result = []
    for axis in list(plot_area):
        if etree.QName(axis).localname not in ('catAx', 'valAx'): continue
        clone = copy.deepcopy(axis)
        for e in clone:
            if e.tag in ('{' + C + '}axId', '{' + C + '}crossAx'):
                e.set('val', str(ids.get(int(e.get('val')), int(e.get('val')))))
        if clone.tag == '{' + C + '}valAx':
            clone.find('{' + C + '}axPos').set('val', 'r')
            _strip(clone, {'majorGridlines'})
        else:
            existing = clone.find('{' + C + '}delete')
            if existing is not None: existing.set('val', '1')
        result.append(clone)
    for e in result: plot_area.append(e)


def legacy_xml(payload, data):
    kind = canonical(payload.get('type', 'bar'))
    if kind == 'combination':
        root = parse_xml(data.xml_bytes(CT.COLUMN_CLUSTERED))
        plot_area = root.find('.//{' + C + '}plotArea')
        base = plot_area.find('{' + C + '}barChart')
        axis_ids = [int(e.get('val')) for e in base.findall('{' + C + '}axId')]
        secondary = {v: v + 1000 for v in axis_ids}
        plot_area.remove(base)
        position = 1
        for i, item in enumerate(payload['series']):
            t = canonical(item.get('type', 'column' if i == 0 else 'line'))
            source = parse_xml(data.xml_bytes(CHART_TYPES[t])).find('.//{' + C + '}plotArea')
            plot = next(e for e in source if e.tag.endswith('Chart'))
            for j, ser in enumerate(list(plot.findall('{' + C + '}ser'))):
                if i != j: plot.remove(ser)
            for j, e in enumerate(plot.findall('{' + C + '}axId')):
                e.set('val', str(secondary[axis_ids[j]] if item.get('secondary_axis') else axis_ids[j]))
            plot_area.insert(position, plot); position += 1
        if any(s.get('secondary_axis') for s in payload['series']): _secondary_axes(plot_area, secondary)
        return root
    root = parse_xml(data.xml_bytes(_base_type(kind)))
    plot_area = root.find('.//{' + C + '}plotArea')
    plot = next(e for e in plot_area if e.tag.endswith('Chart'))
    if kind in {'pie_of_pie', 'bar_of_pie'}:
        plot.tag = '{' + C + '}ofPieChart'
        plot.insert(0, etree.Element('{' + C + '}ofPieType', val='pie' if kind == 'pie_of_pie' else 'bar'))
        node(plot, 'c:gapWidth', val=150); node(plot, 'c:secondPieSize', val=75)
    elif kind.startswith('stock_'):
        plot.tag = '{' + C + '}stockChart'
        _strip(plot, {'grouping', 'varyColors', 'marker', 'smooth'})
        series = plot.findall('{' + C + '}ser')
        for ser in series: _strip(ser, {'marker', 'smooth'})
        axis_pos = next((i for i,e in enumerate(plot) if e.tag == '{' + C + '}axId'), len(plot))
        plot.insert(axis_pos, etree.Element('{' + C + '}hiLowLines'))
        if kind in {'stock_ohlc', 'stock_vohlc'}:
            bars = etree.Element('{' + C + '}upDownBars'); node(bars, 'c:gapWidth', val=150)
            node(bars, 'c:upBars'); node(bars, 'c:downBars'); plot.insert(axis_pos + 1, bars)
        if kind in {'stock_vhlc', 'stock_vohlc'}:
            volume = copy.deepcopy(plot)
            volume.tag = '{' + C + '}barChart'; _strip(volume, {'hiLowLines', 'upDownBars'})
            volume.insert(0, etree.Element('{' + C + '}barDir', val='col'))
            volume.insert(1, etree.Element('{' + C + '}grouping', val='clustered'))
            for ser in list(volume.findall('{' + C + '}ser'))[1:]: volume.remove(ser)
            plot.remove(series[0])
            axis_ids = [int(e.get('val')) for e in plot.findall('{' + C + '}axId')]
            ids = {v: v + 1000 for v in axis_ids}
            for e in plot.findall('{' + C + '}axId'): e.set('val', str(ids[int(e.get('val'))]))
            plot_area.insert(1, volume); _secondary_axes(plot_area, ids)
    elif 'surface' in kind:
        # OOXML surfaceChart is the 2-D contour view; surface3DChart is the 3-D surface.
        plot.tag = '{' + C + '}' + ('surfaceChart' if 'top_view' in kind else 'surface3DChart')
        _strip(plot, {'grouping', 'varyColors', 'dLbls', 'dropLines'})
        plot.insert(0, etree.Element('{' + C + '}wireframe', val='1' if 'wireframe' in kind else '0'))
        for ser in plot.findall('{' + C + '}ser'): _strip(ser, {'pictureOptions', 'dPt', 'dLbls', 'trendline', 'errBars'})
        _depth_axis(plot_area, plot)
        _view3d(root, top='top_view' in kind)
    elif kind.startswith(('three_d_', 'cone_', 'cylinder_', 'pyramid_')):
        original = etree.QName(plot).localname
        plot.tag = '{' + C + '}' + original.replace('Chart', '3DChart')
        _strip(plot, {'overlap', 'marker', 'smooth', 'holeSize', 'firstSliceAng'})
        for ser in plot.findall('{' + C + '}ser'):
            _strip(ser, {'marker', 'smooth'})
        if original == 'barChart':
            if kind in {'three_d_column', 'cone_col', 'cylinder_col', 'pyramid_col'}:
                plot.find('{' + C + '}grouping').set('val', 'standard')
            insert = next((i for i,e in enumerate(plot) if e.tag == '{' + C + '}axId'), len(plot))
            shape = kind.split('_')[0] if not kind.startswith('three_d_') else 'box'
            plot.insert(insert, etree.Element('{' + C + '}shape', val=shape))
        if original != 'pieChart': _depth_axis(plot_area, plot)
        _view3d(root)
    return root


def _view3d(root, top=False):
    chart = root.find('{' + C + '}chart')
    view = etree.Element('{' + C + '}view3D')
    node(view, 'c:rotX', val=90 if top else 15); node(view, 'c:rotY', val=0 if top else 20)
    node(view, 'c:rAngAx', val=1); node(view, 'c:perspective', val=30)
    chart.insert(list(chart).index(chart.find('{' + C + '}plotArea')), view)


def _depth_axis(plot_area, plot):
    axis = etree.Element('{' + C + '}serAx')
    node(axis, 'c:axId', val=50000001); scaling = node(axis, 'c:scaling'); node(scaling, 'c:orientation', val='minMax')
    node(axis, 'c:delete', val=0); node(axis, 'c:axPos', val='b')
    node(axis, 'c:tickLblPos', val='nextTo')
    value_axis = plot_area.find('{' + C + '}valAx')
    node(axis, 'c:crossAx', val=value_axis.find('{' + C + '}axId').get('val'))
    node(axis, 'c:crosses', val='autoZero'); node(axis, 'c:tickLblSkip', val=1); node(axis, 'c:tickMarkSkip', val=1)
    node(plot, 'c:axId', val=50000001); plot_area.append(axis)


def _workbook(payload):
    book = Workbook(); sheet = book.active; sheet.title = 'Sheet1'
    cats = payload['categories']; depth = max(len(c) if isinstance(c, (list, tuple)) else 1 for c in cats)
    sheet.append(['Category' + str(i + 1) for i in range(depth)] + [s['name'] for s in payload['series']])
    for i, cat in enumerate(cats):
        path = list(cat) if isinstance(cat, (list, tuple)) else [cat]
        # Explicit string cells prevent labels beginning '=' becoming formulas.
        for j, label in enumerate(path + [''] * (depth - len(path)), 1):
            cell = sheet.cell(i + 2, j, label); cell.data_type = 's'
        for j, s in enumerate(payload['series'], depth + 1): sheet.cell(i + 2, j, s['values'][i])
    for cell in sheet[1]: cell.data_type = 's'
    out = io.BytesIO(); book.save(out)
    return out.getvalue(), depth


def chartex_xml(payload, rid):
    kind = validate(payload); cats = payload['categories']; count = len(cats)
    root = etree.Element('{' + CX + '}chartSpace', nsmap={'cx': CX, 'a': A, 'r': R})
    cd = node(root, 'cx:chartData'); external = node(cd, 'cx:externalData'); external.set('{' + R + '}id', rid)
    external.set('{' + CX + '}autoUpdate', '0')
    depth = max(len(v) if isinstance(v, (list, tuple)) else 1 for v in cats)
    chart = node(root, 'cx:chart'); pa = node(chart, 'cx:plotArea'); region = node(pa, 'cx:plotAreaRegion')
    for i, item in enumerate(payload['series']):
        data = node(cd, 'cx:data', id=i)
        dim = node(data, 'cx:strDim', type='cat')
        node(dim, 'cx:f', f"Sheet1!$A$2:${get_column_letter(depth)}${count + 1}")
        # Cache hierarchy levels from leaf to root, matching Office's multi-level categories.
        for d in reversed(range(depth)):
            lvl = node(dim, 'cx:lvl', ptCount=count)
            for j, cat in enumerate(cats):
                path = list(cat) if isinstance(cat, (list, tuple)) else [cat]
                node(lvl, 'cx:pt', path[d] if d < len(path) else '', idx=j)
        dim = node(data, 'cx:numDim', type='val'); col = get_column_letter(depth + i + 1)
        node(dim, 'cx:f', f'Sheet1!${col}$2:${col}${count + 1}')
        lvl = node(dim, 'cx:lvl', ptCount=count, formatCode='General', name=item['name'])
        for j, val in enumerate(item['values']): node(lvl, 'cx:pt', val, idx=j)
        series = node(region, 'cx:series', layoutId=MODERN[kind], uniqueId=str(i), formatIdx=i)
        tx = node(series, 'cx:tx'); td = node(tx, 'cx:txData'); node(td, 'cx:f', f'Sheet1!${col}$1')
        node(td, 'cx:v', item['name']); node(series, 'cx:dataId', val=i)
        if kind in {'waterfall', 'histogram', 'pareto', 'box_whisker', 'treemap', 'sunburst', 'map'}:
            props = node(series, 'cx:layoutPr')
            if kind in {'treemap', 'sunburst'}: node(props, 'cx:parentLabelLayout', val='banner')
            if kind == 'waterfall': node(props, 'cx:visibility', connectorLines=1)
            if kind in {'histogram', 'pareto'}:
                if kind == 'pareto': node(props, 'cx:aggregation')
                else:
                    bins = node(props, 'cx:binning', intervalClosed='r')
                    if 'bin_size' in payload: node(bins, 'cx:binSize', payload['bin_size'])
                    else: node(bins, 'cx:binCount', payload.get('bin_count', 10))
            if kind == 'box_whisker': node(props, 'cx:statistics', quartileMethod='inclusive')
            if kind == 'map':
                node(props, 'cx:geography', projectionType='mercator', cultureLanguage='en-US', cultureRegion='US', attribution='')
            if kind == 'waterfall':
                totals = node(props, 'cx:subtotals')
                for j in payload.get('totals', []): node(totals, 'cx:idx', val=j)
        if kind in {'histogram', 'pareto'}:
            node(series, 'cx:axisId', val=1)
    if kind == 'pareto':
        line = node(region, 'cx:series', layoutId='paretoLine', ownerIdx=0, uniqueId='pareto-line', formatIdx=1)
        node(line, 'cx:axisId', val=2)
    if kind in {'waterfall', 'histogram', 'pareto', 'box_whisker'}:
        for i in range(3 if kind == 'pareto' else 2):
            axis = node(pa, 'cx:axis', id=i); node(axis, 'cx:catScaling' if i == 0 else 'cx:valScaling')
            node(axis, 'cx:tickLabels'); node(axis, 'cx:numFmt', formatCode='0%' if i == 2 else 'General', sourceLinked='0')
    node(chart, 'cx:legend', pos='b', align='ctr', overlay=0)
    return root


def is_chart(shape):
    return bool(shape.has_chart or shape._element.findall('.//{' + CX + '}chart'))


def chart_part(shape):
    refs = shape._element.findall('.//{' + CX + '}chart')
    return shape.part.related_part(refs[0].get('{' + R + '}id')) if refs else shape.chart.part


def infer_type(shape):
    """Read the actual plot family, without inventing category/XY semantics."""
    part = chart_part(shape)
    metadata = shape._element.findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr')
    if metadata:
        try:
            meta = json.loads(metadata[0].get('descr', '{}'))
            if meta.get('kind') == 'chart' and meta.get('chart',{}).get('type') in SUPPORTED:
                return canonical(meta['chart']['type'])
        except (ValueError, TypeError): pass
    if part.content_type == CX_CT:
        layout = part._element.find('.//{' + CX + '}series').get('layoutId')
        if layout == 'clusteredColumn':
            return 'pareto' if part._element.find('.//{' + CX + '}series[@layoutId="paretoLine"]') is not None else 'histogram'
        return next(k for k,v in MODERN.items() if v == layout)
    plots = [e for e in part._element.findall('.//{' + C + '}plotArea/*') if e.tag.endswith('Chart')]
    names = [etree.QName(e).localname for e in plots]
    if 'stockChart' in names:
        price = next(e for e in plots if e.tag == '{' + C + '}stockChart')
        return 'stock_' + ('v' if len(plots) > 1 else '') + ('ohlc' if len(price.findall('{' + C + '}ser')) == 4 else 'hlc')
    if any('surface' in name for name in names):
        plot = plots[0]; wire = plot.find('{' + C + '}wireframe')
        return 'surface' + ('_top_view' if plot.tag == '{' + C + '}surfaceChart' else '') + ('_wireframe' if wire is not None and wire.get('val') == '1' else '')
    if len(plots) > 1 and not any(e.tag == '{' + C + '}stockChart' for e in plots): return 'combination'
    # python-pptx can identify most legacy types even where its XML writer cannot create them.
    try:return shape.chart.chart_type.name.lower()
    except (ValueError, AttributeError):
        names = [etree.QName(e).localname for e in plots]
        if 'stockChart' in names:
            price = next(e for e in plots if e.tag == '{' + C + '}stockChart')
            return 'stock_' + ('v' if len(plots) > 1 else '') + ('ohlc' if len(price.findall('{' + C + '}ser')) == 4 else 'hlc')
        raise ValueError('Unrecognized native chart type; supply a supported chart payload')


def _set_root(part, root):
    external = part._element.find('{' + C + '}externalData')
    if external is not None and root.find('{' + C + '}externalData') is None:
        root.append(copy.deepcopy(external))
    part._element = root
    part.__dict__.pop('chart', None)
    part.__dict__.pop('chart_workbook', None)


def create(slide, payload, position):
    kind = validate(payload); data = chart_data(payload)
    shape = slide.shapes.add_chart(CT.COLUMN_CLUSTERED if kind in MODERN or kind == 'combination' else _base_type(kind), *position, data)
    if kind not in MODERN:
        _set_root(shape.chart.part, legacy_xml(payload, data))
        return shape
    package = slide.part.package
    workbook, _ = _workbook(payload)
    part = XmlPart(package.next_partname('/ppt/charts/chartEx%d.xml'), CX_CT, package, etree.Element('unused'))
    xlsx = Part(package.next_partname('/ppt/embeddings/Microsoft_Excel_Sheet%d.xlsx'), XLSX_CT, package, workbook)
    rid = part.relate_to(xlsx, PACKAGE_REL); part._element = chartex_xml(payload, rid)
    gd = shape._element.graphic.graphicData
    old = gd.chart.rId; gd.remove(gd.chart); gd.set('uri', CX)
    ref = etree.SubElement(gd, '{' + CX + '}chart', nsmap={'cx': CX}); ref.set('{' + R + '}id', slide.part.relate_to(part, CX_REL))
    slide.part.drop_rel(old)
    return shape


def replace_data(shape, payload):
    kind = validate(payload); part = chart_part(shape)
    if kind in MODERN:
        if part.content_type != CX_CT: raise ValueError('Changing chart type requires chart.change_type')
        rid = next(r.rId for r in part.rels.values() if r.reltype == PACKAGE_REL)
        xlsx = part.related_part(rid); xlsx._blob = _workbook(payload)[0]
        old = part._element; new = chartex_xml(payload, rid)
        before = old.findall('.//{' + CX + '}series')
        after = new.findall('.//{' + CX + '}series')
        if len(before) == len(after):
            olddata = old.find('{' + CX + '}chartData')
            old.replace(olddata, new.find('{' + CX + '}chartData'))
            for target, source in zip(before, after):
                for tag in ('tx','dataId','layoutPr'):
                    current = target.find('{' + CX + '}' + tag)
                    replacement = source.find('{' + CX + '}' + tag)
                    if tag == 'layoutPr' and kind == 'map' and current is not None: continue
                    if current is not None and replacement is not None: target.replace(current, copy.deepcopy(replacement))
            part._element = old
        else:
            part._element = new
    else:
        if part.content_type == CX_CT: raise ValueError('Changing chart type requires chart.change_type')
        data = chart_data(payload)
        try:
            shape.chart.replace_data(data)
        except (NotImplementedError, ValueError, AttributeError):
            # Unsupported legacy plot rewriters (stock, surface and 3-D).
            # Preserve styles while replacing only the data/plot representation.
            old = part._element
            new = legacy_xml(payload, data)
            oldseries = sorted(old.findall('.//{' + C + '}ser'), key=lambda e: int(e.find('{' + C + '}idx').get('val')))
            newseries = sorted(new.findall('.//{' + C + '}ser'), key=lambda e: int(e.find('{' + C + '}idx').get('val')))
            if len(oldseries) == len(newseries):
                # Copy only data refs/caches, retaining template axes, labels and formatting.
                for target, source in zip(oldseries, newseries):
                    for tag in ('tx','cat','val','xVal','yVal','bubbleSize'):
                        replacement = source.find('{' + C + '}' + tag)
                        current = target.find('{' + C + '}' + tag)
                        if replacement is not None and current is not None: current.getparent().replace(current, copy.deepcopy(replacement))
                _set_root(part, old)
            else:
                for i, ser in enumerate(newseries):
                    if i >= len(oldseries): break
                    for child in oldseries[i]:
                        if etree.QName(child).localname == 'spPr':
                            _strip(ser, {'spPr'}); ser.insert(3, copy.deepcopy(child))
                _set_root(part, new)
            part.chart_workbook.update_from_xlsx_blob(data.xlsx_blob)
    verify_workbook(shape, payload)
    return dict(chart_part=str(part.partname), series=[s.get('values', s.get('points')) for s in payload['series']])


def verify_workbook(shape, payload):
    """Check embedded data against the request, including XY and ChartEx caches."""
    kind = validate(payload); part = chart_part(shape)
    external = part._element.find('{' + (CX if kind in MODERN else C) + '}chartData/{' + CX + '}externalData') if kind in MODERN else part._element.find('{' + C + '}externalData')
    if external is None: raise ValueError('Chart has no embedded workbook reference')
    blob = part.related_part(external.get('{' + R + '}id')).blob
    expected = _workbook(payload)[0] if kind in MODERN else chart_data(payload).xlsx_blob
    def cells(raw):
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
        return [tuple(row) for row in wb.active.iter_rows(values_only=True)]
    if cells(blob) != cells(expected): raise ValueError('Embedded workbook does not match requested chart data')
    root = part._element
    if kind in MODERN:
        caches = root.findall('.//{' + CX + '}numDim')
        actual = [[float(p.text) for p in e.findall('.//{' + CX + '}pt')] for e in caches]
        if actual != [s['values'] for s in payload['series']]: raise ValueError('ChartEx cache does not match workbook')
    else:
        cache = sorted(root.findall('.//{' + C + '}ser'), key=lambda e: int(e.find('{' + C + '}idx').get('val')))
        for ser, item in zip(cache, payload['series']):
            tags = ('xVal', 'yVal', 'bubbleSize') if kind in BUBBLE else ('xVal', 'yVal') if kind in XY else ('val',)
            for i, tag in enumerate(tags):
                values = [float(v.text) for v in ser.findall('.//{' + C + '}' + tag + '/{' + C + '}numRef/{' + C + '}numCache/{' + C + '}pt/{' + C + '}v')]
                expected_values = [p[i] for p in item['points']] if kind in XY | BUBBLE else item['values']
                if values != expected_values: raise ValueError('Chart cache does not match workbook')
        if len(cache) != len(payload['series']): raise ValueError('Chart lost series')
    return True


def replace_chart(slide, shape, new_type, meta):
    chart = copy.deepcopy(meta['chart']); new_type = canonical(new_type)
    if canonical(chart.get('type', 'bar')) == new_type: raise ValueError('Chart is already a ' + new_type + ' chart')
    chart['type'] = new_type
    # No invented X coordinates, bubble sizes, hierarchy or OHLC data on conversion.
    validate(chart)
    parent = shape._element.getparent(); index = parent.index(shape._element)
    position = shape.left, shape.top, shape.width, shape.height
    oldpart = chart_part(shape)
    old_rid = next(r.rId for r in slide.part.rels.values() if not r.is_external and r.target_part is oldpart)
    new = create(slide, chart, position); new.name = shape.name
    parent.remove(new._element); parent.insert(index, new._element); parent.remove(shape._element)
    slide.part.drop_rel(old_rid)
    meta = copy.deepcopy(meta); meta['chart'] = chart
    if new_type in RADIAL:
        meta.get('style_overrides', {}).pop('gridlines', None)
        meta.get('style_overrides', {}).pop('emphasis', None)
        if meta.get('preset') == 'chart.emphasis': meta.pop('preset')
    verify_workbook(new, chart)
    return new, meta


def style_xml(shape, meta, state):
    """Style all plot kinds directly, including ones python-pptx cannot inspect."""
    part = chart_part(shape); root = part._element; modern = part.content_type == CX_CT
    ns = CX if modern else C
    palette = state['theme']; overrides = meta.get('style_overrides') or {}
    colors = overrides.get('series_colors') or [palette['accent'], palette['muted'], '60A5B8', 'C0715A', '92B14B', '7A6FD0']
    colors = [palette.get(c, c) for c in colors]
    series = root.findall('.//{' + ns + '}' + ('series' if modern else 'ser'))
    kind = canonical(meta['chart'].get('type', 'bar'))
    for i, s in enumerate(series):
        old = s.find('{' + ns + '}spPr')
        if old is not None: s.remove(old)
        sp = etree.Element('{' + ns + '}spPr')
        s.insert((1 if s.find('{' + CX + '}tx') is not None else 0) if modern else min(3, len(s)), sp)
        fill = node(sp, 'a:solidFill'); node(fill, 'a:srgbClr', val=colors[i % len(colors)])
        line = node(sp, 'a:ln', w=round(overrides.get('line_width_pt', 2.0) * 12700))
        fill = node(line, 'a:solidFill'); node(fill, 'a:srgbClr', val=colors[i % len(colors)])
        if kind in {'scatter','xy_scatter'}:
            line.remove(fill); node(line, 'a:noFill')
        if kind in RADIAL | {'treemap', 'sunburst', 'funnel', 'waterfall'}:
            _strip(s, {'dataPt' if modern else 'dPt'})
            count = len(meta['chart'].get('categories') or [])
            for j in range(count):
                color = colors[j % len(colors)]
                if kind == 'waterfall':
                    value = meta['chart']['series'][0]['values'][j]
                    color = palette.get('accent') if j in meta['chart'].get('totals', []) else palette.get('positive', '2E7D4F') if value >= 0 else palette.get('negative', 'B3372F')
                if modern:
                    point = etree.Element('{' + CX + '}dataPt', idx=str(j)); s.insert(list(s).index(s.find('{' + CX + '}dataId')), point)
                else:
                    point = etree.Element('{' + C + '}dPt'); node(point, 'c:idx', val=j)
                    insert = next((k for k,e in enumerate(s) if etree.QName(e).localname in ('cat','val','xVal','yVal')), len(s)); s.insert(insert, point)
                psp = node(point, 'cx:spPr' if modern else 'c:spPr'); f = node(psp, 'a:solidFill'); node(f, 'a:srgbClr', val=color)
    # Font, color and chart background follow the chosen template's actual palette.
    old = root.find('{' + ns + '}spPr')
    if old is not None: root.remove(old)
    sp = node(root, 'cx:spPr' if modern else 'c:spPr'); node(sp, 'a:noFill'); ln = node(sp, 'a:ln'); node(ln, 'a:noFill')
    old = root.find('{' + ns + '}txPr')
    if old is not None: root.remove(old)
    tx = node(root, 'cx:txPr' if modern else 'c:txPr'); node(tx, 'a:bodyPr'); node(tx, 'a:lstStyle')
    p = node(tx, 'a:p'); pp = node(p, 'a:pPr'); rp = node(pp, 'a:defRPr', sz=round(overrides.get('font_size', 12) * 100))
    f = node(rp, 'a:solidFill'); node(f, 'a:srgbClr', val=palette['text']); node(rp, 'a:latin', typeface=state['font_family']); node(rp, 'a:ea', typeface=state['font_family'])
    chart = root.find('{' + ns + '}chart')
    legend = chart.find('{' + ns + '}legend')
    placement = overrides.get('legend', 'auto')
    if placement == 'none' and legend is not None: chart.remove(legend)
    elif placement not in ('none', 'auto'):
        if legend is None: legend = node(chart, 'cx:legend' if modern else 'c:legend')
        pos = {'bottom':'b','right':'r','top':'t'}[placement]
        if modern: legend.set('pos', pos)
        else:
            _strip(legend, {'legendPos'}); legend.insert(0, etree.Element('{' + C + '}legendPos', val=pos))
    if 'data_labels' in overrides:
        targets = series if modern else [e for e in root.findall('.//{' + C + '}plotArea/*') if e.tag.endswith('Chart') and 'surface' not in e.tag.lower()]
        for target in targets:
            _strip(target, {'dataLabels' if modern else 'dLbls'})
            if overrides['data_labels']:
                labels = etree.Element('{' + ns + '}' + ('dataLabels' if modern else 'dLbls'))
                if modern:
                    node(labels, 'cx:visibility', value=1)
                    anchor = target.find('{' + CX + '}dataId')
                    target.insert(list(target).index(anchor) if anchor is not None else 0, labels)
                else:
                    node(labels, 'c:showVal', val=1)
                    members = target.findall('{' + C + '}ser')
                    target.insert(list(target).index(members[-1]) + 1, labels)
    axes = root.findall('.//{' + ns + '}axis') if modern else root.findall('.//{' + C + '}valAx') + root.findall('.//{' + C + '}catAx')
    for axis in axes:
        _strip(axis, {'txPr'}); axis.append(copy.deepcopy(tx))
        unit = meta['chart'].get('unit')
        value_axis = axis.find('{' + CX + '}valScaling') is not None if modern else axis.tag == '{' + C + '}valAx'
        if unit and value_axis and kind != 'combination' and not kind.startswith('stock_'):
            _strip(axis, {'title'})
            title = etree.Element('{' + ns + '}title')
            container = node(title, 'cx:tx' if modern else 'c:tx')
            rich = node(container, 'cx:rich' if modern else 'c:rich')
            node(rich, 'a:bodyPr'); node(rich, 'a:lstStyle'); p = node(rich, 'a:p')
            run = node(p, 'a:r'); node(run, 'a:t', str(unit))
            if modern:
                axis.insert(1, title)
            else:
                axis.append(title)
        if 'number_format' in overrides:
            _strip(axis, {'numFmt'})
            fmt = etree.Element('{' + ns + '}numFmt', formatCode=overrides['number_format'], sourceLinked='0')
            # In both schemas numFmt precedes shape/text properties.
            axis.insert(list(axis).index(axis.find('{' + ns + '}txPr')), fmt)
        if overrides.get('gridlines') == 'none': _strip(axis, {'majorGridlines','minorGridlines'})
        if not modern:
            order = ['axId','scaling','delete','axPos','majorGridlines','minorGridlines','title','numFmt',
                     'majorTickMark','minorTickMark','tickLblPos','spPr','txPr','crossAx','crosses','crossesAt',
                     'auto','lblAlgn','lblOffset','tickLblSkip','tickMarkSkip','noMultiLvlLbl',
                     'crossBetween','majorUnit','minorUnit','dispUnits','extLst']
            axis[:] = sorted(list(axis), key=lambda e: order.index(etree.QName(e).localname) if etree.QName(e).localname in order else 999)
    # Legacy chartSpace has a strict child order.
    if not modern:
        order = ['date1904','lang','roundedCorners','AlternateContent','style','clrMapOvr','pivotSource','protection','chart','spPr','txPr','externalData','printSettings','userShapes','extLst']
        children = sorted(list(root), key=lambda e: order.index(etree.QName(e).localname) if etree.QName(e).localname in order else 999)
        root[:] = children
    meta['chart_style'] = dict(series_colors=colors[:len(meta['chart']['series'])], native=True,
                               format='chartex' if modern else 'drawingml',
                               preview_requires_office=modern or 'surface' in kind or kind.startswith('three_d_'))


def catalog():
    return [dict(type=k, data_model=family(k), format='chartex' if k in MODERN else 'drawingml',
                 scenarios=scenarios(k),
                 scenarios_reference='references/charts.md',
                 preview='Requires PowerPoint verification' if requires_office(k) else 'formal render')
            for k in sorted(SUPPORTED)]


def audit(prs, plan=None):
    """Count actual native charts and embedded workbooks against the page plan.

    Shapes/images never count as native charts. Unsupported chart types can
    be declared as explicit fallbacks with a reason and real object IDs.
    Without a plan this is inventory only, never proof of chart completeness.
    """
    pages=[]
    def walk(shapes):
        for shape in shapes:
            yield shape
            if hasattr(shape,'shapes'):yield from walk(shape.shapes)
    objects=[]
    for index,slide in enumerate(prs.slides,1):
        charts=[]
        aliases={}
        for shape in walk(slide.shapes):
            stable=shape.name[3:] if shape.name.startswith('da:') else f'template-{shape.shape_id}'
            for alias in {stable,shape.name}:aliases.setdefault(alias,[]).append(shape)
            if not is_chart(shape):continue
            part=chart_part(shape)
            charts.append(dict(name=shape.name,workbook=any(r.reltype==PACKAGE_REL for r in part.rels.values())))
        objects.append(aliases)
        pages.append(dict(page=index,native_charts=len(charts),charts=charts,fallback_charts=0,fallbacks=[]))
    issues=[]
    if plan is not None:
        declared=plan.get('slides')
        if not isinstance(declared,list) or len(declared)!=len(pages):raise ValueError('Chart plan must declare every slide')
        expected={}
        for item in declared:
            page,count=item.get('page'),item.get('statistical_charts')
            if type(page)is not int or page in expected or not 1<=page<=len(pages) or type(count)is not int or count<0:
                raise ValueError('Each slide needs a unique page and nonnegative statistical_charts count')
            fallbacks=item.get('chart_fallbacks',[])
            if not isinstance(fallbacks,list):raise ValueError('chart_fallbacks must be a list')
            expected[page]=(count,fallbacks)
        for page in pages:
            total,fallbacks=expected[page['page']]
            used=set()
            for fallback in fallbacks:
                if not isinstance(fallback,dict):raise ValueError('Each fallback must be an object')
                kind,reason,ids=fallback.get('type'),fallback.get('reason'),fallback.get('element_ids')
                if (not isinstance(kind,str) or not kind.strip() or not isinstance(reason,str) or not reason.strip()
                    or not isinstance(ids,list) or not ids or any(not isinstance(i,str) or not i.strip() for i in ids)):
                    issues.append(dict(page=page['page'],type='invalid_chart_fallback'));continue
                kind=kind.strip().lower().replace('-','_')
                if kind in SUPPORTED:
                    issues.append(dict(page=page['page'],type='supported_chart_declared_as_fallback',chart_type=kind));continue
                selected=[];invalid=False
                for identity in ids:
                    matches=objects[page['page']-1].get(identity,[])
                    if len(matches)!=1:
                        issues.append(dict(page=page['page'],type='unknown_or_ambiguous_fallback_element',element_id=identity));invalid=True;continue
                    shape=matches[0]
                    if shape._element in used or any(s._element is shape._element for s in selected):
                        issues.append(dict(page=page['page'],type='reused_fallback_element',element_id=identity));invalid=True
                    if any(is_chart(child) for child in walk([shape])):
                        issues.append(dict(page=page['page'],type='native_chart_counted_as_fallback',element_id=identity));invalid=True
                    selected.append(shape)
                if invalid:continue
                used.update(shape._element for shape in selected)
                page['fallbacks'].append(dict(type=kind,reason=reason.strip(),element_ids=list(ids)))
            page['fallback_charts']=len(page['fallbacks'])
            page['expected_charts']=total
            page['expected_native_charts']=total-page['fallback_charts']
            if page['expected_native_charts']<0:issues.append(dict(page=page['page'],type='chart_fallback_count_exceeds_plan'))
            if page['native_charts']!=page['expected_native_charts']:issues.append(dict(page=page['page'],type='native_chart_count_mismatch',expected=page['expected_native_charts'],actual=page['native_charts']))
    for page in pages:
        if any(not chart['workbook'] for chart in page['charts']):issues.append(dict(page=page['page'],type='native_chart_without_workbook'))
    return dict(status='failed' if issues else 'passed' if plan is not None else 'inventory',
        native_charts=sum(p['native_charts'] for p in pages),fallback_charts=sum(p['fallback_charts'] for p in pages),
        all_native=plan is not None and not issues and not any(p['fallback_charts'] for p in pages),pages=pages,issues=issues)


def requires_office(kind):
    kind = canonical(kind)
    return kind in MODERN or 'surface' in kind or kind in {'stock_vhlc','stock_vohlc'}


def scenarios(kind):
    kind = canonical(kind)
    special = {
        'waterfall': ['利润从收入到净利的增减桥接', '预算到实际的差异归因', '期初到期末现金余额变动'],
        'funnel': ['访客到付费的转化链路', '销售线索到签约的阶段流失', '招聘简历到入职的人数漏斗'],
        'histogram': ['订单金额的频数分布', '接口延迟的集中区间和长尾', '员工工龄或考试成绩分布'],
        'pareto': ['投诉原因的关键少数分析', '缺陷类别的累计贡献排序', '停机原因的改善优先级'],
        'box_whisker': ['不同版本的延迟分布比较', '工厂产线质量的中位数与异常值', '不同地区订单金额的离散程度'],
        'treemap': ['地区与产品两层收入构成', '部门与成本科目的预算占用', '目录与子目录的存储空间分布'],
        'sunburst': ['公司到事业部再到团队的层级占比', '用户渠道到子渠道的来源构成', '商品大类到子类的销售层级'],
        'map': ['各省销售额的地域分布', '各国客户覆盖率比较', '不同地区风险或渗透率的空间差异'],
        'combination': ['收入柱形与增长率折线双轴比较', '订单量柱形与转化率趋势', '成本面积与利润折线的联合分析'],
    }
    if kind in special: return special[kind]
    if kind in BUBBLE: return ['以市场规模编码大小的增长与份额比较', '以用户数编码大小的留存与获客成本分析', '以投入编码大小的项目风险与回报评估']
    if kind in XY: return ['广告投入与销售额的相关性', '并发量与响应延迟的关系', '实验温度与良率的数值关系']
    if kind.startswith('stock_'):
        return ['股票高低收盘价走势' if 'ohlc' not in kind else '股票开高低收盘价走势',
                '商品期货价格区间与波动比较',
                '成交量与价格走势对照' if kind in {'stock_vhlc','stock_vohlc'} else '汇率或能源价格的周期区间分析']
    if 'surface' in kind: return ['温度与压力共同影响的产品良率', '学习率与批量大小的模型性能网格', '期限与利率维度的估值敏感性网格']
    if 'radar' in kind: return ['产品多个能力维度对标', '团队技能结构比较', '供应商质量交付成本的综合评估']
    if kind in RADIAL:
        if 'doughnut' in kind: return ['少量业务线的收入占比', '预算使用与剩余额度', '客户来源渠道构成']
        if kind in {'pie_of_pie','bar_of_pie'}: return ['展开收入中的其他业务细项', '展开小渠道的获客占比', '展示成本长尾科目的二级构成']
        return ['少量产品的销量份额', '客户类型的整体构成', '固定时点的成本占比']
    if '100' in kind: return ['不同地区的渠道占比比较', '不同季度的收入结构变化', '不同团队的工时分配比例']
    if 'stacked' in kind: return ['各季度不同业务线对总收入的贡献', '各地区不同产品的销量构成', '预算各科目随时间累积的规模']
    if 'line' in kind: return ['月度收入与目标趋势', '每日活跃用户变化', '接口成功率的时间序列比较']
    if 'area' in kind: return ['累计用户规模随时间变化', '资源消耗总量的阶段走势', '订单量在活动前后的整体趋势']
    if '_bar_' in kind or kind.startswith('bar_'): return ['长名称产品的销售额排名', '部门预算执行金额比较', '多个问题原因的发生次数排序']
    return ['季度收入的离散比较', '不同产品的订单量对比', '实际值与目标值的并列比较']
