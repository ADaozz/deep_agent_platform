"""Editable OMML, local updates and real equation rendering (no raster substitute)."""
import importlib.util
import io
from pathlib import Path
import shutil
import zipfile

import pytest
from lxml import etree
from pptx import Presentation
from pptx.util import Inches

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('math_engine',ROOT/'scripts'/'office_pptx.py')
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
math=engine.native_math
M='{'+math.M+'}'

EXAMPLES=[
    (r'\frac{a+b}{c}', 'f'),
    (r'\dfrac{1}{1+\frac{1}{x}}', 'f'),
    (r'A=\begin{bmatrix}a&b\\c&d\end{bmatrix}', 'm'),
    (r'\begin{pmatrix}1&0\\0&1\end{pmatrix}', 'm'),
    (r'\int_0^1 x^2\,dx=\frac{1}{3}', 'nary'),
    (r'\iint_D f(x,y)\,dx\,dy', 'nary'),
    (r'\sum_{i=1}^{n} x_i', 'nary'),
    (r'\prod_{i=1}^{n} x_i', 'nary'),
    (r'x_i^2+\sqrt{x}+\sqrt[3]{y}', 'rad'),
    (r'\hat{x}+\vec{y}', 'acc'),
    (r'\begin{cases}x&x>0\\0&x\le0\end{cases}', 'm'),
    (r'\lim_{x\to0}\frac{\sin x}{x}', 'f'),
    (r'\binom{n}{k}', 'f'),
]


@pytest.mark.parametrize('latex,expected',EXAMPLES)
def test_latex_compiles_to_real_drawingml_math(latex,expected):
    data=math.compile(latex)
    assert data['omml'].findall('.//'+M+expected)
    assert data['omml'].findall('.//{'+math.A+'}rPr')
    assert not any(etree.QName(el).namespace=='http://schemas.openxmlformats.org/wordprocessingml/2006/main' for el in data['omml'].iter())
    assert etree.fromstring(data['mathml'].encode()).tag=='{'+math.ML+'}math'


def test_integral_has_editable_limits_and_integrand():
    formula=math.compile(r'\int_0^1 (x+1)\,dx+y')['omml']
    integral=formula.find('.//'+M+'nary')
    assert ''.join(integral.find(M+'sub').itertext())=='0'
    assert ''.join(integral.find(M+'sup').itertext())=='1'
    body=''.join(integral.find(M+'e').itertext())
    assert 'x+1' in body and 'dx' in body.replace('\u2009','') and 'y' not in body


@pytest.mark.parametrize('latex',[
    '',r'\frac{x}{',r'\unknown{x}',r'\begin{bmatrix}1&2\\3\end{bmatrix}',
    r'\begin{bmatrix}1&2',r'\begin{aligned}a&=b\end{aligned}',r'\phantom{x}',
    r'\input{/etc/passwd}',r'\begin{matrix}x\end{pmatrix}',
])
def test_invalid_or_unsupported_formulas_are_not_silently_flattened(latex):
    with pytest.raises(ValueError):math.compile(latex)


def op(store,operation,sid='p1',**fields):
    state=store.status()
    return store.apply_one(dict(presentation_id='math',operation_id=f'op-{state["revision"]+1}',
        base_revision=state['revision'],operation=operation,slide_id=sid,**fields))


def test_atomic_equation_update_style_clone_and_failed_edit_preserve_native_structure(tmp_path):
    store=engine.PresentationStore(tmp_path/'deck.pptx');store.init('math')
    op(store,'slide.add');op(store,'slide.add',sid='p2')
    op(store,'element.add_equation',element=dict(id='eq',latex=r'\frac{a}{b}',font_size=28,
        geometry=dict(x=.1,y=.25,w=.7,h=.4)))
    before=Presentation(store.path);other=before.slides[1]._element.xml
    shape_id=before.slides[0].shapes[0].shape_id
    op(store,'element.update',element=dict(id='eq',latex=r'\int_0^1 x^2\,dx'))
    op(store,'element.set_style',element=dict(id='eq',style=dict(font_size=30,color='accent',align='right')))
    prs=Presentation(store.path);shape=prs.slides[0].shapes[0]
    assert shape.shape_id==shape_id and math.is_equation(shape)
    assert prs.slides[1]._element.xml==other
    assert len(shape._element.findall('.//{'+math.A14+'}m'))==1
    assert len(shape._element.findall('.//{'+math.A+'}pPr'))==1
    assert shape._element.find('.//{'+math.A+'}rPr').get('sz')=='3000'
    assert engine.renderer.metadata(shape)['resolved_style']['font_family']=='Cambria Math'
    element=store.status()['manifest']['slides'][0]['elements'][0]
    assert element['type']=='equation' and element['native_equation'] and element['mathml']
    assert element['align']=='right'
    data=store.path.read_bytes();revision=store.status()['revision']
    for fields in (dict(latex=r'\badcommand'),dict(text='not a formula')):
        with pytest.raises(ValueError):op(store,'element.update',element=dict(id='eq',**fields))
        assert store.path.read_bytes()==data and store.status()['revision']==revision
    with pytest.raises(ValueError,match='native equation'):
        engine.template_fill.fill_shape_text(shape,'overwrite')
    clone=engine.template_clone.clone_slide(prs,prs.slides[0],'copy')
    assert math.is_equation(clone.shapes[0])
    out=io.BytesIO();prs.save(out)
    assert math.is_equation(Presentation(io.BytesIO(out.getvalue())).slides[2].shapes[0])


def test_preview_preprocessing_unwraps_only_math_and_keeps_user_file_native(tmp_path):
    prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
    math.create(slide,r'\frac{a}{b}',(Inches(1),Inches(1),Inches(3),Inches(2)))
    out=io.BytesIO();prs.save(out);original=out.getvalue();render=math.prepare_render(original)
    assert render!=original
    for data,alternate in ((original,True),(render,False)):
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            root=etree.fromstring(package.read('ppt/slides/slide1.xml'))
            assert bool(root.findall('.//{'+math.MC+'}AlternateContent')) is alternate
            assert root.findall('.//'+M+'f')
            assert not any(n.startswith('ppt/media/') for n in package.namelist())
    assert math.prepare_render(render)==render


def test_script_equation_keeps_custom_style_during_content_and_color_edits(tmp_path):
    prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
    shape=math.create(slide,r'\frac{a}{b}',(Inches(1),Inches(1),Inches(4),Inches(2)),font_size=32,color='E53935')
    shape.name='da:eq';path=tmp_path/'deck.pptx';prs.save(path)
    store=engine.PresentationStore(path);store.track_external('math')
    sid=store.status()['manifest']['slides'][0]['slide_id']
    op(store,'element.update',sid=sid,element=dict(id='eq',latex=r'\sqrt{x}'))
    meta=engine.renderer.metadata(Presentation(path).slides[0].shapes[0])
    assert meta['resolved_style']['font_size']==32 and meta['resolved_style']['color']=='E53935'
    op(store,'element.set_style',sid=sid,element=dict(id='eq',style=dict(color='accent')))
    meta=engine.renderer.metadata(Presentation(path).slides[0].shapes[0])
    assert meta['resolved_style']['font_size']==32 and meta['resolved_style']['color']=='246BCE'


def test_equation_can_use_an_existing_text_layout_anchor(tmp_path):
    store=engine.PresentationStore(tmp_path/'deck.pptx');store.init('math')
    op(store,'slide.add',layout='title_body')
    op(store,'element.add_equation',element=dict(id='eq',latex=r'\frac{a}{b}',anchor='body'))
    assert store.status()['manifest']['slides'][0]['elements'][0]['type']=='equation'


def test_vision_context_includes_equation_source_and_stable_locator():
    spec=importlib.util.spec_from_file_location('math_vision_context',ROOT/'vision_review_service.py')
    vision=importlib.util.module_from_spec(spec);spec.loader.exec_module(vision)
    context=vision.element_context(dict(id='eq-1',type='equation',native_equation=True,latex=r'\frac{a}{b}',
        geometry=dict(x=.1,y=.2,w=.5,h=.3)))
    assert context['id']=='eq-1' and context['equation']==dict(format='OMML',latex=r'\frac{a}{b}',source_truncated=False)


@pytest.mark.skipif(not shutil.which('libreoffice'),reason='LibreOffice required')
def test_real_preview_renders_fraction_matrix_integral_without_modifying_ppt(tmp_path):
    import pymupdf
    prs=Presentation();prs.slide_width=Inches(13.333);prs.slide_height=Inches(7.5)
    slide=prs.slides.add_slide(prs.slide_layouts[6])
    for i,formula in enumerate((r'\frac{a+b}{c}',r'\begin{bmatrix}a&b\\c&d\end{bmatrix}',r'\int_0^1 x^2\,dx')):
        shape=math.create(slide,formula,(Inches(1+4*i),Inches(2),Inches(3),Inches(3)),font_size=28);shape.name=f'da:eq-{i}'
    path=tmp_path/'deck.pptx';prs.save(path);original=path.read_bytes()
    store=engine.PresentationStore(path);store.track_external('math')
    folder=engine.reviewer().render_snapshot(store,0)
    with pymupdf.open(folder/'deck.pdf') as pdf:
        text=pdf[0].get_text()
        assert 'a+b' in text.replace(' ','') and '∫' in text and 'd' in text
        assert all(pdf[0].get_text(clip=pymupdf.Rect((1+4*i)*72,144,(4+4*i)*72,360)).strip() for i in range(3))
    assert path.read_bytes()==original and store.snapshot(0)==original
    sid=store.status()['manifest']['slides'][0]['slide_id']
    check=store.check_deck({sid:'核对分式分子分母、矩阵四个单元及积分上下限和积分项'})
    assert check['status']=='passed',check
