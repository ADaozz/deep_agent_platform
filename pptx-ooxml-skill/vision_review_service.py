"""Host-side multimodal aesthetic review of formally rendered PPTX slides and decks.

This module runs in the platform process only; it is never copied into the
Agent sandbox and never sees an API key in a script. It:

* builds the review request from a real host-rendered PNG (sent as an image
  block, never as a path or URL), the design profile, the slide intent, the
  native element manifest, chart data, diagram nodes/edges and the automatic
  check results;
* validates the model's JSON against a fixed schema, rejects invented element
  IDs, and derives the status from confirmed issues rather than from the
  model's own verdict or any score;
* persists every result with server-generated identifiers and hashes in a
  host-private ledger.

Timeouts, malformed output, missing images and models without image input are
never treated as a pass.
"""
import base64
import concurrent.futures
import hashlib
import io
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError, create_model

PROMPT_VERSION = 'pptx-aesthetic-2026.10-1'
DECK_PROMPT_VERSION = 'pptx-deck-2026.10-1'
STATUSES = ('pending', 'passed', 'failed', 'uncertain', 'unavailable')
SEVERITIES = ('critical', 'major', 'minor')
CONFIDENCES = ('high', 'medium', 'low')
DIMENSIONS = {
    'composition': '页面构图是否平衡',
    'focal_point': '视觉焦点是否明确',
    'hierarchy': '信息层级是否合理',
    'legibility': '文字与背景是否清晰可读',
    'spacing': '留白和元素间距是否协调',
    'chart_quality': '图表是否专业、简约且有单位/标签/重点',
    'diagram_semantics': '流程/架构图连线是否有意义且与结构化关系一致',
    'media_balance': '图片、图标与文字比例是否合适',
    'decoration': '装饰是否多余',
    'profile_consistency': '是否与当前 Design Profile 一致',
    'templating': '是否模板化、过度重复或低质量拼装',
}
DECK_CATEGORIES = {
    'design_system': '整体是否属于同一设计体系',
    'transitions': '章节过渡是否自然',
    'repetition': '相邻页面构图是否过度重复',
    'card_overuse': '是否频繁使用相同的卡片布局',
    'consistency': '颜色、字体、图表是否一致',
    'density_rhythm': '页面信息密度是否有合理变化',
    'bookends': '封面与总结页是否呼应',
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 150.0

Verdict = Literal['ok', 'issue', 'not_applicable', 'uncertain']
Severity = Literal['critical', 'major', 'minor']
Confidence = Literal['high', 'medium', 'low']
Text = Annotated[str, Field(min_length=1, max_length=800)]
Fraction = Annotated[float, Field(ge=0, le=1)]


class Check(BaseModel):
    verdict: Verdict = Field(description='ok / issue / not_applicable / uncertain')
    evidence: Text = Field(description='截图中可见的具体证据：文字、对象、位置或颜色')


SlideChecks = create_model('SlideChecks', **{key: (Check, Field(description=label)) for key, label in DIMENSIONS.items()})
DeckChecks = create_model('DeckChecks', **{key: (Check, Field(description=label)) for key, label in DECK_CATEGORIES.items()})


class SlideIssue(BaseModel):
    id: str = Field(pattern=r'^visual-\d{3}$', description='visual-001, visual-002 …')
    category: Literal[tuple(DIMENSIONS)]
    severity: Severity
    element_ids: list[str] = Field(default_factory=list, max_length=8, description='只能使用元素清单中的 ID；无法确认时为空')
    region: list[Fraction] = Field(min_length=4, max_length=4, description='问题区域，相对截图的归一化 [x, y, w, h]')
    observation: Text
    reason: Text
    suggestion: Text = Field(description='可局部执行的修改建议')
    confidence: Confidence
    needs_verification: bool = Field(default=False, description='无法根据输入确认时为 true')


class SlideReview(BaseModel):
    """单页幻灯片视觉审查结果。"""
    status: Literal['pass', 'fail', 'uncertain']
    summary: Text
    checks: SlideChecks
    issues: list[SlideIssue] = Field(default_factory=list, max_length=12)


class DeckIssue(BaseModel):
    id: str = Field(pattern=r'^deck-\d{3}$', description='deck-001, deck-002 …')
    category: Literal[tuple(DECK_CATEGORIES)]
    severity: Severity
    slides: list[Annotated[int, Field(ge=1)]] = Field(min_length=1, max_length=6, description='具体页码，1 起算')
    observation: Text
    reason: Text
    suggestion: Text = Field(description='可局部执行的修改建议')
    confidence: Confidence
    needs_verification: bool = False


class DeckReview(BaseModel):
    """整套幻灯片视觉一致性审查结果。"""
    status: Literal['pass', 'fail', 'uncertain']
    summary: Text
    checks: DeckChecks
    issues: list[DeckIssue] = Field(default_factory=list, max_length=12)


SLIDE_SYSTEM_PROMPT = """你是演示文稿视觉审查员。你会收到一张由 LibreOffice Impress 渲染的真实幻灯片截图（图像输入），以及该页的结构化上下文（Design Profile、版式、页面用途、原生元素清单、图表数据、流程节点与连线、自动校验结果、用户明确的版式约束）。

审查规则：
1. 观察必须来自截图本身。结构化清单只用于把问题映射到元素 ID、核对图表数据与流程关系，不能代替看图。
2. 对每个维度给出 verdict 和具体证据。证据必须指向截图中可见的具体内容（文字、对象、位置、颜色），"看起来很好""设计专业"之类的泛泛评价不是证据。
3. 流程图与架构图：逐条比对截图中可见的连线和 edges 列表。可见但不在 edges 中的线、方向不明的箭头、悬空或不知连接什么的短线、穿过文字的线都是问题。无法根据输入确认的关系必须 needs_verification=true 且 confidence=low，绝不能推断为正确。
4. element_ids 只能使用清单中存在的 ID；无法可靠对应时返回空数组，并用 region 标出区域。region 为相对整张截图的归一化 [x, y, w, h]。
5. 严重程度：critical = 内容不可读、错误或误导；major = 专业人士不会交付的明确缺陷（无意义连线、对比度不足、文字被截断/拥挤、没有视觉焦点、图表缺少必要单位或标签等）；minor = 可选的打磨建议。
6. 留白多但焦点明确、层级清楚的极简页面不是缺陷。用户明确要求保留的版式属于已接受的设计决定，不要据此报问题，但可读性、数据正确性问题仍需报告。
7. 不要给分数；不要建议重做整页，只给出可以通过局部修改完成的建议。
8. 通过提供的结构返回结果。"""

DECK_SYSTEM_PROMPT = """你是演示文稿整体视觉一致性审查员。你会收到整套幻灯片的缩略图总览（图像输入，每张缩略图左上角有页码）以及每页的版式、标题和元素统计。

只检查跨页面的视觉设计问题：设计体系是否统一、章节过渡、相邻页构图重复、卡片布局滥用、颜色/字体/图表一致性、信息密度节奏、封面与总结页呼应。不要重复逐页文字校验。
每个问题必须给出具体页码（slides，1 起算，最多 6 页），不允许笼统要求重做整个演示文稿；建议必须是可局部执行的修改。
对每个维度给出 verdict 和指向具体页面的证据。泛泛评价不是证据。不要给分数。通过提供的结构返回结果。"""


class VisionUnavailable(Exception):
    """No image-capable model, or the call could not produce a usable answer."""

    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code


def png_info(data):
    from PIL import Image
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise VisionUnavailable('image_missing', 'rendered image is missing or oversized')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG':
            raise VisionUnavailable('image_missing', 'rendered image is not a PNG')
        rgb = image.convert('RGB')
        return dict(width=rgb.width, height=rgb.height, pixel_sha256=hashlib.sha256(rgb.tobytes()).hexdigest())


def _round(value):
    return round(float(value), 3)


def element_context(element):
    item = dict(id=element['id'], type=element.get('type'), layer=element.get('layer'),
        geometry={key: _round(value) for key, value in (element.get('geometry') or {}).items()})
    if element.get('text'):
        item['text'] = element['text'][:240]
    style = element.get('style') or {}
    if style.get('font_size'):
        item['font'] = dict(size=style.get('font_size'), bold=style.get('bold'), color=style.get('color'))
    for key in ('shape', 'fill', 'anchor', 'component_instance', 'diagram_id', 'diagram_role', 'node_id', 'edge_id', 'source', 'target', 'relation', 'arrow', 'rotation'):
        if element.get(key) is not None:
            item[key] = element[key]
    if element.get('chart'):
        chart = element['chart']
        item['chart'] = dict(type=chart.get('type'), categories=chart.get('categories'), unit=chart.get('unit'),
            series=[dict(name=s.get('name'), values=s.get('values')) for s in chart.get('series') or []])
    if element.get('rows'):
        item['rows'] = [row[:8] for row in element['rows'][:10]]
    if element.get('component'):
        item['component'] = dict(component_id=element['component'].get('component_id'), variant=element['component'].get('variant'))
    connector = element.get('connector')
    if connector:
        item['connector'] = dict(begin=connector.get('begin'), end=connector.get('end'),
            bound_start=(connector.get('start') or {}).get('element_id'), bound_end=(connector.get('finish') or {}).get('element_id'),
            arrow_start=connector.get('arrow_start'), arrow_end=connector.get('arrow_end'))
    return item


def diagram_context(elements):
    diagrams = {}
    for element in elements:
        identity = element.get('diagram_id')
        if not identity:
            continue
        entry = diagrams.setdefault(identity, dict(id=identity, nodes=[], edges=[]))
        if element.get('diagram_role') == 'node':
            entry['nodes'].append(dict(element_id=element['id'], node_id=element.get('node_id'), label=element.get('label'), rank=element.get('rank')))
        elif element.get('diagram_role') == 'edge':
            entry['edges'].append(dict(element_id=element['id'], source=element.get('source'), target=element.get('target'),
                relation=element.get('relation'), arrow=element.get('arrow'), label=element.get('label')))
    return list(diagrams.values())


def profile_context(profile):
    if not profile:
        return None
    return dict(id=profile.get('id'), name=profile.get('name'), description=profile.get('description'),
        colors={key: value for key, value in (profile.get('colors') or {}).items() if key != 'chart_series'},
        fonts=profile.get('fonts'), density=profile.get('density'), charts=profile.get('charts'), tables=profile.get('tables'),
        shape=profile.get('shape'))


def slide_request(context):
    """User text for one slide; the PNG is attached separately as an image block."""
    slide = context['slide']
    elements = [element_context(element) for element in slide.get('elements', [])][:80]
    payload = dict(
        task='审查附带截图这一页的视觉审美与表达质量。',
        page=dict(index=context['index'] + 1, total=context['total'], slide_id=slide['slide_id'], layout=slide.get('layout'),
            layout_variant=slide.get('layout_variant'), purpose=context.get('intent') or '未提供', user_constraints=context.get('constraints') or []),
        design_profile=profile_context(context.get('profile')),
        elements=elements,
        diagrams=diagram_context(slide.get('elements', [])),
        automatic_check_issues=[{key: issue.get(key) for key in ('type', 'severity', 'element_id', 'elements', 'message') if issue.get(key) is not None}
            for issue in context.get('technical_issues') or []][:40],
    )
    return json.dumps(payload, ensure_ascii=False)


def deck_request(context):
    payload = dict(
        task='审查附带的整套缩略图总览，检查跨页面视觉一致性。',
        total=len(context['slides']),
        design_profile=profile_context(context.get('profile')),
        slides=context['slides'],
    )
    return json.dumps(payload, ensure_ascii=False)


def parse(answer, schema):
    """Structured output (or its JSON text) -> validated dict. Raises VisionUnavailable."""
    if isinstance(answer, BaseModel):
        answer = answer.model_dump()
    elif isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError as exc:
            raise VisionUnavailable('invalid_json', 'model reply is not JSON') from exc
    if not isinstance(answer, dict):
        raise VisionUnavailable('invalid_json', 'model returned no review')
    try:
        return schema.model_validate(answer).model_dump()
    except ValidationError as exc:
        error = exc.errors()[0]
        raise VisionUnavailable('schema_invalid', f'{"/".join(map(str, error["loc"]))}: {error["msg"]}'[:200]) from exc


def _decide(model_status, issues, checks, flags):
    blocking = [issue for issue in issues if issue['severity'] in ('critical', 'major') and not issue.get('dismissed')]
    confirmed = [issue for issue in blocking if issue['confidence'] in ('high', 'medium') and not issue.get('needs_verification')]
    if confirmed:
        if model_status == 'pass':
            flags.append(dict(type='status_contradiction', message='model said pass but reported confirmed blocking issues'))
        return 'failed'
    if blocking or model_status == 'uncertain' or any(item['verdict'] == 'uncertain' for item in checks.values()):
        return 'uncertain'
    if model_status == 'fail':
        flags.append(dict(type='unsupported_failure', message='model said fail without a confirmed critical or major issue'))
        return 'uncertain'
    return 'passed'


def evaluate_slide(answer, slide):
    """Model answer -> validated, status-derived review. Raises VisionUnavailable."""
    value = parse(answer, SlideReview)
    valid_ids = {element['id'] for element in slide.get('elements', [])}
    flags, issues, seen = [], [], set()
    for issue in value['issues']:
        if issue['id'] in seen:
            raise VisionUnavailable('schema_invalid', f'duplicate issue id {issue["id"]}')
        seen.add(issue['id'])
        known = [identity for identity in issue['element_ids'] if identity in valid_ids]
        unknown = [identity for identity in issue['element_ids'] if identity not in valid_ids]
        item = dict(issue, element_ids=known)
        if unknown:
            item['unmapped_element_ids'] = unknown
            flags.append(dict(type='unknown_element_ids', issue=issue['id'], ids=unknown))
        x, y, w, h = issue['region']
        if w <= 0 or h <= 0 or x + w > 1.001 or y + h > 1.001:
            item['region'] = None
            item['region_invalid'] = True
            if not known:
                item['needs_verification'] = True
        issues.append(item)
    status = _decide(value['status'], issues, value['checks'], flags)
    return dict(status=status, model_status=value['status'], summary=value['summary'], checks=value['checks'], issues=issues, flags=flags)


def evaluate_deck(answer, total):
    value = parse(answer, DeckReview)
    flags, issues, rejected = [], [], []
    for issue in value['issues']:
        pages = sorted(set(issue['slides']))
        if any(page > total for page in pages):
            rejected.append(dict(issue, rejected_reason='slide number outside the deck'))
            continue
        if total >= 5 and len(pages) > max(3, int(total * .6)):
            rejected.append(dict(issue, rejected_reason='not page-specific; deck review must name concrete pages'))
            continue
        issues.append(dict(issue, slides=pages))
    if rejected:
        flags.append(dict(type='rejected_issues', count=len(rejected)))
    status = _decide(value['status'], issues, value['checks'], flags)
    return dict(status=status, model_status=value['status'], summary=value['summary'], checks=value['checks'], issues=issues,
        rejected_issues=rejected, flags=flags)


def review_with_retry(client, system, text, images, schema, evaluate):
    """One corrective retry when the answer does not fit the schema; returns (result, last answer)."""
    answer = client.complete(system, text, images, schema)
    try:
        return evaluate(answer), answer
    except VisionUnavailable as exc:
        if exc.code not in ('invalid_json', 'schema_invalid'):
            raise
        hint = f'\n\n上一次回答不符合要求（{exc}），请按要求的结构重新给出审查结果。'
        answer = client.complete(system, text + hint, images, schema)
        return evaluate(answer), answer


def issue_fingerprint(issue):
    if issue.get('element_ids'):
        anchor = ','.join(sorted(issue['element_ids']))
    elif issue.get('region'):
        x, y, w, h = issue['region']
        anchor = f'r{round((x + w / 2) * 4)}{round((y + h / 2) * 4)}'
    else:
        anchor = 'page'
    return f'{issue["category"]}:{anchor}'


def contact_sheet(images, columns=4, width=480):
    """Numbered thumbnail overview for deck review, built on the host."""
    from PIL import Image, ImageDraw, ImageFont
    thumbs = []
    for data in images:
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert('RGB')
            height = round(rgb.height * width / rgb.width)
            thumbs.append(rgb.resize((width, height), Image.LANCZOS))
    if not thumbs:
        raise VisionUnavailable('image_missing', 'no rendered slides')
    gap, label = 16, 30
    cell_h = max(thumb.height for thumb in thumbs)
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * width + (columns + 1) * gap, rows * (cell_h + label) + (rows + 1) * gap), '#DDE2E8')
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype('DejaVuSans-Bold.ttf', 20)
    except OSError:
        font = ImageFont.load_default()
    for index, thumb in enumerate(thumbs):
        row, column = divmod(index, columns)
        x = gap + column * (width + gap)
        y = gap + row * (cell_h + label + gap)
        draw.text((x, y), f'{index + 1:02d}', fill='#17324D', font=font)
        sheet.paste(thumb, (x, y + label))
    buffer = io.BytesIO()
    sheet.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------- model call

class ModelClient:
    """One multimodal request. ``invoke(system, text, images, schema)`` returns the structured review."""

    def __init__(self, model_id, invoke, timeout=DEFAULT_TIMEOUT):
        self.model_id = model_id
        self._invoke = invoke
        self.timeout = timeout

    def complete(self, system, text, images, schema):
        if not images:
            raise VisionUnavailable('image_missing', 'no image attached to the review request')
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='pptx-vision-call')
        future = executor.submit(self._invoke, system, text, images, schema)
        try:
            return future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError as exc:
            raise VisionUnavailable('timeout', f'vision model did not answer within {self.timeout:g}s') from exc
        except VisionUnavailable:
            raise
        except Exception as exc:  # provider errors may carry credentials; keep the type only
            raise VisionUnavailable('provider_error', type(exc).__name__) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def langchain_invoker(chat_model):
    """Provider JSON mode with the review schema in the prompt; Pydantic validates the result.

    JSON mode rather than a forced function call: reasoning models (DeepSeek
    thinking mode) reject a forced tool_choice. Providers without JSON mode get
    the same prompt as a plain request.
    """
    import openai
    from langchain_core.exceptions import OutputParserException
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.messages.content import create_image_block, create_text_block
    json_mode = [True]

    def invoke(system, text, images, schema):
        content = [create_text_block(text)]
        for mime, data in images:
            content.append(create_image_block(base64=base64.b64encode(data).decode('ascii'), mime_type=mime))
        instructions = system + '\n\n以 JSON 对象返回结果，结构遵循以下 JSON Schema：\n' + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        messages = [SystemMessage(content=instructions), HumanMessage(content=content)]
        if json_mode[0]:
            try:
                return chat_model.with_structured_output(schema, method='json_mode').invoke(messages)
            except (OutputParserException, ValidationError) as exc:
                raise VisionUnavailable('schema_invalid', str(exc).splitlines()[0][:200]) from exc
            except openai.BadRequestError:
                json_mode[0] = False
        result = chat_model.invoke(messages)
        return result.content if isinstance(result.content, str) else ''.join(
            block.get('text', '') for block in result.content if isinstance(block, dict))
    return invoke


# ---------------------------------------------------------------- ledger

class ReviewLedger:
    """Host-private, append-only record of model reviews and human decisions."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, kind TEXT, presentation_id TEXT, path TEXT,
                slide_id TEXT, revision INTEGER, render_sha256 TEXT, pixel_sha256 TEXT, snapshot_sha256 TEXT, model_id TEXT,
                prompt_version TEXT, status TEXT, summary TEXT, issues TEXT, checks TEXT, flags TEXT, error TEXT,
                applied INTEGER, review_timestamp REAL, response_sha256 TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, review_id TEXT, presentation_id TEXT,
                slide_id TEXT, revision INTEGER, decision TEXT, issue_ids TEXT, notes TEXT, actor TEXT, decided_at REAL)''')

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def record(self, **values):
        values = dict(values)
        values.setdefault('id', uuid.uuid4().hex)
        values.setdefault('review_timestamp', time.time())
        for key in ('issues', 'checks', 'flags'):
            values[key] = json.dumps(values.get(key) or [], ensure_ascii=False)
        columns = ('id', 'kind', 'presentation_id', 'path', 'slide_id', 'revision', 'render_sha256', 'pixel_sha256', 'snapshot_sha256',
            'model_id', 'prompt_version', 'status', 'summary', 'issues', 'checks', 'flags', 'error', 'applied', 'review_timestamp', 'response_sha256')
        with self.lock, self.connect() as c:
            c.execute(f'INSERT INTO reviews ({",".join(columns)}) VALUES ({",".join("?" * len(columns))})', [values.get(key) for key in columns])
        return values['id']

    def mark_applied(self, review_id, applied):
        with self.lock, self.connect() as c:
            c.execute('UPDATE reviews SET applied=? WHERE id=?', (1 if applied else 0, review_id))

    def get(self, review_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM reviews WHERE id=?', (review_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        for key in ('issues', 'checks', 'flags'):
            value[key] = json.loads(value[key] or '[]')
        return value

    def decide(self, **values):
        values = dict(values)
        values.setdefault('id', uuid.uuid4().hex)
        values.setdefault('decided_at', time.time())
        values['issue_ids'] = json.dumps(values.get('issue_ids') or [])
        columns = ('id', 'review_id', 'presentation_id', 'slide_id', 'revision', 'decision', 'issue_ids', 'notes', 'actor', 'decided_at')
        with self.lock, self.connect() as c:
            c.execute(f'INSERT INTO decisions ({",".join(columns)}) VALUES ({",".join("?" * len(columns))})', [values.get(key) for key in columns])
        return values['id']

    def decision(self, decision_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM decisions WHERE id=?', (decision_id,)).fetchone()
        return dict(row) if row else None


def failure(code, message=''):
    return dict(status='unavailable', error=dict(code=code, message=str(message)[:200]), issues=[], checks={}, flags=[], summary='')
