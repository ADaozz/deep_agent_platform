"""PR03-PR05: host-side multimodal review is real, schema-checked, revision-safe and never a silent pass."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
pytestmark = pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice Impress required')

spec = importlib.util.spec_from_file_location('ppt_vision_adapter', ROOT / 'pptx-ooxml-skill' / 'platform_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
vision = adapter.vision
engine = adapter.engine
PNG = b'\x89PNG\r\n\x1a\n'


def checks(note='标题在左上方，正文三点左对齐，右侧留白给图表'):
    return {key: dict(verdict='ok', evidence=f'{label}：{note}') for key, label in vision.DIMENSIONS.items()}


def answer(status='pass', issues=(), **extra):
    return json.dumps(dict(status=status, summary='逐项核对截图', checks=extra.pop('checks', checks()), issues=list(issues), **extra), ensure_ascii=False)


def issue(number=1, severity='major', confidence='high', element_ids=('title',), category='legibility', **extra):
    return dict(id=f'visual-{number:03d}', category=category, severity=severity, element_ids=list(element_ids), region=[.05, .05, .5, .12],
        observation='标题文字与背景对比不足', reason='投影时难以辨认', suggestion='将标题改为深色文字', confidence=confidence, **extra)


class FakeVision:
    """Scripted multimodal model; records exactly what the host sent."""

    def __init__(self, *responses, gate=None, delay=0):
        self.responses = list(responses)
        self.calls = []
        self.gate = gate
        self.delay = delay

    def __call__(self, system, text, images, schema=None):
        self.calls.append(dict(system=system, text=text, images=images))
        if self.gate:
            self.gate.wait(30)
        if self.delay:
            time.sleep(self.delay)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return response(system, text) if callable(response) else response


class Harness:
    def __init__(self, tmp_path, model=None, reason='no image-capable model configured', timeout=10):
        self.root = tmp_path / 'files'
        self.root.mkdir()
        self.events = []
        self.model = model
        host = dict(private_root=tmp_path / 'private', vision=lambda: ('fake/vision', model) if model else (None, reason), vision_timeout=timeout)
        self.service = adapter.create_service(self.root, self.events.append, temporary_root=tmp_path / 'renders', host=host)
        self.path = self.root / 'deck.pptx'
        self.store = self.service.store(self.path)
        self.store.init('deck')

    def op(self, kind, sid='s1', **kwargs):
        state = self.store.status()
        return self.store.apply_one(dict(presentation_id='deck', operation_id=f'op-{state["revision"] + 1}', base_revision=state['revision'],
            slide_id=sid, operation=kind, **kwargs))

    def slide(self, sid='s1', title='AI Agent 平台建设方案', **check):
        self.op('slide.add', sid=sid, layout='title_body')
        self.op('element.add_text', sid=sid, element=dict(id='title', anchor='title', text=title))
        return self.check(sid, **check)

    def check(self, sid='s1', **kwargs):
        result = self.store.check_slide(sid, '逐字核对标题', kwargs.get('intent'), kwargs.get('constraints'))
        self.service.sync()
        return result

    def review(self, sid='s1', timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            review = self.store.review_status(sid)['slides'][0]
            if review['aesthetic_review'].get('status') != 'pending':
                return review
            time.sleep(.2)
        raise AssertionError('vision review did not finish')

    def deck(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            deck = self.store.review_status()['deck_review']
            if deck and deck['status'] != 'pending':
                return deck
            time.sleep(.2)
        raise AssertionError('deck review did not finish')

    def close(self):
        self.service.close()


@pytest.fixture
def harness(tmp_path):
    made = []

    def make(*args, **kwargs):
        made.append(Harness(tmp_path / f'h{len(made)}', *args, **kwargs))
        return made[-1]
    (tmp_path).mkdir(exist_ok=True)
    for index in range(4):
        (tmp_path / f'h{index}').mkdir()
    yield make
    for item in made:
        item.close()


def test_model_receives_the_real_host_render_and_structured_context(harness):
    model = FakeVision(answer())
    h = harness(model)
    h.slide(intent='开篇点题', constraints=['保留左对齐的标题'])
    review = h.review()
    assert review['aesthetic_review']['status'] == 'passed' and review['status'] == 'passed'
    call = model.calls[0]
    mime, png = call['images'][0]
    assert mime == 'image/png' and png[:8] == PNG
    request = json.loads(call['text'])
    assert request['page']['purpose'] == '开篇点题' and request['page']['user_constraints'] == ['保留左对齐的标题']
    assert {e['id'] for e in request['elements']} == {'title'}
    assert str(h.root) not in call['text'] and 'render-' not in call['text'] and '.png' not in call['text']
    record = h.service.reviews.ledger.get(review['aesthetic_review']['review_id'])
    import hashlib
    assert record['render_sha256'] == hashlib.sha256(png).hexdigest()
    assert record['model_id'] == 'fake/vision' and record['prompt_version'] == vision.PROMPT_VERSION
    assert record['presentation_id'] == 'deck' and record['slide_id'] == 's1' and record['applied'] == 1 and record['review_timestamp']
    # The screenshot came from the host-private render, not the sandbox-writable cache.
    assert (h.service.reviews.private_root / 'renders').exists()


def test_confirmed_major_issue_fails_but_allows_next_slide_minor_is_advice(harness):
    model = FakeVision(answer('fail', [issue()]), answer('pass', [issue(2, severity='minor', category='spacing')]))
    h = harness(model)
    h.slide()
    review = h.review()
    assert review['aesthetic_review']['status'] == 'failed' and review['status'] == 'passed'
    assert review['critical_issues'] == [] and review['suggestions'][0]['element_ids'] == ['title']
    assert 'Leave the suggestions to the user' in review['next_action']
    h.slide('s2', title='第二页')
    second = h.review('s2')
    assert second['aesthetic_review']['status'] == 'passed'
    assert second['aesthetic_review']['issues'][0]['severity'] == 'minor'
    event = h.store.finalize(h.store.status()['revision'])
    assert event['aesthetics']['s1']['status'] == 'failed' and event['outstanding_critical'] == {}


@pytest.mark.parametrize('response,expected', [
    (answer(score=98), 'passed'),
    (answer('pass', [issue(confidence='low')]), 'uncertain'),
    (answer('pass', [issue(needs_verification=True, category='diagram_semantics')]), 'uncertain'),
    (answer('pass', [issue()]), 'failed'),
    (answer('fail'), 'uncertain'),
    (answer('uncertain'), 'uncertain'),
])
def test_status_is_derived_from_evidence_not_from_the_models_verdict(harness, response, expected):
    h = harness(FakeVision(response))
    h.slide()
    aesthetic = h.review()['aesthetic_review']
    assert aesthetic['status'] == expected


def test_answers_outside_the_structure_never_pass(harness):
    wrong = json.loads(answer())
    wrong['checks']['decoration']['verdict'] = 'minor'
    for response in (json.dumps(dict(status='pass')), json.dumps(wrong, ensure_ascii=False)):
        model = FakeVision(response)
        h = harness(model)
        h.slide()
        aesthetic = h.review()['aesthetic_review']
        assert aesthetic['status'] == 'unavailable' and aesthetic['error']['code'] == 'schema_invalid'
        assert len(model.calls) == 2


def test_unparseable_first_answer_is_retried_once(harness):
    model = FakeVision('稍等，我先看一下截图', answer())
    h = harness(model)
    h.slide()
    assert h.review()['aesthetic_review']['status'] == 'passed' and len(model.calls) == 2


def test_invented_element_ids_are_dropped_and_reported(harness):
    h = harness(FakeVision(answer('fail', [issue(element_ids=('ghost-01', 'title'))])))
    h.slide()
    item = h.review()['aesthetic_review']['issues'][0]
    assert item['element_ids'] == ['title'] and item['unmapped_element_ids'] == ['ghost-01']


@pytest.mark.parametrize('kind', ['no_model', 'invalid_json', 'timeout'])
def test_unavailable_reviews_never_pass(harness, kind):
    model = {'no_model': None, 'invalid_json': FakeVision('看起来很专业'), 'timeout': FakeVision(answer(), delay=3)}[kind]
    h = harness(model, timeout=.5)
    h.slide()
    aesthetic = h.review()['aesthetic_review']
    assert aesthetic['status'] == 'unavailable'
    assert aesthetic['error']['code'] == {'no_model': 'no_vision_model', 'invalid_json': 'invalid_json', 'timeout': 'timeout'}[kind]
    if kind == 'invalid_json':
        assert len(model.calls) == 2 and '上一次回答无法使用' in model.calls[1]['text']
    status = h.store.review_status('s1')['slides'][0]
    assert 'Do not claim visual acceptance' in status['next_action']
    assert h.store.finalize(h.store.status()['revision'])['aesthetics']['s1']['status'] == 'unavailable'


def test_agent_can_rerequest_only_after_unavailable(harness):
    model = FakeVision('not json', answer())
    h = harness(model)
    h.slide()
    assert h.review()['aesthetic_review']['status'] == 'unavailable'
    h.store.request_review('s1')
    h.service.sync()
    assert h.review()['aesthetic_review']['status'] == 'passed'
    with pytest.raises(ValueError, match='only an unavailable review'):
        h.store.request_review('s1')


def test_late_result_for_an_old_revision_is_discarded(harness):
    gate = threading.Event()
    model = FakeVision(answer(), gate=gate)
    h = harness(model)
    h.slide()
    deadline = time.time() + 30
    while not model.calls and time.time() < deadline:
        time.sleep(.1)
    h.op('element.update', element=dict(id='title', text='修改后的标题'))
    h.check()
    gate.set()
    review = h.review()
    assert review['revision'] == h.store.status()['revision']
    assert review['aesthetic_review']['status'] == 'passed'
    deadline = time.time() + 30
    while len(model.calls) < 2 and time.time() < deadline:
        time.sleep(.1)
    time.sleep(.5)
    with h.service.reviews.ledger.connect() as c:
        rows = c.execute('SELECT revision, applied FROM reviews ORDER BY revision').fetchall()
    assert [tuple(row) for row in rows][0][1] == 0 and [tuple(row) for row in rows][-1][1] == 1


def test_major_issue_is_left_to_the_user_and_can_be_delegated(harness):
    h = harness(FakeVision(answer('fail', [issue(), issue(2, severity='minor', category='spacing')])))
    h.slide()
    review = h.review()
    assert review['critical_issues'] == [] and [i['id'] for i in review['suggestions']] == ['visual-001', 'visual-002']
    assert 'change nothing unless asked' in review['next_action'] and review['status'] == 'passed'


def test_sandbox_cannot_self_accept_and_human_override_is_ledgered(harness):
    h = harness(None)
    h.slide()
    assert h.review()['aesthetic_review']['status'] == 'unavailable'
    revision = h.store.status()['revision']
    h.store.review('s1', revision, True, '我看过截图，没问题')
    assert h.store.review_status('s1')['slides'][0]['effective_aesthetic'] == 'unavailable'
    h.service.human_decision('deck', revision, 'accept', '人工查看正式截图：标题清晰、留白合理', 's1')
    assert h.store.review_status('s1')['slides'][0]['effective_aesthetic'] == 'passed'
    h.store.finalize(revision)
    h.service.sync()
    deadline = time.time() + 60
    while time.time() < deadline and not any(e.get('type') == 'presentation_finalize_verification' for e in h.events):
        time.sleep(.2)
    verification = next(e for e in h.events if e.get('type') == 'presentation_finalize_verification')
    assert verification['finalize_verified'], verification


def test_critical_issue_escalates_to_the_user_after_two_repair_rounds(harness):
    h = harness(FakeVision(answer('fail', [issue(severity='critical')])))
    h.slide()
    attempts = []
    for round_number in range(3):
        review = h.review()
        attempts.append(review['aesthetic_review']['issues'][0]['attempt'])
        if round_number < 2:
            h.op('element.update', element=dict(id='title', text=f'修复第 {round_number + 1} 轮'))
            h.check()
    assert attempts == [1, 2, 3]
    assert review['aesthetic_review']['status'] == 'failed' and review['status'] == 'needs_attention'
    assert review['aesthetic_review']['auto_repair'] is False and review['aesthetic_review']['escalated_issues'] == ['visual-001']
    assert 'tell the user' in review['next_action']
    event = h.store.finalize(h.store.status()['revision'])
    assert event['outstanding_critical'] == {'s1': ['visual-001']}


def test_accept_needs_no_notes_but_reject_needs_change_requests(harness):
    h = harness(None)
    h.slide()
    h.review()
    revision = h.store.status()['revision']
    with pytest.raises(ValueError, match='修改意见'):
        h.service.human_decision('deck', revision, 'reject', '  ', 's1')
    h.service.human_decision('deck', revision, 'reject', '标题改为深色并加大字号', 's1')
    rejected = h.store.review_status('s1')['slides'][0]
    assert rejected['effective_aesthetic'] == 'failed' and rejected['aesthetic_review']['human']['notes'] == '标题改为深色并加大字号'
    h.service.human_decision('deck', revision, 'accept', '', 's1')
    assert h.store.review_status('s1')['slides'][0]['effective_aesthetic'] == 'passed'


def test_human_decision_survives_a_late_model_answer(harness):
    gate = threading.Event()
    model = FakeVision(answer('fail', [issue()]), gate=gate)
    h = harness(model)
    h.slide()
    deadline = time.time() + 30
    while not model.calls and time.time() < deadline:
        time.sleep(.1)
    h.service.human_decision('deck', h.store.status()['revision'], 'accept', '用户确认标题配色为品牌要求', 's1')
    gate.set()
    deadline = time.time() + 30
    while time.time() < deadline and h.store.review_status('s1')['slides'][0]['aesthetic_review'].get('status') == 'pending':
        time.sleep(.2)
    review = h.store.review_status('s1')['slides'][0]
    assert review['aesthetic_review']['status'] == 'failed' and review['aesthetic_review']['human']['decision'] == 'accept'
    assert review['effective_aesthetic'] == 'passed'


def test_forged_acceptance_in_sandbox_state_fails_host_verification(harness):
    h = harness(None)
    h.slide()
    h.review()
    with h.store.locked() as (c, state):
        review = state['reviews']['s1']
        review['aesthetic_review'] = dict(status='passed', review_id='forged')
        review['status'] = 'passed'
        h.store.save_state(c, state)
        c.commit()
    h.store.finalize(h.store.status()['revision'])
    h.service.sync()
    deadline = time.time() + 60
    while time.time() < deadline and not any(e.get('type') == 'presentation_finalize_verification' for e in h.events):
        time.sleep(.2)
    verification = next(e for e in h.events if e.get('type') == 'presentation_finalize_verification')
    assert not verification['finalize_verified'] and 's1' in verification['verification_problems'][0]


def deck_answer(system, text):
    if system != vision.DECK_SYSTEM_PROMPT:
        return answer()
    request = json.loads(text)
    assert request['total'] == 3 and [s['page'] for s in request['slides']] == [1, 2, 3]
    deck_checks = {key: dict(verdict='ok', evidence=f'{label}：第 1-3 页统一深蓝标题') for key, label in vision.DECK_CATEGORIES.items()}
    return json.dumps(dict(status='fail', summary='第 2、3 页构图重复', checks=deck_checks, issues=[
        dict(id='deck-001', category='repetition', severity='major', slides=[2, 3], observation='第 2、3 页都是左上标题加大片留白',
            reason='相邻页节奏单调', suggestion='第 3 页改用数据版式', confidence='high'),
        dict(id='deck-002', category='design_system', severity='major', slides=[1], observation='整体风格一般',
            reason='不够高级', suggestion='重做整个演示文稿', confidence='medium')]), ensure_ascii=False)


def test_deck_review_returns_specific_pages_and_gates_finalize(harness):
    model = FakeVision(deck_answer)
    h = harness(model)
    for index in range(1, 4):
        h.slide(f's{index}', title=f'第 {index} 页')
        h.review(f's{index}')
    revision = h.store.status()['revision']
    h.store.request_deck_review(revision)
    h.service.sync()
    deck = h.deck()
    assert deck['status'] == 'failed'
    assert [i['slides'] for i in deck['issues']] == [[2, 3]] and deck['issues'][0]['slide_ids'] == ['s2', 's3']
    assert deck['rejected_issues'][0]['id'] == 'deck-002'
    sheet = next(call for call in model.calls if call['system'] == vision.DECK_SYSTEM_PROMPT)['images'][0][1]
    assert sheet[:8] == PNG
    h.store.finalize(revision)


def test_feature_snapshot_keeps_vision_service_on_the_host(tmp_path):
    from platform_app import format_plugins
    format_plugins.snapshot_skills(tmp_path)
    copied = tmp_path / 'pptx-ooxml-author'
    assert (copied / 'scripts' / 'office_pptx.py').is_file()
    assert not (copied / 'vision_review_service.py').exists() and not (copied / 'platform_adapter.py').exists()
