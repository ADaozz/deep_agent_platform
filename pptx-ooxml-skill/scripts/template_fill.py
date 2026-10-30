"""Fill template slots and replace chart or picture bytes without rebuilding the shape."""
import math

from lxml import etree
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.oxml.ns import qn

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def slot_targets(slide, slots, aliases=None):
    """Resolve slot name → shape. ``aliases`` maps a slot name to an element id."""
    if not isinstance(slots, dict) or not slots:
        raise ValueError('slot.fill requires a slots object')
    aliases = aliases or {}
    resolved = []
    for name, text in slots.items():
        if not isinstance(name, str) or not isinstance(text, str):
            raise ValueError('slot.fill values must be strings')
        element_id = aliases.get(name, name)
        matches = [shape for shape in slide.shapes if _element_id(shape) == element_id]
        if len(matches) != 1:
            raise ValueError(f'Unknown or ambiguous slot {name}')
        resolved.append((name, matches[0], text))
    return resolved


def assert_capacity(shape, text):
    """Reject text that cannot fit the existing box. Font size is not reduced."""
    if len(text) > 1200:
        raise ValueError('One bounded text element, maximum 1200 characters')
    needed, limit = _line_budget(shape, text)
    if needed > limit:
        raise ValueError(
            f'capacity conflict: text needs about {needed} lines and the slot holds {limit}; '
            'shorten the copy, choose another layout, or enlarge this box before filling it')


def fill_shape_text(shape, text):
    """Replace visible text and keep the first run's paragraph and character properties."""
    if not getattr(shape, 'has_text_frame', False) or shape.has_table or shape.has_chart:
        raise ValueError('slot.fill requires a native text box')
    assert_capacity(shape, text)
    body = shape._element.find(P + 'txBody')
    if body is None:
        body = shape._element.find('.//' + A + 'txBody')
    if body is None:
        raise ValueError('slot.fill requires a text body')
    paragraphs = [child for child in body if etree.QName(child).localname == 'p']
    if not paragraphs:
        raise ValueError('Text shape has no paragraph to preserve')
    lines = text.split('\n')
    while len(paragraphs) < len(lines):
        clone = _deepcopy_paragraph(paragraphs[-1])
        paragraphs[-1].addnext(clone)
        paragraphs.append(clone)
    for extra in paragraphs[len(lines):]:
        body.remove(extra)
    for paragraph, line in zip(paragraphs, lines):
        _set_paragraph_line(paragraph, line)


def replace_chart_data(shape, payload, build_data):
    """Replace a chart's workbook. ``build_data`` is the engine's bounded chart validator."""
    if not getattr(shape, 'has_chart', False) or not shape.has_chart:
        raise ValueError('chart.replace_data requires a native chart')
    if payload.get('type') and _chart_family(shape) and payload['type'] != _chart_family(shape):
        raise ValueError('Changing chart type uses chart.change_type, which replaces the chart object and keeps its data')
    data = build_data(payload)
    partname_before = str(shape.chart.part.partname)
    shape.chart.replace_data(data)
    actual = []
    for series in shape.chart.series:
        actual.append([float(value) for value in series.values])
    expected = [[float(value) for value in item['values']] for item in payload['series']]
    if actual != expected:
        raise ValueError('Chart workbook does not match the replacement data')
    return dict(chart_part=partname_before, series=actual)


def replace_picture(slide, shape, image_path):
    """Point one picture at a new image part. Shared media used by other slides stays put."""
    if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
        raise ValueError('image.replace requires a picture')
    from PIL import Image
    with Image.open(image_path) as image:
        if image.format not in ('PNG', 'JPEG') or image.width * image.height > 40000000:
            raise ValueError('Unsupported bounded image')
        if shape.height <= 0 or abs(image.width / image.height - shape.width / shape.height) > 0.03:
            raise ValueError('Image geometry must preserve aspect ratio')
    _image_part, rel_id = slide.part.get_or_add_image_part(str(image_path))
    blip = shape._element.find('.//' + qn('a:blip'))
    if blip is None:
        raise ValueError('Picture has no image relationship')
    blip.set(qn('r:embed'), rel_id)
    return dict(rel_id=rel_id)


def apply_imported_style(shape, style):
    """Change fill or run properties on a template shape without clearing its text."""
    if shape.has_chart or shape.has_table or shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        raise ValueError('This imported object is not restyled in place; use presentation.recolor, chart.replace_data or image.replace')
    if not isinstance(style, dict) or not style:
        raise ValueError('style must change at least one field')
    allowed = {'color', 'bold', 'italic', 'font_size', 'align', 'fill', 'text_color'}
    unknown = set(style) - allowed
    if unknown:
        raise ValueError('Imported element.set_style cannot apply ' + ', '.join(sorted(unknown)) + ' without rebuilding the shape')
    if 'fill' in style:
        _set_solid_fill(_sp_pr(shape), _hex(style['fill']))
    text_color = style.get('text_color', style.get('color'))
    runs = [node for node in shape._element.iter(A + 'r')]
    if any(key in style for key in ('color', 'text_color', 'bold', 'italic', 'font_size')):
        targets = runs or _default_runs(shape)
        for run in targets:
            props = _run_props(run)
            if text_color:
                _set_solid_fill(props, _hex(text_color))
            if 'bold' in style:
                props.set('b', '1' if style['bold'] else '0')
            if 'italic' in style:
                props.set('i', '1' if style['italic'] else '0')
            if 'font_size' in style:
                size = style['font_size']
                if type(size) not in (int, float) or not 8 <= size <= 96:
                    raise ValueError('Imported font_size must be between 8 and 96')
                props.set('sz', str(int(round(size * 100))))
    if 'align' in style:
        align = {'left': 'l', 'center': 'ctr', 'right': 'r'}.get(style['align'])
        if align is None:
            raise ValueError('align must be left, center or right')
        for paragraph in shape._element.iter(A + 'p'):
            props = paragraph.find(A + 'pPr')
            if props is None:
                props = etree.Element(A + 'pPr')
                paragraph.insert(0, props)
            props.set('algn', align)


def _element_id(shape):
    return shape.name[3:] if shape.name.startswith('da:') else f'template-{shape.shape_id}'


def _line_budget(shape, text):
    size = _font_pt(shape)
    width_pt = max(1, shape.width / 12700)
    height_pt = max(1, shape.height / 12700)
    chars_per_line = max(1, int(width_pt / size))
    limit = max(1, int(height_pt / (size * 1.3)))
    needed = 0
    for line in text.split('\n') or ['']:
        needed += max(1, math.ceil(len(line) / chars_per_line)) if line else 1
    return needed, limit


def _font_pt(shape):
    for node in shape._element.iter():
        if etree.QName(node).localname in ('rPr', 'defRPr', 'endParaRPr') and node.get('sz'):
            return max(1.0, int(node.get('sz')) / 100)
    return 18.0


def _deepcopy_paragraph(paragraph):
    return etree.fromstring(etree.tostring(paragraph))


def _set_paragraph_line(paragraph, line):
    runs = [child for child in paragraph if etree.QName(child).localname == 'r']
    text_runs = []
    for run in runs:
        texts = [child for child in run if etree.QName(child).localname == 't']
        if texts:
            text_runs.append((run, texts))
    if not text_runs:
        run = etree.SubElement(paragraph, A + 'r')
        text = etree.SubElement(run, A + 't')
        text.text = line
        if line[:1].isspace() or line[-1:].isspace():
            text.set(XML_SPACE, 'preserve')
        return
    run, texts = text_runs[0]
    texts[0].text = line
    if line[:1].isspace() or line[-1:].isspace():
        texts[0].set(XML_SPACE, 'preserve')
    for extra in texts[1:]:
        run.remove(extra)
    for extra_run, _texts in text_runs[1:]:
        paragraph.remove(extra_run)


def _chart_family(shape):
    from pptx.enum.chart import XL_CHART_TYPE
    chart_type = shape.chart.chart_type
    if chart_type == XL_CHART_TYPE.COLUMN_CLUSTERED:
        return 'bar'
    if chart_type in (XL_CHART_TYPE.LINE, XL_CHART_TYPE.LINE_MARKERS):
        return 'line'
    if chart_type == XL_CHART_TYPE.PIE:
        return 'pie'
    return None


def _hex(value):
    if not isinstance(value, str):
        raise ValueError('Colour must be RRGGBB')
    text = value.strip().lstrip('#')
    if len(text) != 6 or any(c not in '0123456789abcdefABCDEF' for c in text):
        raise ValueError('Colour must be RRGGBB')
    return text.upper()


def _sp_pr(shape):
    props = shape._element.find('.//' + A + 'spPr')
    if props is None:
        raise ValueError('Shape has no properties to recolor')
    return props


def _run_props(run):
    props = run.find(A + 'rPr')
    if props is None:
        props = etree.Element(A + 'rPr')
        run.insert(0, props)
    return props


def _default_runs(shape):
    """A text box with no runs yet gets one run so style still has a target."""
    paragraph = next(shape._element.iter(A + 'p'), None)
    if paragraph is None:
        raise ValueError('Text shape has no paragraph to style')
    run = etree.SubElement(paragraph, A + 'r')
    etree.SubElement(run, A + 't')
    return [run]


def _set_solid_fill(parent, color):
    for child in list(parent):
        if etree.QName(child).localname == 'solidFill':
            parent.remove(child)
    solid = etree.Element(A + 'solidFill')
    # Character properties keep latin/ea children after the fill; spPr wants fill first.
    if etree.QName(parent).localname == 'rPr':
        parent.insert(0, solid)
    else:
        parent.append(solid)
    srgb = etree.SubElement(solid, A + 'srgbClr')
    srgb.set('val', color)
