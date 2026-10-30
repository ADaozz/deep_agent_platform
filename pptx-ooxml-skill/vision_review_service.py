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
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator

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
GENERIC_EVIDENCE = re.compile(r'^(看起来(很|还)?(好|不错|专业)|设计(很)?专业|整体(很)?(好|不错|协调)|没有问题|无问题|良好|ok|fine|good|looks (good|fine|great|professional)|no issues?)[。.!！]*$', re.I)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 90.0

_REGION = {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'minItems': 4, 'maxItems': 4}
_TEXT = lambda limit: {'type': 'string', 'minLength': 1, 'maxLength': limit}

SLIDE_SCHEMA = {
    'type': 'object',
    'required': ['status', 'summary', 'checks', 'issues'],
    'additionalProperties': False,
    'properties': {
        'status': {'enum': ['pass', 'fail', 'uncertain']},
        'summary': _TEXT(400),
        'checks': {
            'type': 'object', 'additionalProperties': False, 'required': list(DIMENSIONS),
            'properties': {key: {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'evidence'],
                'properties': {'verdict': {'enum': ['ok', 'issue', 'not_applicable', 'uncertain']}, 'evidence': _TEXT(300)}} for key in DIMENSIONS},
        },
        'issues': {
            'type': 'array', 'maxItems': 12,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['id', 'category', 'severity', 'element_ids', 'region', 'observation', 'reason', 'suggestion', 'confidence'],
                'properties': {
                    'id': {'type': 'string', 'pattern': r'^visual-\d{3}$'},
                    'category': {'enum': list(DIMENSIONS)},
                    'severity': {'enum': list(SEVERITIES)},
                    'element_ids': {'type': 'array', 'maxItems': 8, 'items': {'type': 'string', 'maxLength': 120}},
                    'region': _REGION,
                    'observation': _TEXT(300), 'reason': _TEXT(300), 'suggestion': _TEXT(300),
                    'confidence': {'enum': list(CONFIDENCES)},
                    'needs_verification': {'type': 'boolean'},
                },
            },
        },
    },
}

DECK_SCHEMA = {
    'type': 'object',
    'required': ['status', 'summary', 'checks', 'issues'],
    'additionalProperties': False,
    'properties': {
        'status': {'enum': ['pass', 'fail', 'uncertain']},
        'summary': _TEXT(400),
        'checks': {
            'type': 'object', 'additionalProperties': False, 'required': list(DECK_CATEGORIES),
            'properties': {key: {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'evidence'],
                'properties': {'verdict': {'enum': ['ok', 'issue', 'not_applicable', 'uncertain']}, 'evidence': _TEXT(300)}} for key in DECK_CATEGORIES},
        },
        'issues': {
            'type': 'array', 'maxItems': 12,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['id', 'category', 'severity', 'slides', 'observation', 'reason', 'suggestion', 'confidence'],
                'properties': {
                    'id': {'type': 'string', 'pattern': r'^deck-\d{3}$'},
                    'category': {'enum': list(DECK_CATEGORIES)},
                    'severity': {'enum': list(SEVERITIES)},
                    'slides': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': {'type': 'integer', 'minimum': 1}},
                    'observation': _TEXT(300), 'reason': _TEXT(300), 'suggestion': _TEXT(300),
                    'confidence': {'enum': list(CONFIDENCES)},
                    'needs_verification': {'type': 'boolean'},
                },
            },
        },
    },
}

SLIDE_SYSTEM_PROMPT = """你是演示文稿视觉审查员。你会收到一张由 LibreOffice Impress 渲染的真实幻灯片截图（图像输入），以及该页的结构化上下文（Design Profile、版式、页面用途、原生元素清单、图表数据、流程节点与连线、自动校验结果、用户明确的版式约束）。

审查规则：
1. 观察必须来自截图本身。结构化清单只用于把问题映射到元素 ID、核对图表数据与流程关系，不能代替看图。
2. 对每个维度给出 verdict 和具体证据。证据必须指向截图中可见的具体内容（文字、对象、位置、颜色），"看起来很好""设计专业"之类的泛泛评价不是证据。
3. 流程图与架构图：逐条比对截图中可见的连线和 edges 列表。可见但不在 edges 中的线、方向不明的箭头、悬空或不知连接什么的短线、穿过文字的线都是问题。无法根据输入确认的关系必须 needs_verification=true 且 confidence=low，绝不能推断为正确。
4. element_ids 只能使用清单中存在的 ID；无法可靠对应时返回空数组，并用 region 标出区域。region 为相对整张截图的归一化 [x, y, w, h]。
5. 严重程度：critical = 内容不可读、错误或误导；major = 专业人士不会交付的明确缺陷（无意义连线、对比度不足、文字被截断/拥挤、没有视觉焦点、图表缺少必要单位或标签等）；minor = 可选的打磨建议。
6. 留白多但焦点明确、层级清楚的极简页面不是缺陷。用户明确要求保留的版式属于已接受的设计决定，不要据此报问题，但可读性、数据正确性问题仍需报告。
7. 不要给分数；不要建议重做整页，只给出可以通过局部修改完成的建议。
8. 只输出一个 JSON 对象，不要 Markdown，不要额外文字。"""

DECK_SYSTEM_PROMPT = """你是演示文稿整体视觉一致性审查员。你会收到整套幻灯片的缩略图总览（图像输入，每张缩略图左上角有页码）以及每页的版式、标题和元素统计。

只检查跨页面的视觉设计问题：设计体系是否统一、章节过渡、相邻页构图重复、卡片布局滥用、颜色/字体/图表一致性、信息密度节奏、封面与总结页呼应。不要重复逐页文字校验。
每个问题必须给出具体页码（slides，1 起算，最多 6 页），不允许笼统要求重做整个演示文稿；建议必须是可局部执行的修改。
对每个维度给出 verdict 和指向具体页面的证据。泛泛评价不是证据。不要给分数。只输出一个 JSON 对象。"""


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
        task='审查附带截图这一页的视觉审美与表达质量，按 schema 输出 JSON。',
        page=dict(index=context['index'] + 1, total=context['total'], slide_id=slide['slide_id'], layout=slide.get('layout'),
            layout_variant=slide.get('layout_variant'), purpose=context.get('intent') or '未提供', user_constraints=context.get('constraints') or []),
        design_profile=profile_context(context.get('profile')),
        elements=elements,
        diagrams=diagram_context(slide.get('elements', [])),
        automatic_check_issues=[{key: issue.get(key) for key in ('type', 'severity', 'element_id', 'elements', 'message') if issue.get(key) is not None}
            for issue in context.get('technical_issues') or []][:40],
        output_schema=SLIDE_SCHEMA,
    )
    return json.dumps(payload, ensure_ascii=False)


def deck_request(context):
    payload = dict(
        task='审查附带的整套缩略图总览，检查跨页面视觉一致性，按 schema 输出 JSON。',
        total=len(context['slides']),
        design_profile=profile_context(context.get('profile')),
        slides=context['slides'],
        output_schema=DECK_SCHEMA,
    )
    return json.dumps(payload, ensure_ascii=False)


def extract_json(text):
    if not isinstance(text, str) or not text.strip():
        raise VisionUnavailable('invalid_json', 'empty model response')
    stripped = text.strip()
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', stripped, re.S)
    if fence:
        stripped = fence.group(1)
    try:
        value = json.loads(stripped)
    except ValueError:
        start, end = stripped.find('{'), stripped.rfind('}')
        if start < 0 or end <= start:
            raise VisionUnavailable('invalid_json', 'model response is not JSON')
        try:
            value = json.loads(stripped[start:end + 1])
        except ValueError as exc:
            raise VisionUnavailable('invalid_json', 'model response is not JSON') from exc
    if not isinstance(value, dict):
        raise VisionUnavailable('invalid_json', 'model response is not a JSON object')
    return value


def _strip_extras(value, schema):
    """Unknown fields (for example a score) are recorded and dropped, never used."""
    ignored = sorted(set(value) - set(schema['properties']))
    cleaned = {key: item for key, item in value.items() if key in schema['properties']}
    item_schema = schema['properties']['issues']['items']
    if isinstance(cleaned.get('issues'), list):
        issues = []
        for issue in cleaned['issues']:
            if isinstance(issue, dict):
                extra = set(issue) - set(item_schema['properties'])
                if extra:
                    ignored.extend('issues.' + key for key in sorted(extra))
                issues.append({key: item for key, item in issue.items() if key in item_schema['properties']})
            else:
                issues.append(issue)
        cleaned['issues'] = issues
    return cleaned, sorted(set(ignored))


def _validate(value, schema):
    cleaned, ignored = _strip_extras(value, schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(cleaned), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise VisionUnavailable('schema_invalid', f'{"/".join(map(str, first.path)) or "root"}: {first.message[:160]}')
    return cleaned, ignored


def _evidence_flags(checks):
    flags = []
    evidence = [item['evidence'].strip() for item in checks.values()]
    weak = [key for key, item in checks.items() if item['verdict'] in ('ok', 'issue') and (len(item['evidence'].strip()) < 6 or GENERIC_EVIDENCE.match(item['evidence'].strip()))]
    if weak:
        flags.append(dict(type='generic_evidence', dimensions=weak))
    if len(set(evidence)) < max(3, len(evidence) // 2):
        flags.append(dict(type='repeated_evidence'))
    return flags


def _decide(model_status, issues, checks, flags):
    blocking = [issue for issue in issues if issue['severity'] in ('critical', 'major') and not issue.get('dismissed')]
    confirmed = [issue for issue in blocking if issue['confidence'] in ('high', 'medium') and not issue.get('needs_verification')]
    if confirmed:
        if model_status == 'pass':
            flags.append(dict(type='status_contradiction', message='model said pass but reported confirmed blocking issues'))
        return 'failed'
    if blocking or model_status == 'uncertain' or any(item['verdict'] == 'uncertain' for item in checks.values()):
        return 'uncertain'
    if any(flag['type'] in ('generic_evidence', 'repeated_evidence') for flag in flags):
        return 'uncertain'
    if model_status == 'fail':
        # A failure claim without a confirmed critical/major issue is not actionable.
        flags.append(dict(type='unsupported_failure', message='model said fail without a confirmed critical or major issue'))
        return 'uncertain'
    return 'passed'


def evaluate_slide(text, slide):
    """Model text -> validated, status-derived review. Raises VisionUnavailable."""
    value, ignored = _validate(extract_json(text), SLIDE_SCHEMA)
    valid_ids = {element['id'] for element in slide.get('elements', [])}
    flags = [dict(type='ignored_fields', fields=ignored)] if ignored else []
    issues, seen = [], set()
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
    flags.extend(_evidence_flags(value['checks']))
    status = _decide(value['status'], issues, value['checks'], flags)
    return dict(status=status, model_status=value['status'], summary=value['summary'], checks=value['checks'], issues=issues, flags=flags)


def evaluate_deck(text, total):
    value, ignored = _validate(extract_json(text), DECK_SCHEMA)
    flags = [dict(type='ignored_fields', fields=ignored)] if ignored else []
    issues, rejected = [], []
    broad = re.compile(r'(重做|重新制作|推翻)(整[个套份]|全部)|redo (the )?(entire|whole)|rebuild (the )?(entire|whole)', re.I)
    for issue in value['issues']:
        pages = sorted(set(issue['slides']))
        if any(page > total for page in pages):
            rejected.append(dict(issue, rejected_reason='slide number outside the deck'))
            continue
        if (total >= 5 and len(pages) > max(3, int(total * .6))) or broad.search(issue['suggestion']):
            rejected.append(dict(issue, rejected_reason='not page-specific; deck review must name concrete pages and local changes'))
            continue
        issues.append(dict(issue, slides=pages))
    if rejected:
        flags.append(dict(type='rejected_issues', count=len(rejected)))
    flags.extend(_evidence_flags(value['checks']))
    status = _decide(value['status'], issues, value['checks'], flags)
    return dict(status=status, model_status=value['status'], summary=value['summary'], checks=value['checks'], issues=issues,
        rejected_issues=rejected, flags=flags)


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
    """Single multimodal request. The callable receives (system, text, images)."""

    def __init__(self, model_id, invoke, timeout=DEFAULT_TIMEOUT):
        self.model_id = model_id
        self._invoke = invoke
        self.timeout = timeout

    def complete(self, system, text, images):
        if not images:
            raise VisionUnavailable('image_missing', 'no image attached to the review request')
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='pptx-vision-call')
        future = executor.submit(self._invoke, system, text, images)
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
    """Adapter for a LangChain chat model; images become base64 image blocks."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.messages.content import create_image_block, create_text_block

    def invoke(system, text, images):
        content = [create_text_block(text)]
        for mime, data in images:
            content.append(create_image_block(base64=base64.b64encode(data).decode('ascii'), mime_type=mime))
        result = chat_model.invoke([SystemMessage(content=system), HumanMessage(content=content)])
        value = getattr(result, 'text', None)
        if not isinstance(value, str):
            value = value() if callable(value) else None
        if not value and isinstance(result.content, str):
            value = result.content
        if not value and isinstance(result.content, list):
            value = ''.join(block.get('text', '') for block in result.content if isinstance(block, dict))
        return value or ''
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
