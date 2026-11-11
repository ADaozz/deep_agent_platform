"""Rich atomic blocks: runs, inline math, complex tables, images and headers/footers."""
import importlib.util
import io
from pathlib import Path
import re

import pytest
from docx import Document
from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('atomic_rich', SCRIPTS / 'atomic_docx.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
o = a.o


def build(*operations, assets=Path('.')):
    parts = o.default_package()
    for operation in operations:
        parts, _changed, _extra = a.mutate(parts, operation, assets)
    return parts


def xml(parts, name=None):
    return parts[name or o.MAIN].decode()


def test_runs_formatting_and_inline_math():
    parts = build({'type': 'append_paragraph', 'block_id': 'p', 'runs': [
        {'text': 'Bold', 'bold': True, 'color': 'c00000', 'size': 12}, {'text': 'x', 'italic': True},
        {'text': '2', 'vertical': 'superscript'}, {'text': 'wide', 'spacing': 2, 'highlight': 'yellow', 'underline': True},
        {'math': r'\frac{a}{b}'}, {'text': r' and {{math:\sqrt{x_{i}}}} end'}]})
    text = xml(parts)
    assert '<w:b/><w:color w:val="C00000"/><w:sz w:val="24"/>' in text
    assert '<w:vertAlign w:val="superscript"/>' in text and '<w:spacing w:val="40"/>' in text
    assert '<w:highlight w:val="yellow"/><w:u w:val="single"/>' in text
    assert len(re.findall(r'<m:oMath[ >]', text)) == 2 and ' end' in text
    Document(io.BytesIO(a.packed(parts)))
    for bad in ({'text': 'x', 'size': 100}, {'text': 'x', 'color': 'red'}, {'text': 'x', 'weight': 700}):
        with pytest.raises(ValueError):
            build({'type': 'append_paragraph', 'block_id': 'p', 'runs': [bad]})
    with pytest.raises(ValueError, match='Unclosed'):
        build({'type': 'append_paragraph', 'block_id': 'p', 'text': '{{math:\\frac{a}{b}'})


def test_format_text_splits_one_run_and_keeps_existing_properties():
    parts = build({'type': 'append_paragraph', 'block_id': 'p', 'runs': [{'text': '前缀关键词后缀', 'size': 11}]},
                  {'type': 'format_text', 'block_id': 'p', 'match': '关键词', 'bold': True, 'color': '00AA00'})
    paragraph = Document(io.BytesIO(a.packed(parts))).paragraphs[0]
    assert [run.text for run in paragraph.runs] == ['前缀', '关键词', '后缀']
    assert paragraph.runs[1].bold and not paragraph.runs[0].bold
    assert all(run.font.size.pt == 11 for run in paragraph.runs)
    with pytest.raises(ValueError, match='not inside a single run'):
        a.mutate(parts, {'type': 'format_text', 'block_id': 'p', 'match': '不存在', 'bold': True}, Path('.'))


def test_heading_reference_shows_text_and_page_field():
    parts = build({'type': 'append_paragraph', 'block_id': 'h', 'style': 'Heading1', 'text': '2 方法', 'ref_id': 'sec-m'},
                  {'type': 'append_paragraph', 'block_id': 'p', 'text': '见{{ref:sec-m}}第{{pageref:sec-m}}页'})
    text = xml(parts)
    assert re.search(r'REF _Ref_sec_m \\h "><w:r><w:t[^>]*>2 方法<', text)
    assert 'PAGEREF _Ref_sec_m \\h' in text
    with pytest.raises(ValueError, match='only valid in headers'):
        build({'type': 'append_paragraph', 'block_id': 'p', 'text': '{{page}}'})


def test_complex_table_spans_fills_and_cell_edit():
    operation = {'type': 'insert_table', 'block_id': 't', 'header_rows': 2,
        'columns': [{'width_twips': 2000}, {'width_twips': 2000}, {'width_twips': 2000}],
        'rows': [[{'text': 'A', 'rowspan': 2}, {'text': 'B', 'colspan': 2}], ['b1', 'b2'],
                 ['x', {'text': 'y', 'fill': 'E2EFDA'}, {'text': '{{math:x^2}}'}]]}
    parts = build(operation)
    text = xml(parts)
    assert text.count('<w:gridSpan w:val="2"/>') == 1 and text.count('<w:vMerge w:val="restart"/>') == 1
    assert text.count('<w:tblHeader/>') == 2 and 'w:fill="E2EFDA"' in text
    table = Document(io.BytesIO(a.packed(parts))).tables[0]
    assert len(table.rows) == 3 and table.cell(0, 0).text == 'A' and table.cell(1, 0).text == 'A'
    parts, _c, _e = a.mutate(parts, {'type': 'set_table_cell', 'block_id': 't', 'row': 2, 'column': 0, 'text': 'z', 'bold': True}, Path('.'))
    assert Document(io.BytesIO(a.packed(parts))).tables[0].cell(2, 0).text == 'z'
    with pytest.raises(ValueError, match='row span'):
        a.mutate(parts, {'type': 'set_table_cell', 'block_id': 't', 'row': 1, 'column': 0, 'text': 'no'}, Path('.'))
    for rows in ([['a', 'b']], [[{'text': 'a', 'colspan': 4}]], [[{'text': 'a', 'rowspan': 3}, 'b', 'c']]):
        with pytest.raises(ValueError):
            build(dict(operation, rows=rows))


def test_floating_image_and_image_row(tmp_path):
    for name in ('a.png', 'b.png'):
        Image.new('RGB', (200, 120), (10, 20, 30)).save(tmp_path / name)
    parts = build({'type': 'insert_image', 'block_id': 'f', 'path': 'a.png', 'width_cm': 3, 'float': 'right'},
                  {'type': 'insert_image_row', 'block_id': 'row', 'images': [{'path': 'a.png', 'caption': '(a) 甲'}, {'path': 'b.png', 'caption': '(b) 乙'}]},
                  assets=tmp_path)
    text = xml(parts)
    assert '<wp:anchor' in text and '<wp:wrapSquare wrapText="bothSides"/>' in text and '<wp:align>right</wp:align>' in text
    ids = re.findall(r'<wp:docPr id="(\d+)"', text)
    assert len(ids) == 3 and len(set(ids)) == 3
    document = Document(io.BytesIO(a.packed(parts)))
    assert [cell.text for cell in document.tables[0].rows[1].cells] == ['(a) 甲', '(b) 乙']


def test_header_footer_with_page_fields_and_update_in_place():
    parts = build({'type': 'set_header_footer', 'part': 'footer', 'text': '第 {{page}} 页 共 {{pages}} 页'},
                  {'type': 'set_header_footer', 'part': 'header', 'text': '报告', 'align': 'right', 'border': True})
    footer = xml(parts, 'word/footer1.xml')
    assert 'w:instr=" PAGE "' in footer and 'w:instr=" NUMPAGES "' in footer
    assert '<w:pBdr><w:bottom' in xml(parts, 'word/header1.xml')
    parts, _c, _e = a.mutate(parts, {'type': 'set_header_footer', 'part': 'header', 'text': '新页眉'}, Path('.'))
    assert '新页眉' in xml(parts, 'word/header1.xml') and not any(n.startswith('word/header2') for n in parts)
    section = Document(io.BytesIO(a.packed(parts))).sections[0]
    assert section.header.paragraphs[0].text == '新页眉'
