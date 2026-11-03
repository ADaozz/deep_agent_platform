"""Keep the documented two-slide example executable without the platform."""
import json
import runpy
import sys
from pathlib import Path

from pptx import Presentation

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'standalone_deck.py'


def test_standalone_example_writes_native_chart_and_equation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', [str(EXAMPLE), '--output', 'files/demo.pptx'])
    runpy.run_path(str(EXAMPLE), run_name='__main__')
    output = tmp_path / 'files' / 'demo.pptx'
    assert output.is_file()
    plan = json.loads((tmp_path / 'tmp' / 'deck-plan.json').read_text())
    notes = json.loads((tmp_path / 'tmp' / 'deck-check.json').read_text())['content_notes']
    assert plan['style_id'] == 'consulting/red-black-growth'
    assert notes['results'] and notes['formula']
    prs = Presentation(output)
    assert any(shape.has_chart for shape in prs.slides[0].shapes)
    assert 'm:oMath' in prs.slides[1].shapes._spTree.xml
