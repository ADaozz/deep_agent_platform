---
name: pptx-ooxml-author
description: Build and revise editable PowerPoint presentations incrementally with template-aware layouts, semantic design profiles, reviewed SVG components, stable element IDs, slide-level quality gates, actual Impress renders and steering-safe operations.
compatibility: Optional platform PPTX extension; python-pptx, lxml, Pillow, PyMuPDF, LibreOffice Impress and fonts.
metadata:
  version: "1.1.0"
---

# PPTX authoring protocol

This feature is optional and independently removable. DeepAgents loads this SKILL.md; use the existing `execute` tool to run the Python CLI located at `/agent-skills/pptx-ooxml-author/scripts/office_pptx.py` (or `scripts/office_pptx.py` relative to this skill outside the platform). Run from the Thread user-file directory. Sandbox `python` includes the required dependencies. Each command is one operation and one normal approval boundary; repeat the absolute script path and working directory on each execute call. Never create another planner, Agent Loop, model worker or free-form SVG generator.

1. Inspect explicit uploaded PPTX templates first and reuse their masters, layouts, placeholders, theme and resources. Uploaded template rules outrank any built-in profile. Never flatten a template or a whole slide into an image.
2. Run `catalog` once per deck when the visual direction is unclear: it lists validated profiles, semantic layouts and reviewed components. Pick a profile from task nature (analysis, launch, technical, financial). If undecidable, use `corporate_blue`. A profile is configuration; it never contains page content.
3. Define a concise narrative and design direction; do not precompute the whole deck body. Run `init deck.pptx --presentation-id project-report [--profile consulting] [--template brand.pptx]`. Each following command is approved independently.
4. Build one slide at a time with `apply-one deck.pptx --request /path/operation.json`. Decide the page's semantic intent first, then call `plan --request intent.json` to rank layouts for the content, then `slide.add` with that layout and, when the variant matters, its `modules` count. Never invent coordinates when a declared slot exists.
5. Prefer declared slots and profile style tokens. The six original layouts (`cover`, `section`, `title_body`, `comparison`, `three_columns`, `data`) remain available; additional semantic layouts (`statement`, `hero`, `bento`, `evidence`, `kpi`, `timeline`, `architecture`, `matrix`) must be listed by `layouts` before use. Layout capacity is a hard limit: split content or choose another layout instead of shrinking type below the profile minimum.
6. Native operations support text, rectangles/rounded rectangles/arrows/ellipses/lines, PNG/JPEG, bounded editable tables, editable bar/line/pie charts with an optional real `unit`, and `element.add_component` for reviewed SVG assets. Stable IDs persist in native OOXML metadata; never target the nth textbox.
7. Use SVG components only from the reviewed registry (`components`): component id, version, allowed variant, bounded params and declared text slots. A component is inserted as a PowerPoint **SVG picture** (the reviewed vector source embedded in the package) with a deterministic PNG fallback for viewers that do not render SVG pictures; it is never converted into native shapes. Declared text slots are emitted as native editable text boxes with stable child IDs. Never hand-write, patch or download SVG at runtime, and never reference external images, fonts or scripts. If no component fits, fall back to native shapes, a different layout or plain text.
8. Every successful mutation validates the candidate package, stores an immutable full PPTX revision and atomically publishes it. Failed validation never publishes a partial file. Recovery uses the shared artifact transaction store; restoring creates a NEW revision.
9. Complete each slide with `check-slide deck.pptx --slide-id slide-001 --notes "specific content checks"`. Actually inspect its structured text/chart values and supply concrete evidence. The engine renders the exact PPTX with Impress, then checks package integrity, bounds, measured overflow, collisions, WCAG contrast, font sizes, safe margins, slot/component capacity, chart metadata and profile consistency. Errors block; warnings and advisories are recorded and never substitute for visual review.
10. Every new or affected slide keeps `visual_review: pending` until real screenshot inspection. Screenshots are temporary cache files under the check result's `render_directory`; do not copy them into output files. A vision-capable model may inspect the real PNG and run `review-slide ... --passed`; a text-only model cannot. Request human inspection when necessary. Do not infer visual quality from successful ZIP creation.
11. At every atomic boundary, prefer steering for the identified slide/viewed revision. Re-read status and repair the intended slide, including historical slides. Layout, profile and component changes invalidate affected reviews; recheck before appending another slide. `follow_up` is a later task, not steering.
12. Before announcing a finished deck, run `finalize deck.pptx --base-revision N` after all slides pass content and actual visual checks. Report which objects are native editable PowerPoint objects and which are reviewed SVG picture components (graphic vector, native text), plus any preserved unsupported animations, SmartArt or objects.
13. Never trade numeric correctness, text readability or native editability for decoration. Do not fabricate data in charts or illustrative components; the reference artwork shipped for design study is not a template enum and must not be copied page-for-page.

## Commands and operations

No PPTX-specific tool is registered in the Agent. Examples (one command per execute call, from the file directory):

```bash
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py catalog
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py init deck.pptx --presentation-id project-report --profile consulting
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py plan deck.pptx --request ../tmp/plan.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py apply-one deck.pptx --request ../tmp/operation.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py check-slide deck.pptx --slide-id slide-001 --notes "Checked title, labels and values"
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py finalize deck.pptx --base-revision 12
```

Write request JSON, screenshots and intermediate renders to the Thread temporary directory. A request contains one operation:

```json
{"presentation_id":"project-report","operation_id":"op-004","base_revision":3,"slide_id":"slide-001","operation":"element.add_text","element":{"id":"title-001","text":"系统总体架构","style":"slide_title","anchor":"title"}}
```

One component call, with text that stays native:

```json
{"presentation_id":"project-report","operation_id":"op-009","base_revision":8,"slide_id":"slide-003","operation":"element.add_component","element":{"id":"flow","component":"process.chevron_flow","params":{"steps":4},"texts":{"step1":"需求确认","step2":"方案设计","step3":"试点验证","step4":"规模推广"},"geometry":{"x":0.06,"y":0.58,"w":0.88,"h":0.313}}}
```

Operations: `slide.add`, `slide.set_layout`, `slide.reorder`, `presentation.set_theme`, `presentation.set_profile`, `element.add_text|shape|image|table|chart|component`, `element.update`, `element.remove`. Details, capacities and component semantics are in `references/operations.md`, `references/layout-rules.md` and `references/design-system.md`; the component registry is the authority for ids, versions and parameters.

The UI is read-only, with thumbnails, selected-slide preservation, profile/layout labels, per-issue severity, independent per-slide check state and the formal screenshot as final visual evidence. All final content that can remain native does remain editable; component rasters are declared as such. Removing the feature removes its routes/skill/UI; it does not alter Agent execution or delete generated artifacts.
