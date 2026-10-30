"""Reliability of template fill, palette, logo protection, links and delivery slides."""
import importlib.util
import io
from pathlib import Path

import pytest
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def load(name):
    spec = importlib.util.spec_from_file_location('pptx_rel_' + name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


office = load('office_pptx')
sample = load('sample_template')
fill = load('template_fill')


def accept_all(store):
    with store.locked() as (connection, state):
        for slide in store.view(connection, state)['manifest']['slides']:
            state['reviews'][slide['slide_id']] = dict(
                automatic_passed=True, status='pending', visual_review='pending',
                revision=state['revision'], content_notes='fixture')
        store.save_state(connection, state)
        connection.commit()


def apply(store, kind, **kwargs):
    state = store.status()
    return store.apply_one(dict(
        presentation_id='deck', operation_id=f'op-{state["revision"] + 1}',
        base_revision=state['revision'], operation=kind, **kwargs))


def solid_values(shape):
    return [node.get('val').upper() for node in shape._element.iter(A + 'srgbClr') if node.get('val')]


def test_shape_fill_keeps_schema_order_geometry_and_line():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    rounded = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, Inches(1), Inches(1), Inches(2), Inches(1))
    rounded.fill.gradient()
    rounded.fill.gradient_angle = 45
    rounded.line.color.rgb = RGBColor(0x11, 0x22, 0x33)
    rounded.line.width = Pt(2)
    box = (rounded.left, rounded.top, rounded.width, rounded.height)
    fill.set_shape_fill(rounded, '707780')
    assert [etree.QName(child).localname for child in rounded._element.spPr] == ['xfrm', 'prstGeom', 'solidFill', 'ln']
    assert (rounded.left, rounded.top, rounded.width, rounded.height) == box
    assert str(rounded.line.color.rgb) == '112233'
    rectangle = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(4), Inches(1), Inches(1.2), Inches(0.8))
    rectangle.fill.solid()
    rectangle.fill.fore_color.rgb = RGBColor(0, 0, 255)
    rectangle.line.width = Pt(1.5)
    fill.set_shape_fill(rectangle, 'AABBCC')
    assert [etree.QName(child).localname for child in rectangle._element.spPr][:4] == ['xfrm', 'prstGeom', 'solidFill', 'ln']
    text_box = slide.shapes.add_textbox(Inches(1), Inches(3), Inches(3), Inches(0.6))
    text_box.text_frame.paragraphs[0].text = '边框'
    text_box.line.color.rgb = RGBColor(0x22, 0x22, 0x22)
    text_box.line.width = Pt(1)
    fill.set_shape_fill(text_box, 'F4F4F4')
    assert str(text_box.line.color.rgb) == '222222'
    picture = slide.shapes.add_picture(io.BytesIO(_png()), Inches(6), Inches(1), Inches(1), Inches(1))
    with pytest.raises(ValueError, match='blipFill'):
        fill.set_shape_fill(picture, '000000')


def test_imported_fill_failure_does_not_publish(tmp_path):
    deck = _bordered_shape(tmp_path / 'shapes.pptx')
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    store.init('deck', deck)
    before = store.path.read_bytes()
    revision = store.status()['revision']
    with pytest.raises(ValueError, match='not restyled|blipFill|picture'):
        apply(store, 'element.set_style', slide_id='tpl-004', element={'id': 'photo', 'style': {'fill': '112233'}})
    assert store.path.read_bytes() == before
    assert store.status()['revision'] == revision
    apply(store, 'element.set_style', slide_id='tpl-004', element={'id': 'band', 'style': {'fill': '445566'}})
    opened = Presentation(store.path)
    band = next(shape for slide in opened.slides for shape in slide.shapes if shape.name == 'da:band')
    assert '445566' in solid_values(band)
    assert band.left > 0 and band.width > 0
    office.validator.validate_package(store.path.read_bytes())


def test_recolor_palette_applies_to_new_objects_and_restores(tmp_path):
    sample.build(tmp_path)
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    store.init('deck', tmp_path / 'template.pptx')
    before = store.status()['revision']
    apply(store, 'presentation.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'})
    assert store.status()['theme']['accent'] == '707780'
    assert store.status()['template_index']['slides']
    tokens = store.status()  # theme is synced; original tokens stay on the design record
    accept_all(store)
    apply(store, 'slide.add', slide_id='fresh', layout='title_body')
    apply(store, 'element.add_shape', slide_id='fresh', element={
        'id': 'swatch', 'geometry': {'x': 0.1, 'y': 0.3, 'w': 0.2, 'h': 0.2}, 'shape': 'rectangle', 'fill_token': 'accent'})
    apply(store, 'element.add_chart', slide_id='fresh', element={
        'id': 'bars', 'geometry': {'x': 0.4, 'y': 0.3, 'w': 0.5, 'h': 0.4},
        'chart': {'type': 'bar', 'categories': ['Q1', 'Q2'], 'series': [{'name': '收入', 'values': [3, 5]}]}})
    apply(store, 'element.add_table', slide_id='fresh', element={
        'id': 'grid', 'geometry': {'x': 0.1, 'y': 0.6, 'w': 0.25, 'h': 0.2}, 'rows': [['项', '值'], ['甲', '1']]})
    opened = Presentation(store.path)
    fresh = opened.slides[-1]
    swatch = next(shape for shape in fresh.shapes if shape.name == 'da:swatch')
    assert '707780' in solid_values(swatch)
    assert '246BCE' not in solid_values(swatch)
    chart = next(shape for shape in fresh.shapes if shape.has_chart)
    assert '707780' in etree.tostring(chart.chart._element).decode().upper()
    table = next(shape for shape in fresh.shapes if shape.has_table)
    header = ''.join(solid_values(table)[:1]) or etree.tostring(table._element).decode().upper()
    assert 'EDF4FC' not in header
    restored = store.restore(before, store.status()['revision'], 'restore-palette')
    assert restored['revision'] == store.status()['revision']
    assert store.status()['theme']['accent'] != '707780'
    rolled = Presentation(store.path)
    band = next(shape for slide in rolled.slides for shape in slide.shapes if shape.name == 'da:band')
    assert '0B2545' in solid_values(band)
    del tokens


def test_company_logo_survives_rename_clone_and_recolor(tmp_path):
    path = tmp_path / 'brand.pptx'
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    logo = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.OVAL, Inches(11), Inches(0.4), Inches(0.8), Inches(0.8))
    logo.name = 'Company Logo'
    logo.fill.solid()
    logo.fill.fore_color.rgb = RGBColor.from_string('0B2545')
    band = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(0.5), Inches(0.4), Inches(0.3), Inches(2))
    band.fill.solid()
    band.fill.fore_color.rgb = RGBColor.from_string('0B2545')
    _paint_theme(presentation, '0B2545')
    presentation.save(path)
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    store.init('deck', path)
    accept_all(store)
    source = store.status()['manifest']['slides'][0]['slide_id']
    apply(store, 'slide.clone', slide_id='copy', source_slide_id=source)
    apply(store, 'presentation.recolor', palette={'accent': '707780', 'background': 'FFFFFF', 'text': '20242A', 'muted': '79818A'})
    opened = Presentation(store.path)
    logos = [shape for slide in opened.slides for shape in slide.shapes if shape.name.startswith('da:shape-') and '0B2545' in solid_values(shape)]
    assert len(logos) == 2
    bands = [shape for slide in opened.slides for shape in slide.shapes if '707780' in solid_values(shape)]
    assert bands


def test_internal_slide_link_is_not_copied_as_a_new_slide(tmp_path):
    path = tmp_path / 'linked.pptx'
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    first._element.cSld.set('name', 'da-slide:origin')
    second._element.cSld.set('name', 'da-slide:target')
    button = first.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, Inches(1), Inches(1), Inches(2), Inches(1))
    rel_id = first.part.relate_to(second.part, RT.SLIDE)
    props = button._element.find('.//' + qn('p:cNvPr'))
    link = etree.SubElement(props, qn('a:hlinkClick'))
    link.set(qn('r:id'), rel_id)
    link.set('action', 'ppaction://hlinksldjump')
    presentation.save(path)
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    store.init('deck', path)
    accept_all(store)
    apply(store, 'slide.clone', slide_id='origin-2', source_slide_id='origin')
    opened = Presentation(store.path)
    assert len(opened.slides) == 3
    clone = next(slide for slide in opened.slides if slide._element.cSld.get('name') == 'da-slide:origin-2')
    target_names = []
    for rel in clone.part.rels.values():
        if rel.reltype == RT.SLIDE and not rel.is_external:
            target_names.append(str(rel.target_part.partname))
    original_target = str(second.part.partname)
    assert target_names
    assert all(name == original_target or name.endswith(original_target.split('/')[-1]) for name in target_names)
    office.validator.validate_package(store.path.read_bytes())


def test_template_reference_delivers_only_generated_slides(tmp_path):
    sample.build(tmp_path)
    store = office.PresentationStore(tmp_path / 'deck.pptx')
    store.init('deck', tmp_path / 'template.pptx', use='template_reference')
    prototypes = set(store.status()['manifest']['slides'][index]['slide_id'] for index in range(4))
    sources = [slide['slide_id'] for slide in store.status()['manifest']['slides']]
    for index in range(10):
        accept_all(store)
        apply(store, 'slide.clone', slide_id=f'page-{index + 1}', source_slide_id=sources[index % 4])
    apply(store, 'presentation.discard_prototypes')
    final_ids = [slide['slide_id'] for slide in store.status()['manifest']['slides']]
    assert final_ids == [f'page-{index + 1}' for index in range(10)]
    assert prototypes.isdisjoint(final_ids)
    office.validator.validate_package(store.path.read_bytes())


def _png():
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (32, 32), '#123456').save(stream, format='PNG')
    stream.seek(0)
    return stream.getvalue()


def _bordered_shape(path):
    # Reuse the sample data slide, which already has a picture named da:photo and a band.
    sample = load('sample_template')
    directory = path.parent / 'sample'
    sample.build(directory)
    return directory / 'template.pptx'


def _paint_theme(presentation, accent):
    theme = presentation.slide_masters[0].part.part_related_by(RT.THEME)
    root = etree.fromstring(theme.blob)
    namespace = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    scheme = root.find('.//' + namespace + 'clrScheme')
    for child in scheme:
        if etree.QName(child).localname == 'accent1':
            for node in list(child):
                child.remove(node)
            color = etree.SubElement(child, namespace + 'srgbClr')
            color.set('val', accent)
    theme.blob = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
