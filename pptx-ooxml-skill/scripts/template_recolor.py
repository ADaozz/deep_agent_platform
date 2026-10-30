"""Recolor a presentation through theme colors and known direct RGB values.

Pictures and logo shapes are not repainted. A direct color that is not part of
the recorded theme palette is left unchanged and reported. The result must not
be described as a finished global recolor while ``unhandled`` is non-empty.
"""
from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
# DrawingML scheme names that have a single semantic role in the requested palette.
ROLE = {
    'accent1': 'accent', 'accent3': 'accent', 'accent4': 'accent', 'accent5': 'accent', 'accent6': 'accent',
    'accent2': 'muted', 'dk2': 'muted', 'dk1': 'text', 'lt1': 'background', 'lt2': 'background',
}
REQUIRED = ('accent', 'background', 'text')


def recolor(prs, palette, preserve=None, protected=None):
    colors = _palette(palette)
    preserve = set(preserve or ('images', 'logos'))
    protected = {key: set(value or []) for key, value in (protected or {}).items()}
    themes = _theme_parts(prs)
    if not themes:
        raise ValueError('Presentation has no theme part to recolor')
    theme_roots = [_theme_root(part) for part in themes]
    old = _scheme_hexes(theme_roots[0][0])
    mapping, ambiguous = _hex_mapping(old, colors)
    frozen = []
    if 'logos' in preserve:
        frozen, modified = _freeze_logos(prs, old, protected)
        # A tinted scheme color cannot be pinned without flattening it.
        frozen.extend(name + ':modified-scheme-left' for name in modified)
    for part, (root, copied) in zip(themes, theme_roots):
        _write_scheme(root, colors)
        if copied:
            part.blob = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    unhandled = []
    seen = set()
    for label, element, skip_logo, report, ids in _paint_roots(prs, preserve, protected):
        _remap_tree(element, mapping, label, unhandled, seen, skip_logo=skip_logo, report=report, protected_ids=ids)
    for color in ambiguous:
        _add(unhandled, seen, 'theme', color, 'ambiguous theme color maps to more than one role', 'unmapped')
    complete = not any(item.get('status') in ('unmapped', 'needs_confirmation') for item in unhandled)
    return dict(
        recolor_complete=complete,
        remapped=[{'from': src, 'to': dst} for src, dst in sorted(mapping.items())],
        unhandled=unhandled[:40],
        unhandled_count=len(unhandled),
        preserved=sorted(preserve),
        logos_frozen=frozen,
    )


def _palette(palette):
    if not isinstance(palette, dict):
        raise ValueError('palette must be an object of semantic colors')
    missing = [key for key in REQUIRED if key not in palette]
    if missing:
        raise ValueError('palette requires ' + ', '.join(missing))
    colors = {}
    for key in (*REQUIRED, 'muted'):
        if key not in palette:
            continue
        colors[key] = _hex(palette[key])
    return colors


def _hex(value):
    if not isinstance(value, str):
        raise ValueError('palette colors must be RRGGBB')
    text = value.strip().lstrip('#')
    if len(text) != 6 or any(c not in '0123456789abcdefABCDEF' for c in text):
        raise ValueError('palette colors must be RRGGBB')
    return text.upper()


def _theme_root(part):
    element = getattr(part, '_element', None)
    if element is not None:
        return element, False
    return etree.fromstring(part.blob), True


def _theme_parts(prs):
    found = []
    seen = set()
    for rel in prs.part.package.iter_rels():
        if rel.is_external or rel.reltype != RT.THEME:
            continue
        part = rel.target_part
        if str(part.partname) in seen:
            continue
        seen.add(str(part.partname))
        found.append(part)
    return found


def _scheme_hexes(theme):
    scheme = theme.find('.//' + A + 'clrScheme')
    if scheme is None:
        return {}
    found = {}
    for child in scheme:
        name = etree.QName(child).localname
        srgb = child.find(A + 'srgbClr')
        sysclr = child.find(A + 'sysClr')
        if srgb is not None and srgb.get('val'):
            found[name] = srgb.get('val').upper()
        elif sysclr is not None and sysclr.get('lastClr'):
            found[name] = sysclr.get('lastClr').upper()
    return found


def _hex_mapping(old, colors):
    """Map an old explicit hex only when every theme role using it agrees."""
    groups = {}
    for name, value in old.items():
        role = ROLE.get(name)
        if role is None or role not in colors:
            continue
        groups.setdefault(value.upper(), set()).add(colors[role])
    mapping, ambiguous = {}, []
    for value, targets in groups.items():
        if len(targets) == 1:
            mapping[value] = next(iter(targets))
        else:
            ambiguous.append(value)
    return mapping, ambiguous


def _write_scheme(theme, colors):
    scheme = theme.find('.//' + A + 'clrScheme')
    if scheme is None:
        raise ValueError('Theme has no color scheme')
    for child in scheme:
        role = ROLE.get(etree.QName(child).localname)
        if role is None or role not in colors:
            continue
        for node in list(child):
            child.remove(node)
        srgb = etree.SubElement(child, A + 'srgbClr')
        srgb.set('val', colors[role])


def _freeze_logos(prs, old, protected):
    """Pin protected scheme colors to the previous hex so a theme edit does not repaint them."""
    frozen, modified = [], []
    for slide in prs.slides:
        ids = protected.get(_slide_id(slide), set())
        for shape in slide.shapes:
            if not _is_protected(shape._element, ids):
                continue
            changed, has_modified = _freeze_element(shape._element, old)
            if changed:
                frozen.append(_name(shape._element))
            if has_modified:
                modified.append(_name(shape._element))
    return frozen, modified


def _freeze_element(element, old):
    changed = False
    modified = False
    for node in list(element.iter(A + 'schemeClr')):
        if len(node):
            modified = True
            continue
        value = old.get(node.get('val'))
        if not value:
            continue
        srgb = etree.Element(A + 'srgbClr')
        srgb.set('val', value)
        node.getparent().replace(node, srgb)
        changed = True
    return changed, modified


def _paint_roots(prs, preserve, protected):
    roots = []
    # Masters and layouts are repainted when a color is a known theme value.
    # Their extra style-gallery colors are not slide content, so they are not reported.
    for master in prs.slide_masters:
        roots.append(('master', master._element, 'logos' in preserve, False, set()))
        for layout in master.slide_layouts:
            roots.append(('layout', layout._element, 'logos' in preserve, False, set()))
    for index, slide in enumerate(prs.slides, 1):
        ids = protected.get(_slide_id(slide), set())
        roots.append((f'slide-{index}', slide._element, 'logos' in preserve, True, ids))
        for shape in slide.shapes:
            if shape.has_chart and not ('logos' in preserve and _is_protected(shape._element, ids)):
                roots.append((f'slide-{index}:chart', shape.chart._element, False, True, set()))
    return roots


def _remap_tree(element, mapping, label, unhandled, seen, skip_logo, report, protected_ids):
    _walk(element, mapping, label, unhandled, seen, skip_logo=skip_logo, preserved=False, report=report, protected_ids=protected_ids)


def _walk(node, mapping, label, unhandled, seen, skip_logo, preserved, report, protected_ids):
    local = etree.QName(node).localname
    # Bitmap pictures are never repainted, including when the caller omits them from preserve.
    if local == 'pic' or local == 'blipFill' or node.tag == P + 'pic':
        return
    if local == 'graphicFrame' and _is_complex_frame(node):
        if report:
            _add(unhandled, seen, label, _name(node), 'complex graphic needs confirmation before recolor', 'needs_confirmation')
        return
    if skip_logo and local in ('sp', 'cxnSp', 'graphicFrame') and _is_protected(node, protected_ids):
        preserved = True
    if not preserved and local == 'srgbClr':
        value = (node.get('val') or '').upper()
        if value in mapping:
            if mapping[value] != value:
                node.set('val', mapping[value])
        elif value and report:
            _add(unhandled, seen, label, value, 'direct color is outside the theme palette', 'unmapped')
    elif not preserved and local == 'schemeClr' and report:
        name = node.get('val') or ''
        if name not in ROLE and name not in ('phClr', 'bg1', 'tx1', 'bg2', 'tx2'):
            _add(unhandled, seen, label, name, 'scheme color has no semantic mapping', 'unmapped')
    for child in node:
        _walk(child, mapping, label, unhandled, seen, skip_logo, preserved, report, protected_ids)


def _slide_id(slide):
    name = slide._element.cSld.get('name') or ''
    return name[9:] if name.startswith('da-slide:') else ''


def _is_protected(element, ids):
    for node in element.iter(P + 'cNvPr'):
        name = node.get('name') or ''
        descr = node.get('descr') or ''
        element_id = name[3:] if name.startswith('da:') else name
        if element_id in ids or 'logo' in f'{name} {descr}'.lower():
            return True
    return False


def _is_complex_frame(element):
    blob = etree.tostring(element).lower()
    return b'oleobj' in blob or b'<dgm:' in blob or b'diagram' in blob


def _name(element):
    node = element.find('.//' + P + 'cNvPr')
    return node.get('name') if node is not None else 'logo'


def _add(unhandled, seen, part, color, reason, status):
    key = (part, str(color), reason, status)
    if key in seen:
        return
    seen.add(key)
    unhandled.append(dict(part=part, color=color, reason=reason, status=status, location=part))
