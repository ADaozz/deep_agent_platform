"""PR01: bounded native styling, presets and relative adjustments keep objects native."""
import importlib.util
import io
from pathlib import Path
import shutil

import pytest
from lxml import etree

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'office_pptx.py'
spec = importlib.util.spec_from_file_location('ppt_style_engine', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def op(store, kind, sid='s1', **kwargs):
    state = store.status()
    return store.apply_one(dict(presentation_id='deck', operation_id=f'op-{state["revision"] + 1}', base_revision=state['revision'],
        slide_id=sid, operation=kind, **kwargs))


def shapes(store):
    prs = m.Presentation(io.BytesIO(store.path.read_bytes()))
    return prs, {m.renderer.element_identity(shape): shape for shape in prs.slides[0].shapes}


def xml(shape):
    return etree.tostring(shape._element)


@pytest.fixture
def deck(tmp_path):
    store = m.PresentationStore(tmp_path / 'deck.pptx')
    store.init('deck')
    op(store, 'slide.add', layout='title_body')
    op(store, 'element.add_text', element=dict(id='title', anchor='title', text='第 4 页：原生对象微调'))
    op(store, 'element.add_component', element=dict(id='icon', component='icons.check', geometry=dict(x=.06, y=.3, w=.045, h=.08)))
    op(store, 'element.add_chart', element=dict(id='chart', geometry=dict(x=.3, y=.28, w=.36, h=.42),
        chart=dict(type='bar', categories=['Q1', 'Q2', 'Q3'], series=[dict(name='收入', values=[12, 18, 25])], unit='亿元')))
    op(store, 'element.add_table', element=dict(id='table', geometry=dict(x=.7, y=.28, w=.24, h=.3), rows=[['阶段', '状态'], ['试点', '完成'], ['推广', '进行']]))
    op(store, 'element.add_text', element=dict(id='note', geometry=dict(x=.06, y=.78, w=.6, h=.08), text='其他对象不受影响', style='caption'))
    return store


def test_natural_language_adjustments_keep_native_objects_and_leave_others_untouched(deck):
    prs, before = shapes(deck)
    untouched = {key: xml(before[key]) for key in ('title', 'note')}
    icon = before['icon']
    center = (icon.left + icon.width / 2, icon.top + icon.height / 2)
    # "第 4 页图标缩小 15%"
    op(deck, 'element.adjust', element=dict(id='icon', scale=.85))
    # "往右移动 0.2 英寸"
    op(deck, 'element.adjust', element=dict(id='icon', dx_in=.2))
    # "把图表换成折线图"
    op(deck, 'chart.change_type', element=dict(id='chart', type='line'))
    # "表头改为蓝底白字"
    op(deck, 'element.set_style', element=dict(id='table', style=dict(header_fill='246BCE', header_text='FFFFFF')))
    prs, after = shapes(deck)
    resized = after['icon']
    assert abs(resized.width / icon.width - .85) < .002 and abs(resized.height / icon.height - .85) < .002
    assert abs(resized.width / resized.height - icon.width / icon.height) < .01
    assert abs((resized.left + resized.width / 2) - (center[0] + m.Inches(.2))) < 2000
    assert abs((resized.top + resized.height / 2) - center[1]) < 2000
    chart = after['chart']
    assert chart.has_chart and m.renderer.metadata(chart)['chart']['type'] == 'line'
    assert tuple(chart.chart.series[0].values) == (12.0, 18.0, 25.0)
    assert (chart.left, chart.top, chart.width, chart.height) == (before['chart'].left, before['chart'].top, before['chart'].width, before['chart'].height)
    m.native_style.verify_workbook(chart, m.renderer.metadata(chart)['chart'])
    header = after['table'].table.cell(0, 0)
    assert str(header.fill.fore_color.rgb) == '246BCE'
    assert str(header.text_frame.paragraphs[0].runs[0].font.color.rgb) == 'FFFFFF'
    assert str(after['table'].table.cell(1, 0).fill.fore_color.rgb) != '246BCE'
    for key, value in untouched.items():
        assert xml(after[key]) == value, key
    assert len(list(prs.slides[0].shapes)) == len(before)
    m.validator.validate_package(deck.path.read_bytes())


def test_style_overrides_survive_profile_change_and_data_updates(deck):
    op(deck, 'element.set_style', element=dict(id='table', style=dict(header_fill='246BCE', header_text='FFFFFF')))
    op(deck, 'presentation.set_profile', profile='consulting', sid=None)
    op(deck, 'element.update', element=dict(id='table', rows=[['阶段', '状态'], ['试点', '已完成'], ['推广', '进行中']]))
    _, after = shapes(deck)
    assert str(after['table'].table.cell(0, 0).fill.fore_color.rgb) == '246BCE'
    assert after['table'].table.cell(1, 1).text == '已完成'
    op(deck, 'element.set_style', element=dict(id='table', style=dict(header_fill=None, header_text=None)))
    _, after = shapes(deck)
    assert str(after['table'].table.cell(0, 0).fill.fore_color.rgb) != '246BCE'


def test_presets_and_rejected_effects(deck):
    op(deck, 'element.apply_preset', element=dict(id='chart', preset='chart.emphasis'))
    _, after = shapes(deck)
    meta = m.renderer.metadata(after['chart'])
    assert meta['preset'] == 'chart.emphasis' and meta['chart_style']['data_labels'] and meta['chart_style']['emphasis'] == 2
    with pytest.raises(ValueError, match='Decorative effect'):
        op(deck, 'element.set_style', element=dict(id='table', style=dict(glow='accent')))
    with pytest.raises(ValueError, match='applies to chart'):
        op(deck, 'element.apply_preset', element=dict(id='table', preset='chart.clean'))
    with pytest.raises(ValueError, match='Unsupported text style'):
        op(deck, 'element.set_style', element=dict(id='note', style=dict(letter_case='upper')))
    with pytest.raises(ValueError, match='Decorative effect'):
        op(deck, 'element.set_style', element=dict(id='note', style=dict(gradient_fill='x')))
    with pytest.raises(ValueError, match='profile minimum'):
        op(deck, 'element.set_style', element=dict(id='note', style=dict(font_size=6)))
    with pytest.raises(ValueError, match='do not support|does not support'):
        op(deck, 'element.set_style', element=dict(id='icon', style=dict(fill='accent')))


def test_adjust_bounds_and_chart_conversion_rules(deck):
    with pytest.raises(ValueError, match='leave the slide canvas'):
        op(deck, 'element.adjust', element=dict(id='table', dx=.2))
    with pytest.raises(ValueError, match='aspect ratio'):
        op(deck, 'element.adjust', element=dict(id='icon', scale_x=.8, scale_y=1))
    with pytest.raises(ValueError, match='not rotated'):
        op(deck, 'element.adjust', element=dict(id='chart', rotate=15))
    with pytest.raises(ValueError, match='already a bar'):
        op(deck, 'chart.change_type', element=dict(id='chart', type='bar'))
    op(deck, 'chart.change_type', element=dict(id='chart', type='pie'))
    _, after = shapes(deck)
    assert m.renderer.metadata(after['chart'])['chart']['type'] == 'pie'
    revision = deck.status()['revision']
    op(deck, 'element.add_chart', element=dict(id='multi', geometry=dict(x=.3, y=.72, w=.3, h=.2),
        chart=dict(type='bar', categories=['A', 'B'], series=[dict(name='x', values=[1, 2]), dict(name='y', values=[2, 3])])))
    with pytest.raises(ValueError, match='exactly one series'):
        op(deck, 'chart.change_type', element=dict(id='multi', type='pie'))
    assert deck.status()['revision'] == revision + 1
    op(deck, 'element.adjust', element=dict(id='note', rotate=5))
    _, after = shapes(deck)
    assert abs(after['note'].rotation - 5) < .01


def test_chart_change_is_one_transaction_and_restorable(deck):
    revision = deck.status()['revision']
    op(deck, 'chart.change_type', element=dict(id='chart', type='line'))
    deck.restore(revision, revision + 1, 'restore-bar')
    _, after = shapes(deck)
    assert m.renderer.metadata(after['chart'])['chart']['type'] == 'bar'
    assert after['chart'].chart.chart_type == m.native_style.CHART_TYPES['bar']


@pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice Impress required')
def test_adjusted_slide_renders_with_impress(deck):
    op(deck, 'element.adjust', element=dict(id='icon', scale=.85))
    op(deck, 'chart.change_type', element=dict(id='chart', type='line'))
    op(deck, 'element.set_style', element=dict(id='table', style=dict(header_fill='246BCE', header_text='FFFFFF')))
    result = deck.check_slide('s1', '标题、图标、折线图、表头均已核对')
    assert result['review']['rendered'] and result['review']['automatic_passed'], result['review']['layout_issues']
