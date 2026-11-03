# Element protocol

## Text: create or fill

`slot.fill` edits an existing text object. `element.add_text` creates one. A layout anchor is only a position; `slide.add` does not populate it with a text box.

### Existing PPT page (editing only)

Analyze the existing user-provided PPT with `import-template` and read the selected page's `slots`. After import, `status` returns `template_slots[slide_id]` and `manifest.slides[].elements[].id`. For `init --template`, slot maps are in `template_index.slides[].slots`. Use the actual slot alias or stable text element ID, not a guessed name. These requests assume a user template staged at `files/inputs/template.pptx`, with a `tpl-001` page containing `title` and `subtitle` slots; substitute the actual names from its manifest:

```json
{"presentation_id":"deck","operation_id":"import-1","base_revision":0,"operation":"slide.import","template_id":"user-brand","template_path":"inputs/template.pptx","source_slide_id":"tpl-001","slide_id":"p1"}
```

```json
{"presentation_id":"deck","operation_id":"fill-1","base_revision":1,"operation":"slot.fill","slide_id":"p1","slots":{"title":"AI Agent","subtitle":"未来办公方式"}}
```

### Blank page

Only when no template page fits, create a page and then its content. The `cover` layout has a `title` anchor:

```json
{"presentation_id":"deck","operation_id":"add-1","base_revision":0,"operation":"slide.add","slide_id":"p1","layout":"cover"}
```

```json
{"presentation_id":"deck","operation_id":"text-1","base_revision":1,"operation":"element.add_text","slide_id":"p1","element":{"id":"heading","anchor":"title","text":"AI Agent","style":"cover_title"}}
```

To update that generated text:

```json
{"presentation_id":"deck","operation_id":"text-2","base_revision":2,"operation":"element.update","slide_id":"p1","element":{"id":"heading","text":"AI Agent 与未来办公"}}
```

Save each request in the Thread's `tmp/` directory and run `apply-one files/deck.pptx --request tmp/<request>.json`. Each example starts from its own freshly initialized `deck` at revision 0; use actual returned revisions when continuing a real file. Finish all pages, then run `check-deck`; fix listed overlaps locally until passed. All vision findings are advice.

`Unknown or ambiguous slot title` means `title` resolves to zero or multiple objects. Read the slot map and element IDs, choose a unique existing text object, or create text if the page is blank. `slot.fill` does not create a missing text box. `capacity conflict` instead means the object exists but the replacement text does not fit.

## Operation parameters

Every mutation requires `presentation_id`, `operation_id`, `base_revision` and one operation. Slide-specific operations require `slide_id`. An element has `id` plus `geometry` or a layout anchor. Style tokens come from the active profile (`slide_title`, `body`, `body_small`, `caption`, `card_title`, `cover_title`, `section_title`, `statement`, `metric`, `metric_label`, `kicker`, `table_header`, `table_cell`). A text element without `style` inherits the slot's declared style.

- Text: ≤1200 characters; style token or slot default; optional `align` is applied from the slot.
- Shape: `rectangle`, `rounded_rectangle`, `arrow`, `ellipse` or `line`; `fill_token` must be a profile colour token (accent/card/background/text/muted/surface/border/…); declare `layer = content|background|decoration`. `overlap_group` explicitly permits known stacking, including component + its native text.
- Image: workspace-relative PNG/JPEG ≤12 MB; geometry must preserve the aspect ratio.
- Table: uniform rows ≤10×8, each cell ≤160 characters; stored as a native table and restyled from the profile's table tokens.
- Chart: `chart` uses a registered native type from `charts`; category charts use `categories` and `series:[{name,values}]`, scatter/bubble use `series:[{name,points}]`. Up to 2000 points and 12 named finite series, subject to the 30 KB operation limit. `unit` records the real measurement unit (亿元, %, 天). See [charts.md](charts.md) for hierarchy, OHLC, combination and ChartEx data. Types must be replaced, not mutated.
- Component: `component` id, optional `version`, `params`, `texts`, geometry. The geometry must keep the reviewed canvas aspect (±2%) and meet the component's minimum size. The reviewed SVG is embedded as the picture's vector source (`asvg:svgBlip`) with a deterministic PNG fallback; no SVG-to-shape conversion happens. Declared text slots become native text boxes with stable IDs `<component-id>.<slot>`. Params/texts can be updated later and are re-rendered deterministically; the previous vector part is dropped. Removing a component removes its text children and vector source.
- Update: target `element.id`; change text/style/geometry/fill/layer, table rows, chart data/unit, or component `params`/`texts`. Component geometry changes move its native text with it.
- Adjust (`element.adjust`): relative change from the element's current geometry; the rest of the page is untouched. `dx`/`dy` (fraction of slide width/height) or `dx_in`/`dy_in` (inches), `scale` (uniform, 0.2–5, around the centre), `scale_x`/`scale_y` (not for images/components/icons, which keep their aspect ratio), `rotate` (relative degrees; text, images and simple shapes only). A result outside the canvas is rejected. Anchored elements keep their slot but are marked `manual_geometry` so profile changes do not snap them back; `slide.set_layout` re-anchors them. Moving a diagram node re-routes its connectors; connectors themselves cannot be moved.
- Style (`element.set_style`): `style` is a small, per-kind vocabulary; values are profile colour tokens or `RRGGBB`; `null` removes an override.
  - text: `color`, `bold`, `italic`, `font_size` (≥ profile minimum), `font_family` (profile/template fonts), `align`
  - shape: `fill` (`none` allowed), `gradient` (`{start, end, angle?}`, two-colour linear; replaces `fill`), `shadow` (`none|soft`), `line_color` (`none` allowed), `line_width_pt` (0.25–4), `dash` (`solid|dash|dot`), `text_color`, `bold`, `font_size`
  - connector/line: `line_color`, `line_width_pt`, `dash`
  - table: `header_fill`, `header_text`, `header_bold`, `body_fill`, `body_text`, `divider_color`, `font_size`, `zebra`
  - chart: `series_colors` (≤6), `gridlines` (`none|light`), `data_labels`, `number_format`, `emphasis` (`max|min|none|<category>|<index>`), `legend` (`auto|none|bottom|right|top`), `font_size`, `line_width_pt`
  Defaults stay flat; glow, bevel, reflection, inner shadow and 3-D keys are rejected. Overrides are stored and re-applied after profile defaults, data updates and profile changes.
- Preset (`element.apply_preset`): `chart.clean|chart.labeled|chart.emphasis`, `table.clean|table.header_accent|table.minimal`, `shape.card|shape.outline|shape.accent|shape.subtle|shape.raised|shape.gradient`, `text.emphasis|text.muted`. Applying a preset drops overrides for the keys it defines; `preset: null` removes it. Creation operations also accept `preset`.
- Chart type (`chart.change_type`): `{id, type:<registered type>}` replaces the native chart object in one transaction, keeping compatible data, geometry, z-order and stable ID; the workbook and chart caches are verified before commit. Conversion rejects missing coordinates, bubble sizes, hierarchy or OHLC data. See [charts.md](charts.md).
- Diagram (`diagram.add`): `{id, geometry|anchor, direction: TB|LR, preset: layered|flow|tree, spacing?:{rank_gap_in,node_gap_in}, node_size?:{w_in,h_in}, node_shape?: rounded_rectangle|rectangle, nodes:[{id,label,detail?,rank?,emphasis?,shape?,position?:{x,y}}], edges:[{id?,source,target,relation,label?}]}`. Presets are defaults; `spacing`/`node_size`/`node_shape` adjust them, and giving every node a `position` (top-left, slide fractions) places nodes freely for hub, matrix or custom arrangements — connectors are still routed between facing sides and bound. Up to 14 nodes, 24 edges, 6 ranks, 6 nodes per rank; labels ≤40, details ≤60, edge labels ≤12 characters. `rank` is all-or-none; otherwise ranks follow the directed relations (cycles must use `returns`). Connector coordinates, points or routes are never accepted. Each node is a native rounded rectangle with native text (`<diagram>.<node>`); each edge is a native connector bound (`stCxn`/`endCxn`) to both nodes (`<diagram>.<edge>`), with arrowheads and dashes from the relation. One connector per node pair. If the rank-based route is blocked the engine also tries the sides facing each other geometrically; edges that still cross another node or slide text, nodes that would cover text, and labels without free space are rejected with the reason.
- Diagram update (`diagram.update`): `{id, nodes?|edges?}` replace lists, or patch with `add_nodes`, `update_nodes`, `remove_nodes`, `add_edges`, `remove_edges`; `direction`, `preset`, `spacing`, `node_size`, `node_shape`, `geometry|anchor` may change (`null` resets). Removing a node that still has edges is rejected — remove or rebind those edges explicitly. Element IDs and style overrides persist. `element.update`/`element.remove` refuse diagram parts.
- `slide.import`: `{template_id, source_slide_id, slide_id, insert_after?, template_path?}`. Copies one authorized page from an existing PPTX into the working PPTX for editing; use template_path. Library lookup remains for compatibility, but the bundled library is empty. The source package is never modified. Masters, layouts and theme from that template are copied once and reused. Pictures from the same template may be shared. Charts and their workbooks are always new copies. An internal jump is rebound only when that template page has already been imported exactly once; otherwise the operation fails with `unresolved_internal_link` and publishes nothing. Notes are omitted and reported. OLE, SmartArt and any relationship without a share/copy policy fail the import instead of converting the object to a picture. The first import into an empty deck adopts the source size; incompatible slide sizes are refused when the working deck already has pages. Logo protection and slot maps are copied to the new slide id. Import all requested pages before running `check-deck`.
- `slide.clone`: `{source_slide_id, slide_id, insert_after?}`. Copies one slide already in the working PPTX. Masters, layouts, theme and ordinary pictures are shared. Charts and their workbooks are copied. A hyperlink to another slide keeps pointing at that slide and does not create a new one. Notes are omitted and reported. OLE, SmartArt and any relationship without a share/copy policy fail the clone instead of being guessed. Logo protection marked on the source is copied to the new slide id. Clone all requested pages before running `check-deck`. This is not the template generation entry; use `slide.import` to take a page from the library.
- `slide.remove`: removes one slide and its presentation relationship.
- `effective_palette`: `presentation.recolor` stores the semantic palette on the deck. New shapes, charts, tables and slide backgrounds read it before the profile palette. `template_tokens` keeps the original theme and is not overwritten. `presentation.set_profile` changes type scale and layout policy and does not replace an effective palette. Restore reloads the palette that belonged to that revision.
- `slot.fill`: `{slide_id, slots:{title, subtitle, …}}`. Replaces slot text and keeps the first run's properties. Raises `capacity conflict` instead of shrinking fonts when the text does not fit.
- `chart.replace_data`: `{slide_id, element_id, chart:{categories, series, type?}}`. Replaces a native chart's data, including an imported chart. A different `type` is rejected; use `chart.change_type`.
- `image.replace`: `{slide_id, element_id, path}`. Replaces one picture with a workspace PNG/JPEG of the same aspect ratio. Shared media used by other slides is left in place.
- `presentation.recolor`: `{palette:{accent, background, text, muted?}, preserve?:["images","logos"]}`. Updates the theme color scheme and direct RGB values that match it. Pictures stay as they are. Logo and brand shapes listed in `protected_elements` (stable ids recorded at import) keep their previous color. The result includes `recolor.unhandled` and `recolor.recolor_complete`; do not claim a global recolor when that flag is false.
- `slide.add`: layout id, optional `modules` variant, optional real template `layout_index`. Generate all requested pages before running check-deck. Use this to create new pages from the style MD and reference images.
- `slide.set_layout`: lossless intent change; rejects missing anchors and capacity loss; repositions known anchored elements.
- `presentation.set_profile` (or legacy `presentation.set_theme`): applies profile tokens to generated objects, backgrounds, tables, charts and components, repositions anchored elements for new margins and invalidates all slide acceptance. Template masters/theme parts are untouched; a template accent/font overrides the profile's accent/font.
- `slide.reorder`: moves one stable slide to an index; rechecks required.

For `slide.import`, omitting `template_path` selects a registered library entry by `template_id`. When `template_path` is supplied, it takes precedence over library lookup and is resolved against `asset_root`; CLI `apply-one` sets this to the working PPTX's parent directory. Prefer a relative path such as `inputs/template.pptx`. The request JSON's location does not affect resolution. The file must exist and its resolved path must stay inside `asset_root`; traversal or symlinks escaping that root are rejected with `Unsafe or missing template path`. Absolute paths inside the root also pass, but a direct `/agent-skills/...` path outside it fails. Copy an authorized source into that root before importing; do not loosen the path check. See the staging example in `template-rules.md`.

A duplicate ID/payload returns the original result; reused ID with a different payload is rejected. Stale bases are rejected. Scope grants never authorize a different path or presentation.

`plan --request plan.json` takes `{"intent":"data","content":{"blocks":[{"type":"text","chars":120},{"type":"chart"}],"modules":0}}` and returns the chosen layout, variant, resolved slots, alternatives and rejected layouts with reasons.


## 原生公式

`element.add_equation` 创建 LaTeX 对应的 PowerPoint 原生可编辑公式；`element.update` 的 latex 局部修改内容，`element.set_style` 支持 font_size/color/align。分式、矩阵、积分等示例与 Python API 见 [equations.md](equations.md)。公式不使用 slot.fill、普通文本替换或图片代替。

## 完整原生图表

类型、三个使用场景、分类/坐标/层级/股票数据格式与预览限制统一见 [charts.md](charts.md)。`office_pptx.py charts` 输出可调用目录。`element.add_chart`、`chart.replace_data`、`element.update` 与 `chart.change_type` 共用数据校验；不要自行拼 OOXML 或绕开工作簿更新。

`audit-charts deck.pptx --request deck-plan.json` verifies every slide's declared statistical_charts count against actual native chart parts and embedded workbooks. Missing plans produce inventory only. New decks use style MD and references; template import/fill operations apply only to editing an existing deck. The platform retains the latest two snapshots and previews; older file revisions are unavailable for restore.

风格查询：`list-styles [--category <category>] [--style-id <category/theme_id>]` 返回资料库路径，读取 design_path 并查看 reference_path；不导入 PPT 模板。统计图降级使用每页 chart_fallbacks（type、reason、element_ids），仅未支持类型可申报。
