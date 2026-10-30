# Template-first handling

Create from a template library when one fits. A blank deck is the fallback, used when the user did not supply a template and none of the recorded templates has a layout for the content. The working PPTX stores only generated pages.

## Where a template comes from

1. A directory under `templates/` whose `manifest.json` says `license.redistributable` is true. `list-templates` shows those directories and their candidate pages. Source files are immutable; import reads them and never writes XML, relationships or media back.
2. A file the user uploaded or named by path. Analyze it with `import-template`. Do not copy an unlicensed file into this skill. User templates stay in the authorized workspace; they are not written into the public `templates/` tree.
3. Edit a complete existing PPTX with `init deck.pptx --template /path/to/file.pptx`. That path keeps every slide the user already has.

Third-party files (including consulting decks that require attribution or forbid redistribution) stay outside the repository. The bundled `templates/consulting-brief/` deck is original project content for technical verification, not a substitute for a licensed brand template.

## Manifest

`import-template` and library catalogs record:

- `source_sha256`: hash of `template.pptx`. A replaced file is refused.
- `slide_size`: page width and height in EMU.
- `slides[].role`: `cover`, `section`, `comparison`, `data`, `table` or `content` (a retrieval tag, not a hard constraint)
- `slides[].slots`: slot name to stable element id (`title`, `left_body`, `chart`, …)
- `slides[].preview`: preview image path under `previews/`
- `slides[].native_objects`: counts of chart, table, image, shape and text
- theme tokens, fonts, and precheck warnings (missing fonts, SmartArt, no LibreOffice)

The heuristic is a draft. A `manifest.json` beside the PPTX overrides role and slots. Stable names are `da-slide:{slide_id}` and `da:{element_id}`. Later edits use those ids; they do not re-parse the whole package on every turn.

`match-template --template-id consulting-brief --role comparison` searches the library and does not need a working PPTX. Pick one page and `slide.import` it. Do not start from `slide.add` when a library page already fits.

## Operations that keep the template

- `slide.import`: copies one library page, its layout relationship, pictures, and a **new** chart part plus workbook into the working PPTX. Editing the imported chart must not change the template or another imported copy. `insert_after` places the new slide. Existing generated slides must have passed `check-slide` first, the same gate as `slide.add`. After import and fill, `check-slide` that new page before importing another. The same library page may be imported more than once.
- `slot.fill`: replaces text in the named slot and keeps the first run's paragraph and character properties. If the text does not fit the box, the operation raises `capacity conflict` and changes nothing. Shorten the copy, choose another layout, or enlarge that one box with `element.adjust`. Do not shrink every font on the page.
- `chart.replace_data`: replaces categories and series on a native chart, including a chart that came from the template. Type changes still go through `chart.change_type`.
- `image.replace`: points one picture at a new PNG/JPEG of the same aspect ratio. Other slides that share the old image keep it.
- `presentation.recolor`: writes the semantic palette (`accent`, `background`, `text`, optional `muted`) into the theme color scheme and remaps direct RGB values that belong to that scheme. Pictures are not repainted. Protection follows the stable element id recorded in `protected_elements` at import, including after the shape is renamed to `da:…` and after `slide.import`. Any other direct color is left unchanged and listed in `recolor.unhandled`. A result with `recolor_complete: false` is not a finished global recolor.
- `element.adjust` and a limited `element.set_style` work on imported shapes by id. They change geometry or existing run/fill properties only. They do not rebuild the shape, and they do not mark it as a generated object that a profile change would restyle. `element.update` text and chart payloads on an imported object are rejected; use `slot.fill` and `chart.replace_data`.

`presentation.set_profile` still restyles objects this skill created. It does not replace the template theme, masters, or media.

## Three documented paths

- New template-driven PPTX: `init` (empty working file) → library `match-template` → `slide.import` → edit → `check-slide`.
- Modify an existing PPTX: `init --template` → edit → `check-slide`. The opened file is not treated as a prototype library.
- No matching template page: `init` → `plan` → `slide.add` → edit → `check-slide`.

`effective_palette` is the deck's current color set. Recolor updates it in the same transaction as the PPTX. New native objects use it. The original theme tokens remain available as `template_tokens`.

Logo and brand shapes are recorded from their original name and description before they are given a `da:` id. Protection follows that id through import and recolor, not the renamed shape.

Internal jumps between template pages are rebound only when the target page has already been imported uniquely. A missing or ambiguous target fails with `unresolved_internal_link`. Jump targets are never guessed from slide order.

## What must remain true

- Masters, layouts, theme parts and image relationships stay in the package.
- The template source file is never modified.
- A failed operation does not publish a new revision.
- Charts, tables and pictures stay native. Do not flatten a slide or a template object into a picture.
- Opening an existing file with `init --template` keeps the original package as-is, including SmartArt, OLE and animation timing that this skill does not edit. `slide.import` is narrower: notes are omitted; OLE, SmartArt and any relationship without a share/copy policy fail the import instead of being guessed. Do not claim those objects survive import unless the import result listed them.
- After a batch of template edits, compare geometry, text, chart values and picture counts. A recolor that moves a box, or an import whose chart workbook is still the source workbook, is a failure.
