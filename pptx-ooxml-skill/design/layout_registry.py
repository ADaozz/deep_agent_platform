"""Validated layout descriptions: slots, capacities and variants.

Layouts are declarative JSON. Adding a layout file registers a new page
structure without touching the Agent loop or the renderer. Coordinates are
normalised to the 16:9 canvas and expressed against the reference margin 0.06;
``layout_resolver`` adapts them to the active design profile.
"""
import json
import math
import re
from pathlib import Path

SCHEMA_VERSION = 1
REFERENCE_MARGIN = 0.06
LAYOUT_ID = re.compile(r'[a-z][a-z0-9_]{1,40}\Z')
SLOT_ID = re.compile(r'[a-z][a-z0-9_]{0,30}\Z')
VARIANT_ID = re.compile(r'[0-9a-z]{1,4}\Z')
ACCEPTS = ('text','chart','table','image','component','shape')
ROLES = ('title','statement','body','module','metric','caption','kicker','chart','visual','decoration','footer')
INTENTS = ('cover','section','statement','explain','compare','enumerate','data','evidence','kpi','timeline','architecture','modules')
DENSITIES = ('minimal','balanced','dense')
TOP_KEYS = ('schema','id','version','intent','name','purpose','density','blocks','modules','tags','slots','variants','default_variant')
DEFAULT_DIR = Path(__file__).with_name('layouts')
LEGACY_LAYOUTS = ('cover','section','title_body','comparison','three_columns','data')


class LayoutError(ValueError):
    """Invalid layout description. Never silently repaired."""


def _fail(source, message):
    raise LayoutError(f'{source}: {message}')


def _rect(value, source, slot):
    if not isinstance(value, list) or len(value) != 4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in value):
        _fail(source, f'slot {slot}.rect must be four finite numbers')
    x, y, w, h = (float(v) for v in value)
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
        _fail(source, f'slot {slot}.rect must stay inside the slide canvas')
    return [round(x,6), round(y,6), round(w,6), round(h,6)]


def _validate_slots(slots, source, label='slots'):
    if not isinstance(slots, dict) or not 2 <= len(slots) <= 24:
        _fail(source, f'{label} must contain 2 to 24 named slots')
    result = {}
    for name, slot in slots.items():
        if not isinstance(name, str) or not SLOT_ID.match(name):
            _fail(source, f'{label} has an invalid slot name: {name!r}')
        if not isinstance(slot, dict):
            _fail(source, f'{label}.{name} must be an object')
        unknown = set(slot) - {'rect','accepts','role','style','capacity','absolute','align'}
        if unknown:
            _fail(source, f'{label}.{name} has unknown field(s): ' + ', '.join(sorted(unknown)))
        accepts = slot.get('accepts')
        if not isinstance(accepts, list) or not accepts or any(kind not in ACCEPTS for kind in accepts):
            _fail(source, f'{label}.{name}.accepts must list supported content types')
        role = slot.get('role')
        if role not in ROLES:
            _fail(source, f'{label}.{name}.role is unsupported')
        capacity = slot.get('capacity') or {}
        if not isinstance(capacity, dict) or set(capacity) - {'chars','lines'}:
            _fail(source, f'{label}.{name}.capacity must contain chars and lines')
        chars = capacity.get('chars', 0)
        lines = capacity.get('lines', 0)
        if type(chars) is not int or chars < 0 or type(lines) is not int or lines < 0:
            _fail(source, f'{label}.{name}.capacity must use non-negative integers')
        if 'text' in accepts and chars <= 0:
            _fail(source, f'{label}.{name} accepts text but has no character capacity')
        align = slot.get('align','left')
        if align not in ('left','center','right'):
            _fail(source, f'{label}.{name}.align is unsupported')
        result[name] = dict(rect=_rect(slot.get('rect'), source, name), accepts=list(accepts), role=role,
            style=slot.get('style','body'), capacity=dict(chars=chars, lines=lines),
            absolute=bool(slot.get('absolute', False)), align=align)
    return result


def _modules_in(slots):
    # Metrics are module slots: a KPI row is a set of parallel content cards.
    return [name for name, slot in slots.items() if slot['role'] in ('module','metric')]


def _validate(data, source):
    if not isinstance(data, dict):
        _fail(source, 'layout must be a JSON object')
    unknown = set(data) - set(TOP_KEYS)
    if unknown:
        _fail(source, 'unknown field(s): ' + ', '.join(sorted(unknown)))
    if data.get('schema') != SCHEMA_VERSION:
        _fail(source, f'schema must be {SCHEMA_VERSION}')
    identifier = data.get('id')
    if not isinstance(identifier, str) or not LAYOUT_ID.match(identifier):
        _fail(source, 'id must be a lowercase identifier')
    intent = data.get('intent')
    if intent not in INTENTS:
        _fail(source, 'intent is unsupported: ' + ', '.join(INTENTS))
    density = data.get('density')
    if density not in DENSITIES:
        _fail(source, 'density must be minimal, balanced or dense')
    labels = data.get('name')
    if not isinstance(labels, dict) or not labels.get('en') or not isinstance(labels['en'], str):
        _fail(source, 'name must contain an English label')
    blocks = data.get('blocks') or {}
    if not isinstance(blocks, dict) or set(blocks) != {'min','max'} or not 1 <= blocks['min'] <= blocks['max'] <= 12:
        _fail(source, 'blocks must define min and max between 1 and 12')
    modules = data.get('modules') or {}
    if not isinstance(modules, dict) or set(modules) != {'min','max'} or not 0 <= modules['min'] <= modules['max'] <= 8:
        _fail(source, 'modules must define min and max between 0 and 8')
    slots = _validate_slots(data.get('slots'), source)
    variants = data.get('variants') or {}
    if not isinstance(variants, dict) or len(variants) > 8:
        _fail(source, 'variants must be an object with at most eight entries')
    resolved_variants = {}
    module_count = len(_modules_in(slots))
    if module_count < modules['min'] or module_count > modules['max']:
        _fail(source, f'the default slot set has {module_count} module slots, outside modules.min/max')
    default_variant = data.get('default_variant') or str(module_count)
    if not isinstance(default_variant, str) or not VARIANT_ID.match(default_variant):
        _fail(source, 'default_variant must be a short identifier')
    for key, variant in variants.items():
        if not isinstance(key, str) or not VARIANT_ID.match(key):
            _fail(source, f'variant id is invalid: {key!r}')
        if not isinstance(variant, dict) or set(variant) != {'modules','slots'}:
            _fail(source, f'variant {key} must contain modules and slots')
        count = variant['modules']
        if type(count) is not int or not 1 <= count <= 8:
            _fail(source, f'variant {key}.modules must be an integer between 1 and 8')
        variant_slots = _validate_slots(variant['slots'], source, 'variants.' + key + '.slots')
        if len(_modules_in(variant_slots)) != count:
            _fail(source, f'variant {key} declares {count} modules but defines {len(_modules_in(variant_slots))}')
        if str(count) != key:
            _fail(source, f'variant {key} must be keyed by its module count')
        resolved_variants[key] = dict(modules=count, slots=variant_slots)
    if resolved_variants and default_variant in resolved_variants:
        _fail(source, 'default_variant must not repeat a variant entry; top-level slots are the default')
    return dict(schema=SCHEMA_VERSION, id=identifier, version=str(data.get('version','1.0.0')),
        intent=intent, name={key: str(value) for key, value in labels.items()},
        purpose=str(data.get('purpose','')), density=density, tags=[str(tag) for tag in data.get('tags',[])],
        blocks=dict(min=int(blocks['min']), max=int(blocks['max'])),
        modules=dict(min=int(modules['min']), max=int(modules['max'])),
        slots=slots, default_variant=default_variant, default_modules=module_count,
        variants=resolved_variants, module_counts=sorted({module_count, *[v['modules'] for v in resolved_variants.values()]}))


class LayoutRegistry:
    """Immutable layout catalogue with deterministic variant selection."""

    def __init__(self, directory=None):
        self.directory = Path(directory or DEFAULT_DIR)
        self.layouts = {}
        for path in sorted(self.directory.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise LayoutError(f'{path.name}: unreadable layout ({exc})') from exc
            layout = _validate(data, path.name)
            if path.stem != layout['id']:
                raise LayoutError(f'{path.name}: id must match the file name')
            if layout['id'] in self.layouts:
                raise LayoutError('Duplicate layout id: ' + layout['id'])
            self.layouts[layout['id']] = layout
        if not self.layouts:
            raise LayoutError('No layouts found in ' + str(self.directory))
        missing = [name for name in LEGACY_LAYOUTS if name not in self.layouts]
        if missing:
            raise LayoutError('Legacy layouts must remain available: ' + ', '.join(missing))

    def ids(self):
        return sorted(self.layouts)

    def get(self, layout_id):
        if not isinstance(layout_id, str) or layout_id not in self.layouts:
            raise LayoutError(f'Unknown layout: {layout_id!r}. Available: ' + ', '.join(self.ids()))
        return self.layouts[layout_id]

    def variant_for(self, layout_id, modules=None):
        layout = self.get(layout_id)
        if modules is None or modules == layout['default_modules']:
            return layout['default_variant'], layout['slots']
        key = str(modules)
        if key in layout['variants']:
            return key, layout['variants'][key]['slots']
        return None, None

    def slot_for(self, layout_id, modules, name):
        key, slots = self.variant_for(layout_id, modules)
        if slots is None or name not in slots:
            raise LayoutError(f'Layout {layout_id!r} has no slot {name!r} for {modules!r} modules')
        return key, slots[name]

    def capacity_error(self, layout_id, modules, name, content):
        """Return a human readable capacity error, or None when content fits."""
        _, slot = self.slot_for(layout_id, modules, name)
        kind = content.get('type')
        if kind not in slot['accepts']:
            return f'{name} accepts {", ".join(slot["accepts"])}, not {kind}'
        if kind == 'text':
            chars = len(content.get('text',''))
            lines = content.get('text','').count('\n') + 1
            if chars > slot['capacity']['chars'] or lines > slot['capacity']['lines']:
                return (f'{name} holds {slot["capacity"]["chars"]} characters / {slot["capacity"]["lines"]} lines, '
                        f'content needs {chars} / {lines}; split the content or choose a roomier layout')
        return None

    def summary(self):
        return [dict(id=layout['id'], intent=layout['intent'], name=layout['name'], density=layout['density'],
            modules=layout['module_counts'], blocks=layout['blocks'], tags=layout['tags']) for layout in self.layouts.values()]


_CACHE = {}


def load_layouts(directory=None):
    key = str(Path(directory or DEFAULT_DIR).resolve())
    if key not in _CACHE:
        _CACHE[key] = LayoutRegistry(directory)
    return _CACHE[key]
