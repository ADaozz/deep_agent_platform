#!/usr/bin/env python3
"""Render reviewed SVG components to deterministic PNG instances.

The library is read-only at runtime. Callers may only pick a component, a
declared variant, bounded parameters and short text for declared native slots.
"""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import component_validator as components  # noqa: E402
from design import profile_registry as profiles  # noqa: E402

PREVIEW_ROOT = ROOT / 'design' / 'previews'


class RenderError(ValueError):
    """A component call that cannot become a reviewed PNG instance."""


def _raster_width(canvas_width):
    return min(2400, max(256, int(canvas_width) * 2))


def load(require_previews=True):
    return components.load_registry(require_previews=require_previews)


def render(registry, profile_registry, profile_id, component_id, params=None, texts=None, version=None):
    """Validate a component call and rasterise it for the active profile."""
    entry = components.get(registry, component_id)
    profile = profile_registry.get(profile_id)
    if not profile_registry.allows_component(profile_id, component_id, entry['category']):
        raise RenderError(f'Profile {profile_id} does not allow component {component_id} ({entry["category"]})')
    key, variant, params, texts = components.validate_call(entry, params, texts, version)
    colors = components.palette_for(profile_registry, profile_id)
    svg, digest = components.render_svg(entry, variant, params, colors)
    png = components.rasterize(svg, _raster_width(entry['canvas']['width']))
    return dict(component_id=component_id, version=entry['version'], category=entry['category'],
        variant=key, params=dict(params), texts=dict(texts), svg=svg, svg_sha256=digest,
        svg_template_sha256=variant['svg_sha256'], png=png,
        canvas=dict(entry['canvas']), min_size_in=components.minimum_size(entry),
        profile_id=profile_id, layer=entry['layer'], usage=entry['usage'], intents=list(entry['intents']))


def text_children(instance_id, entry, variant, texts, geometry):
    """Native text children placed inside a component instance.

    Important text stays in editable PowerPoint text boxes instead of being
    baked into the raster, so later edits and content checks keep working.
    """
    canvas = entry['canvas']
    x, y, w, h = (float(geometry[key]) for key in ('x', 'y', 'w', 'h'))
    children = []
    for name, text in texts.items():
        slot = variant['text_slots'][name]
        sx, sy, sw, sh = slot['rect']
        child = dict(id=f'{instance_id}.{name}', slot=name, text=text, style=slot['style'], align=slot['align'],
            geometry=dict(x=round(x + sx / canvas['width'] * w, 6), y=round(y + sy / canvas['height'] * h, 6),
                w=round(sw / canvas['width'] * w, 6), h=round(sh / canvas['height'] * h, 6)))
        children.append(child)
    return children


def catalog(registry):
    result = []
    for entry in registry['components'].values():
        result.append(dict(component_id=entry['component_id'], version=entry['version'], name=entry['name'],
            category=entry['category'], intents=entry['intents'], tags=entry['tags'],
            aspect_ratio=entry['aspect_ratio'], min_size_in=entry['min_size_in'], layer=entry['layer'],
            params={name: {key: value for key, value in schema.items() if key != 'scale'} for name, schema in entry['params'].items()},
            variants={key: dict(svg=value['svg'], preview=value['preview'], text_slots=value['text_slots']) for key, value in entry['variants'].items()},
            usage=entry['usage']))
    return result


def build_previews(registry=None, profile_registry=None):
    """Regenerate every reviewed preview from the committed SVG sources."""
    registry = registry or load(require_previews=False)
    profile_registry = profile_registry or profiles.load_registry()
    colors = components.palette_for(profile_registry, 'corporate_blue')
    written = []
    for entry in registry['components'].values():
        for key, variant in entry['variants'].items():
            params = {name: schema['default'] for name, schema in entry['params'].items()}
            if entry['variant_param']:
                schema = entry['params'][entry['variant_param']]
                params[entry['variant_param']] = next(value for value in schema['values'] if str(value) == key)
            svg, _ = components.render_svg(entry, variant, params, colors)
            width = 256 if entry['category'] == 'icons' else min(960, max(480, entry['canvas']['width']))
            png = components.rasterize(svg, width)
            path = PREVIEW_ROOT / variant['preview']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
            written.append(str(path.relative_to(ROOT)))
    return written


def main():
    parser = argparse.ArgumentParser(description='Reviewed PPTX component library')
    parser.add_argument('command', choices=['catalog', 'render', 'previews'])
    parser.add_argument('--component')
    parser.add_argument('--profile', default='corporate_blue')
    parser.add_argument('--params', default='{}')
    parser.add_argument('--texts', default='{}')
    parser.add_argument('--version')
    parser.add_argument('--out')
    args = parser.parse_args()
    if args.command == 'catalog':
        print(json.dumps(catalog(load(require_previews=False)), ensure_ascii=False, indent=1))
        return
    if args.command == 'previews':
        print(json.dumps(build_previews(), ensure_ascii=False, indent=1))
        return
    if not args.component or not args.out:
        parser.error('render needs --component and --out')
    try:
        result = render(load(), profiles.load_registry(), args.profile, args.component,
            json.loads(args.params), json.loads(args.texts), args.version)
        Path(args.out).write_bytes(result['png'])
        print(json.dumps({key: result[key] for key in ('component_id','version','variant','params','texts','svg_sha256','profile_id')}, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        print(json.dumps(dict(status='rejected', error=str(exc)), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
