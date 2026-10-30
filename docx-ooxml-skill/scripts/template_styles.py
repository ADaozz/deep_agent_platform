"""Recolor a DOCX through its theme and named styles.

Direct formatting is remapped only when its color is one of the recorded theme
colors. Other explicit colors are returned as unhandled and left in place.
"""
from lxml import etree

ROLE = {
    'accent1': 'accent', 'accent3': 'accent', 'accent4': 'accent', 'accent5': 'accent', 'accent6': 'accent',
    'accent2': 'muted', 'dk2': 'muted', 'dk1': 'text', 'lt1': 'background', 'lt2': 'background',
}
WORD_THEME = {
    'accent1': 'accent', 'accent2': 'muted', 'accent3': 'accent', 'accent4': 'accent',
    'accent5': 'accent', 'accent6': 'accent', 'dark1': 'text', 'dark2': 'muted',
    'light1': 'background', 'light2': 'background', 'text1': 'text', 'text2': 'muted',
    'background1': 'background', 'background2': 'background',
}
REQUIRED = ('accent', 'background', 'text')
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def recolor(parts, op, o):
    colors = _palette(op.get('palette'))
    theme_name = 'word/theme/theme1.xml'
    old = {}
    if theme_name in parts:
        theme = o.parse_xml(parts[theme_name])
        old = _scheme_hexes(theme)
        _write_scheme(theme, colors)
        parts[theme_name] = o.xml_bytes(theme)
    mapping, ambiguous = _hex_mapping(old, colors)
    unhandled, seen = [], set()
    for name in _targets(parts):
        root = o.parse_xml(parts[name])
        _walk(root, colors, mapping, name, unhandled, seen, o)
        parts[name] = o.xml_bytes(root)
    for color in ambiguous:
        _add(unhandled, seen, 'theme', color, 'ambiguous theme color maps to more than one role', 'unmapped', 'theme/clrScheme')
    outstanding = [item for item in unhandled if item['status'] in ('unmapped', 'unsupported')]
    report = dict(
        recolor_complete=not outstanding,
        remapped=[{'from': src, 'to': dst} for src, dst in sorted(mapping.items())],
        unhandled=outstanding[:40],
        unhandled_count=len(outstanding),
        findings=unhandled[:80],
        preserved=[item for item in unhandled if item['status'] == 'preserved'][:40],
    )
    return parts, ['styles'], report


def _targets(parts):
    names = ['word/styles.xml', 'word/document.xml']
    names.extend(sorted(name for name in parts if name.startswith(('word/header', 'word/footer')) and name.endswith('.xml')))
    return [name for name in names if name in parts]


def _palette(palette):
    if not isinstance(palette, dict):
        raise ValueError('palette must be an object of semantic colors')
    missing = [key for key in REQUIRED if key not in palette]
    if missing:
        raise ValueError('palette requires ' + ', '.join(missing))
    colors = {}
    for key in (*REQUIRED, 'muted'):
        if key in palette:
            colors[key] = _hex(palette[key])
    return colors


def _hex(value):
    if not isinstance(value, str):
        raise ValueError('palette colors must be RRGGBB')
    text = value.strip().lstrip('#')
    if len(text) != 6 or any(c not in '0123456789abcdefABCDEF' for c in text):
        raise ValueError('palette colors must be RRGGBB')
    return text.upper()


def _scheme_hexes(theme):
    scheme = theme.find('.//' + A + 'clrScheme')
    found = {}
    if scheme is None:
        return found
    for child in scheme:
        srgb = child.find(A + 'srgbClr')
        sysclr = child.find(A + 'sysClr')
        value = srgb.get('val') if srgb is not None else (sysclr.get('lastClr') if sysclr is not None else None)
        if value:
            found[etree.QName(child).localname] = value.upper()
    return found


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


def _hex_mapping(old, colors):
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


def _walk(node, colors, mapping, part, unhandled, seen, o):
    if not isinstance(node.tag, str):
        return
    local = etree.QName(node).localname
    if local == 'drawing' and node.find('.//' + A + 'blip') is not None:
        _add(unhandled, seen, part, '', 'picture was kept', 'preserved', _location(node))
        return
    if local == 'drawing':
        for color_node in node.iter(A + 'srgbClr'):
            value = (color_node.get('val') or '').upper()
            if value:
                _add(unhandled, seen, part, value, 'drawing shape color is outside the recolor scope', 'unsupported', _location(color_node))
        return
    if local == 'color':
        value = (node.get(o.q(o.W, 'val')) or '').upper()
        theme = node.get(o.q(o.W, 'themeColor'))
        if theme in WORD_THEME and WORD_THEME[theme] in colors:
            node.set(o.q(o.W, 'val'), colors[WORD_THEME[theme]])
        elif value in mapping:
            node.set(o.q(o.W, 'val'), mapping[value])
        elif _hex_color(value):
            _add(unhandled, seen, part, value, 'direct color is outside the theme palette', 'unmapped', _location(node))
    elif local in ('shd', 'highlight'):
        value = (node.get(o.q(o.W, 'fill') if local == 'shd' else o.q(o.W, 'val')) or '').upper()
        if value in mapping:
            attribute = o.q(o.W, 'fill') if local == 'shd' else o.q(o.W, 'val')
            node.set(attribute, mapping[value])
        elif _hex_color(value):
            kind = 'table or paragraph shading' if local == 'shd' else 'highlight'
            _add(unhandled, seen, part, value, kind + ' is outside the theme palette', 'unmapped', _location(node))
    for child in list(node):
        _walk(child, colors, mapping, part, unhandled, seen, o)


def _hex_color(value):
    return len(value) == 6 and value not in ('AUTO',) and all(character in '0123456789ABCDEF' for character in value)


def _location(node):
    names = []
    current = node
    while current is not None and len(names) < 6 and isinstance(current.tag, str):
        names.append(etree.QName(current).localname)
        current = current.getparent()
    return '/'.join(reversed(names))


def _add(unhandled, seen, part, color, reason, status, location):
    key = (part, color, reason, status, location)
    if key in seen:
        return
    seen.add(key)
    unhandled.append(dict(part=part, color=color, reason=reason, status=status, location=location))
