"""Hand-labelled visual-defect corpus and metrics for the host vision reviewer.

``build <dir>`` renders the labelled slides with Impress; ``run <dir> --model
<source/model>`` sends each real render to a configured image-capable model
through the production prompt, schema and status rules, then reports:

* major-defect miss rate (defect slides without a blocking issue of an expected category),
* false-positive rate (clean slides reported as failed),
* localisation accuracy (detected issues whose element IDs or region hit the labelled elements),
* deck repetition detection on the repeated three-column pages.

Model self-scores are never used. Labels are the annotator's expectations below.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
ROOT = SKILL.parent
for folder in (ROOT, SKILL):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = _load('vision_eval_engine', SKILL / 'scripts' / 'office_pptx.py')
vision = _load('vision_eval_service', SKILL / 'vision_review_service.py')

CARD_TEXT = ['统一入口\n减少重复操作', '受控执行\n审批与回滚', '可用成果\n原生可编辑']


def _cards(sid, prefix='card', count=3, body=CARD_TEXT):
    ops = []
    for index in range(count):
        x = .06 + index * .31
        ops += [('element.add_shape', dict(id=f'{prefix}-bg-{index}', shape='rounded_rectangle', fill_token='card', layer='background', geometry=dict(x=x, y=.3, w=.26, h=.5))),
                ('element.add_text', dict(id=f'{prefix}-body-{index}', text=body[index % len(body)], style='body', geometry=dict(x=x + .02, y=.36, w=.22, h=.3)))]
    return ops


def _title(text, **geometry):
    element = dict(id='title', text=text, style='slide_title')
    element.update(dict(geometry=geometry) if geometry else dict(anchor='title'))
    return ('element.add_text', element)


CASES = [
    dict(id='c01', name='meaningless-connector', blocking=['diagram_semantics'], elements=['stub'],
        ops=[_title('五层结构'),
             ('element.add_shape', dict(id='upper', shape='rounded_rectangle', fill_token='card', layer='background', geometry=dict(x=.07, y=.3, w=.6, h=.12))),
             ('element.add_text', dict(id='upper-text', text='Web 交互层', style='body', geometry=dict(x=.1, y=.32, w=.4, h=.08))),
             ('element.add_shape', dict(id='lower', shape='rounded_rectangle', fill_token='card', layer='background', geometry=dict(x=.07, y=.46, w=.6, h=.12))),
             ('element.add_text', dict(id='lower-text', text='Agent 执行层', style='body', geometry=dict(x=.1, y=.48, w=.4, h=.08))),
             ('element.add_shape', dict(id='stub', shape='line', fill_token='positive', layer='decoration', geometry=dict(x=.45, y=.42, w=.003, h=.04)))]),
    dict(id='c02', name='low-contrast-chart', blocking=['chart_quality', 'legibility'], elements=['chart'],
        ops=[_title('收入增长'), ('element.add_chart', dict(id='chart', geometry=dict(x=.1, y=.25, w=.8, h=.6),
             chart=dict(type='line', categories=['Q1', 'Q2', 'Q3', 'Q4'], series=[dict(name='收入', values=[12, 15, 19, 26])], unit='亿元'))),
             ('element.set_style', dict(id='chart', style=dict(series_colors=['EEF2F7'], data_labels=False, gridlines='none')))]),
    dict(id='c03', name='dense-cards', blocking=['spacing', 'hierarchy', 'composition'], elements=[],
        ops=[_title('能力清单')] + [('element.add_shape', dict(id=f'd-bg-{i}', shape='rounded_rectangle', fill_token='card', layer='background',
             geometry=dict(x=.04 + (i % 4) * .235, y=.2 + (i // 4) * .25, w=.225, h=.24))) for i in range(12)] +
            [('element.add_text', dict(id=f'd-text-{i}', style='caption', text='能力说明文字较多\n第二行补充说明\n第三行细节', geometry=dict(x=.045 + (i % 4) * .235, y=.205 + (i // 4) * .25, w=.215, h=.23))) for i in range(12)]),
    dict(id='c04', name='truncated-title', blocking=['legibility'], elements=['title'],
        ops=[_title('AI Agent 平台建设方案与技术路线全景说明', x=.06, y=.07, w=.86, h=.12),
             ('element.add_shape', dict(id='mask', shape='rectangle', fill_token='background', layer='decoration', geometry=dict(x=.52, y=.05, w=.46, h=.16))),
             ('element.add_text', dict(id='body', text='统一入口、受控执行与可用成果', style='body', geometry=dict(x=.06, y=.3, w=.6, h=.1)))]),
    dict(id='c05', name='repeated-three-column-a', blocking=[], deck='repetition', elements=[], ops=[_title('背景与目标')] + _cards('c05')),
    dict(id='c06', name='repeated-three-column-b', blocking=[], deck='repetition', elements=[], ops=[_title('核心能力')] + _cards('c06')),
    dict(id='c07', name='repeated-three-column-c', blocking=[], deck='repetition', elements=[], ops=[_title('交付边界')] + _cards('c07')),
    dict(id='c08', name='empty-without-focus', blocking=['focal_point', 'composition'], elements=[],
        ops=[('element.add_text', dict(id='note', text='备注：详见附件', style='caption', geometry=dict(x=.06, y=.9, w=.2, h=.04)))]),
    dict(id='c09', name='chart-without-unit-or-labels', blocking=['chart_quality'], elements=['chart'],
        ops=[_title('指标变化'), ('element.add_chart', dict(id='chart', geometry=dict(x=.1, y=.25, w=.8, h=.6),
             chart=dict(type='bar', categories=['A', 'B', 'C', 'D'], series=[dict(name='S1', values=[3, 7, 4, 9])]))),
             ('element.set_style', dict(id='chart', style=dict(data_labels=False, gridlines='none')))]),
    dict(id='c10', name='good-minimal', blocking=[], clean=True, elements=[],
        ops=[('element.add_text', dict(id='statement', text='让每一次执行都有边界', style='statement', geometry=dict(x=.1, y=.38, w=.8, h=.14))),
             ('element.add_text', dict(id='caption', text='受控执行 · 可审计 · 可回滚', style='caption', geometry=dict(x=.1, y=.55, w=.8, h=.05)))]),
    dict(id='c11', name='user-required-three-columns', blocking=[], clean=True, constraints=['用户明确要求保留三栏卡片结构'], elements=[],
        ops=[_title('三项承诺')] + _cards('c11')),
    dict(id='c12', name='native-diagram', blocking=[], clean=True, elements=[],
        ops=[_title('调用关系'), ('diagram.add', dict(id='arch', geometry=dict(x=.1, y=.25, w=.8, h=.6), preset='layered',
             nodes=[dict(id='web', label='Web 交互层'), dict(id='agent', label='Agent 执行层'), dict(id='tool', label='Tool 能力层')],
             edges=[dict(source='web', target='agent', relation='calls'), dict(source='agent', target='tool', relation='calls')]))]),
]


def build(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    store = engine.PresentationStore(directory / 'vision-eval.pptx', temporary_root=directory / 'renders')
    if not store.path.exists():
        store.init('vision-eval')
    for case in CASES:
        state = store.status()
        if any(slide['slide_id'] == case['id'] for slide in state['manifest']['slides']):
            continue
        with store.locked() as (c, current):
            for slide in state['manifest']['slides']:
                current['reviews'][slide['slide_id']] = dict(automatic_passed=True, status='pending', revision=current['revision'])
            store.save_state(c, current)
            c.commit()
        steps = [('slide.add', None)] + case['ops']
        for kind, element in steps:
            state = store.status()
            request = dict(presentation_id='vision-eval', operation_id=f'{case["id"]}-{state["revision"] + 1}', base_revision=state['revision'],
                slide_id=case['id'], operation=kind)
            if kind == 'slide.add':
                request['layout'] = 'title_body'
            elif kind.startswith('diagram.'):
                request['diagram'] = element
            else:
                request['element'] = element
            store.apply_one(request)
    revision = store.status()['revision']
    render = engine.reviewer().render_snapshot(store, revision)
    labels = dict(revision=revision, render_directory=str(render), cases=[{k: v for k, v in case.items() if k != 'ops'} for case in CASES])
    (directory / 'labels.json').write_text(json.dumps(labels, ensure_ascii=False, indent=2))
    return directory / 'labels.json'


def _iou(a, b):
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0


def evaluate(directory, client):
    directory = Path(directory)
    labels = json.loads((directory / 'labels.json').read_text())
    store = engine.PresentationStore(directory / 'vision-eval.pptx', temporary_root=directory / 'renders')
    state = store.status()
    slides = {slide['slide_id']: (index, slide) for index, slide in enumerate(state['manifest']['slides'])}
    render = Path(labels['render_directory'])
    profile = engine.profiles().get(state['profile_id'])
    results, defects, hits, clean, false_positive, located, detected_issues = [], 0, 0, 0, 0, 0, 0
    for case in labels['cases']:
        index, slide = slides[case['id']]
        png = (render / f'slide-{index + 1:03d}.png').read_bytes()
        context = dict(slide=slide, index=index, total=len(slides), profile=profile, intent=case['name'], constraints=case.get('constraints'),
            technical_issues=[])
        try:
            result = vision.evaluate_slide(client.complete(vision.SLIDE_SYSTEM_PROMPT, vision.slide_request(context), [('image/png', png)]), slide)
        except vision.VisionUnavailable as exc:
            result = vision.failure(exc.code, str(exc))
        blocking = [i for i in result.get('issues', []) if i['severity'] in ('critical', 'major')]
        geometry = {e['id']: e['geometry'] for e in slide['elements']}
        if case.get('blocking'):
            defects += 1
            matched = [i for i in blocking if i['category'] in case['blocking']]
            hit = result['status'] in ('failed', 'uncertain') and bool(matched)
            hits += hit
            if hit and case.get('elements'):
                for issue in matched:
                    detected_issues += 1
                    targets = [geometry[e] for e in case['elements'] if e in geometry]
                    by_id = bool(set(issue['element_ids']) & set(case['elements']))
                    by_region = issue.get('region') and any(_iou(issue['region'], [t['x'], t['y'], t['w'], t['h']]) > .05 for t in targets)
                    located += bool(by_id or by_region)
        if case.get('clean'):
            clean += 1
            false_positive += result['status'] == 'failed'
        results.append(dict(case=case['id'], name=case['name'], status=result['status'], issues=[(i['severity'], i['category'], i['element_ids']) for i in result.get('issues', [])],
            error=result.get('error')))
    repeated = [case['id'] for case in labels['cases'] if case.get('deck')]
    order = sorted(slides, key=lambda sid: slides[sid][0])
    images = [(render / f'slide-{slides[sid][0] + 1:03d}.png').read_bytes() for sid in order]
    summary = [dict(page=i + 1, slide_id=sid, layout=slides[sid][1].get('layout')) for i, sid in enumerate(order)]
    try:
        deck = vision.evaluate_deck(client.complete(vision.DECK_SYSTEM_PROMPT, vision.deck_request(dict(slides=summary, profile=profile)),
            [('image/png', vision.contact_sheet(images))]), len(order))
    except vision.VisionUnavailable as exc:
        deck = vision.failure(exc.code, str(exc))
    expected_pages = {order.index(sid) + 1 for sid in repeated}
    deck_hit = any(i['category'] in ('repetition', 'card_overuse') and len(set(i['slides']) & expected_pages) >= 2 for i in deck.get('issues', []))
    return dict(model=client.model_id, prompt_version=vision.PROMPT_VERSION,
        major_defect_miss_rate=round(1 - hits / defects, 3) if defects else None,
        false_positive_rate=round(false_positive / clean, 3) if clean else None,
        localisation_accuracy=round(located / detected_issues, 3) if detected_issues else None,
        deck_repetition_detected=deck_hit, slides=results, deck_status=deck['status'])


def platform_client(model_id, timeout=120):
    from platform_app import models
    from platform_app.agent_core.config import Settings
    from platform_app.agent_core.llm import build_chat_model
    config, _ = models.current()
    settings = Settings.from_mapping({'llm': config})
    profile = settings.get_profile(model_id)
    if not profile.supports_input('image'):
        raise SystemExit(f'{profile.id} does not declare image input')
    model = build_chat_model(profile, streaming=False, trust_env=True, timeout=timeout)
    return vision.ModelClient(profile.id, vision.langchain_invoker(model), timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=['build', 'run'])
    parser.add_argument('directory')
    parser.add_argument('--model')
    args = parser.parse_args()
    if args.command == 'build':
        print(build(args.directory))
        return
    if not args.model:
        parser.error('--model <source/model> is required for run')
    if not (Path(args.directory) / 'labels.json').exists():
        build(args.directory)
    report = evaluate(args.directory, platform_client(args.model))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
