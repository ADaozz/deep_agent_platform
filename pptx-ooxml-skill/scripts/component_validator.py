#!/usr/bin/env python3
"""Validation for the reviewed SVG component library.

Components are static, reviewed assets. This module enforces that a component
can only be parameterised through its declared schema, that its SVG is free of
scripts, external references and embedded text, and that its preview and source
files stay inside the component library.

Nothing here executes user drawing code or fetches network resources.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from design import profile_registry  # noqa: E402

COMPONENT_ROOT = ROOT / 'design' / 'components'
PREVIEW_ROOT = ROOT / 'design' / 'previews'
REGISTRY_FILE = COMPONENT_ROOT / 'registry.json'
COMPONENT_ID = re.compile(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z')
VERSION = re.compile(r'\d+\.\d+\.\d+\Z')
PLACEHOLDER = re.compile(r'\{\{([a-zA-Z0-9_:.\-]+)\}\}')
SVG_NS = 'http://www.w3.org/2000/svg'
FORBIDDEN_TAGS = {'script','foreignobject','image','use','iframe','audio','video','animate','animatetransform',
                  'animatemotion','set','filter','mask','style','pattern','symbol','marker','cursor','switch'}
SERIES_TOKENS = tuple(f'series{index}' for index in range(1, 9))
ALLOWED_TOKENS = tuple(profile_registry.REQUIRED_COLORS) + SERIES_TOKENS
TOP_KEYS = ('schema','version','canvas','license','previews','compatibility','safety','editing','components')
ENTRY_KEYS = ('component_id','version','name','category','intents','tags','canvas','aspect_ratio','min_size_in','layer',
              'baked_text','tokens','params','variant_param','variants','usage')
CATEGORIES = ('process','architecture','bento','infographic','illustration','icons')
LAYERS = ('content','background','decoration')
MAX_SVG_BYTES = 262144


class ComponentError(ValueError):
    """Invalid component source, registry entry or parameter call."""


def _fail(source, message):
    raise ComponentError(f'{source}: {message}')


def _hex(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9A-Fa-f]{6}', value))


def validate_svg(data, source='svg', tokens=(), params=(), baked_text=False):
    """Parse and security-check one SVG source or rendered instance."""
    if not isinstance(data, (bytes, bytearray)):
        _fail(source, 'SVG must be bytes')
    if len(data) > MAX_SVG_BYTES:
        _fail(source, f'SVG exceeds {MAX_SVG_BYTES} bytes')
    try:
        text = bytes(data).decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ComponentError(f'{source}: SVG must be UTF-8') from exc
    upper = text.upper()
    if '<!DOCTYPE' in upper or '<!ENTITY' in upper or '<![' in upper:
        _fail(source, 'DTD, entities and CDATA wrappers are forbidden')
    if '{' in PLACEHOLDER.sub('', text) or '}' in PLACEHOLDER.sub('', text):
        _fail(source, 'unresolved or malformed placeholder braces')
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=False)
    try:
        root = etree.fromstring(bytes(data), parser)
    except etree.XMLSyntaxError as exc:
        raise ComponentError(f'{source}: SVG is not well-formed XML') from exc
    if etree.QName(root).localname != 'svg' or etree.QName(root).namespace != SVG_NS:
        _fail(source, 'root element must be an SVG in the SVG namespace')
    width = root.get('width'); height = root.get('height'); view = root.get('viewBox')
    try:
        box = [float(value) for value in re.split(r'[ ,]+', view.strip())] if view else []
        size = float(width), float(height)
    except (TypeError, ValueError):
        _fail(source, 'SVG needs numeric width, height and viewBox')
    if len(box) != 4 or any(not math.isfinite(value) for value in box + list(size)) or min(*size) <= 0 or min(box[2], box[3]) <= 0:
        _fail(source, 'SVG needs a positive width, height and viewBox')
    allowed = set(tokens) | {f'c:{name}' for name in params} | {f'p:{name}' for name in params} | {f'px:{name}' for name in params}
    remaining = PLACEHOLDER.findall(text)
    unknown = sorted({name for name in remaining if name not in allowed})
    if unknown:
        _fail(source, 'undeclared placeholder(s): ' + ', '.join(unknown))
    elements = 0
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node).localname
        elements += 1
        if tag.lower() in FORBIDDEN_TAGS:
            _fail(source, 'forbidden SVG element: ' + tag)
        if tag == 'text' and not baked_text:
            _fail(source, 'SVG must not embed text; text belongs to native editable elements')
        for key, value in node.attrib.items():
            name = etree.QName(key).localname
            if name.lower().startswith('on'):
                _fail(source, 'event handler attributes are forbidden')
            if name in ('href','src') or key.endswith('}href'):
                _fail(source, 'external references are forbidden')
            lowered = str(value).lower()
            if any(token in lowered for token in ('http://','https://','file:','data:','//','@import','expression','url(')):
                _fail(source, 'external or dynamic references are forbidden')
        if tag == 'style':
            _fail(source, 'inline style blocks are forbidden')
    if elements > 3000:
        _fail(source, 'SVG has too many elements')
    return root


def _relative(value, base, source, suffix):
    if not isinstance(value, str) or not value.endswith(suffix) or value.startswith('/') or '\\' in value or '..' in Path(value).parts:
        _fail(source, f'path must be a relative {suffix} inside the library')
    path = (base / value).resolve()
    if not path.is_relative_to(base.resolve()):
        _fail(source, 'path escapes the component library')
    return path


def _text_slots(slots, canvas, source):
    if not isinstance(slots, dict) or len(slots) > 16:
        _fail(source, 'text_slots must be an object with at most 16 slots')
    result = {}
    for name, slot in slots.items():
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,20}', name):
            _fail(source, 'text slot names must be lowercase identifiers')
        if not isinstance(slot, dict) or set(slot) - {'rect','style','chars','lines','align'}:
            _fail(source, f'text slot {name} has unsupported fields')
        rect = slot.get('rect')
        if not isinstance(rect, list) or len(rect) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) for value in rect):
            _fail(source, f'text slot {name}.rect must be four finite numbers')
        x, y, w, h = (float(value) for value in rect)
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > canvas['width'] + .001 or y + h > canvas['height'] + .001:
            _fail(source, f'text slot {name}.rect must stay inside the component canvas')
        style = slot.get('style')
        if style not in profile_registry.REQUIRED_TOKENS:
            _fail(source, f'text slot {name}.style must be a profile type token')
        chars = slot.get('chars'); lines = slot.get('lines')
        if type(chars) is not int or not 1 <= chars <= 160 or type(lines) is not int or not 1 <= lines <= 8:
            _fail(source, f'text slot {name} needs bounded chars and lines')
        align = slot.get('align','left')
        if align not in ('left','center','right'):
            _fail(source, f'text slot {name}.align is unsupported')
        result[name] = dict(rect=[round(x, 2), round(y, 2), round(w, 2), round(h, 2)], style=style, chars=chars, lines=lines, align=align)
    return result


def _params(schema, source):
    if not isinstance(schema, dict) or len(schema) > 8:
        _fail(source, 'params must be an object with at most eight parameters')
    result = {}
    for name, param in schema.items():
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,20}', name):
            _fail(source, 'parameter names must be lowercase identifiers')
        if not isinstance(param, dict):
            _fail(source, f'parameter {name} must be an object')
        kind = param.get('type')
        if kind == 'integer':
            if set(param) - {'type','min','max','default','scale'}:
                _fail(source, f'parameter {name} has unsupported fields')
            low, high, default = param.get('min'), param.get('max'), param.get('default')
            if type(low) is not int or type(high) is not int or low > high or type(default) is not int or not low <= default <= high:
                _fail(source, f'parameter {name} needs integer min, max and default')
            item = dict(type='integer', min=low, max=high, default=default)
            if 'scale' in param:
                scale = param['scale']
                if not isinstance(scale, dict) or set(scale) != {'extent','offset','min_px'}:
                    _fail(source, f'parameter {name}.scale must define extent, offset and min_px')
                if any(type(scale[key]) is not int for key in scale) or scale['extent'] <= 0 or scale['min_px'] < 0:
                    _fail(source, f'parameter {name}.scale values must be bounded integers')
                item['scale'] = dict(scale)
            result[name] = item
        elif kind == 'enum':
            if set(param) - {'type','values','default'}:
                _fail(source, f'parameter {name} has unsupported fields')
            values = param.get('values')
            if not isinstance(values, list) or not 2 <= len(values) <= 12 or any(type(value) not in (int, str) for value in values) or len(set(map(str, values))) != len(values):
                _fail(source, f'parameter {name}.values must list 2 to 12 unique scalars')
            if param.get('default') not in values:
                _fail(source, f'parameter {name}.default must be one of its values')
            result[name] = dict(type='enum', values=list(values), default=param['default'])
        elif kind == 'color_token':
            if set(param) - {'type','default'} or param.get('default') not in profile_registry.REQUIRED_COLORS:
                _fail(source, f'parameter {name} must default to a colour token')
            result[name] = dict(type='color_token', default=param['default'])
        else:
            _fail(source, f'parameter {name} has unsupported type')
    return result


def _entry(entry, root, require_previews):
    if not isinstance(entry, dict):
        _fail('registry', 'each component must be an object')
    identifier = entry.get('component_id')
    if not isinstance(identifier, str) or not COMPONENT_ID.match(identifier):
        _fail('registry', 'component_id must look like category.name')
    source = identifier
    unknown = set(entry) - set(ENTRY_KEYS)
    if unknown:
        _fail(source, 'unknown field(s): ' + ', '.join(sorted(unknown)))
    missing = set(ENTRY_KEYS) - set(entry)
    if missing:
        _fail(source, 'missing field(s): ' + ', '.join(sorted(missing)))
    if not VERSION.match(str(entry['version'])):
        _fail(source, 'version must look like 1.0.0')
    category = entry['category']
    if category not in CATEGORIES or not identifier.startswith(category + '.'):
        _fail(source, 'category must match the component id prefix')
    names = entry['name']
    if not isinstance(names, dict) or set(names) != {'en','zh'} or any(not isinstance(value, str) or not value.strip() for value in names.values()):
        _fail(source, 'name must contain English and Chinese labels')
    if not isinstance(entry['intents'], list) or not 1 <= len(entry['intents']) <= 8 or any(not isinstance(value, str) or not value for value in entry['intents']):
        _fail(source, 'intents must list 1 to 8 semantic intents')
    if not isinstance(entry['tags'], list) or len(entry['tags']) > 10 or any(not isinstance(value, str) for value in entry['tags']):
        _fail(source, 'tags must be a bounded list of strings')
    canvas = entry['canvas']
    if not isinstance(canvas, dict) or set(canvas) != {'width','height'} or any(type(canvas[key]) is not int or not 16 <= canvas[key] <= 4096 for key in ('width','height')):
        _fail(source, 'canvas must define integer width and height between 16 and 4096')
    aspect = canvas['width'] / canvas['height']
    if type(entry['aspect_ratio']) not in (int, float) or abs(float(entry['aspect_ratio']) - aspect) > .002:
        _fail(source, 'aspect_ratio must match the canvas')
    minimum = entry['min_size_in']
    if not isinstance(minimum, dict) or set(minimum) != {'w','h'} or any(type(minimum[key]) not in (int, float) or minimum[key] <= 0 for key in ('w','h')):
        _fail(source, 'min_size_in must define positive w and h')
    if abs(minimum['w'] / minimum['h'] - aspect) > aspect * .03:
        _fail(source, 'min_size_in must keep the component aspect ratio')
    if entry['layer'] not in LAYERS:
        _fail(source, 'layer must be content, background or decoration')
    if entry['baked_text'] is not False:
        _fail(source, 'this library keeps all text native; baked_text must be false')
    tokens = entry['tokens']
    if not isinstance(tokens, list) or not tokens or any(token not in ALLOWED_TOKENS for token in tokens):
        _fail(source, 'tokens must list supported colour tokens')
    params = _params(entry['params'], source)
    variant_param = entry['variant_param']
    if variant_param is not None:
        if variant_param not in params or params[variant_param]['type'] not in ('enum','integer'):
            _fail(source, 'variant_param must name an enum or integer parameter')
    variants = entry['variants']
    if not isinstance(variants, dict) or not 1 <= len(variants) <= 8:
        _fail(source, 'variants must contain 1 to 8 entries')
    if variant_param is None and set(variants) != {'default'}:
        _fail(source, 'components without a variant parameter must use the single default variant')
    if variant_param is not None:
        schema = params[variant_param]
        if schema['type'] == 'enum':
            expected = {str(value) for value in schema['values']}
            if expected != set(variants):
                _fail(source, 'variant keys must match the parameter values')
    resolved = {}
    for key, variant in variants.items():
        if not isinstance(variant, dict) or set(variant) != {'svg','preview','text_slots'}:
            _fail(source, f'variant {key} must define svg, preview and text_slots')
        svg_path = _relative(variant['svg'], root, source, '.svg')
        preview_path = _relative(variant['preview'], ROOT / 'design' / 'previews', source, '.png')
        if not svg_path.is_file():
            _fail(source, f'missing SVG source: {variant["svg"]}')
        if require_previews and not preview_path.is_file():
            _fail(source, f'missing reviewed preview: {variant["preview"]}')
        data = svg_path.read_bytes()
        slots = _text_slots(variant['text_slots'], canvas, source + ':' + key)
        svg_root = validate_svg(data, source + ':' + key, tokens, tuple(params), False)
        if abs(float(svg_root.get('width')) - canvas['width']) > .5 or abs(float(svg_root.get('height')) - canvas['height']) > .5:
            _fail(source, 'SVG width and height must match the declared component canvas')
        lowered = data.decode('utf-8')
        for name in PLACEHOLDER.findall(lowered):
            if name in profile_registry.REQUIRED_COLORS and name not in tokens:
                _fail(source, f'placeholder {name} is used but not declared in tokens')
            if name in SERIES_TOKENS and name not in tokens:
                _fail(source, f'placeholder {name} is used but not declared in tokens')
        if require_previews:
            header = preview_path.read_bytes()[:8]
            if header != b'\x89PNG\r\n\x1a\n' or preview_path.stat().st_size > 2 * 1024 * 1024:
                _fail(source, f'preview {variant["preview"]} must be a bounded PNG')
        resolved[key] = dict(svg=str(variant['svg']), preview=str(variant['preview']), text_slots=slots,
            svg_sha256=hashlib.sha256(data).hexdigest())
    return dict(component_id=identifier, version=str(entry['version']), name=dict(names), category=category,
        intents=list(entry['intents']), tags=list(entry['tags']), canvas=dict(canvas), aspect_ratio=aspect,
        min_size_in=dict(w=float(minimum['w']), h=float(minimum['h'])), layer=entry['layer'], baked_text=False,
        tokens=list(tokens), params=params, variant_param=variant_param, variants=resolved,
        usage=str(entry.get('usage','')))


def validate_registry(data, root=None, require_previews=True):
    root = Path(root or COMPONENT_ROOT)
    if not isinstance(data, dict):
        _fail('registry', 'registry must be a JSON object')
    unknown = set(data) - set(TOP_KEYS)
    if unknown:
        _fail('registry', 'unknown field(s): ' + ', '.join(sorted(unknown)))
    if data.get('schema') != 1 or not VERSION.match(str(data.get('version',''))):
        _fail('registry', 'schema must be 1 and version must look like 1.0.0')
    canvas = data.get('canvas') or {}
    if not isinstance(canvas, dict) or set(canvas) != {'width','height'}:
        _fail('registry', 'canvas must define width and height')
    compatibility = data.get('compatibility') or {}
    if not isinstance(compatibility, dict) or any(not isinstance(value, str) for value in compatibility.values()):
        _fail('registry', 'compatibility must describe the reviewed renderer/fallback targets')
    safety = data.get('safety') or {}
    if not isinstance(safety, dict) or safety.get('validator') != 'component_validator.validate_svg':
        _fail('registry', 'safety must record the validator that checked the sources')
    if any(safety.get(key) is not False for key in ('external_references','scripts','embedded_text','embedded_media')):
        _fail('registry', 'safety flags must confirm no external references, scripts, embedded text or media')
    editing = data.get('editing') or {}
    if not isinstance(editing, dict) or not {'text','graphics'} <= set(editing) or any(not isinstance(value, str) or not value for value in editing.values()):
        _fail('registry', 'editing must explain which parts stay native and which are fixed graphics')
    entries = data.get('components')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 200:
        _fail('registry', 'components must list 1 to 200 reviewed components')
    components = {}
    for entry in entries:
        resolved = _entry(entry, root, require_previews)
        if resolved['component_id'] in components:
            _fail('registry', 'duplicate component_id: ' + resolved['component_id'])
        components[resolved['component_id']] = resolved
    return dict(schema=1, version=str(data['version']), canvas=dict(canvas), license=str(data.get('license','')),
        previews=str(data.get('previews','previews')), compatibility=dict(compatibility), safety=dict(safety),
        editing=dict(editing), components=components)


_CACHE = {}


def load_registry(directory=None, require_previews=True):
    root = Path(directory or COMPONENT_ROOT)
    key = (str(root.resolve()), bool(require_previews))
    if key in _CACHE:
        return _CACHE[key]
    try:
        data = json.loads((root / 'registry.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ComponentError('Component registry is unreadable: ' + str(exc)) from exc
    registry = validate_registry(data, root, require_previews)
    _CACHE[key] = registry
    return registry


def get(registry, component_id):
    if not isinstance(component_id, str) or component_id not in registry['components']:
        raise ComponentError(f'Unknown component: {component_id!r}. Use catalog to list reviewed components')
    return registry['components'][component_id]


def validate_call(entry, params=None, texts=None, version=None):
    """Validate a component call; returns variant key, variant, params, texts."""
    if version is not None and str(version) != entry['version']:
        raise ComponentError(f'Component {entry["component_id"]} version {version} is not available; library provides {entry["version"]}')
    params = params if params is not None else {}
    if not isinstance(params, dict):
        raise ComponentError('params must be an object')
    unknown = sorted(set(params) - set(entry['params']))
    if unknown:
        raise ComponentError('Unknown parameter(s) for ' + entry['component_id'] + ': ' + ', '.join(unknown) + '; allowed: ' + (', '.join(entry['params']) or 'none'))
    filled = {}
    for name, schema in entry['params'].items():
        value = params.get(name, schema['default'])
        if schema['type'] == 'integer':
            if type(value) is not int or not schema['min'] <= value <= schema['max']:
                raise ComponentError(f'Parameter {name} must be an integer between {schema["min"]} and {schema["max"]}')
        elif schema['type'] == 'enum':
            if value not in schema['values']:
                raise ComponentError(f'Parameter {name} must be one of ' + ', '.join(map(str, schema['values'])))
        elif schema['type'] == 'color_token':
            if value not in profile_registry.REQUIRED_COLORS:
                raise ComponentError(f'Parameter {name} must be a colour token')
        filled[name] = value
    key = str(filled[entry['variant_param']]) if entry['variant_param'] else 'default'
    if key not in entry['variants']:
        raise ComponentError(f'No reviewed variant {key} for {entry["component_id"]}')
    variant = entry['variants'][key]
    texts = texts if texts is not None else {}
    if not isinstance(texts, dict):
        raise ComponentError('texts must be an object')
    unknown = sorted(set(texts) - set(variant['text_slots']))
    if unknown:
        raise ComponentError('Unknown text slot(s) for ' + entry['component_id'] + ': ' + ', '.join(unknown) + '; allowed: ' + (', '.join(variant['text_slots']) or 'none'))
    normalized = {}
    for name, text in texts.items():
        slot = variant['text_slots'][name]
        if not isinstance(text, str) or not text.strip() or len(text) > slot['chars'] or text.count('\n') + 1 > slot['lines']:
            raise ComponentError(f'Text slot {name} allows {slot["chars"]} characters in {slot["lines"]} line(s); content does not fit')
        normalized[name] = text
    return key, variant, filled, normalized


def minimum_size(entry):
    return dict(entry['min_size_in'])


def _scale_param(schema, value):
    if 'scale' not in schema:
        return str(value)
    scale = schema['scale']
    span = schema['max'] - schema['min']
    ratio = (value - schema['min']) / span if span else 0
    pixels = scale['offset'] + round(ratio * scale['extent'])
    return str(max(scale['min_px'], int(pixels)))


def render_svg(entry, variant, params, colors):
    """Substitute declared tokens only, then security-check the result."""
    source = (COMPONENT_ROOT / variant['svg']).read_bytes().decode('utf-8')
    for name in PLACEHOLDER.findall(source):
        if ':' in name:
            kind, parameter = name.split(':', 1)
            schema = entry['params'].get(parameter)
            if schema is None:
                raise ComponentError('Undeclared placeholder parameter: ' + parameter)
            if kind == 'c':
                value = colors.get(params[parameter])
                if value is None:raise ComponentError('No profile colour for token: ' + str(params[parameter]))
            elif kind == 'p':
                value = str(params[parameter])
            elif kind == 'px':
                value = _scale_param(schema, params[parameter])
            else:
                raise ComponentError('Unsupported placeholder kind: ' + kind)
        else:
            if name not in entry['tokens']:
                raise ComponentError('Unknown colour token in component: ' + name)
            value = colors.get(name)
            if value is None:raise ComponentError('No profile colour for token: ' + name)
        source = source.replace('{{' + name + '}}', value)
    if PLACEHOLDER.search(source):
        raise ComponentError('Unresolved placeholder in ' + entry['component_id'])
    data = source.encode('utf-8')
    validate_svg(data, entry['component_id'], tuple(entry['tokens']), tuple(entry['params']), entry['baked_text'])
    return data, hashlib.sha256(data).hexdigest()


def rasterize(svg_bytes, width_px=1600):
    """Deterministic SVG to PNG conversion through MuPDF."""
    if type(width_px) is not int or not 128 <= width_px <= 3200:
        raise ComponentError('raster width must be an integer between 128 and 3200')
    import pymupdf
    from PIL import Image
    document = pymupdf.open(stream=svg_bytes, filetype='svg')
    if document.page_count != 1:
        raise ComponentError('Component SVG must render exactly one page')
    page = document[0]
    matrix = pymupdf.Matrix(width_px / page.rect.width, width_px / page.rect.width)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    image = Image.open(io.BytesIO(pixmap.tobytes('png'))).convert('RGB')
    out = io.BytesIO()
    image.save(out, format='PNG', optimize=True)
    document.close()
    return out.getvalue()


def palette_for(registry_profiles, profile_id):
    """Colour map for SVG substitution; tokens are stored without '#'."""
    profile = registry_profiles.get(profile_id)
    colors = {name: '#' + value for name, value in profile['colors'].items() if _hex(value)}
    for index, value in enumerate(profile['colors']['chart_series'], 1):
        colors[f'series{index}'] = '#' + value
    return colors
