"""LaTeX to editable Word Office Math (OMML inside a paragraph, never a picture)."""
import math
import re
from lxml import etree as ET

M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML = 'http://www.w3.org/XML/1998/namespace'
ML = 'http://www.w3.org/1998/Math/MathML'
NS = {'m': M, 'w': W}
NARY = set('∫∬∭∮∯∰∑∏∐⋃⋂')
SUPPORTED = {'math', 'mrow', 'mstyle', 'mi', 'mn', 'mo', 'mtext', 'mspace', 'mfrac',
    'msqrt', 'mroot', 'msub', 'msup', 'msubsup', 'munder', 'mover', 'munderover',
    'mtable', 'mtr', 'mtd', 'mpadded', 'mphantom'}


def node(tag, *children, **attrs):
    prefix, name = tag.split(':')
    result = ET.Element('{' + NS[prefix] + '}' + name)
    for key, value in attrs.items():
        result.set('{' + M + '}' + key, str(value))
    result.extend(children)
    return result


def tag(element):
    return ET.QName(element).localname


def parse(latex):
    if not isinstance(latex, str) or not latex.strip() or len(latex) > 8000:
        raise ValueError('One nonempty LaTeX formula of at most 8000 characters required')
    source = latex.strip()
    for opening, closing in (('$$', '$$'), ('$', '$'), (r'\[', r'\]'), (r'\(', r'\)')):
        if source.startswith(opening) and source.endswith(closing):
            source = source[len(opening):-len(closing)].strip()
            break
    if not source:
        raise ValueError('Empty formula')
    environments = []
    for event, name in re.findall(r'\\(begin|end)\{([^}]+)\}', source):
        if name not in {'matrix', 'pmatrix', 'bmatrix', 'Bmatrix', 'vmatrix', 'Vmatrix', 'cases', 'smallmatrix'}:
            raise ValueError('Unsupported equation environment: ' + name)
        if event == 'begin':
            environments.append(name)
        elif not environments or environments.pop() != name:
            raise ValueError('Unbalanced equation environments')
    if environments:
        raise ValueError('Unclosed equation environment')
    depth = 0
    for match in re.finditer(r'\\.|[{}]', source):
        value = match.group()
        if value == '{':
            depth += 1
        elif value == '}':
            depth -= 1
        if depth < 0 or depth > 40:
            raise ValueError('Unbalanced or excessively nested LaTeX groups')
    if depth:
        raise ValueError('Unbalanced LaTeX groups')
    try:
        from latex2mathml.converter import convert
    except ImportError as exc:
        raise ValueError('Install latex2mathml from the skill requirements') from exc
    try:
        root = ET.fromstring(convert(source, display='block').encode(), ET.XMLParser(resolve_entities=False, no_network=True))
    except Exception as exc:
        raise ValueError('Invalid LaTeX formula: ' + type(exc).__name__) from exc
    elements = list(root.iter())
    if len(elements) > 2000:
        raise ValueError('Formula is too complex')
    for el in elements:
        if ET.QName(el).namespace != ML or tag(el) not in SUPPORTED:
            raise ValueError('Unsupported formula construct: ' + tag(el))
        if tag(el) in ('mi', 'mo') and '\\' in (el.text or ''):
            raise ValueError('Unsupported LaTeX command: ' + str(el.text))
    return source, root


class Builder:
    def __init__(self, size, color):
        self.size = size
        self.color = color

    def run(self, text, variant=None):
        result = node('m:r')
        if variant:
            style = {'normal': 'p', 'bold': 'b', 'italic': 'i', 'bold-italic': 'bi'}.get(variant)
            if style:
                result.append(node('m:rPr', node('m:sty', val=style)))
        properties = ET.Element('{' + W + '}rPr')
        fonts = ET.SubElement(properties, '{' + W + '}rFonts')
        for key in ('ascii', 'hAnsi', 'cs'):
            fonts.set('{' + W + '}' + key, 'Cambria Math')
        ET.SubElement(properties, '{' + W + '}sz').set('{' + W + '}val', str(int(round(self.size * 2))))
        ET.SubElement(properties, '{' + W + '}color').set('{' + W + '}val', self.color)
        result.append(properties)
        text_node = node('m:t')
        text_node.text = text
        text_node.set('{' + XML + '}space', 'preserve')
        result.append(text_node)
        return result

    def sequence(self, elements, variant=None):
        elements = list(elements)
        out = []
        i = 0
        if elements and tag(elements[0]) == 'mo' and elements[0].text in ('(', '[', '{', '|', '‖'):
            left = elements[0].text
            expected = {'(': ')', '[': ']', '{': '}', '|': '|', '‖': '‖'}[left]
            closed = len(elements) > 1 and tag(elements[-1]) == 'mo' and elements[-1].text == expected
            if closed and left not in ('|', '‖'):
                level = 0
                for index, part in enumerate(elements):
                    if tag(part) == 'mo' and part.text == left:
                        level += 1
                    elif tag(part) == 'mo' and part.text == expected:
                        level -= 1
                    if level == 0 and index < len(elements) - 1:
                        closed = False
                        break
            if closed or (elements[0].get('fence') == 'true' and len(elements) == 2 and tag(elements[1]) == 'mtable'):
                end = expected if closed else ''
                return [node('m:d', node('m:dPr', node('m:begChr', val=left), node('m:endChr', val=end)),
                    node('m:e', *self.sequence(elements[1:-1] if closed else elements[1:], variant)))]
        while i < len(elements):
            el = elements[i]
            kind = tag(el)
            base = el[0] if kind in ('msub', 'msup', 'msubsup', 'munder', 'mover', 'munderover') else el
            if tag(base) == 'mo' and base.text in NARY:
                lower = el[1] if kind in ('msub', 'msubsup', 'munder', 'munderover') else None
                upper = el[2] if kind in ('msubsup', 'munderover') else el[1] if kind in ('msup', 'mover') else None
                j = i + 1
                level = 0
                while j < len(elements):
                    current = elements[j].text if tag(elements[j]) == 'mo' else ''
                    if current in ('(', '[', '{'):
                        level += 1
                    elif current in (')', ']', '}'):
                        level -= 1
                    if level == 0 and j > i + 1 and current in ('=', '+', '−', '-', '≤', '≥', '<', '>', '≈', ','):
                        break
                    j += 1
                props = node('m:naryPr', node('m:chr', val=base.text),
                    node('m:limLoc', val='subSup' if base.text in set('∫∬∭∮∯∰') else 'undOvr'),
                    node('m:subHide', val='1' if lower is None else '0'), node('m:supHide', val='1' if upper is None else '0'))
                out.append(node('m:nary', props, node('m:sub', *(self.convert(lower, variant) if lower is not None else [])),
                    node('m:sup', *(self.convert(upper, variant) if upper is not None else [])),
                    node('m:e', *self.sequence(elements[i + 1:j], variant))))
                i = j
                continue
            out.extend(self.convert(el, variant))
            i += 1
        return out

    def convert(self, el, variant=None):
        kind = tag(el)
        variant = el.get('mathvariant', variant)
        if kind in ('math', 'mrow', 'mstyle', 'mtd'):
            return self.sequence(el, variant)
        if kind in ('mi', 'mn', 'mo', 'mtext'):
            return [self.run(el.text or '', variant or ('normal' if kind == 'mtext' else None))]
        if kind == 'mspace':
            return [self.run('\u2009')]
        if kind == 'mpadded':
            raise ValueError('Unsupported spacing construct: mpadded')
        if kind == 'mphantom':
            raise ValueError('Invisible formula content is not supported')
        if kind == 'mfrac':
            if len(el) != 2:
                raise ValueError('Fraction needs numerator and denominator')
            props = node('m:fPr')
            if el.get('linethickness') in ('0', '0px'):
                props.append(node('m:type', val='noBar'))
            return [node('m:f', props, node('m:num', *self.convert(el[0], variant)), node('m:den', *self.convert(el[1], variant)))]
        if kind in ('msqrt', 'mroot'):
            if kind == 'mroot' and len(el) != 2:
                raise ValueError('Root needs radicand and degree')
            return [node('m:rad', node('m:radPr', node('m:degHide', val='1' if kind == 'msqrt' else '0')),
                node('m:deg', *(self.convert(el[1], variant) if kind == 'mroot' else [])),
                node('m:e', *(self.convert(el[0], variant) if kind == 'mroot' else self.sequence(el, variant))))]
        if kind in ('msub', 'msup', 'msubsup'):
            expected = 3 if kind == 'msubsup' else 2
            if len(el) != expected:
                raise ValueError('Invalid script arguments')
            result = node({'msub': 'm:sSub', 'msup': 'm:sSup', 'msubsup': 'm:sSubSup'}[kind], node('m:e', *self.convert(el[0], variant)))
            if kind in ('msub', 'msubsup'):
                result.append(node('m:sub', *self.convert(el[1], variant)))
            if kind in ('msup', 'msubsup'):
                result.append(node('m:sup', *self.convert(el[-1], variant)))
            return [result]
        if kind in ('munder', 'mover', 'munderover'):
            if kind == 'mover' and len(el) == 2 and tag(el[1]) == 'mo' and el[1].text in ('^', 'ˆ', '~', '˜', '→', '˙', '¨', '¯', '―'):
                return [node('m:acc', node('m:accPr', node('m:chr', val=el[1].text)), node('m:e', *self.convert(el[0], variant)))]
            if kind == 'munderover':
                low = node('m:limLow', node('m:e', *self.convert(el[0], variant)), node('m:lim', *self.convert(el[1], variant)))
                return [node('m:limUpp', node('m:e', low), node('m:lim', *self.convert(el[2], variant)))]
            return [node('m:limLow' if kind == 'munder' else 'm:limUpp', node('m:e', *self.convert(el[0], variant)), node('m:lim', *self.convert(el[1], variant)))]
        if kind == 'mtable':
            rows = list(el)
            if not rows or len(rows) > 12 or not 1 <= len(rows[0]) <= 12 or any(tag(row) != 'mtr' or len(row) != len(rows[0]) or any(tag(cell) != 'mtd' for cell in row) for row in rows):
                raise ValueError('Matrix must be rectangular, at most 12 x 12')
            return [node('m:m', *(node('m:mr', *(node('m:e', *self.convert(cell, variant)) for cell in row)) for row in rows))]
        raise ValueError('Unsupported formula construct: ' + kind)


def compile_omml(latex, font_size=18, color='1A1A1A'):
    if type(font_size) not in (int, float) or not math.isfinite(font_size) or not 8 <= font_size <= 72:
        raise ValueError('Equation font_size must be 8..72 pt')
    if not isinstance(color, str) or not re.fullmatch(r'[0-9A-Fa-f]{6}', color):
        raise ValueError('Equation color must be RRGGBB')
    source, mathml = parse(latex)
    formula = node('m:oMath', *Builder(font_size, color.upper()).convert(mathml))
    if not formula.findall('.//{' + M + '}t') and not formula.findall('.//{' + M + '}chr'):
        raise ValueError('Formula has no visible content')
    return dict(latex=source, mathml=ET.tostring(mathml, encoding='unicode'), omml=formula, font_size=font_size, color=color.upper())


def paragraph(latex, block_id, *, font_size=18, color='1A1A1A', align='center'):
    """Return one w:p containing native OMML. block_id is applied by the caller."""
    if align not in ('left', 'center', 'right'):
        raise ValueError('Equation align must be left, center or right')
    compiled = compile_omml(latex, font_size, color)
    paragraph_node = ET.Element('{' + W + '}p')
    properties = ET.SubElement(paragraph_node, '{' + W + '}pPr')
    ET.SubElement(properties, '{' + W + '}jc').set('{' + W + '}val', align)
    wrapper = ET.SubElement(paragraph_node, '{' + M + '}oMathPara')
    wrapper.append(compiled['omml'])
    compiled['paragraph'] = paragraph_node
    compiled['block_id'] = block_id
    return compiled


def catalog():
    return dict(format='Word Office Math (OMML)', input='LaTeX math body', editable=True,
        examples=dict(fraction=r'\frac{a+b}{c}', matrix=r'\begin{bmatrix}a&b\\c&d\end{bmatrix}',
            integral=r'\int_0^1 x^2\,dx=\frac{1}{3}', sum=r'\sum_{i=1}^{n} x_i',
            root=r'\sqrt{x}+\sqrt[3]{y}', scripts=r'x_i^2', cases=r'\begin{cases}x&x>0\\0&x\le 0\end{cases}'),
        unsupported=['TeX documents, macros and packages', 'aligned environments', 'pictures or DrawingML stand-ins'])
