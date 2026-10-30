# Template-first handling

Create from a template when one fits. A blank deck is the fallback, used when the user did not supply a template and none of the recorded templates has a layout for the content.

## Where a template comes from

1. A file the user uploaded or named by path. Load it with `init deck.pptx --template /path/to/file.pptx`. Do not copy it into this skill.
2. A directory under `templates/` whose `manifest.json` says `license.redistributable` is true. `list-templates` shows those directories.
3. `import-template --template file.pptx` writes a manifest only. It never downloads a deck and it refuses to write that manifest into `templates/` unless the license record allows redistribution.

Third-party files (including consulting decks that require attribution or forbid redistribution) stay outside the repository. The bundled `templates/consulting-brief/` deck is original project content for technical verification, not a substitute for a licensed brand template.

## Manifest

`import-template` and `init --template` record:

- `slides[].role`: `cover`, `section`, `comparison`, `data`, `table` or `content`
- `slides[].slots`: slot name to stable element id (`title`, `left_body`, `chart`, …)
- theme tokens, fonts, and precheck warnings (missing fonts, SmartArt, no LibreOffice)

The heuristic is a draft. A `manifest.json` beside the PPTX overrides role and slots. Stable names are `da-slide:{slide_id}` and `da:{element_id}`. Later edits use those ids; they do not re-parse the whole package on every turn.

`match-template deck.pptx --role comparison` returns the template slides of that role. Pick one and `slide.clone` it. Do not start from `slide.add` when a cloned layout already fits.

## Operations that keep the template

- `slide.clone`: copies the slide, its layout relationship, pictures (shared), and a **new** chart part plus workbook. Editing the clone's chart must not change the source chart. `insert_after` places the new slide. The same slide-acceptance gate as `slide.add` applies.
- `slot.fill`: replaces text in the named slot and keeps the first run's paragraph and character properties. If the text does not fit the box, the operation raises `capacity conflict` and changes nothing. Shorten the copy, choose another layout, or enlarge that one box with `element.adjust`. Do not shrink every font on the page.
- `chart.replace_data`: replaces categories and series on a native chart, including a chart that came from the template. Type changes still go through `chart.change_type`.
- `image.replace`: points one picture at a new PNG/JPEG of the same aspect ratio. Other slides that share the old image keep it.
- `presentation.recolor`: writes the semantic palette (`accent`, `background`, `text`, optional `muted`) into the theme color scheme and remaps direct RGB values that belong to that scheme. Pictures are not repainted. Protection follows the stable element id recorded in `protected_elements` at import, including after the shape is renamed to `da:…` and after `slide.clone`. Any other direct color is left unchanged and listed in `recolor.unhandled`. A result with `recolor_complete: false` is not a finished global recolor.
- `element.adjust` and a limited `element.set_style` work on imported shapes by id. They change geometry or existing run/fill properties only. They do not rebuild the shape, and they do not mark it as a generated object that a profile change would restyle. `element.update` text and chart payloads on an imported object are rejected; use `slot.fill` and `chart.replace_data`.

`presentation.set_profile` still restyles objects this skill created. It does not replace the template theme, masters, or media.

## Two ways to open a template

- `init --use edit_existing` keeps every sample slide. Use it when the user wants that file edited.
- `init --use template_reference` records those slides as prototypes. Clone the pages you need, fill them, then `presentation.discard_prototypes` with `redirects` for any internal slide jump that still points at a prototype. `finalize` refuses this mode while prototypes remain. The delivered file contains only the generated slides.

`effective_palette` is the deck's current color set. Recolor updates it in the same transaction as the PPTX. New native objects use it. The original theme tokens remain available as `template_tokens`.

Logo and brand shapes are recorded from their original name and description before they are given a `da:` id. Protection follows that id through clone and recolor, not the renamed shape.

## What must remain true

- Masters, layouts, theme parts and image relationships stay in the package.
- A failed operation does not publish a new revision.
- Charts, tables and pictures stay native. Do not flatten a slide or a template object into a picture.
- Opening `edit_existing` keeps the original package as-is, including SmartArt, OLE and animation timing that this skill does not edit. `slide.clone` is narrower: notes are omitted; OLE, SmartArt and any relationship without a share/copy policy fail the clone instead of being guessed. Do not claim those objects survive cloning unless the clone result listed them.
- After a batch of template edits, compare geometry, text, chart values and picture counts. A recolor that moves a box, or a clone whose chart workbook is still the source workbook, is a failure.
