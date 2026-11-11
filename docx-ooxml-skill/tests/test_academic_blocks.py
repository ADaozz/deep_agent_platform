"""Atomic academic blocks: equations, charts, captions, cross-references and the TOC."""
import importlib.util
import io
from pathlib import Path
import re
import zipfile

import pytest
from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('atomic_academic', SCRIPTS / 'atomic_docx.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
o = a.o


def build(*operations):
    parts, results = o.default_package(), []
    for operation in operations:
        parts, _changed, extra = a.mutate(parts, operation, Path('.'))
        results.append(extra)
    return parts, results


def xml(parts):
    return parts[o.MAIN].decode()


def ref_texts(parts):
    return dict(re.findall(r'w:instr=" REF (\S+) \\h "><w:r><w:t[^>]*>([^<]*)</w:t>', xml(parts)))


def seq_numbers(parts, label):
    return re.findall(r'w:instr=" SEQ %s \\\* ARABIC "><w:r><w:t[^>]*>(\d+)<' % label, xml(parts))


def test_captions_number_per_label_and_renumber_on_earlier_insert():
    parts, _ = build(
        {'type': 'append_paragraph', 'block_id': 'intro', 'text': '引言'},
        {'type': 'insert_caption', 'block_id': 'c1', 'label': '图', 'text': '第一张', 'ref_id': 'fig-a'},
        {'type': 'insert_caption', 'block_id': 't1', 'label': '表', 'text': '第一表', 'ref_id': 'tab-a'},
        {'type': 'append_paragraph', 'block_id': 'p', 'text': '见{{ref:fig-a}}与{{ref:tab-a}}'},
    )
    assert ref_texts(parts) == {'_Ref_fig_a': '图 1', '_Ref_tab_a': '表 1'}
    parts, _changed, _extra = a.mutate(parts, {'type': 'insert_caption', 'block_id': 'c0', 'label': '图', 'text': '插到前面', 'after_block_id': 'intro'}, Path('.'))
    assert seq_numbers(parts, '图') == ['1', '2'] and seq_numbers(parts, '表') == ['1']
    assert ref_texts(parts) == {'_Ref_fig_a': '图 2', '_Ref_tab_a': '表 1'}
    with pytest.raises(ValueError, match='Duplicate cross-reference'):
        a.mutate(parts, {'type': 'insert_caption', 'block_id': 'c9', 'label': '图', 'text': 'x', 'ref_id': 'fig-a'}, Path('.'))


def test_forward_reference_is_reported_then_resolved():
    parts, results = build({'type': 'append_paragraph', 'block_id': 'p', 'text': '见{{ref:eq-x}}'})
    assert results[0] == {'unresolved_references': ['_Ref_eq_x']}
    assert ref_texts(parts) == {'_Ref_eq_x': '??'}
    parts, _changed, extra = a.mutate(parts, {'type': 'insert_equation', 'block_id': 'eq', 'latex': r'E=mc^2', 'numbered': True, 'ref_id': 'eq-x'}, Path('.'))
    assert extra is None and ref_texts(parts) == {'_Ref_eq_x': '(1)'}


def test_equation_is_native_omml_with_standard_prefix():
    parts, _ = build(
        {'type': 'insert_equation', 'block_id': 'eq1', 'latex': r'\mathrm{softmax}\left(\frac{QK^\top}{\sqrt{d_k}}\right)V'},
        {'type': 'insert_equation', 'block_id': 'eq2', 'latex': r'\sum_{i=1}^{n} x_i', 'numbered': True},
    )
    text = xml(parts)
    assert len(re.findall(r'<m:oMath[ >]', text)) == 2 and not re.search(r'<ns\d+:', text)
    assert '>softmax<' in text  # upright function name is one run, not one run per letter
    with pytest.raises(ValueError):
        build({'type': 'insert_equation', 'block_id': 'bad', 'latex': r'\unknowncommand{x}'})


def test_chart_has_data_inside_points_colors_and_workbook():
    parts, _ = build({'type': 'insert_chart', 'block_id': 'ch', 'chart': {
        'type': 'column', 'title': 'HBM', 'categories': ['A', 'B'], 'series': [{'name': 'GB', 'values': [3.5, 1.2]}]}})
    chart = parts['word/charts/chart1.xml'].decode()
    assert '<c:pt idx="0"><c:v>3.5</c:v></c:pt>' in chart and '<a:srgbClr val="2F5597"/>' in chart
    assert 'word/embeddings/chart-data-1.xlsx' in parts
    rels = parts[o.DOC_RELS].decode()
    assert 'Target="charts/chart1.xml"' in rels
    Document(io.BytesIO(a.packed(parts)))
    with pytest.raises(ValueError):
        build({'type': 'insert_chart', 'block_id': 'ch', 'chart': {'type': 'radar3d', 'series': [{'name': 'x', 'values': [1]}]}})


def test_toc_tracks_headings_and_requests_field_update():
    parts, _ = build(
        {'type': 'insert_toc', 'block_id': 'toc', 'levels': 2},
        {'type': 'append_paragraph', 'block_id': 'h1', 'style': 'Heading1', 'text': '1 引言'},
        {'type': 'append_paragraph', 'block_id': 'h2', 'style': 'Heading2', 'text': '1.1 背景'},
        {'type': 'append_paragraph', 'block_id': 'h3', 'style': 'Heading3', 'text': '1.1.1 细节'},
    )
    text = xml(parts)
    assert 'TOC \\o "1-2"' in text
    assert re.findall(r'w:anchor="(_Toc_\d+)"', text) == ['_Toc_0001', '_Toc_0002']  # level 3 excluded
    assert '<w:updateFields w:val="true"/>' in parts['word/settings.xml'].decode()
    assert 'word/settings.xml' in [n for n in zipfile.ZipFile(io.BytesIO(a.packed(parts))).namelist()]


def test_long_table_repeats_header_and_may_break():
    rows = [['h1', 'h2']] + [[str(i), str(i * 2)] for i in range(40)]
    parts, _ = build({'type': 'append_table', 'block_id': 'tbl', 'rows': rows})
    table = Document(io.BytesIO(a.packed(parts))).tables[0]
    assert len(table.rows) == 41
    assert xml(parts).count('<w:tblHeader/>') == 1 and '<w:keepNext/>' not in xml(parts)
    with pytest.raises(ValueError):
        build({'type': 'append_table', 'block_id': 'tbl', 'rows': [['x']] * 61})
