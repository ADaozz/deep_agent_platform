# Design system reference

The visual system is data, not engine code. Three layers, all under `design/`:

```
design/
├── profiles/*.json          design tokens (colours, type scale, spacing, density, policy)
├── layouts/*.json           semantic page structures, slots, capacities, variants
├── components/              reviewed SVG sources + registry.json
│   ├── process/ architecture/ bento/ infographic/ illustration/ icons/
│   └── registry.json
└── previews/                reviewed thumbnails, one per component variant
```

`profile_registry.py`, `layout_registry.py`, `layout_resolver.py` and `design_quality.py` validate and execute this data. `scripts/component_validator.py` validates component sources and calls; `scripts/component_renderer.py` renders deterministic PNG instances and rebuilds previews.

## Profiles

Built in: `corporate_blue` (default), `slate_dark`, `minimal_keynote`, `consulting`, `tech_dark`, `financial`. Each declares:

- `colors`: background, surface, text, muted, accent, accent_soft, card, border, highlight, positive, negative, warning, `chart_series` (4–8).
- `fonts` (title/body/fallback) and `type_scale` (13 required tokens with size, weight, colour token, line height, optional letter spacing).
- `spacing` (margins, gutter, card padding, footer), `shape` (radius, stroke, shadow flag, accent rule).
- `density` (minimal/balanced/dense, bullets, modules, characters per line).
- `charts`, `tables`, `layouts` policy, `components` policy, `quality` thresholds.

`extends` merges token-by-token; unknown fields, bad colours, cycles and missing tokens are rejected with a precise error. Adding a profile is one JSON file — no Agent, frontend or engine change. A profile never contains page content.

Precedence when they disagree: explicit user instruction > uploaded template assets and theme > selected built-in profile > default theme. The engine keeps a template's accent and body font, reports the override, and never rewrites the template's theme part.

## Layouts

See `layout-rules.md`. Slot rectangles use the reference margin 0.06 and are adapted to the profile's margins at resolution time. `modules` selects a variant; capacities are enforced at insertion and on migration.

## Components

`registry.json` is the allow-list. Every entry carries:

- `component_id` (`category.name`), semver `version`, bilingual name, category, intents and tags.
- `canvas` and `aspect_ratio`; `min_size_in` (reviewed minimum, matching the aspect).
- `layer` (content/background/decoration), `baked_text: false`, supported colour `tokens`.
- `params` schema (integer with bounds, enum variants, colour token), optional `variant_param`.
- `variants` with `svg`, `preview` and `text_slots` (rect, profile style token, char/line capacity, align).
- `usage` guidance and the source/preview digest used for traceability.

Runtime rules: only declared params/text slots, only reviewed variants, XML-escaped bounded text, aspect-preserving geometry, no external references, scripts, fonts, images, filters or embedded text. Same component + params + profile + size yields byte-identical PNGs. Instances record component id, version, variant, params, rendered/template digests and profile id; the library source itself is versioned and immutable.

Text that matters is native: declared text slots are inserted as editable text boxes over the raster, with stable child ids and one transaction boundary. The registry deliberately keeps SVG text-free.

Previews are committed. Rebuild them after intentionally changing a source:

```bash
python scripts/component_renderer.py previews
```

## Adding a component

1. Author a static SVG inside `design/components/<category>/` using declared colour placeholders (`{{accent}}`, `{{series1}}`, `{{c:color}}`, `{{px:percent}}`).
2. Add a registry entry with capacity-limited native text slots; version it.
3. Run `python scripts/component_renderer.py previews` and inspect the actual PNG at its insertion size.
4. Run the design-system tests. Missing previews, undeclared placeholders, unsafe SVG or duplicate ids fail validation.

Do not copy the four design-study decks as page components. They informed colour, spacing and hierarchy rules; the shipped components and profiles are original and independently reviewed.
