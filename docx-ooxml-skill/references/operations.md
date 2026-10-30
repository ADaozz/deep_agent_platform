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

- `append_paragraph`: `block_id`, `text` (≤3000 characters, no newline), optional existing `style`, `after_block_id`.
- `append_table`: uniform `rows`, at most 10×8, cell text ≤300 characters.
- `insert_image`: workspace PNG/JPEG, optional `width_cm` and `alt`.
- `insert_block`: one bounded paragraph XML fragment. Not the default way to add content.
- `replace_text`, `delete_block`, `set_paragraph_style`, `set_paragraph_format`, `set_table_format`, `set_page`: repairs. See the property names below.

`set_page` applies to the final section only. Values are twips (1 inch = 1440). `alignment` is `left`, `center` or `right`.

## Unsupported without an explicit error

SmartArt, OLE, fields, hyperlinks, merged or nested table cells, mixed run formatting and rich-text content controls are not silently flattened. The operation raises and the previous revision stays in place.
