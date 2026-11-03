"""Apply one design parameter set to a Word package, then add native blocks.

style_id records the source. Missing numeric parameters are left unset; this
module does not invent a default palette, type scale or margin.
"""
import importlib.util
from pathlib import Path


def _office():
    path = Path(__file__).with_name('office_docx.py')
    spec = importlib.util.spec_from_file_location('docx_build_office', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('docx_build_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def twips(margins):
    unit = margins.get('unit')
    if unit not in ('mm', 'in', 'pt', 'twip'):
        raise ValueError('margins.unit must be mm, in, pt or twip')
    factor = {'mm': 1440 / 25.4, 'in': 1440, 'pt': 20, 'twip': 1}[unit]
    result = {}
    for side in ('left', 'right', 'top', 'bottom'):
        if side not in margins:
            raise ValueError('margins requires left, right, top and bottom')
        result[side] = int(round(float(margins[side]) * factor))
        if not 100 <= result[side] <= 50000:
            raise ValueError('Margin out of range: ' + side)
    return result


def half_points(value):
    return str(int(round(float(value) * 2)))


def apply_design(parts, design):
    """Write only the parameters that are actually present."""
    if not design:
        return parts
    office = _office()
    styles = office.parse_xml(parts['word/styles.xml'])
    fonts = design.get('fonts') or {}
    if fonts:
        for node in styles.findall('.//' + office.q(office.W, 'rFonts')):
            for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
                if fonts.get(key):
                    node.set(office.q(office.W, key), fonts[key])
    scale = design.get('type_scale') or {}
    for style_id, key in (('Title', 'title'), ('Heading1', 'heading1'), ('Heading2', 'heading2'), ('Normal', 'body')):
        if key not in scale:
            continue
        style = styles.xpath('//w:style[@w:styleId=$id]', namespaces=office.NS, id=style_id)
        if not style:
            continue
        size = style[0].find('.//' + office.q(office.W, 'sz'))
        if size is None:
            props = style[0].find(office.q(office.W, 'rPr'))
            if props is None:
                props = office.etree.SubElement(style[0], office.q(office.W, 'rPr'))
            size = office.etree.SubElement(props, office.q(office.W, 'sz'))
        size.set(office.q(office.W, 'val'), half_points(scale[key]))
    palette = design.get('palette') or {}
    if palette.get('text'):
        for style_id in ('Title', 'Heading1', 'Heading2', 'Normal'):
            style = styles.xpath('//w:style[@w:styleId=$id]', namespaces=office.NS, id=style_id)
            if not style:
                continue
            props = style[0].find(office.q(office.W, 'rPr'))
            if props is None:
                props = office.etree.SubElement(style[0], office.q(office.W, 'rPr'))
            color = props.find(office.q(office.W, 'color'))
            if color is None:
                color = office.etree.SubElement(props, office.q(office.W, 'color'))
            color.set(office.q(office.W, 'val'), palette['text'])
    if palette.get('accent'):
        for style_id in ('Title', 'Heading1'):
            style = styles.xpath('//w:style[@w:styleId=$id]', namespaces=office.NS, id=style_id)
            if not style:
                continue
            color = style[0].find('.//' + office.q(office.W, 'color'))
            if color is not None:
                color.set(office.q(office.W, 'val'), palette['accent'])
    parts['word/styles.xml'] = office.xml_bytes(styles)
    margins = design.get('margins')
    if margins:
        values = twips(margins)
        root = office.parse_xml(parts[office.MAIN])
        section = root.find('.//' + office.q(office.W, 'sectPr'))
        mar = section.find(office.q(office.W, 'pgMar'))
        for side, value in values.items():
            mar.set(office.q(office.W, side), str(value))
        parts[office.MAIN] = office.xml_bytes(root)
    header, footer = design.get('header'), design.get('footer')
    if header or footer:
        _headers(parts, office, header, footer)
    return parts


def _headers(parts, office, header, footer):
    root = office.parse_xml(parts[office.MAIN])
    rels = office.parse_xml(parts[office.DOC_RELS])
    section = root.find('.//' + office.q(office.W, 'sectPr'))

    def add(kind, text):
        name = f'word/{kind}1.xml'
        tag = 'hdr' if kind == 'header' else 'ftr'
        paragraph = f'<w:{tag} xmlns:w="{office.W}"><w:p><w:r><w:t>{office.etree_escape(text)}</w:t></w:r></w:p></w:{tag}>'
        parts[name] = paragraph.encode()
        content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.%s+xml' % kind
        content = office.parse_xml(parts[office.CT_FILE])
        office.etree.SubElement(content, office.q(office.CT, 'Override'), PartName='/' + name, ContentType=content_type)
        parts[office.CT_FILE] = office.xml_bytes(content)
        rid = 'rId' + kind.title()
        office.etree.SubElement(rels, office.q(office.RELS, 'Relationship'), Id=rid,
            Type=office.R + '/' + kind, Target=f'{kind}1.xml')
        office.etree.SubElement(section, office.q(office.W, kind + 'Reference'), {office.q(office.W, 'type'): 'default', office.q(office.R, 'id'): rid})

    if header:
        add('header', header)
    if footer:
        add('footer', footer)
    parts[office.DOC_RELS] = office.xml_bytes(rels)
    parts[office.MAIN] = office.xml_bytes(root)


def blank(design=None, style_id=None):
    office = _office()
    parts = office.default_package()
    if design:
        apply_design(parts, design)
    if style_id:
        _load('style_library').apply_reference(parts, style_id, design=design)
    return parts


def add_paragraph(parts, block_id, text, style='Normal'):
    return _mutate(parts, dict(type='append_paragraph', block_id=block_id, text=text, style=style))


def add_table(parts, block_id, rows, widths=None):
    operation = dict(type='append_table', block_id=block_id, rows=rows, header=True)
    if widths:
        operation['column_widths_twips'] = widths
    return _mutate(parts, operation)


def add_equation(parts, block_id, latex, **kwargs):
    compiled = _load('native_math').paragraph(latex, block_id, **kwargs)
    office = _office()
    root = office.parse_xml(parts[office.MAIN])
    if 'm' not in root.nsmap:
        replacement = office.etree.Element(root.tag, nsmap={**root.nsmap, 'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'})
        replacement.attrib.update(root.attrib)
        replacement.extend(list(root))
        root = replacement
    compiled['paragraph'].set('{urn:deep-agent:docx:blocks:v1}id', block_id)
    body = root.find(office.q(office.W, 'body'))
    index = next((i for i, child in enumerate(body) if child.tag == office.q(office.W, 'sectPr')), len(body))
    body.insert(index, compiled['paragraph'])
    parts[office.MAIN] = office.xml_bytes(root)
    return parts


def add_chart(parts, block_id, spec):
    office = _office()
    return _load('native_charts').create(parts, spec, block_id, office)


def save(parts, path):
    office = _office()
    checked = office.validate_parts(parts)
    if not checked['ok']:
        raise ValueError('OOXML validation failed: ' + str(checked['errors']))
    office.save_parts(parts, path)
    return path


def _mutate(parts, operation):
    path = Path(__file__).with_name('atomic_docx.py')
    spec = importlib.util.spec_from_file_location('docx_build_atomic', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    updated, _changed, _extra = module.mutate(parts, operation, Path('.'))
    return updated
