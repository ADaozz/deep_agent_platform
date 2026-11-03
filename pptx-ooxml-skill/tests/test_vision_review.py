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


def test_model_uses_selected_style_instead_of_internal_blue_profile(harness):
    model=FakeVision(answer());h=harness(model)
    h.op('slide.add',layout='title_body')
    h.op('element.add_text',element=dict(id='title',anchor='title',text='风格审查'))
    from pptx import Presentation
    prs=Presentation(h.path)
    engine.style_library.apply_reference(prs,'consulting/red-black-growth',design={
        'palette':{'accent':'308050','text':'17324D'},'user_requirements':['用户指定绿色'],
        'margins':{'unit':'fraction','left':.04,'right':.04,'top':.04,'bottom':.04}})
    prs.save(h.path);h.service.detect_external()
    assert h.store.check_deck({'s1':'核对页面标题'})['status']=='passed'
    h.service.sync();review=h.review()
    assert review['aesthetic_review']['status']=='passed'
    request=json.loads(model.calls[0]['text'])
    assert request['design_profile']['id']=='consulting/red-black-growth'
    assert request['design_profile']['colors']['accent']=='308050'
    assert request['selected_design']['design_md']
    assert request['selected_design']['parameters']['user_requirements']==['用户指定绿色']


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
        assert len(model.calls) == 2 and '上一次回答' in model.calls[1]['text'] and '重新' in model.calls[1]['text']
    status = h.store.review_status('s1')['slides'][0]
    assert 'Do not claim visual acceptance' in status['next_action']
    assert h.store.finalize(h.store.status()['revision'])['aesthetics']['s1']['status'] == 'unavailable'


def test_agent_can_rerequest_only_after_unavailable(harness):
    # An automatic corrective retry is attempted before the agent can re-request.
    model = FakeVision('not json', 'still not json', answer())
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


def test_self_inspection_cannot_accept_and_manual_override_is_unavailable(harness):
    h=harness(None);h.slide()
    assert h.review()['aesthetic_review']['status']=='unavailable'
    revision=h.store.status()['revision']
    h.store.review('s1',revision,True,'Agent 自检说明')
    assert h.store.review_status('s1')['slides'][0]['effective_aesthetic']=='unavailable'
    assert not hasattr(h.service,'human_decision') and not hasattr(h.store,'host_request_review')
    with pytest.raises(ValueError,match='no longer supported'):
        h.store.review('s1',revision,True,'manual','human')
    h.store.finalize(revision);h.service.sync()
    deadline=time.time()+60
    while time.time()<deadline and not any(e.get('type')=='presentation_finalize_verification' for e in h.events):time.sleep(.2)
    verification=next(e for e in h.events if e.get('type')=='presentation_finalize_verification')
    assert verification['finalize_verified'],verification


def test_even_critical_visual_findings_are_advice_without_automatic_repairs(harness):
    h = harness(FakeVision(answer('fail', [issue(severity='critical')])))
    h.slide()
    before = h.store.path.read_bytes()
    review = h.review()
    assert review['aesthetic_review']['status'] == 'failed' and review['status'] == 'needs_attention'
    assert review['aesthetic_review']['auto_repair'] is False
    assert review['aesthetic_review']['advisory_only'] is True
    assert 'change nothing unless asked' in review['next_action']
    assert h.store.path.read_bytes() == before
    event = h.store.finalize(h.store.status()['revision'])
    assert event['outstanding_critical'] == {'s1': ['visual-001']}


def test_dismissal_keeps_model_verdict_without_implicit_human_acceptance(harness):
    h=harness(FakeVision(answer('fail',[issue(severity='critical')])));h.slide();h.review()
    revision=h.store.status()['revision']
    h.service.dismiss_issues('deck',revision,['visual-001'],slide_id='s1')
    result=h.store.review_status('s1')['slides'][0]
    assert result['effective_aesthetic']=='failed'
    assert result['aesthetic_review']['issues'][0]['dismissed']
    assert not result['aesthetic_review'].get('human')
    assert result['critical_issues']==[]
    assert len(h.model.calls)==1


def test_legacy_human_flags_do_not_override_a_model_answer(harness):
    gate=threading.Event();model=FakeVision(answer('fail',[issue()]),gate=gate);h=harness(model)
    try:
        h.slide();deadline=time.time()+30
        while not model.calls and time.time()<deadline:time.sleep(.1)
        with h.store.locked() as(c,state):
            state['reviews']['s1']['aesthetic_review']['human']={'decision':'accept'}
            h.store.save_state(c,state);c.commit()
        gate.set();result=h.review()
        assert result['effective_aesthetic']=='failed'
        assert not result['aesthetic_review'].get('human')
    finally:gate.set()


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
        # Invalid page references are rejected deterministically, without relying on prose heuristics.
        dict(id='deck-002', category='design_system', severity='major', slides=[4], observation='第 4 页标题颜色与前文不同',
            reason='标题颜色不一致', suggestion='统一第 4 页标题颜色', confidence='medium')]), ensure_ascii=False)


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


def test_stale_async_deck_review_does_not_overwrite_new_revision(harness):
    gate = threading.Event()

    def two_page_deck(system, text):
        if system != vision.DECK_SYSTEM_PROMPT:
            return answer()
        checks = {key: dict(verdict='ok', evidence=f'{label}：两页一致') for key, label in vision.DECK_CATEGORIES.items()}
        return json.dumps(dict(status='pass', summary='过期整套审查', checks=checks, issues=[]), ensure_ascii=False)

    model = FakeVision(two_page_deck, gate=gate)
    h = harness(model)
    for index in range(1, 3):
        h.slide(f's{index}', title=f'第 {index} 页')
        h.review(f's{index}')
    requested = h.store.request_deck_review(h.store.status()['revision'])
    request_id = requested['deck_review']['request_id']
    h.service.sync()
    deadline = time.time() + 30
    while time.time() < deadline and not model.calls:
        time.sleep(.1)
    assert model.calls
    h.op('element.adjust', sid='s1', element={'id': 'title', 'dx_in': 0.15})
    assert h.store.status()['deck_review']['status'] == 'stale'
    gate.set()
    time.sleep(1)
    assert h.store.record_deck_review(request_id, dict(status='passed', summary='late')) == dict(applied=False, reason='stale')
    assert h.store.status()['deck_review']['status'] == 'stale'
    assert h.store.status()['deck_review'].get('summary') != '过期整套审查'


def test_feature_snapshot_keeps_vision_service_on_the_host(tmp_path):
    from platform_app import format_plugins
    format_plugins.snapshot_skills(tmp_path)
    copied = tmp_path / 'pptx-ooxml-author'
    assert (copied / 'scripts' / 'office_pptx.py').is_file()
    assert not (copied / 'vision_review_service.py').exists() and not (copied / 'platform_adapter.py').exists()


def test_vision_uses_effective_template_profile(harness):
    model = FakeVision(answer())
    h = harness(model)
    with h.store.locked() as (connection, state):
        state['template_visual_system'] = {'name': 'Dark Analytics Dashboard'}
        state['template_font'] = 'Montserrat'
        h.store.save_state(connection, state)
        connection.commit()
    h.slide()
    h.review()
    profile = json.loads(model.calls[0]['text'])['design_profile']
    assert profile['name'] == 'Dark Analytics Dashboard'
    assert profile['fonts']['body'] == 'Montserrat'


def test_changed_page_exposes_previous_review_without_accepting_it(harness):
    h = harness(FakeVision(answer()))
    h.slide()
    previous = h.review()
    h.op('element.update', element={'id': 'title', 'text': '更新后的汇报标题'})
    slide = h.store.status()['manifest']['slides'][0]
    assert slide['review']['status'] == 'writing'
    assert slide['previous_review']['aesthetic_review']['review_id'] == previous['aesthetic_review']['review_id']
    h.store.check_slide('s1', '标题已更新')
    slide = h.store.status()['manifest']['slides'][0]
    assert slide['review']['aesthetic_review']['status'] == 'pending'
    assert slide['previous_review']['aesthetic_review']['review_id'] == previous['aesthetic_review']['review_id']


def test_unsupported_chart_preview_is_not_sent_as_a_successful_visual_check(harness):
    model = FakeVision(answer())
    h = harness(model)
    h.op('slide.add', layout='title_body')
    h.op('element.add_chart', element=dict(id='waterfall', geometry=dict(x=.1,y=.25,w=.8,h=.6),
        chart=dict(type='waterfall',categories=['Start','Change','End'],
                   series=[dict(name='Cash',values=[100,-20,80])],totals=[0,2])))
    result = h.check()
    review = result['review']
    assert review['automatic_passed'] and review['rendered']
    assert review['visual_integrity']['status'] == 'unavailable'
    assert review['aesthetic_review']['error']['code'] == 'unsupported_chart_renderer'
    assert not model.calls
