# Atomic operations

Every Agent edit is one `apply-one` request:

```json
{"document_id":"report","operation_id":"op-0007","base_revision":6,"operation":{"type":"slot.fill","tag":"project_title","text":"季度业务报告"}}
```

`operation_id` is idempotent. `base_revision` must be the current revision. A failed operation does not publish a new revision. The offline batch plan is documented in `legacy-operations.md` and is not the default workflow.

## Template operations

- `section.clone`: `source_block_id` is a Heading paragraph. Copies that heading and the following blocks until the next heading of the same or higher level. Optional `heading` and `paragraphs` replace those cloned paragraphs and must match the section's body paragraph count. Optional `tables` rewrites cloned tables. Limit: 40 blocks and 20000 characters. Mixed character styles, merged cells, nested tables, images, fields and hyperlinks are copied only when the operation does not rewrite their text; a rewrite of those structures is rejected.
- `block.clone`: one top-level paragraph or table. `rows` on a table keeps the column count and header-row properties, replaces every paragraph in each cell, and rejects merged cells, nested tables, images, fields and hyperlinks.
- `slot.fill`: `tag` of one plain content control, or `block_id` of one paragraph, plus `text` of at most 3000 characters and no newline. The first run's properties are kept. Mixed character styles, images, fields, hyperlinks and nested content controls are rejected. Other content controls are left untouched.
- `part.replace_text`: `part` is `header` or `footer`, with exactly one `old` occurrence and bounded `new` text.
- `document.recolor`: `palette` requires `accent`, `background` and `text`, and may include `muted`. Theme colors and named styles change. Direct text color, highlight, and paragraph or cell shading that belong to the theme are remapped. Pictures are preserved. Anything else is returned in `unhandled` with `status` `unmapped` or `unsupported` and a `location`. `recolor_complete` is true only when that list is empty.

## Fallback content operations

Use these when the template has no suitable block.

- `append_paragraph`: `block_id`, `text` (≤3000 characters, no newline), optional existing `style`, `after_block_id`. Write a cross-reference inline as `{{ref:<ref_id>}}`; it becomes a Word REF field showing the target's label and number, for example `如{{ref:fig-arch}}所示` renders "如图 1所示".
- `append_table`: uniform `rows`, at most 60×8, cell text ≤300 characters. The first row repeats on every page. Tables of more than 10 rows may break across pages; set `keep_together` to override.
- `insert_image`: workspace PNG/JPEG, optional `width_cm` and `alt`.
- `insert_block`: one bounded paragraph XML fragment. Not the default way to add content.
- `replace_text`, `delete_block`, `set_paragraph_style`, `set_paragraph_format`, `set_table_format`, `set_page`: repairs. See the property names below.

`set_page` applies to the final section only. Values are twips (1 inch = 1440). `alignment` is `left`, `center` or `right`.

## Academic content operations

All are adding operations: they take a new `block_id` and an optional `after_block_id`. Numbers and cross-reference texts are Word fields; the engine recomputes them after every operation, and Word refreshes them on open.

- `insert_equation`: native, editable Office Math (OMML) from a LaTeX math body. `latex` (fractions, roots, sub/superscripts, sums, integrals, matrices, `cases`, `\mathrm`, `\left( \right)`), optional `font_size` (8-72 pt, default 12), `color` (RRGGBB), `align` (`left`/`center`/`right`, unnumbered only). `numbered: true` centres the formula and adds a right-aligned `(n)`; add `ref_id` to cite it with `{{ref:<ref_id>}}`.
  `{"type":"insert_equation","block_id":"eq-attn","latex":"\\mathrm{softmax}\\left(\\frac{QK^\\top}{\\sqrt{d_k}}\\right)V","numbered":true,"ref_id":"eq-attn"}`
- `insert_chart`: native Word chart with an embedded, editable workbook. `chart.type` is `column`, `bar`, `line`, `area`, `pie`, `doughnut`, `scatter` or `bubble`; `chart.categories` and `chart.series` (`[{"name":..., "values":[...]}]`, 1-12 series; scatter points `[x,y]`, bubble `[x,y,size]`); optional `chart.title`, `chart.colors` (RRGGBB list), `width_cm` (2-17, default 14), `height_cm` (2-20, default 8). Use real data only.
  `{"type":"insert_chart","block_id":"chart-hbm","chart":{"type":"column","title":"HBM 读写量","categories":["标准","FA-2"],"series":[{"name":"GB","values":[40.2,7.4]}]}}`
- `insert_caption`: a numbered caption paragraph. `label` (1-10 characters without spaces, such as `图`, `表`), `text` (≤300 characters), optional `ref_id`, `keep_with_next` (use for a table caption placed above its table). Numbering per label follows document order and is renumbered automatically when captions are inserted earlier.
  `{"type":"insert_caption","block_id":"cap-arch","label":"图","text":"系统架构","ref_id":"fig-arch"}`
- `insert_toc`: a Word table of contents built from Heading styles. Optional `levels` (1-3, default 3) and `title` (default `目录`). It is rebuilt from the current headings after every operation; page numbers are filled when Word opens the file. `update_toc` with the TOC `block_id` rebuilds it explicitly.

A `{{ref:...}}` whose target does not exist yet is allowed (forward reference) and shows `??`; the operation result lists it in `unresolved_references` until the target is added.

## Inline content: formatting, inline math and references

Paragraph text (and table cell text) accepts tokens: `{{ref:id}}` (number or bookmarked text), `{{pageref:id}}` (page number, computed by Word/LibreOffice at layout), `{{math:LaTeX}}` (inline Office Math; braces inside are matched).

Instead of `text`, `append_paragraph` (and table cells) may take `runs`, a list of up to 200 items:
- text run: `{"text": "...", "bold": true, "italic": true, "underline": true, "strike": true, "size": 12, "color": "C00000", "highlight": "yellow", "font": "SimHei", "spacing": 2, "vertical": "superscript"}` (`size` 6-72 pt, `spacing` letter spacing -5..20 pt, `vertical` `superscript`/`subscript`; all keys optional).
- inline formula: `{"math": "\\frac{a}{b}"}`
- reference: `{"ref": "sec-method"}` or `{"ref": "sec-method", "show": "page"}`

`append_paragraph` also takes `ref_id` to make the paragraph citable: for a heading, `{{ref:sec-method}}` shows its text and `{{pageref:sec-method}}` its page.

- `format_text`: format part of an existing paragraph. `block_id`, `match` (text inside one run), optional `occurrence` (1-based), plus any run format keys. If the text spans several runs, rewrite the paragraph with `runs`.

## Complex tables

- `insert_table` (adding): `columns` (1-12, each `{"width_twips": 2000, "align": "center"}`; total within the text width), `rows` (1-60), `header_rows` (default 1; repeated on every page and set in bold; count every row of a multi-row header), `borders` (`three_line` default, `grid`, `horizontal`, `none`), optional `font_size`. A cell is a string or `{"text"|"runs", "colspan", "rowspan", "align", "valign", "fill": "E2EFDA", ...run format keys}`. Spans follow HTML: a `rowspan` reserves its columns in the following rows, so those rows list only the remaining cells.
  `{"type":"insert_table","block_id":"tab-cmp","header_rows":2,"columns":[{"width_twips":1800},{"width_twips":2400},{"width_twips":2400}],"rows":[[{"text":"方法","rowspan":2},{"text":"复杂度","colspan":2}],["时间","显存"],["FlashAttention","{{math:O(N^2)}}",{"text":"O(N)","fill":"E2EFDA"}]]}`
- `set_table_cell` (repair): `block_id` of the table, 0-based `row` and `column` on the logical grid, then `text` or `runs`, optional `align`, `fill` and run format keys. Works for every table, including `append_table` ones; a position covered by a span is rejected.

## Image layout

- `insert_image` gains `float: "left"|"right"` (optional `gap_cm`, default 0.3): a square-wrapped picture that the following paragraphs flow around.
- `insert_image_row` (adding): 2-4 pictures side by side, `images: [{"path": "a.png", "caption": "(a) ...", "width_cm": 7}]`; captions are small centred text under each picture. Add an `insert_caption` after it for the figure number.

## Headers and footers

- `set_header_footer` (repair, no `block_id`): `part` `header` or `footer`, `text` or `runs`, optional `align` (default `center`), `size` (default 9 pt), `border` (a rule under the header or above the footer). Use `{{page}}` and `{{pages}}` for page numbers, e.g. `"第 {{page}} 页 共 {{pages}} 页"`. Calling it again replaces the same header or footer.

## Unsupported without an explicit error

SmartArt, OLE, fields and hyperlinks that the engine did not create, merged or nested table cells, mixed run formatting and rich-text content controls are not silently flattened. The operation raises and the previous revision stays in place.
