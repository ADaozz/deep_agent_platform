"""Validated, extensible design profiles.

A profile is configuration only: colours, type scale, spacing, density and the
policy for layouts/components. It never contains page content. Adding a profile
means dropping one JSON file into ``design/profiles``; no Agent or engine code
changes are required.

Use ``load_registry()`` for the default directory, or ``ProfileRegistry(path)``
for tests and future user-provided profile bundles.
"""
import copy
import json
import math
import re
from pathlib import Path

SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r'[a-z][a-z0-9_]{1,40}\Z')
VERSION_PATTERN = re.compile(r'\d+\.\d+\.\d+\Z')
HEX_PATTERN = re.compile(r'[0-9A-Fa-f]{6}\Z')
REQUIRED_COLORS = ('background','surface','text','muted','accent','accent_soft','card','border','highlight','positive','negative','warning')
REQUIRED_TOKENS = ('cover_title','section_title','slide_title','statement','card_title','body','body_small','caption','kicker','metric','metric_label','table_header','table_cell')
DENSITY_LEVELS = ('minimal','balanced','dense')
LEGEND_POSITIONS = ('bottom','right','top','none')
TOP_KEYS = ('schema','id','version','name','label','description','extends','colors','fonts','type_scale','spacing','shape','density','charts','tables','layouts','components','quality')
DEFAULT_PROFILES = Path(__file__).with_name('profiles')
DEFAULT_PROFILE = 'corporate_blue'


class ProfileError(ValueError):
    """Invalid profile definition. Never silently repaired."""


def _fail(source, message):
    raise ProfileError(f'{source}: {message}')


def _number(value, source, field, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        _fail(source, f'{field} must be a finite number in [{low}, {high}]')
    return float(value)


def _boolean(value, source, field):
    if type(value) is not bool:
        _fail(source, f'{field} must be a boolean')
    return value


def _text(value, source, field, maximum=240):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail(source, f'{field} must be a non-empty string of at most {maximum} characters')
    return value.strip()


def _hex(value, source, field):
    if not isinstance(value, str) or not HEX_PATTERN.match(value):
        _fail(source, f'{field} must be a six digit RGB hex colour')
    return value.upper()


def _identifier(value, source, field):
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        _fail(source, f'{field} must be a lowercase identifier')
    return value


def _sequence(value, source, field, maximum=12):
    if not isinstance(value, list) or not value or len(value) > maximum or any(not isinstance(item,str) or len(item)>60 for item in value):
        _fail(source, f'{field} must be a list of at most {maximum} short strings')
    return list(value)


def deep_merge(base, patch):
    """Deterministic token-level merge used by ``extends``."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(base)
    for key, value in patch.items():
        result[key] = deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def _validate_colors(colors, source):
    if not isinstance(colors, dict):
        _fail(source, 'colors must be an object')
    unknown = set(colors) - set(REQUIRED_COLORS) - {'chart_series'}
    if unknown:
        _fail(source, 'unknown colour token(s): ' + ', '.join(sorted(unknown)))
    missing = [name for name in REQUIRED_COLORS if name not in colors]
    if missing:
        _fail(source, 'missing colour token(s): ' + ', '.join(missing))
    result = {name: _hex(colors[name], source, 'colors.' + name) for name in REQUIRED_COLORS}
    series = colors.get('chart_series')
    if not isinstance(series, list) or not 4 <= len(series) <= 8:
        _fail(source, 'colors.chart_series must list 4 to 8 colours')
    result['chart_series'] = [_hex(item, source, 'colors.chart_series') for item in series]
    return result


def _validate_type_scale(scale, colors, source):
    if not isinstance(scale, dict):
        _fail(source, 'type_scale must be an object')
    unknown = set(scale) - set(REQUIRED_TOKENS)
    if unknown:
        _fail(source, 'unknown type token(s): ' + ', '.join(sorted(unknown)))
    missing = [name for name in REQUIRED_TOKENS if name not in scale]
    if missing:
        _fail(source, 'missing type token(s): ' + ', '.join(missing))
    result = {}
    for token in REQUIRED_TOKENS:
        value = scale[token]
        if not isinstance(value, dict):
            _fail(source, f'type_scale.{token} must be an object')
        unknown = set(value) - {'size','bold','color_token','line_height','letter_spacing','font'}
        if unknown:
            _fail(source, f'type_scale.{token} has unknown field(s): ' + ', '.join(sorted(unknown)))
        item = dict(size=_number(value.get('size'), source, f'type_scale.{token}.size', 6, 200),
            bold=_boolean(value.get('bold'), source, f'type_scale.{token}.bold'),
            color_token=value.get('color_token'), line_height=_number(value.get('line_height'), source, f'type_scale.{token}.line_height', .8, 2.2),
            font=value.get('font','body'))
        if item['color_token'] not in colors:
            _fail(source, f'type_scale.{token}.color_token must reference a colour token')
        if item['font'] not in ('title','body'):
            _fail(source, f'type_scale.{token}.font must be title or body')
        if value.get('letter_spacing') is not None:
            item['letter_spacing'] = _number(value['letter_spacing'], source, f'type_scale.{token}.letter_spacing', 0, 6)
        result[token] = item
    return result


def _validate(profile, source):
    if not isinstance(profile, dict):
        _fail(source, 'profile must be a JSON object')
    unknown = set(profile) - set(TOP_KEYS)
    if unknown:
        _fail(source, 'unknown field(s): ' + ', '.join(sorted(unknown)))
    if profile.get('schema') != SCHEMA_VERSION:
        _fail(source, f'schema must be {SCHEMA_VERSION}')
    identifier = _identifier(profile.get('id'), source, 'id')
    if not VERSION_PATTERN.match(str(profile.get('version',''))):
        _fail(source, 'version must look like 1.0.0')
    if profile.get('extends') is not None:
        _identifier(profile['extends'], source, 'extends')
    label = profile.get('label') or {}
    if not isinstance(label, dict) or set(label) - {'zh','en'} or not label:
        _fail(source, 'label must contain zh and/or en')
    result = dict(schema=SCHEMA_VERSION, id=identifier, version=str(profile['version']),
        name=_text(profile.get('name'), source, 'name', 60),
        label={key: _text(value, source, 'label.' + key, 60) for key, value in (label.items() if label else [('en',profile.get('name'))])},
        description=_text(profile.get('description'), source, 'description', 280),
        extends=profile.get('extends'))
    colors = _validate_colors(profile.get('colors'), source)
    result['colors'] = colors
    fonts = profile.get('fonts')
    if not isinstance(fonts, dict) or set(fonts) - {'title','body','fallback'}:
        _fail(source, 'fonts must contain title, body and optional fallback')
    result['fonts'] = dict(title=_text(fonts.get('title'), source, 'fonts.title', 80),
        body=_text(fonts.get('body'), source, 'fonts.body', 80),
        fallback=_sequence(fonts.get('fallback',[]), source, 'fonts.fallback', 6) if fonts.get('fallback') is not None else [])
    result['type_scale'] = _validate_type_scale(profile.get('type_scale'), colors, source)
    spacing = profile.get('spacing')
    if not isinstance(spacing, dict) or set(spacing) != {'margin_x','margin_y','gutter','card_padding','footer_y'}:
        _fail(source, 'spacing must define margin_x, margin_y, gutter, card_padding and footer_y')
    result['spacing'] = dict(margin_x=_number(spacing['margin_x'], source, 'spacing.margin_x', 0, .2),
        margin_y=_number(spacing['margin_y'], source, 'spacing.margin_y', 0, .2),
        gutter=_number(spacing['gutter'], source, 'spacing.gutter', 0, .1),
        card_padding=_number(spacing['card_padding'], source, 'spacing.card_padding', 0, .1),
        footer_y=_number(spacing['footer_y'], source, 'spacing.footer_y', .8, .98))
    shape = profile.get('shape')
    if not isinstance(shape, dict) or set(shape) != {'corner_radius_in','line_width_pt','shadow','accent_rule'}:
        _fail(source, 'shape must define corner_radius_in, line_width_pt, shadow and accent_rule')
    result['shape'] = dict(corner_radius_in=_number(shape['corner_radius_in'], source, 'shape.corner_radius_in', 0, .5),
        line_width_pt=_number(shape['line_width_pt'], source, 'shape.line_width_pt', 0, 6),
        shadow=_boolean(shape['shadow'], source, 'shape.shadow'), accent_rule=_boolean(shape['accent_rule'], source, 'shape.accent_rule'))
    density = profile.get('density')
    if not isinstance(density, dict) or set(density) != {'level','max_bullets','max_modules','max_chars_per_line'}:
        _fail(source, 'density must define level, max_bullets, max_modules and max_chars_per_line')
    if density['level'] not in DENSITY_LEVELS:
        _fail(source, 'density.level must be minimal, balanced or dense')
    result['density'] = dict(level=density['level'],
        max_bullets=int(_number(density['max_bullets'], source, 'density.max_bullets', 1, 12)),
        max_modules=int(_number(density['max_modules'], source, 'density.max_modules', 1, 8)),
        max_chars_per_line=int(_number(density['max_chars_per_line'], source, 'density.max_chars_per_line', 20, 80)))
    charts = profile.get('charts')
    if not isinstance(charts, dict) or set(charts) != {'legend_position','gridlines','label_size','gap_width','line_width_pt'}:
        _fail(source, 'charts must define legend_position, gridlines, label_size, gap_width and line_width_pt')
    if charts['legend_position'] not in LEGEND_POSITIONS:
        _fail(source, 'charts.legend_position is unsupported')
    result['charts'] = dict(legend_position=charts['legend_position'], gridlines=_boolean(charts['gridlines'], source, 'charts.gridlines'),
        label_size=_number(charts['label_size'], source, 'charts.label_size', 8, 20),
        gap_width=_number(charts['gap_width'], source, 'charts.gap_width', 20, 150),
        line_width_pt=_number(charts['line_width_pt'], source, 'charts.line_width_pt', 1, 6))
    tables = profile.get('tables')
    if not isinstance(tables, dict) or set(tables) != {'header_fill_token','header_text_token','border_token','font_size','zebra'}:
        _fail(source, 'tables must define header_fill_token, header_text_token, border_token, font_size and zebra')
    for field in ('header_fill_token','header_text_token','border_token'):
        if tables[field] not in colors:
            _fail(source, f'tables.{field} must reference a colour token')
    result['tables'] = dict(header_fill_token=tables['header_fill_token'], header_text_token=tables['header_text_token'],
        border_token=tables['border_token'], font_size=_number(tables['font_size'], source, 'tables.font_size', 8, 20),
        zebra=_boolean(tables['zebra'], source, 'tables.zebra'))
    layouts = profile.get('layouts')
    if not isinstance(layouts, dict) or set(layouts) != {'allowed','preferred'}:
        _fail(source, 'layouts must define allowed and preferred')
    allowed = layouts['allowed']
    if allowed != '*':
        _sequence(allowed, source, 'layouts.allowed', 40)
    result['layouts'] = dict(allowed=allowed, preferred=_sequence(layouts['preferred'], source, 'layouts.preferred', 40))
    components = profile.get('components')
    if not isinstance(components, dict) or set(components) != {'enabled_categories','allowed_ids'}:
        _fail(source, 'components must define enabled_categories and allowed_ids')
    categories = _sequence(components['enabled_categories'], source, 'components.enabled_categories', 12)
    allowed_ids = components['allowed_ids']
    if allowed_ids != '*':
        _sequence(allowed_ids, source, 'components.allowed_ids', 200)
    result['components'] = dict(enabled_categories=categories, allowed_ids=allowed_ids)
    quality = profile.get('quality')
    if not isinstance(quality, dict) or set(quality) != {'min_font_pt','min_contrast','min_contrast_normal','safe_area'}:
        _fail(source, 'quality must define min_font_pt, min_contrast, min_contrast_normal and safe_area')
    result['quality'] = dict(min_font_pt=_number(quality['min_font_pt'], source, 'quality.min_font_pt', 6, 20),
        min_contrast=_number(quality['min_contrast'], source, 'quality.min_contrast', 1, 7),
        min_contrast_normal=_number(quality['min_contrast_normal'], source, 'quality.min_contrast_normal', 1, 12),
        safe_area=_boolean(quality['safe_area'], source, 'quality.safe_area'))
    if result['quality']['min_contrast_normal'] <= result['quality']['min_contrast']:
        _fail(source, 'quality.min_contrast_normal must exceed quality.min_contrast')
    return result


class ProfileRegistry:
    """Immutable set of validated profiles loaded from one directory."""

    def __init__(self, directory=None):
        self.directory = Path(directory or DEFAULT_PROFILES)
        self.profiles = {}
        self._styles = {}
        raw = {}
        for path in sorted(self.directory.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ProfileError(f'{path.name}: unreadable profile ({exc})') from exc
            raw[path.stem] = (data, path.name)
        if not raw:
            raise ProfileError('No design profiles found in ' + str(self.directory))
        resolved = {}
        for name, (data, source) in raw.items():
            resolved[name] = self._resolve(name, raw, resolved, (), source)
        for name, profile in resolved.items():
            self.profiles[profile['id']] = profile
            if profile['id'] != name:
                raise ProfileError(f'{name}.json: id must match the file name')
        if DEFAULT_PROFILE not in self.profiles:
            raise ProfileError('Default profile corporate_blue must be present')

    def _resolve(self, name, raw, resolved, chain, source):
        if name in resolved:
            return resolved[name]
        if name in chain:
            raise ProfileError('Profile extends cycle: ' + ' -> '.join(chain + (name,)))
        if name not in raw:
            raise ProfileError(f'{source}: extends unknown profile {name}')
        data = copy.deepcopy(raw[name][0])
        parent = data.get('extends')
        if parent:
            base = self._resolve(parent, raw, resolved, chain + (name,), source).copy()
            base.pop('id', None)
            data = deep_merge(base, data)
            data['extends'] = parent
        profile = _validate(data, source)
        profile['extends'] = parent
        resolved[name] = profile
        return profile

    def ids(self):
        return sorted(self.profiles)

    def get(self, profile_id):
        if not isinstance(profile_id, str) or profile_id not in self.profiles:
            raise ProfileError(f'Unknown design profile: {profile_id!r}. Available: ' + ', '.join(self.ids()))
        return self.profiles[profile_id]

    def palette(self, profile_id):
        return dict(self.get(profile_id)['colors'])

    def style(self, profile_id, token):
        profile = self.get(profile_id)
        if token not in profile['type_scale']:
            raise ProfileError(f'Unknown style token: {token!r} for profile {profile_id}. Available: ' + ', '.join(profile['type_scale']))
        item = profile['type_scale'][token]
        family = profile['fonts'][item['font']]
        return dict(style_token=token, font_family=family, font_size=item['size'], bold=item['bold'],
            color=profile['colors'][item['color_token']], line_height=item['line_height'],
            letter_spacing=item.get('letter_spacing'), color_token=item['color_token'])

    def styles(self, profile_id):
        if profile_id not in self._styles:
            self._styles[profile_id] = {token: self.style(profile_id, token) for token in self.get(profile_id)['type_scale']}
        return dict(self._styles[profile_id])

    def allows_layout(self, profile_id, layout_id):
        allowed = self.get(profile_id)['layouts']['allowed']
        return allowed == '*' or layout_id in allowed

    def allows_component(self, profile_id, component_id, category):
        policy = self.get(profile_id)['components']
        return category in policy['enabled_categories'] and (policy['allowed_ids'] == '*' or component_id in policy['allowed_ids'])

    def summary(self):
        return [dict(id=profile['id'], name=profile['name'], version=profile['version'], density=profile['density']['level'],
            extends=profile['extends'], description=profile['description']) for profile in self.profiles.values()]


_CACHE = {}


def load_registry(directory=None):
    """Cached registry for a directory; new files appear on next process start."""
    key = str(Path(directory or DEFAULT_PROFILES).resolve())
    if key not in _CACHE:
        _CACHE[key] = ProfileRegistry(directory)
    return _CACHE[key]
