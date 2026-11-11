"""save_deck writes the deliverable in place and refuses tmp/ working copies."""
import importlib.util
from pathlib import Path

import pytest
from pptx import Presentation

SKILL = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pptx_deck_io', SKILL / 'scripts' / 'deck_io.py')
deck_io = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deck_io)


def deck():
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])
    return prs


def test_save_deck_writes_complete_file_without_leftovers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = deck_io.save_deck(deck(), 'files/deck.pptx')
    assert len(Presentation(target).slides) == 1
    assert sorted(p.name for p in (tmp_path / 'files').iterdir()) == ['deck.pptx']
    deck_io.save_deck(deck(), target)
    assert sorted(p.name for p in (tmp_path / 'files').iterdir()) == ['deck.pptx']


def test_save_deck_rejects_scratch_copies_and_other_formats(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match='tmp/'):
        deck_io.save_deck(deck(), 'tmp/deck.pptx')
    with pytest.raises(ValueError, match='tmp/'):
        deck_io.save_deck(deck(), tmp_path / 'tmp' / 'nested' / 'deck.pptx')
    with pytest.raises(ValueError, match='.pptx'):
        deck_io.save_deck(deck(), 'files/deck.pdf')
    assert not (tmp_path / 'tmp').exists()


def test_guidance_never_saves_the_deck_under_tmp():
    docs = [SKILL / 'SKILL.md', *sorted((SKILL / 'references').glob('*.md')), *sorted((SKILL / 'examples').glob('*.py'))]
    for path in docs:
        text = path.read_text(encoding='utf-8')
        assert 'tmp/deck.pptx' not in text, path
        assert 'os.replace' not in text, path
