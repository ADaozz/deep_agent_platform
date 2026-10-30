"""Deterministic design conformance checks.

Every rule is measurable: font size, WCAG contrast against the real backdrop,
slot capacity, safe area, component registration/capacity and chart metadata.
Aesthetic judgement (composition, narrative rhythm) is deliberately not scored
here; it stays with the actual rendered screenshot review.

Severities:
  error    content may be wrong, lost or unreadable; blocks acceptance
  warning  deviation from the active profile; recorded, must be fixed or accepted
  advisory optimisation hint; never blocks work
"""
ERROR, WARNING, ADVISORY = 'error', 'warning', 'advisory'
RANK = {ERROR: 0, WARNING: 1, ADVISORY: 2}
DEFAULT_SLIDE_WIDTH_IN = 13.333333
LARGE_TEXT_PT = 24.0


def _rgb(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lstrip('#')
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))
    except ValueError:
        return None


def luminance(value):
    rgb = _rgb(value)
    if rgb is None:
        return None
    channels = [channel / 12.92 if channel <= .03928 else ((channel + .055) / 1.055) ** 2.4 for channel in rgb]
    return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]


def contrast(foreground, background):
    first, second = luminance(foreground), luminance(background)
    if first is None or second is None:
        return None
    high, low = max(first, second), min(first, second)
    return round((high + .05) / (low + .05), 2)


def _issue(kind, severity, message, element_id=None, **extra):
    issue = dict(type=kind, severity=severity, message=message)
    if element_id is not None:
        issue['element_id'] = element_id
    issue.update(extra)
    return issue


def _rect(element):
    geometry = element.get('geometry') or {}
    try:
        return tuple(float(geometry[key]) for key in ('x', 'y', 'w', 'h'))
    except (KeyError, TypeError, ValueError):
        return None


def _overlap(a, b):
    return max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))


def _backdrop(element, elements, fallback):
    rect = _rect(element)
    if rect is None or rect[2] * rect[3] <= 0:
        return fallback
    best, area = fallback, 0.0
    for other in elements:
        if other is element or other.get('type') != 'shape' or other.get('layer') != 'background':
            continue
        fill = other.get('fill')
        if not fill:
            continue
        other_rect = _rect(other)
        if other_rect is None:
            continue
        covered = _overlap(rect, other_rect)
        if covered >= .7 * rect[2] * rect[3] and covered > area:
            best, area = fill, covered
    return best


def _is_generated(element):
    return bool(element.get('source') == 'generated' or element.get('style_token') or element.get('component'))


def review(slide, profile=None, components=None, slots=None, width_points=960, height_points=540,
           slide_width_in=DEFAULT_SLIDE_WIDTH_IN, slide_height_in=DEFAULT_SLIDE_WIDTH_IN * 9 / 16):
    """Return deterministic design issues for one manifest slide."""
    elements = [element for element in slide.get('elements', []) if isinstance(element, dict)]
    background = slide.get('background', 'FFFFFF')
    profile = profile or {}
    quality = profile.get('quality', {})
    min_font = float(quality.get('min_font_pt', 10))
    normal_contrast = float(quality.get('min_contrast_normal', 4.5))
    large_contrast = float(quality.get('min_contrast', 3.0))
    style_tokens = profile.get('type_scale', {})
    margin_x = float(profile.get('spacing', {}).get('margin_x', .06))
    margin_y = float(profile.get('spacing', {}).get('margin_y', .06))
    issues = []

    generated_fonts = set()
    for element in elements:
        kind = element.get('type')
        geometry = _rect(element)
        if geometry is None:
            issues.append(_issue('invalid_geometry', ERROR, 'element has no valid normalised geometry', element.get('id')))
            continue
        x, y, w, h = geometry
        generated = _is_generated(element)
        anchor = element.get('anchor')

        if kind == 'text':
            text = element.get('text') or ''
            style = element.get('style') or {}
            size = float(style.get('font_size', 0) or 0)
            family = style.get('font_family')
            color = style.get('color')
            token = element.get('style_token')
            if generated:
                if family:
                    generated_fonts.add(family)
                if size and size < max(8.0, min_font - 1):
                    issues.append(_issue('font_too_small', ERROR, f'{size:g} pt is below the readable minimum of {min_font:g} pt', element.get('id'), font_size=size))
                elif size and size < min_font:
                    issues.append(_issue('font_too_small', WARNING, f'{size:g} pt is below the profile minimum of {min_font:g} pt', element.get('id'), font_size=size))
                if token and style_tokens and token not in style_tokens:
                    issues.append(_issue('unregistered_style_token', ERROR, f'style token {token!r} is not defined by profile {profile.get("id")}', element.get('id')))
                elif token and token in style_tokens and size:
                    expected = style_tokens[token]
                    if abs(float(expected.get('size', 0)) - size) > .01 and abs(float(expected.get('size', 0)) - size) / float(expected.get('size', 1)) > .06:
                        issues.append(_issue('profile_size_drift', ERROR, f'{token} is defined as {expected.get("size")} pt but the element uses {size:g} pt', element.get('id')))
                if color and size:
                    backdrop = _backdrop(element, elements, background)
                    ratio = contrast(color, backdrop)
                    large = size >= LARGE_TEXT_PT or (bool(style.get('bold')) and size >= 18.66)
                    threshold = large_contrast if large else normal_contrast
                    if ratio is not None and ratio < threshold:
                        severity = ERROR if ratio < threshold * .85 else WARNING
                        issues.append(_issue('contrast_insufficient', severity,
                            f'text contrast {ratio:.2f}:1 against #{backdrop} is below {threshold:.1f}:1', element.get('id'),
                            ratio=ratio, threshold=threshold))
            if slots and anchor and anchor in slots and text:
                capacity = slots[anchor].get('capacity') or {}
                if capacity:
                    lines = text.count('\n') + 1
                    if len(text) > capacity.get('chars', 0) or lines > capacity.get('lines', 0):
                        issues.append(_issue('slot_capacity_exceeded', ERROR,
                            f'slot {anchor} allows {capacity.get("chars")} characters / {capacity.get("lines")} lines; element holds {len(text)} / {lines}',
                            element.get('id')))
            elif slots and anchor:
                issues.append(_issue('unknown_anchor', ERROR, f'anchor {anchor!r} does not exist in layout {slide.get("layout")}', element.get('id')))
        elif kind == 'component' or element.get('component'):
            issues.extend(_component_issues(element, geometry, components, slide_width_in, slide_height_in))
        elif kind == 'chart':
            chart = element.get('chart') or {}
            width_in, height_in = w * slide_width_in, h * slide_height_in
            if width_in < 3.0 or height_in < 2.0:
                issues.append(_issue('chart_below_minimum_size', WARNING,
                    f'chart is {width_in:.1f} x {height_in:.1f} in; below 3.0 x 2.0 in it stops being readable', element.get('id')))
            categories = chart.get('categories') or []
            series = chart.get('series') or []
            if not categories:
                issues.append(_issue('chart_without_categories', ERROR, 'chart has no categories', element.get('id')))
            if not series or any(not str(item.get('name', '')).strip() for item in series):
                issues.append(_issue('chart_series_unlabeled', ERROR, 'every chart series needs a name', element.get('id')))
            if len({str(value) for value in categories}) != len(categories):
                issues.append(_issue('chart_duplicate_categories', WARNING, 'chart categories contain duplicates', element.get('id')))
            if chart.get('type') in ('bar', 'line') and not str(chart.get('unit', '')).strip():
                issues.append(_issue('chart_unit_missing', ADVISORY, 'quantitative chart has no unit (for example 亿元, %, 人)', element.get('id')))
            chart_backdrop = _backdrop(element, elements, background)
            # Reviewed profile palettes are only flagged when marks nearly vanish; explicit colours need 3:1.
            limit = 3.0 if (element.get('style_overrides') or {}).get('series_colors') else 1.6
            for color in (element.get('chart_style') or {}).get('series_colors') or []:
                ratio = contrast(color, chart_backdrop)
                if ratio is not None and ratio < limit:
                    issues.append(_issue('chart_series_low_contrast', ERROR if ratio < 1.6 else WARNING,
                        f'series colour #{color} has {ratio:.2f}:1 contrast against #{chart_backdrop}; graphical marks need 3:1', element.get('id'),
                        ratio=ratio))
                    break
        elif kind == 'table':
            rows = element.get('rows') or []
            if rows and any(len(str(cell)) > 40 for row in rows for cell in row):
                issues.append(_issue('table_cell_dense', ADVISORY, 'a table cell holds more than 40 characters', element.get('id')))

    for element in elements:
        geometry = _rect(element)
        if geometry is None or not _is_generated(element):
            continue
        if element.get('layer') in ('background', 'decoration') or element.get('anchor') in ('footer', 'page'):
            continue
        if not quality.get('safe_area', True):
            continue
        x, y, w, h = geometry
        if x < margin_x - .012 or y < margin_y - .012 or x + w > 1 - margin_x + .012 or y + h > 1 - margin_y + .012:
            if not any(issue.get('type') == 'safe_area_violation' and issue.get('element_id') == element.get('id') for issue in issues):
                issues.append(_issue('safe_area_violation', WARNING,
                    f'element overlaps the {margin_x:.0%} safe margin', element.get('id')))

    if len(generated_fonts) > 1:
        issues.append(_issue('mixed_font_family', ERROR, 'generated text mixes font families: ' + ', '.join(sorted(generated_fonts))))

    issues.extend(_alignment_issues(elements))
    issues.extend(_spacing_issues(elements))
    issues.extend(connector_issues(elements, slide_width_in, slide_height_in))
    return sorted(issues, key=lambda item: (RANK[item['severity']], item['type'], item.get('element_id') or ''))


def _line_points(element):
    connector = element.get('connector') or {}
    if element.get('route'):
        return [tuple(point) for point in element['route']]
    if connector.get('begin') and connector.get('end'):
        return [tuple(connector['begin']), tuple(connector['end'])]
    rect = _rect(element)
    if rect is None:
        return None
    x, y, w, h = rect
    return [(x, y + h / 2), (x + w, y + h / 2)] if w >= h else [(x + w / 2, y), (x + w / 2, y + h)]


def _distance_to_rect(point, rect):
    x, y, w, h = rect
    dx = max(x - point[0], 0, point[0] - (x + w))
    dy = max(y - point[1], 0, point[1] - (y + h))
    return (dx * dx + dy * dy) ** .5


def _crosses(a, b, rect, inset=.004):
    x, y, w, h = rect
    left, top, right, bottom = x + inset, y + inset, x + w - inset, y + h - inset
    if right <= left or bottom <= top:
        return False
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - left), (dx, right - a[0]), (-dy, a[1] - top), (dy, bottom - a[1])):
        if abs(p) < 1e-12:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return True


def connector_issues(elements, slide_width_in=DEFAULT_SLIDE_WIDTH_IN, slide_height_in=DEFAULT_SLIDE_WIDTH_IN * 9 / 16):
    """Connections must say what they connect and in which direction.

    Flags short lines bridging two objects without being bound, arrows with no
    declared relation, connectors with a missing endpoint, arrowheads that
    contradict the declared relation and routes that cut through text.
    """
    try:
        from design.diagram_presets import RELATIONS
    except ImportError:  # loaded as a plain module next to diagram_presets
        from diagram_presets import RELATIONS
    issues = []
    by_id = {element.get('id'): element for element in elements}
    # Blocks a line could plausibly be meant to connect: cards, charts, tables, images, components.
    # Text boxes and tiny marker dots are excluded so rails, ticks and underlines are not misread.
    targets = [element for element in elements if _rect(element) and (element.get('type') in ('component', 'chart', 'table', 'image') or
               (element.get('type') == 'shape' and element.get('shape') not in ('line', 'arrow') and _rect(element)[2] * _rect(element)[3] >= .002))]
    texts = [element for element in elements if (element.get('type') == 'text' and element.get('text')) or
             (element.get('type') == 'shape' and element.get('text') and element.get('shape') not in ('line', 'arrow'))]
    for element in elements:
        if element.get('source') == 'template' and element.get('type') == 'complex':
            continue
        is_line = element.get('type') == 'connector' or (element.get('type') == 'shape' and element.get('shape') == 'line')
        if element.get('type') == 'shape' and element.get('shape') == 'arrow' and not element.get('diagram_id'):
            issues.append(_issue('arrow_without_relation', WARNING,
                'decorative arrow implies a direction but declares no source, target or relation; use a diagram edge or remove it',
                element.get('id')))
            continue
        if not is_line:
            continue
        points = _line_points(element)
        if not points:
            continue
        connector = element.get('connector') or {}
        start, finish = connector.get('start'), connector.get('finish')
        length_in = sum((((b[0] - a[0]) * slide_width_in) ** 2 + ((b[1] - a[1]) * slide_height_in) ** 2) ** .5 for a, b in zip(points, points[1:]))
        if element.get('diagram_role') == 'edge':
            expected = {element.get('source_element'), element.get('target_element')}
            bound = {item.get('element_id') for item in (start, finish) if item}
            if not start or not finish or (start or {}).get('missing') or (finish or {}).get('missing') or bound != expected:
                issues.append(_issue('connector_endpoint_missing', ERROR,
                    f'edge is not bound to both of its declared nodes ({", ".join(sorted(str(x) for x in expected))})', element.get('id')))
            relation = RELATIONS.get(element.get('relation'))
            if relation is None:
                issues.append(_issue('connector_relation_missing', ERROR, 'edge has no valid relation', element.get('id')))
            else:
                arrows = (bool(connector.get('arrow_start')), bool(connector.get('arrow_end')))
                wanted = {'end': (False, True), 'both': (True, True), 'none': (False, False)}[relation['arrow']]
                if connector and arrows != wanted:
                    issues.append(_issue('arrow_direction_mismatch', ERROR,
                        f'arrowheads do not match relation {element.get("relation")}', element.get('id')))
        else:
            bound = [item for item in (start, finish) if item and item.get('element_id')]
            if len(bound) == 1:
                issues.append(_issue('connector_dangling', ERROR, 'connector is bound at one end only; the other end points at nothing', element.get('id')))
            elif not bound:
                near = []
                for point in (points[0], points[-1]):
                    candidates = sorted(((_distance_to_rect(point, _rect(target)), target.get('id')) for target in targets), key=lambda item: item[0])
                    near.append(candidates[0][1] if candidates and candidates[0][0] <= .02 else None)
                if near[0] and near[1] and near[0] != near[1] and length_in < 1.2:
                    issues.append(_issue('connector_unbound', ERROR,
                        f'line visually links {near[0]} and {near[1]} but is not bound and declares no relation; use diagram.add edges or remove it',
                        element.get('id'), related=near))
                elif length_in < .5:
                    issues.append(_issue('connector_meaningless_short', ERROR if element.get('layer') == 'content' else WARNING,
                        f'{length_in:.2f} in line connects nothing identifiable', element.get('id')))
            elif element.get('type') == 'connector' or connector.get('arrow_end') or connector.get('arrow_start'):
                issues.append(_issue('connector_relation_missing', WARNING, 'bound connector declares no relation type', element.get('id')))
        ends = {item.get('element_id') for item in (start, finish) if item} | {element.get('source_element'), element.get('target_element')}
        for text in texts:
            if text.get('id') in ends or text.get('id') == element.get('id'):
                continue
            if text.get('diagram_role') == 'edge_label' and text.get('edge_id') == element.get('edge_id'):
                continue
            rect = _rect(text)
            if rect and any(_crosses(a, b, rect) for a, b in zip(points, points[1:])):
                issues.append(_issue('connector_crosses_text', ERROR, f'connector runs through text of {text.get("id")}', element.get('id'), related=text.get('id')))
                break
    return issues


def _component_issues(element, geometry, components, slide_width_in, slide_height_in):
    issues = []
    metadata = element.get('component') or {}
    component_id = metadata.get('component_id') or element.get('component_id')
    if not components:
        return issues
    entry = components.get('components', {}).get(component_id)
    if entry is None:
        return [_issue('component_unregistered', ERROR, f'component {component_id!r} is not in the reviewed registry', element.get('id'))]
    version = str(metadata.get('version') or element.get('component_version') or '')
    if version and version != entry['version']:
        issues.append(_issue('component_version_mismatch', WARNING,
            f'instance uses {component_id} {version} but the library provides {entry["version"]}', element.get('id')))
    variant_key = str(metadata.get('variant') or '')
    variant = entry['variants'].get(variant_key)
    if variant is None:
        issues.append(_issue('component_variant_missing', ERROR, f'variant {variant_key!r} of {component_id} is not reviewed', element.get('id')))
    else:
        digest = metadata.get('svg_template_sha256')
        if digest and digest != variant['svg_sha256']:
            issues.append(_issue('component_source_changed', WARNING,
                f'{component_id} {variant_key} source changed since this instance was rendered; re-render to refresh it', element.get('id')))
        for name, text in (metadata.get('texts') or {}).items():
            slot = variant['text_slots'].get(name)
            if slot is None:
                issues.append(_issue('component_text_slot_unknown', ERROR, f'text slot {name!r} is not declared for {component_id}', element.get('id')))
            elif len(text) > slot['chars'] or text.count('\n') + 1 > slot['lines']:
                issues.append(_issue('component_capacity_exceeded', ERROR, f'text slot {name} of {component_id} exceeded its capacity', element.get('id')))
    width_in, height_in = geometry[2] * slide_width_in, geometry[3] * slide_height_in
    minimum = entry['min_size_in']
    if width_in + 1e-6 < minimum['w'] or height_in + 1e-6 < minimum['h']:
        issues.append(_issue('component_below_minimum_size', WARNING,
            f'{component_id} needs at least {minimum["w"]:.1f} x {minimum["h"]:.1f} in; placed at {width_in:.1f} x {height_in:.1f} in', element.get('id')))
    ratio = width_in / height_in if height_in else 0
    if ratio and abs(ratio - entry['aspect_ratio']) > entry['aspect_ratio'] * .03:
        issues.append(_issue('component_aspect_mismatch', ERROR, f'{component_id} must keep its {entry["aspect_ratio"]:.2f}:1 canvas ratio', element.get('id')))
    return issues


def _alignment_issues(elements):
    candidates = [element for element in elements if element.get('type') in ('text', 'shape', 'chart', 'table', 'component') and _rect(element)]
    issues = []
    for index, first in enumerate(candidates):
        a = _rect(first)
        for second in candidates[index + 1:]:
            b = _rect(second)
            difference = abs(a[0] - b[0])
            if not (.003 < difference < .02) or _overlap(a, b) != 0:
                continue
            widths = a[2] / b[2] if b[2] else 0
            if not .6 <= widths <= 1.67:
                continue
            issues.append(_issue('alignment_drift', ADVISORY,
                f'left edges differ by {difference:.3f} of the slide width', first.get('id'),
                related=second.get('id')))
            break
    return issues


def _spacing_issues(elements):
    rows = {}
    for element in elements:
        geometry = _rect(element)
        if geometry is None or element.get('layer') in ('background', 'decoration'):
            continue
        if element.get('type') not in ('text', 'shape', 'component', 'chart', 'table'):
            continue
        rows.setdefault(round(geometry[1], 2), []).append((geometry, element))
    issues = []
    for items in rows.values():
        if len(items) < 3:
            continue
        items.sort(key=lambda item: item[0][0])
        gaps = [round(items[i + 1][0][0] - (items[i][0][0] + items[i][0][2]), 3) for i in range(len(items) - 1)]
        if not gaps:
            continue
        if max(gaps) - min(gaps) > max(.02, .35 * max(abs(min(gaps)), 1e-6)) and max(gaps) > 0:
            issues.append(_issue('spacing_inconsistency', ADVISORY,
                'gaps between aligned elements vary: ' + ', '.join(f'{gap:g}' for gap in gaps), items[0][1].get('id')))
    return issues


def summary(issues):
    result = {ERROR: 0, WARNING: 0, ADVISORY: 0}
    for issue in issues:
        result[issue.get('severity', ERROR)] = result.get(issue.get('severity', ERROR), 0) + 1
    return dict(errors=result[ERROR], warnings=result[WARNING], advisories=result[ADVISORY])
