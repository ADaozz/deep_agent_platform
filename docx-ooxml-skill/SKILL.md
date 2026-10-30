---
name: docx-ooxml-author
description: Generate and revise editable DOCX files from a template when one exists, using cloned sections, named styles and content controls. A blank package is only the fallback. One atomic operation at a time, with real LibreOffice pagination.
compatibility: Python 3.10+, lxml, Pillow, PyMuPDF, LibreOffice. Runs through execute; no separate Agent or MCP server required.
metadata:
  version: "0.4.0"
  maintainer: "local"
---

# DOCX authoring

This skill does not register a new Agent tool. Run `scripts/office_docx.py` through `execute`, from the Thread user-file directory. In the platform the path is `/agent-skills/docx-ooxml-author/scripts/office_docx.py`. One execute call is one operation. Do not add a planner, loop commands, or write the ZIP yourself.

Prefer a template's sections, paragraphs, named styles, content controls and tables. Author a WordprocessingML fragment only when the template has no suitable block and the atomic operations cannot express the change. Do not claim a page was checked unless you inspected its LibreOffice render.

## 1. Choose a template

Look for an uploaded DOCX before creating a blank file. `import-template` assigns block ids in memory, writes a manifest, and does not modify the user's file.

Template path, the default:

```sh
python "$S" import-template --template input.docx --template-id report --license-note "user file"
python "$S" init --document-id report --output report.docx --template input.docx
python "$S" status report.docx
python "$S" apply-one report.docx ../tmp/operation.json --asset-root .
python "$S" checkpoint report.docx
python "$S" finalize report.docx --base-revision N
```

Blank path, only when nothing suitable exists:

```sh
python "$S" init --document-id report --output report.docx
python "$S" apply-one report.docx ../tmp/operation.json --asset-root .
python "$S" checkpoint report.docx
python "$S" finalize report.docx --base-revision N
```

`$S` is the CLI path above. Repeat it on every execute call.

## 2. Template operations

One call is one bounded semantic operation. `section.clone` may copy the blocks of one heading section inside that limit (40 blocks, 20000 characters). It is not permission to submit the whole document, a batch, or a ZIP write.

- `section.clone` copies a heading and the blocks up to the next same-or-higher heading, including their styles.
- `block.clone` copies one paragraph or table. `rows` replaces cell text and does not leave older paragraphs behind.
- `slot.fill` fills one content control `tag`, or one paragraph `block_id`. Other content controls stay as they are. Rich text, nested controls, mixed run styles, images, fields and hyperlinks are rejected until a lossless edit exists.
- `part.replace_text` changes one occurrence in the header or the footer.
- `document.recolor` updates the theme and named styles, plus direct colors and shading that belong to that theme. Pictures are preserved. `unhandled` lists `unmapped` and `unsupported` colors with a location. `recolor_complete: true` means that list is empty.

Word repaginates. Do not insert page breaks to manufacture a page count.

## 3. Fallback

`append_paragraph`, `append_table` and `insert_image` add content with an existing style. `insert_block` is one bounded paragraph fragment, not the normal way to write. Repairs (`replace_text`, `set_paragraph_style`, `set_paragraph_format`, `delete_block`, `set_table_format`, `set_page`) stay available. Payloads are in `references/operations.md`. The old batch plan is in `references/legacy-operations.md` and is not this workflow.

## 4. Revisions and checkpoints

Use the current `base_revision`, a stable `block_id` and a stable `operation_id`. Repeat the same request after uncertain delivery. A conflict means read `status` and rebase. A failed operation does not increment the revision. Restore creates a new revision.

The engine asks for a checkpoint after three additions and at each section end. Do not skip it by writing the package yourself. Failed automatic checks block further additions until the current content is repaired.

## 5. Render and finish

`checkpoint` renders with LibreOffice and records pages. Inspect those images. A vision-capable reviewer can call `review-page` with notes from that image. Text-only inspection cannot pass a page. A missing renderer is a failed checkpoint.

`finalize --base-revision` requires the final checkpoint and a reviewed final page. The file can be downloaded while visual review is pending; call it a draft until the pages have been seen. Never execute macros or embedded programs from a template.

## Request

```json
{"document_id":"report","operation_id":"op-0007","base_revision":6,"operation":{"type":"section.clone","source_block_id":"template-0013","block_id":"sec-next","heading":"实施范围","paragraphs":["第一段。","第二段。"]}}
```

Structures this skill will not silently simplify: merged or nested tables, mixed character styles, images inside a rewritten paragraph, fields, hyperlinks, and rich-text content controls. Those operations raise and leave the last good file in place.
