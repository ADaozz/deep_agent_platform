---
name: pptx-ooxml-author
description: Build and revise editable PowerPoint presentations incrementally with template-aware layouts, stable element IDs, slide-level quality gates, actual Impress renders and steering-safe operations.
compatibility: Optional platform PPTX extension; python-pptx, lxml, Pillow, PyMuPDF, LibreOffice Impress and fonts.
metadata:
  version: "1.0.0"
---

# PPTX authoring protocol

This feature is optional and independently removable. DeepAgents loads this SKILL.md; use the existing `execute` tool to run the Python CLI located at `/agent-skills/pptx-ooxml-author/scripts/office_pptx.py` (or `scripts/office_pptx.py` relative to this skill outside the platform). Run from the Thread user-file directory. Sandbox `python` includes the required dependencies. Each command is one operation and one normal approval boundary; repeat the absolute script path and working directory on each execute call. Never create another planner, Agent Loop or model worker.

1. Discover and inspect explicit uploaded PPTX templates first; reuse their masters, layouts, placeholders, theme and resources. Never flatten a template or an entire slide into an image.
2. Determine a concise narrative and theme; do not precompute the whole deck body. Run `init deck.pptx --presentation-id project-report`. This follows normal execute approvals; each subsequent command is approved independently in ask mode.
3. Build one slide at a time, using `apply-one deck.pptx --request /path/to/temporary-operation.json` for one slide/element operation. Use `presentation_id`, `slide_id`, element `id`, a unique `operation_id` and exact `base_revision`. Retry an uncertain delivery with the same ID and payload. Never put a whole deck in one call.
4. Prefer one of six layout intents: `cover`, `section`, `title_body`, `comparison`, `three_columns`, `data`. Use named layout anchors and theme style tokens instead of invented coordinates. Geometry, when necessary, uses normalized finite `{x,y,w,h}` within [0,1].
5. Native element operations support text, rectangles/rounded rectangles/arrows/ellipses, PNG/JPEG, bounded editable tables and editable bar/line/pie charts. Stable IDs persist in native OOXML metadata; never target the nth textbox. Background/decoration layers and declared overlap groups distinguish intentional stacking from accidental obstruction.
6. Every successful mutation validates the candidate package, stores an immutable full PPTX revision and atomically publishes it. Failed validation never publishes a partial file. Recovery uses the shared artifact transaction store; restoring creates a NEW revision. The `.pptx-state/` history is part of the artifact.
7. Complete each slide using `check-slide deck.pptx --slide-id slide-001 --notes "specific content checks"`. Actually inspect its structured text/chart values and supply specific content-review evidence. The engine renders the exact PPTX using Impress, checks geometry, text bounds, font availability, overlaps and actual PDF text. Automatic failure blocks adding the next slide. Only fix affected elements and recheck; after three failed checks restore or ask for intervention.
8. Every new or affected slide retains `visual_review: pending` until actual screenshot inspection. Screenshots are temporary cache files under the check result’s `render_directory`; do not copy them into output files. A vision-capable model may inspect the real PNG then run `review-slide deck.pptx --slide-id slide-001 --revision N --reviewer vision_model --notes "actual visual evidence" --passed`; a text-only model cannot. Request human inspection when necessary. Do not infer visual quality from successful ZIP creation. Normal mode permits the next slide after automatic/content checks while keeping visual review pending; strict mode waits for visual acceptance too.
9. Treat rapid SVG/HTML preview as feedback, not final PowerPoint layout. Complete-slide Impress renders replace it. Unsupported template objects use the last formal screenshot, never guessed reconstruction.
10. At every atomic tool boundary, prioritize steering for the identified slide/viewed revision. Re-read status and repair the intended slide, including historical slides. Layout/theme changes invalidate affected reviews; recheck before appending another slide. `follow_up` is a later task, not steering.
11. Before announcing a finished deck, run `finalize deck.pptx --base-revision N` after all slides pass content and actual visual checks. Unverified slides remain clearly marked drafts. Report unsupported animations, SmartArt or objects preserved without editing, rather than claiming support.

## Commands and operations

There are no PPTX-specific tools registered in the Agent. The scripts belong to this skill. Example commands (run one per execute call, from the file directory):

```bash
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py init deck.pptx --presentation-id project-report
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py status deck.pptx
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py apply-one deck.pptx --request ../tmp/operation.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py check-slide deck.pptx --slide-id slide-001 --notes "Checked title, labels and values"
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py finalize deck.pptx --base-revision 12
```

Write request JSON, screenshots and intermediate renders to the Thread temporary directory. `OFFICE_RENDER_TMP` selects the platform's private temporary render cache automatically. The final PPTX is the deliverable. A request file contains one operation:

```json
{"presentation_id":"project-report","operation_id":"op-004","base_revision":3,"slide_id":"slide-001","operation":"element.add_text","element":{"id":"title-001","text":"系统总体架构","style":"slide_title","anchor":"title"}}
```

Other allowed operations: `slide.add`, `slide.set_layout`, `slide.reorder`, `presentation.set_theme`, `element.add_shape`, `element.add_image`, `element.add_table`, `element.add_chart`, `element.update`, `element.remove`. See references for bounds and supported template behavior.

The UI is read-only, with thumbnails, selected-slide preservation and independent per-slide check state. All final content remains native and editable in the PPTX. Removing the feature removes its routes/skill/UI; it does not alter Agent execution or delete generated artifacts.
