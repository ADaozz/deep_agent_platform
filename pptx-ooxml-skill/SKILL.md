---
name: pptx-ooxml-author
description: Build and revise editable PowerPoint presentations incrementally with template-aware layouts, stable element IDs, slide-level quality gates, actual Impress renders and steering-safe operations.
compatibility: Optional platform PPTX extension; python-pptx, lxml, Pillow, PyMuPDF, LibreOffice Impress and fonts.
metadata:
  version: "1.0.0"
---

# PPTX authoring protocol

This feature is optional and independently removable. The existing main Agent drives it through registered tools. Never create another planner, Agent Loop or model worker.

1. Discover and inspect explicit uploaded PPTX templates first; reuse their masters, layouts, placeholders, theme and resources. Never flatten a template or an entire slide into an image.
2. Determine a concise narrative and theme; do not precompute the whole deck body. Call `pptx_init`. In ask mode its one native approval authorizes only this presentation path/session. It does not authorize generic execute, network, arbitrary files or other presentations. A rejected/unapproved init grants nothing.
3. Build one slide at a time, using `pptx_apply_one` for one slide/element operation. Use `presentation_id`, `slide_id`, element `id`, a unique `operation_id` and exact `base_revision`. Retry an uncertain delivery with the same ID and payload. Never put a whole deck in one call.
4. Prefer one of six layout intents: `cover`, `section`, `title_body`, `comparison`, `three_columns`, `data`. Use named layout anchors and theme style tokens instead of invented coordinates. Geometry, when necessary, uses normalized finite `{x,y,w,h}` within [0,1].
5. Native element operations support text, rectangles/rounded rectangles/arrows/ellipses, PNG/JPEG, bounded editable tables and editable bar/line/pie charts. Stable IDs persist in native OOXML metadata; never target the nth textbox. Background/decoration layers and declared overlap groups distinguish intentional stacking from accidental obstruction.
6. Every successful mutation validates the candidate package, stores an immutable full PPTX revision and atomically publishes it. Failed validation never publishes a partial file. Recovery uses the shared artifact transaction store; restoring creates a NEW revision. The `.pptx-state/` history is part of the artifact.
7. Complete each slide using `pptx_check_slide(presentation_id, slide_id, content_notes)`. Actually inspect its structured text/chart values and supply specific content-review evidence. The engine renders the exact PPTX using Impress, checks geometry, text bounds, font availability, overlaps and actual PDF text. Automatic failure blocks adding the next slide. Only fix affected elements and recheck; after three failed checks restore or ask for intervention.
8. Every new or affected slide retains `visual_review: pending` until actual screenshot inspection. A vision-capable model may inspect the real PNG then call `pptx_review`; a text-only model cannot. Request human inspection when necessary. Do not infer visual quality from successful ZIP creation. Normal mode permits the next slide after automatic/content checks while keeping visual review pending; strict mode waits for visual acceptance too.
9. Treat rapid SVG/HTML preview as feedback, not final PowerPoint layout. Complete-slide Impress renders replace it. Unsupported template objects use the last formal screenshot, never guessed reconstruction.
10. At every atomic tool boundary, prioritize steering for the identified slide/viewed revision. Re-read status and repair the intended slide, including historical slides. Layout/theme changes invalidate affected reviews; recheck before appending another slide. `follow_up` is a later task, not steering.
11. Before announcing a finished deck, call `pptx_finalize` after all slides pass content and actual visual checks. Unverified slides remain clearly marked drafts. Report unsupported animations, SmartArt or objects preserved without editing, rather than claiming support.

## Commands and operations

The platform exposes `pptx_init`, `pptx_apply_one`, `pptx_status`, `pptx_check_slide`, `pptx_review`, `pptx_restore`, `pptx_finalize`. CLI equivalents live in `scripts/office_pptx.py` for external authoring; CLI execution does not grant platform tool permissions.

```json
{"presentation_id":"project-report","operation_id":"op-004","base_revision":3,"slide_id":"slide-001","operation":"element.add_text","element":{"id":"title-001","text":"系统总体架构","style":"slide_title","anchor":"title"}}
```

Other allowed operations: `slide.add`, `slide.set_layout`, `slide.reorder`, `presentation.set_theme`, `element.add_shape`, `element.add_image`, `element.add_table`, `element.add_chart`, `element.update`, `element.remove`. See references for bounds and supported template behavior.

The UI is read-only, with thumbnails, selected-slide preservation and independent per-slide check state. All final content remains native and editable in the PPTX. Removing the feature unregisters its tools/routes/skill/UI; it does not alter Agent execution or delete generated artifacts.
