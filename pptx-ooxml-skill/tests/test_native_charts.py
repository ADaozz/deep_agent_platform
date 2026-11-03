"""Exercise native chart parts/workbooks through save, reload, update and restore."""
import importlib.util
import io
from pathlib import Path
from zipfile import ZipFile

import pytest
from pptx import Presentation
from pptx.util import Inches

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'office_pptx.py'
spec = importlib.util.spec_from_file_location('chart_test_engine', SCRIPT)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
charts = engine.native_charts


def payload(kind):
    p = dict(type=kind, categories=['A', 'B', 'C', 'D'],
             series=[dict(name='Revenue', values=[4, 6, 3, 7])])
    if kind in charts.XY | charts.BUBBLE:
        points = [[1, 3, 2], [2, 4, 3], [4, 6, 4]] if kind in charts.BUBBLE else [[1, 3], [2, 4], [4, 6]]
        p = dict(type=kind, series=[dict(name='Samples', points=points)])
    if kind in {'treemap', 'sunburst'}:
        p['categories'] = [['East', 'A'], ['East', 'B'], ['West', 'A'], ['West', 'B']]
    if 'surface' in kind:
        p['series'].append(dict(name='Profit', values=[3, 4, 2, 6]))
    if kind.startswith('stock_'):
        p['series'] = [dict(name='High', values=[8]*4), dict(name='Low', values=[2]*4), dict(name='Close', values=[5]*4)]
        if 'ohlc' in kind: p['series'].insert(0, dict(name='Open', values=[4]*4))
        if kind in {'stock_vhlc', 'stock_vohlc'}: p['series'].insert(0, dict(name='Volume', values=[100, 200, 300, 400]))
    if kind == 'combination':
        p['series'].append(dict(name='Growth', values=[.1, .2, .3, .4], type='line', secondary_axis=True))
    return p


@pytest.mark.parametrize('kind', sorted(set(charts.CHART_TYPES) | set(charts.MODERN) | {'combination'}))
def test_all_native_types_survive_save_reload_and_data_update(kind):
    p = payload(kind)
    prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = charts.create(slide, p, (Inches(1), Inches(1), Inches(8), Inches(5)))
    state = dict(theme=dict(background='FFFFFF', text='17324D', accent='246BCE', muted='536B82'), font_family='Arial')
    meta = dict(kind='chart', chart=p)
    charts.style_xml(shape, meta, state)
    before = charts.chart_part(shape).blob
    charts.style_xml(shape, meta, state)
    assert charts.chart_part(shape).blob == before, 'Restyling must not accumulate points or overrides'
    raw = io.BytesIO(); prs.save(raw)
    engine.validator.validate_package(raw.getvalue())
    with ZipFile(io.BytesIO(raw.getvalue())) as z:
        assert not any(name.startswith('ppt/media/') for name in z.namelist()), 'Native charts must not be rasterized'
        assert any(name.endswith('.xlsx') for name in z.namelist())
        chart_xml = z.read(next(name for name in z.namelist() if name.startswith('ppt/charts/') and name.endswith('.xml')))
        if kind in charts.MODERN: assert charts.MODERN[kind].encode() in chart_xml
        elif 'surface' in kind: assert b'surface' in chart_xml
        elif kind.startswith('stock_'): assert b'stockChart' in chart_xml
        elif kind == 'combination': assert b'barChart' in chart_xml and b'lineChart' in chart_xml
    reopened = Presentation(io.BytesIO(raw.getvalue())).slides[0].shapes[0]
    assert charts.is_chart(reopened)
    charts.verify_workbook(reopened, p)
    if kind in charts.XY | charts.BUBBLE: p['series'][0]['points'][0][1] += 1
    else: p['series'][-1]['values'][-1] += .1
    charts.replace_data(reopened, p)
    charts.verify_workbook(reopened, p)


def op(store, operation, **kw):
    state = store.status()
    return store.apply_one(dict(presentation_id='deck', operation_id='op-' + str(state['revision'] + 1),
                                base_revision=state['revision'], slide_id='s1', operation=operation, **kw))


@pytest.mark.parametrize('kind', ['doughnut', 'scatter', 'bubble', 'stock_vohlc', 'surface', 'combination', *charts.MODERN])
def test_public_operations_preserve_native_data_and_restore(tmp_path, kind):
    store = engine.PresentationStore(tmp_path / 'deck.pptx'); store.init('deck')
    op(store, 'slide.add', layout='title_body')
    p = payload(kind)
    event = op(store, 'element.add_chart', element=dict(id='data', geometry=dict(x=.1, y=.25, w=.8, h=.6), chart=p))
    baseline = event['revision']
    assert event['manifest']['slides'][0]['complex']
    if kind in charts.XY | charts.BUBBLE: p['series'][0]['points'][0][1] += 1
    else: p['series'][-1]['values'][-1] += .1
    op(store, 'chart.replace_data', element_id='data', chart=p)
    shape = Presentation(store.path).slides[0].shapes[0]
    assert engine.renderer.metadata(shape)['chart'] == p
    charts.verify_workbook(shape, p)
    state = store.status()
    store.restore(baseline, state['revision'], 'restore-chart')
    shape = Presentation(store.path).slides[0].shapes[0]
    charts.verify_workbook(shape, payload(kind))


def test_type_conversion_requires_real_coordinates_and_preserves_layout(tmp_path):
    store = engine.PresentationStore(tmp_path / 'deck.pptx'); store.init('deck')
    op(store, 'slide.add', layout='title_body')
    op(store, 'element.add_chart', element=dict(id='data', geometry=dict(x=.1, y=.25, w=.8, h=.6), chart=payload('bar')))
    before = store.path.read_bytes(); rev = store.status()['revision']
    with pytest.raises(ValueError, match='XY points'):
        op(store, 'chart.change_type', element=dict(id='data', type='scatter'))
    assert store.status()['revision'] == rev and store.path.read_bytes() == before
    op(store, 'chart.change_type', element=dict(id='data', type='waterfall'))
    shape = Presentation(store.path).slides[0].shapes[0]
    assert charts.infer_type(shape) == 'waterfall'
    assert shape.name == 'da:data'
    assert shape.left == round(.1 * Presentation(store.path).slide_width)
    op(store, 'chart.change_type', element=dict(id='data', type='doughnut'))
    assert charts.infer_type(Presentation(store.path).slides[0].shapes[0]) == 'doughnut'


@pytest.mark.parametrize('bad', [
    dict(type='bubble', series=[dict(name='x', points=[[1, 2, -1]])]),
    dict(type='scatter', series=[dict(name='x', points=[[float('nan'), 2]])]),
    dict(type='pie', categories=['A'], series=[dict(name='x', values=[-1])]),
    dict(type='sunburst', categories=['flat'], series=[dict(name='x', values=[1])]),
    dict(type='stock_hlc', categories=['A'], series=[dict(name='H', values=[1]), dict(name='L', values=[2]), dict(name='C', values=[3])]),
    dict(type='waterfall', categories=['A'], series=[dict(name='x', values=[1])], totals=[9]),
])
def test_invalid_data_is_rejected_before_building_chart(bad):
    with pytest.raises(ValueError): charts.validate(bad)


def test_every_catalog_type_has_several_use_cases():
    entries = charts.catalog()
    assert len(entries) >= 82
    assert all(len(e['scenarios']) >= 3 for e in entries)


def test_workbook_tampering_is_detected():
    p = payload('waterfall'); prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = charts.create(slide, p, (1, 1, 100, 100))
    part = charts.chart_part(shape)
    part._element.find('.//{' + charts.CX + '}numDim//{' + charts.CX + '}pt').text = '999'
    with pytest.raises(ValueError, match='cache'): charts.verify_workbook(shape, p)
