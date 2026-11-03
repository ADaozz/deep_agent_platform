"""LaTeX -> editable PowerPoint Office Math (DrawingML, never Word XML/images).

The closed MathML visitor rejects unsupported constructs instead of silently
flattening them. Source and browser MathML stay in the element manifest.
"""
import math
import re
import io
import zipfile
from lxml import etree as ET

M='http://schemas.openxmlformats.org/officeDocument/2006/math'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
A14='http://schemas.microsoft.com/office/drawing/2010/main'
MC='http://schemas.openxmlformats.org/markup-compatibility/2006'
ML='http://www.w3.org/1998/Math/MathML'
NS={'m':M,'a':A,'a14':A14,'mc':MC}
NARY=set('∫∬∭∮∯∰∑∏∐⋃⋂')
SUPPORTED={'math','mrow','mstyle','mi','mn','mo','mtext','mspace','mfrac',
           'msqrt','mroot','msub','msup','msubsup','munder','mover','munderover',
           'mtable','mtr','mtd','mpadded','mphantom'}


def node(tag,*children,**attrs):
    prefix,name=tag.split(':')
    result=ET.Element('{'+NS[prefix]+'}'+name)
    for key,value in attrs.items():result.set('{'+M+'}'+key,str(value))
    result.extend(children)
    return result


def tag(element):return ET.QName(element).localname


def parse(latex):
    if not isinstance(latex,str) or not latex.strip() or len(latex)>8000:
        raise ValueError('One nonempty LaTeX formula of at most 8000 characters required')
    source=latex.strip()
    for opening,closing in (('$$','$$'),('$','$'),(r'\[',r'\]'),(r'\(',r'\)')):
        if source.startswith(opening) and source.endswith(closing):
            source=source[len(opening):-len(closing)].strip();break
    if not source:raise ValueError('Empty formula')
    environments=[]
    for event,name in re.findall(r'\\(begin|end)\{([^}]+)\}',source):
        if name not in {'matrix','pmatrix','bmatrix','Bmatrix','vmatrix','Vmatrix','cases','smallmatrix'}:
            raise ValueError('Unsupported equation environment: '+name)
        if event=='begin':environments.append(name)
        elif not environments or environments.pop()!=name:raise ValueError('Unbalanced equation environments')
    if environments:raise ValueError('Unclosed equation environment')
    # No macro expansion or TeX/file execution. Catch unmatched groups early.
    depth=0
    for match in re.finditer(r'\\.|[{}]',source):
        value=match.group()
        if value=='{':depth+=1
        elif value=='}':depth-=1
        if depth<0 or depth>40:raise ValueError('Unbalanced or excessively nested LaTeX groups')
    if depth:raise ValueError('Unbalanced LaTeX groups')
    try:
        from latex2mathml.converter import convert
    except ImportError as exc:raise ValueError('Install latex2mathml from the skill requirements') from exc
    try:root=ET.fromstring(convert(source,display='block').encode(),ET.XMLParser(resolve_entities=False,no_network=True))
    except Exception as exc:raise ValueError('Invalid LaTeX formula: '+type(exc).__name__) from exc
    elements=list(root.iter())
    if len(elements)>2000:raise ValueError('Formula is too complex')
    for el in elements:
        if ET.QName(el).namespace!=ML or tag(el) not in SUPPORTED:
            raise ValueError('Unsupported formula construct: '+tag(el))
        if tag(el) in ('mi','mo') and '\\' in (el.text or ''):
            raise ValueError('Unsupported LaTeX command: '+str(el.text))
        # Browser preview receives only known MathML, with no URL/style/event attrs.
        for key in list(el.attrib):
            if key not in {'display','displaystyle','scriptlevel','mathvariant','stretchy','fence','form','accent','accentunder','columnalign','rowalign','width','height','depth','lspace','rspace','linethickness','minsize','maxsize'}:
                raise ValueError('Unsupported formula attribute: '+key)
        if el.get('scriptlevel') not in (None,'0'):raise ValueError('Explicit script sizing is not supported')
    return source,root


class Builder:
    def __init__(self,size,color):self.size=size;self.color=color
    def run(self,text,variant=None):
        r=node('m:r')
        if variant:
            style={'normal':'p','bold':'b','italic':'i','bold-italic':'bi'}.get(variant)
            if style:r.append(node('m:rPr',node('m:sty',val=style)))
        properties=node('a:rPr');properties.set('sz',str(round(self.size*100)))
        fill=node('a:solidFill');rgb=node('a:srgbClr');rgb.set('val',self.color);fill.append(rgb)
        font=node('a:latin');font.set('typeface','Cambria Math');properties.extend([fill,font]);r.append(properties)
        t=node('m:t');t.text=text;t.set('{http://www.w3.org/XML/1998/namespace}space','preserve');r.append(t)
        return r
    def sequence(self,elements,variant=None):
        elements=list(elements);out=[];i=0
        # Stretchy delimiters, including the one-sided brace of cases.
        if elements and tag(elements[0])=='mo' and elements[0].text in ('(','[','{','|','‖'):
            left=elements[0].text;expected={'(':')','[':']','{':'}','|':'|','‖':'‖'}[left]
            closed=len(elements)>1 and tag(elements[-1])=='mo' and elements[-1].text==expected
            if closed and left not in ('|','‖'):
                level=0
                for index,part in enumerate(elements):
                    if tag(part)=='mo' and part.text==left:level+=1
                    elif tag(part)=='mo' and part.text==expected:level-=1
                    if level==0 and index<len(elements)-1:closed=False;break
            if closed or (elements[0].get('fence')=='true' and len(elements)==2 and tag(elements[1])=='mtable'):
                end=expected if closed else ''
                return [node('m:d',node('m:dPr',node('m:begChr',val=left),node('m:endChr',val=end)),
                             node('m:e',*self.sequence(elements[1:-1] if closed else elements[1:],variant)))]
        while i<len(elements):
            el=elements[i];kind=tag(el);base=el[0] if kind in ('msub','msup','msubsup','munder','mover','munderover') else el
            if tag(base)=='mo' and base.text in NARY:
                lower=el[1] if kind in ('msub','msubsup','munder','munderover') else None
                upper=el[2] if kind in ('msubsup','munderover') else el[1] if kind in ('msup','mover') else None
                j=i+1;level=0
                # An ungrouped relation or +/- ends this integrand/summand.
                while j<len(elements):
                    current=elements[j].text if tag(elements[j])=='mo' else ''
                    if current in ('(','[','{'):level+=1
                    elif current in (')',']','}'):level-=1
                    if level==0 and j>i+1 and current in ('=','+','−','-','≤','≥','<','>','≈',','):break
                    j+=1
                props=node('m:naryPr',node('m:chr',val=base.text),
                    node('m:limLoc',val='subSup' if base.text in set('∫∬∭∮∯∰') else 'undOvr'),
                    node('m:subHide',val='1' if lower is None else '0'),node('m:supHide',val='1' if upper is None else '0'))
                out.append(node('m:nary',props,node('m:sub',*(self.convert(lower,variant) if lower is not None else [])),
                    node('m:sup',*(self.convert(upper,variant) if upper is not None else [])),node('m:e',*self.sequence(elements[i+1:j],variant))))
                i=j;continue
            out.extend(self.convert(el,variant));i+=1
        return out
    def convert(self,el,variant=None):
        kind=tag(el);variant=el.get('mathvariant',variant)
        if kind in ('math','mrow','mstyle','mtd'):return self.sequence(el,variant)
        if kind in ('mi','mn','mo','mtext'):return [self.run(el.text or '',variant or ('normal' if kind=='mtext' else None))]
        if kind=='mspace':return [self.run('\u2009')]
        if kind=='mpadded':raise ValueError('Unsupported spacing construct: mpadded')
        if kind=='mphantom':raise ValueError('Invisible formula content is not supported')
        if kind=='mfrac':
            if len(el)!=2:raise ValueError('Fraction needs numerator and denominator')
            props=node('m:fPr')
            if el.get('linethickness') in ('0','0px'):props.append(node('m:type',val='noBar'))
            return [node('m:f',props,node('m:num',*self.convert(el[0],variant)),node('m:den',*self.convert(el[1],variant)))]
        if kind in ('msqrt','mroot'):
            if kind=='mroot' and len(el)!=2:raise ValueError('Root needs radicand and degree')
            return [node('m:rad',node('m:radPr',node('m:degHide',val='1' if kind=='msqrt' else '0')),
                node('m:deg',*(self.convert(el[1],variant) if kind=='mroot' else [])),
                node('m:e',*(self.convert(el[0],variant) if kind=='mroot' else self.sequence(el,variant))))]
        if kind in ('msub','msup','msubsup'):
            expected=3 if kind=='msubsup' else 2
            if len(el)!=expected:raise ValueError('Invalid script arguments')
            result=node({'msub':'m:sSub','msup':'m:sSup','msubsup':'m:sSubSup'}[kind],node('m:e',*self.convert(el[0],variant)))
            if kind in ('msub','msubsup'):result.append(node('m:sub',*self.convert(el[1],variant)))
            if kind in ('msup','msubsup'):result.append(node('m:sup',*self.convert(el[-1],variant)))
            return [result]
        if kind in ('munder','mover','munderover'):
            if kind=='mover' and len(el)==2 and tag(el[1])=='mo' and el[1].text in ('^','ˆ','~','˜','→','˙','¨','¯','―'):
                return [node('m:acc',node('m:accPr',node('m:chr',val=el[1].text)),node('m:e',*self.convert(el[0],variant)))]
            if kind=='munderover':
                low=node('m:limLow',node('m:e',*self.convert(el[0],variant)),node('m:lim',*self.convert(el[1],variant)))
                return [node('m:limUpp',node('m:e',low),node('m:lim',*self.convert(el[2],variant)))]
            return [node('m:limLow' if kind=='munder' else 'm:limUpp',node('m:e',*self.convert(el[0],variant)),node('m:lim',*self.convert(el[1],variant)))]
        if kind=='mtable':
            rows=list(el)
            if not rows or len(rows)>12 or not 1<=len(rows[0])<=12 or any(tag(r)!='mtr' or len(r)!=len(rows[0]) or any(tag(c)!='mtd' for c in r) for r in rows):
                raise ValueError('Matrix must be rectangular, at most 12 x 12')
            return [node('m:m',*(node('m:mr',*(node('m:e',*self.convert(cell,variant)) for cell in row)) for row in rows))]
        raise ValueError('Unsupported formula construct: '+kind)


def compile(latex,font_size=24,color='17324D',align='center'):
    if type(font_size) not in (int,float) or not math.isfinite(font_size) or not 6<=font_size<=144:raise ValueError('Equation font_size must be 6..144 pt')
    if not isinstance(color,str) or not re.fullmatch('[0-9A-Fa-f]{6}',color):raise ValueError('Equation color must be RRGGBB')
    if align not in ('left','center','right'):raise ValueError('Equation align must be left, center or right')
    source,mathml=parse(latex)
    formula=node('m:oMath',*Builder(font_size,color.upper()).convert(mathml))
    if not formula.findall('.//{'+M+'}t') and not formula.findall('.//{'+M+'}chr'):raise ValueError('Formula has no visible content')
    paragraph=node('m:oMathPara',node('m:oMathParaPr',node('m:jc',val={'left':'left','center':'centerGroup','right':'right'}[align])),formula)
    return dict(latex=source,mathml=ET.tostring(mathml,encoding='unicode'),omml=paragraph,
                font_size=font_size,color=color.upper(),align=align)


def is_equation(shape):return bool(shape._element.findall('.//{'+A14+'}m'))


def catalog():
    return dict(format='PowerPoint Office Math (OMML)',input='LaTeX math body',editable=True,
        examples=dict(fraction=r'\frac{a+b}{c}',matrix=r'\begin{bmatrix}a&b\\c&d\end{bmatrix}',
            integral=r'\int_0^1 x^2\,dx=\frac{1}{3}',sum=r'\sum_{i=1}^{n}x_i',
            root=r'\sqrt{x}+\sqrt[3]{y}',scripts=r'x_i^2',cases=r'\begin{cases}x&x>0\\0&x\le0\end{cases}'),
        unsupported=['TeX documents/macros/packages','aligned/align environments','arbitrary MathML/OMML input'])


def prepare_render(data):
    """Unwrap paragraph math MC choices only in the disposable Impress input.

    Impress reads direct a14:m, but ignores a paragraph's MC Choice. Keep all
    native OMML unchanged; the user's file and version snapshot are untouched.
    """
    if b'PK'!=data[:2]:return data
    output=io.BytesIO();changed=False
    with zipfile.ZipFile(io.BytesIO(data)) as src,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as dst:
        for entry in src.infolist():
            blob=src.read(entry.filename)
            if re.fullmatch(r'ppt/slides/slide\d+\.xml',entry.filename) and A14.encode() in blob:
                root=ET.fromstring(blob,ET.XMLParser(resolve_entities=False,no_network=True));modified=False
                for paragraph in root.findall('.//{'+A+'}p'):
                    for alt in list(paragraph):
                        if alt.tag!='{'+MC+'}AlternateContent':continue
                        choices=[child for child in alt if child.tag=='{'+MC+'}Choice']
                        choice=next((ch for ch in choices if len(ch)==1 and ch[0].tag=='{'+A14+'}m'),None)
                        if choice is not None:
                            paragraph.replace(alt,choice[0]);modified=True
                if modified:blob=ET.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True);changed=True
            dst.writestr(entry,blob)
    return output.getvalue() if changed else data


def write(shape,compiled):
    """Replace only the formula text body, retaining shape identity and geometry."""
    if not shape.has_text_frame:raise ValueError('Equation requires a text shape')
    frame=shape.text_frame;frame.clear();frame.word_wrap=False
    frame.margin_left=frame.margin_right=frame.margin_top=frame.margin_bottom=0
    from pptx.enum.text import MSO_AUTO_SIZE
    frame.auto_size=MSO_AUTO_SIZE.NONE
    p=frame.paragraphs[0]._p
    for child in list(p):p.remove(child)
    ppr=node('a:pPr');ppr.set('algn',{'left':'l','center':'ctr','right':'r'}[compiled['align']]);p.append(ppr)
    alternate=ET.SubElement(p,'{'+MC+'}AlternateContent',nsmap={'mc':MC,'a14':A14,'m':M})
    choice=ET.SubElement(alternate,'{'+MC+'}Choice',Requires='a14')
    wrapper=ET.SubElement(choice,'{'+A14+'}m');wrapper.append(compiled['omml'])
    # No flattened text/image fallback: unsupported viewers must not claim a valid render.
    end=node('a:endParaRPr');end.set('sz',str(round(compiled['font_size']*100)));p.append(end)


def create(slide,latex,position,*,font_size=24,color='17324D',align='center'):
    data=compile(latex,font_size,color,align)
    shape=slide.shapes.add_textbox(*position);write(shape,data)
    import slide_renderer
    slide_renderer.set_metadata(shape,dict(kind='equation',latex=data['latex'],mathml=data['mathml'],align=align,equation_align=align,
        style_overrides=dict(font_size=font_size,color=color.upper(),align=align),
        resolved_style=dict(font_family='Cambria Math',font_size=font_size,color=color.upper())))
    return shape


def replace(shape,latex,*,font_size=24,color='17324D',align='center'):
    if not is_equation(shape):raise ValueError('Target is not a native equation')
    data=compile(latex,font_size,color,align);write(shape,data)
    import slide_renderer
    meta=slide_renderer.metadata(shape)
    meta.update(kind='equation',latex=data['latex'],mathml=data['mathml'],align=align,equation_align=align,
                style_overrides=dict(font_size=font_size,color=color.upper(),align=align),
                resolved_style=dict(font_family='Cambria Math',font_size=font_size,color=color.upper()))
    slide_renderer.set_metadata(shape,meta)
    return data
