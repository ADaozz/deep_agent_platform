"""The labelled evaluation harness measures misses, false positives and localisation, not self-scores."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

pytestmark = pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice Impress required')
spec = importlib.util.spec_from_file_location('ppt_vision_eval', Path(__file__).with_name('vision_eval.py'))
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
vision = harness.vision


def checks(categories):
    return {key: dict(verdict='ok', evidence=f'{label}：按截图逐项核对') for key, label in categories.items()}


class Oracle:
    """Answers like a perfect annotator (or, with ``lazy``, like a model that always says it looks fine)."""

    model_id = 'oracle'

    def __init__(self, lazy=False):
        self.lazy = lazy
        self.cases = {case['id']: case for case in harness.CASES}

    def complete(self, system, text, images):
        assert images and images[0][1][:8] == b'\x89PNG\r\n\x1a\n'
        request = json.loads(text)
        if system == vision.DECK_SYSTEM_PROMPT:
            pages = [s['page'] for s in request['slides'] if self.cases[s['slide_id']].get('deck')]
            issues = [] if self.lazy else [dict(id='deck-001', category='repetition', severity='major', slides=pages, observation='三页卡片结构完全相同',
                reason='节奏单调', suggestion='其中一页改为数据版式', confidence='high')]
            return json.dumps(dict(status='fail' if issues else 'pass', summary='整套审查', checks=checks(vision.DECK_CATEGORIES), issues=issues))
        case = self.cases[request['page']['slide_id']]
        if self.lazy or not case.get('blocking'):
            return json.dumps(dict(status='pass', summary='页面清晰', checks=checks(vision.DIMENSIONS), issues=[]))
        issue = dict(id='visual-001', category=case['blocking'][0], severity='major', element_ids=case['elements'], region=[.1, .1, .3, .2],
            observation='可见缺陷', reason='影响理解', suggestion='局部修改', confidence='high')
        return json.dumps(dict(status='fail', summary='发现缺陷', checks=checks(vision.DIMENSIONS), issues=[issue]))


def test_labelled_corpus_metrics(tmp_path):
    harness.build(tmp_path)
    labels = json.loads((tmp_path / 'labels.json').read_text())
    assert len(labels['cases']) == 12
    perfect = harness.evaluate(tmp_path, Oracle())
    assert perfect['major_defect_miss_rate'] == 0 and perfect['false_positive_rate'] == 0
    assert perfect['localisation_accuracy'] == 1 and perfect['deck_repetition_detected']
    lazy = harness.evaluate(tmp_path, Oracle(lazy=True))
    assert lazy['major_defect_miss_rate'] == 1 and not lazy['deck_repetition_detected']
