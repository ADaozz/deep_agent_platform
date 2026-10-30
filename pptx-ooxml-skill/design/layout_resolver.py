"""Deterministic layout selection and slot geometry.

The model decides the *intent*; this module decides which declared layout can
hold the content, which variant fits the module count and which normalised
rectangles the anchored elements receive under the active profile.
"""
from .layout_registry import REFERENCE_MARGIN

ADJACENT_DENSITY = {('minimal','balanced'),('balanced','minimal'),('balanced','dense'),('dense','balanced')}
BLOCK_ALIASES = {'text':'text','bullets':'text','list':'text','chart':'chart','table':'table','image':'image','picture':'image','component':'component','shape':'shape'}


class ResolutionError(ValueError):
    """Content cannot be placed in any declared layout."""


def describe(content):
    """Normalise an agent supplied content summary into comparable numbers."""
    if content is None:
        content = {}
    if not isinstance(content, dict):
        raise ResolutionError('content must be an object')
    blocks = content.get('blocks') or []
    if not isinstance(blocks, list) or len(blocks) > 24:
        raise ResolutionError('content.blocks must be a list of at most 24 blocks')
    normalized = []
    for block in blocks:
        if isinstance(block, str):
            kind = BLOCK_ALIASES.get(block)
            block = {'type': kind or block}
        if not isinstance(block, dict):
            raise ResolutionError('each content block must be an object')
        kind = BLOCK_ALIASES.get(block.get('type'))
        if kind is None:
            raise ResolutionError('unsupported content block type: ' + str(block.get('type')))
        text = block.get('text')
        chars = block.get('chars', len(text) if isinstance(text, str) else 0)
        if type(chars) is not int or chars < 0 or chars > 4000:
            raise ResolutionError('content block chars must be a bounded integer')
        normalized.append(dict(type=kind, chars=chars, lines=block.get('lines', 0)))
    modules = content.get('modules', 0)
    if type(modules) is not int or not 0 <= modules <= 12:
        raise ResolutionError('content.modules must be an integer between 0 and 12')
    if not normalized:
        normalized = [dict(type='text', chars=content.get('chars', 0), lines=0)]
    types = sorted({block['type'] for block in normalized})
    chars = sum(block['chars'] for block in normalized)
    lines = sum(block['lines'] for block in normalized)
    return dict(blocks=len(normalized), chars=chars, lines=lines, modules=modules, types=types)


def _adapt(rect, margin_x, margin_y):
    x, y, w, h = rect
    kx = (1 - 2 * margin_x) / (1 - 2 * REFERENCE_MARGIN)
    ky = (1 - 2 * margin_y) / (1 - 2 * REFERENCE_MARGIN)
    return [round(margin_x + (x - REFERENCE_MARGIN) * kx, 6), round(margin_y + (y - REFERENCE_MARGIN) * ky, 6),
            round(w * kx, 6), round(h * ky, 6)]


def slot_view(slot, profile, style_for):
    """Resolve one layout slot under a profile.

    ``style_for`` maps a profile type token to a resolved text style, so the
    engine can reuse this function with its own state-backed token lookup.
    """
    rect = list(slot['rect']) if slot['absolute'] else _adapt(slot['rect'], profile['spacing']['margin_x'], profile['spacing']['margin_y'])
    style = style_for(slot['style'])
    return dict(rect=rect, accepts=list(slot['accepts']), role=slot['role'], style_token=slot['style'],
        align=slot['align'], capacity=dict(slot['capacity']), style=style, absolute=slot['absolute'])


def _candidate(layout, key, slots, profile, style_for):
    return {name: slot_view(slot, profile, style_for) for name, slot in slots.items()}


def _fit(layout, slots, desc):
    modules = [name for name, slot in slots.items() if slot['role'] in ('module','metric')]
    if desc['modules'] > len(modules):
        return f"needs {desc['modules']} modules but declares {len(modules)}"
    # A module count already governs the structure; explicit block counts only
    # guard against overfull prose pages.
    if desc['modules']:
        if desc['blocks'] > layout['blocks']['max']:
            return f"holds at most {layout['blocks']['max']} content blocks, content has {desc['blocks']}"
    elif not layout['blocks']['min'] <= desc['blocks'] <= layout['blocks']['max']:
        return f"holds {layout['blocks']['min']}-{layout['blocks']['max']} blocks, content has {desc['blocks']}"
    text_slots = [slot for slot in slots.values() if 'text' in slot['accepts']]
    for kind in desc['types']:
        if not any(kind in slot['accepts'] for slot in slots.values()):
            return f'does not accept a {kind} block'
    capacity = sum(slot['capacity']['chars'] for slot in text_slots)
    if desc['chars'] > capacity:
        return f'holds {capacity} text characters, content needs {desc["chars"]}'
    lines = sum(slot['capacity']['lines'] for slot in text_slots)
    if desc['lines'] and desc['lines'] > lines:
        return f'holds {lines} text lines, content needs {desc["lines"]}'
    return None


def plan(profile_registry, layout_registry, profile_id, intent=None, content=None, limit=3):
    """Rank declared layouts for the given intent/content/profile.

    Returns the chosen layout with profile-adjusted slots plus the alternatives
    and explicit rejection reasons. The result is deterministic for equal input.
    """
    profile = profile_registry.get(profile_id)
    if intent is not None and not isinstance(intent, str):
        raise ResolutionError('intent must be a string')
    desc = describe(content)
    rejected, candidates = [], []
    for layout in layout_registry.layouts.values():
        if not profile_registry.allows_layout(profile_id, layout['id']):
            rejected.append(dict(layout=layout['id'], reason='not enabled by profile ' + profile_id))
            continue
        keys = [layout['default_variant']] + sorted(layout['variants'])
        for key in keys:
            slots = layout['slots'] if key == layout['default_variant'] else layout['variants'][key]['slots']
            problem = _fit(layout, slots, desc)
            if problem:
                rejected.append(dict(layout=layout['id'], variant=key, reason=problem))
                continue
            score = 0.0
            reasons = []
            if intent is not None:
                if layout['intent'] == intent:
                    score += 40; reasons.append('intent ' + intent)
                elif intent in layout['tags']:
                    score += 20; reasons.append('tag ' + intent)
            modules = len([slot for slot in slots.values() if slot['role'] in ('module','metric')])
            if desc['modules']:
                if modules == desc['modules']:
                    score += 12; reasons.append('exact module count')
                else:
                    score -= 2 * abs(modules - desc['modules'])
            if layout['density'] == profile['density']['level']:
                score += 8; reasons.append('density match')
            elif (layout['density'], profile['density']['level']) in ADJACENT_DENSITY:
                score += 3
            text_capacity = sum(slot['capacity']['chars'] for slot in slots.values() if 'text' in slot['accepts'])
            if text_capacity:
                score += round(6 * (1 - desc['chars'] / text_capacity), 2)
            if layout['id'] in profile['layouts']['preferred']:
                score += 5; reasons.append('profile preferred')
            score -= round(.5 * (layout['blocks']['max'] - desc['blocks']), 2)
            candidates.append(dict(layout=layout, key=key, slots=slots, score=round(score, 2), reasons=reasons))
    candidates.sort(key=lambda item: (-item['score'], item['layout']['id'], item['key']))
    if not candidates:
        detail = '; '.join(f"{item['layout']}: {item['reason']}" for item in rejected[:6])
        raise ResolutionError('No layout can hold this content. ' + detail)
    chosen = candidates[0]
    style_for = lambda token: profile_registry.style(profile_id, token)  # noqa: E731
    slots = _candidate(chosen['layout'], chosen['key'], chosen['slots'], profile, style_for)
    alternatives = [dict(layout=item['layout']['id'], variant=item['key'], score=item['score'], reasons=item['reasons']) for item in candidates[1:limit]]
    return dict(profile=profile_id, intent=chosen['layout']['intent'], layout=chosen['layout']['id'], variant=chosen['key'],
        modules=desc['modules'], score=chosen['score'], reasons=chosen['reasons'], slots=slots,
        alternatives=alternatives, rejected=rejected[:24])
