# Element protocol

Every mutation requires `presentation_id`, `operation_id`, `base_revision` and one operation. Slide-specific operations require `slide_id`. An element has `id` plus `geometry` or a layout anchor. Style tokens come from the active profile (`slide_title`, `body`, `body_small`, `caption`, `card_title`, `cover_title`, `section_title`, `statement`, `metric`, `metric_label`, `kicker`, `table_header`, `table_cell`). A text element without `style` inherits the slot's declared style.

- Text: ≤1200 characters; style token or slot default; optional `align` is applied from the slot.
- Shape: `rectangle`, `rounded_rectangle`, `arrow`, `ellipse` or `line`; `fill_token` must be a profile colour token (accent/card/background/text/muted/surface/border/…); declare `layer = content|background|decoration`. `overlap_group` explicitly permits known stacking, including component + its native text.
- Image: workspace-relative PNG/JPEG ≤12 MB; geometry must preserve the aspect ratio.
- Table: uniform rows ≤10×8, each cell ≤160 characters; stored as a native table and restyled from the profile's table tokens.
- Chart: `chart = {type:bar|line|pie, categories:[…], series:[{name,values}], unit?}`; ≤12 categories and ≤4 finite series. `unit` is a real unit (亿元, %, 天) and becomes the value-axis title. Types must be replaced, not mutated.
- Component: `component` id, optional `version`, `params`, `texts`, geometry. The geometry must keep the reviewed canvas aspect (±2%) and meet the component's minimum size. The reviewed SVG is embedded as the picture's vector source (`asvg:svgBlip`) with a deterministic PNG fallback; no SVG-to-shape conversion happens. Declared text slots become native text boxes with stable IDs `<component-id>.<slot>`. Params/texts can be updated later and are re-rendered deterministically; the previous vector part is dropped. Removing a component removes its text children and vector source.
- Update: target `element.id`; change text/style/geometry/fill/layer, table rows, chart data/unit, or component `params`/`texts`. Component geometry changes move its native text with it.
- `slide.add`: layout id, optional `modules` variant, optional real template `layout_index`. Existing slide acceptance gates further additions.
- `slide.set_layout`: lossless intent change; rejects missing anchors and capacity loss; repositions known anchored elements.
- `presentation.set_profile` (or legacy `presentation.set_theme`): applies profile tokens to generated objects, backgrounds, tables, charts and components, repositions anchored elements for new margins and invalidates all slide acceptance. Template masters/theme parts are untouched; a template accent/font overrides the profile's accent/font.
- `slide.reorder`: moves one stable slide to an index; rechecks required.

A duplicate ID/payload returns the original result; reused ID with a different payload is rejected. Stale bases are rejected. Scope grants never authorize a different path or presentation.

`plan --request plan.json` takes `{"intent":"data","content":{"blocks":[{"type":"text","chars":120},{"type":"chart"}],"modules":0}}` and returns the chosen layout, variant, resolved slots, alternatives and rejected layouts with reasons.
