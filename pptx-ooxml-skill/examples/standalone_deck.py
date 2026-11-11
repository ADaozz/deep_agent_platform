"""Two-slide editable example; run from a task directory, not the skill directory."""
import argparse
import json
from pathlib import Path
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
import deck_io
import native_charts
import native_math
import style_library

STYLE_ID = 'consulting/red-black-growth'
DESIGN = {
    'font_family': 'Arial',
    'theme': {'background': 'FFFFFF', 'text': '111111', 'accent': 'E53935',
              'muted': '6B7280', 'border': 'D1D5DB'},
    'type_scale': {'title': 34, 'body': 20, 'caption': 12},
    'margins': {'unit': 'fraction', 'left': .05, 'right': .05, 'top': .05, 'bottom': .05},
}


def text(slide, identity, value, x, y, w, h, size=20, color='111111'):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    shape.name = 'da:' + identity
    shape.text_frame.word_wrap = True
    run = shape.text_frame.paragraphs[0].add_run()
    run.text = value
    run.font.name = DESIGN['font_family']
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    return shape


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='files/demo.pptx')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path('tmp').resolve()
    temporary.mkdir(exist_ok=True)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    style_library.apply_reference(prs, STYLE_ID, design=DESIGN)

    def slide(identity, title):
        page = prs.slides.add_slide(prs.slide_layouts[6])
        page._element.cSld.set('name', 'da-slide:' + identity)
        page.background.fill.solid()
        page.background.fill.fore_color.rgb = RGBColor.from_string(DESIGN['theme']['background'])
        text(page, 'title', title, .8, .6, 11.7, .8, size=34)
        return page

    page = slide('results', 'Editable chart: illustrative revenue')
    data = {'type': 'column_clustered', 'categories': ['Q1', 'Q2', 'Q3'],
            'series': [{'name': 'Revenue', 'values': [12, 18, 25]}]}
    chart = native_charts.create(page, data, (Inches(.9), Inches(2), Inches(11.5), Inches(4.4)))
    chart.name = 'da:revenue'
    native_charts.style_xml(chart, {'kind': 'chart', 'chart': data}, DESIGN)
    native_charts.verify_workbook(chart, data)
    text(page, 'source', 'Synthetic sample data; replace with verified task data.', .8, 6.7, 11.7, .3, size=12, color='6B7280')

    page = slide('formula', 'Editable equation: Office Math')
    equation = native_math.create(page, r'\int_0^1 x^2\,dx=\frac{1}{3}',
        (Inches(.9), Inches(2.4), Inches(11.5), Inches(2)), font_size=28, color='111111', align='center')
    equation.name = 'da:integral'
    text(page, 'note', 'Verify equation editing in Microsoft PowerPoint before claiming it passed.', .8, 6.7, 11.7, .3, size=12, color='6B7280')

    plan = {'pptx': str(output), 'style_id': STYLE_ID, **DESIGN,
            'slides': [{'page': 1, 'statistical_charts': 1}, {'page': 2, 'statistical_charts': 0}]}
    if native_charts.audit(prs, plan)['status'] != 'passed':
        raise ValueError('Native chart audit failed')
    deck_io.save_deck(prs, output)
    (temporary / 'deck-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    (temporary / 'deck-check.json').write_text(json.dumps({'content_notes': {
        'results': 'Verified synthetic Q1/Q2/Q3 values 12/18/25 and the sample-data label.',
        'formula': 'Verified integral bounds 0/1, x squared, differential dx and result 1/3.'}}, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    main()
