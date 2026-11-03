# 编辑已有 PPT：导入与填充规则

此文仅用于编辑用户已有 PPT。新建演示文稿依据风格设计 MD 和参考图，不从 PPT 模板库选页；内置模板已经清空。

用户已有文件留在授权工作区。`init files/edit.pptx --template /path/to/existing.pptx` 保留原稿全部页面作为编辑起点；修改已有页用 slot.fill、element.update、chart.replace_data；仅在修改需求确实需要复制原稿页面时用 slide.import。用户原稿不写回技能目录。

## Manifest

`import-template` and library catalogs record:

- `source_sha256`: hash of `template.pptx`. A replaced file is refused.
- `slide_size`: page width and height in EMU.
- `slides[].role`: `cover`, `section`, `comparison`, `data`, `table` or `content` (a retrieval tag, not a hard constraint)
- `slides[].slots`: slot name to stable element id (`title`, `left_body`, `chart`, …)
- `slides[].preview`: preview image path under `previews/`
- `slides[].native_objects`: counts of chart, table, image, shape and text
- theme tokens, fonts, and precheck warnings (missing fonts, SmartArt, no LibreOffice)
- `visual_system`: curated actual background/text/accent/card/border colours, primary font and typography guide. This takes precedence over generic Office theme colours for new native additions, without repainting imported objects.

The heuristic is a draft. A `manifest.json` beside the PPTX overrides role and slots. Stable names are `da-slide:{slide_id}` and `da:{element_id}`. Later edits use those ids; they do not re-parse the whole package on every turn.

`match-template --template-id <installed-id> --role comparison` searches installed templates without a working PPTX. This compatibility command is for an explicitly supplied existing deck, not the new-deck workflow. Match category and exact slide size before combining pages; portrait infographics and print assets should use their own deck.

## Importing a user file or an unregistered local template

`import-template` analyzes a source file; it neither copies it nor registers it in `list-templates`. Registered library imports use `template_id` without `template_path`. For other files, stage a copy inside the working PPTX's parent directory (`asset_root`), then import by path. The CLI resolves `template_path` from that parent, regardless of the request JSON's location or the shell's current directory. If the working file is `output/deck.pptx`, `inputs/template.pptx` means `output/inputs/template.pptx`.

Example, run from the current task workspace root, with each shell command in a separate execute call. Replace `<uploaded-pptx>` with the actual authorized user file.

```bash
mkdir -p files/inputs tmp
cp "<uploaded-pptx>" files/inputs/template.pptx
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" import-template --template files/inputs/template.pptx --template-id user-brand --output files/inputs/manifest.json --license-note "working copy, not redistributed"
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" init files/project-report.pptx --presentation-id project-report
```

Read the generated manifest to choose a real `source_slide_id`; the request below assumes it contains `tpl-001`. The first import into an empty deck adopts the source slide size; a deck with existing pages refuses a different size. Save this request as `tmp/import-page-1.json`:

```json
{"presentation_id":"project-report","operation_id":"import-page-1","base_revision":0,"operation":"slide.import","template_id":"user-brand","template_path":"inputs/template.pptx","source_slide_id":"tpl-001","slide_id":"page-1"}
```

```bash
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" apply-one files/project-report.pptx --request tmp/import-page-1.json
```

Then inspect `status` → `template_slots[slide_id]`, fill real text slots and run `check-deck` after all pages are ready. A layout anchor is not a text object. See `operations.md#text-create-or-fill` for the two complete text workflows. Use the returned revision as the next operation's `base_revision`.

`Unsafe or missing template path` means the resolved file is missing or outside `asset_root`. Absolute paths inside that root are accepted, but direct `/agent-skills/...` paths outside it are rejected. `..` and symlinks cannot escape the root. Check both file existence and the working PPTX's parent, then correct the relative path or stage a copy there. This error does not mean the PPTX is corrupt or that permission mode must be changed. Preserve the original source; do not modify library files or copy user files into the public skill tree.

## Operations that keep the template

- `slide.import`: copies one library page, its layout relationship, pictures, and a **new** chart part plus workbook into the working PPTX. Editing the imported chart must not change the template or another imported copy. `insert_after` places the new slide. There is no per-page check gate; run `check-deck` after all pages are ready. The same library page may be imported more than once.
- `slot.fill`: replaces text in the named slot and keeps the first run's paragraph and character properties. If the text does not fit the box, the operation raises `capacity conflict` and changes nothing. Shorten the copy, choose another layout, or enlarge that one box with `element.adjust`. Do not shrink every font on the page.
- `chart.replace_data`: replaces categories and series on a native chart, including a chart that came from the template. Type changes still go through `chart.change_type`.
- `image.replace`: points one picture at a new PNG/JPEG of the same aspect ratio. Other slides that share the old image keep it.
- `presentation.recolor`: writes the semantic palette (`accent`, `background`, `text`, optional `muted`) into the theme color scheme and remaps direct RGB values that belong to that scheme. Pictures are not repainted. Protection follows the stable element id recorded in `protected_elements` at import, including after the shape is renamed to `da:…` and after `slide.import`. Any other direct color is left unchanged and listed in `recolor.unhandled`. A result with `recolor_complete: false` is not a finished global recolor.
- `element.adjust` and a limited `element.set_style` work on imported shapes by id. They change geometry or existing run/fill properties only. They do not rebuild the shape, and they do not mark it as a generated object that a profile change would restyle. `element.update` text and chart payloads on an imported object are rejected; use `slot.fill` and `chart.replace_data`.

`presentation.set_profile` still restyles objects this skill created. It does not replace the template theme, masters, or media.

## Creation versus existing-file editing

- New PPTX: read the style-design MD and reference images, plan native objects, create from scratch and audit native charts; see `style-authoring.md`. No PPT library import.
- Modify an existing PPTX: `init --template` → finish edits → `check-deck`. The opened file is not treated as a prototype library.
- Add pages while editing an existing PPTX: preserve its visual system, then `slide.add` → edit → `check-deck` after all requested edits.

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

## Whole-deck consistency

Prefer the primary template's own cover, section, content, chart, process and closing pages. If the alternate template is a better overall fit, switch before producing pages; do not interleave light business and dark dashboard styles. Record concrete structural gaps in both before a custom layout. Background replacement and decorative image scripts are not the default adaptation path. Fill and check the first page before spending time on optional assets.

The first imported template with a recorded `visual_system` establishes the working palette and primary font. Explicit user recolouring remains authoritative; importing another family does not reset that choice. `status.template_visual_system` records the reference style; `effective_palette` records current colours. Profile changes do not replace them. Imported backgrounds, fonts, chart styles and object geometry are preserved.

For figures built from shapes, changing text alone does not update bar lengths, segments or axes. Change the complete native representation consistently, choose an actual chart page, or choose a non-quantitative layout when data is unavailable. Keep meaning-to-colour mappings, units, number precision, footers, title positions and source conventions consistent across pages. Review the real whole-deck contact sheet before finalization.
