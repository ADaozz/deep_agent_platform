---
name: pptx-ooxml-author
description: Build and revise editable PowerPoint presentations incrementally with native charts, shapes, tables, connectors and diagrams first, bounded style presets and natural-language fine-tuning, template-aware layouts, design profiles, reviewed SVG components as a fallback, stable element IDs, actual Impress renders, host-side multimodal aesthetic review and steering-safe operations.
compatibility: Optional platform PPTX extension; python-pptx, lxml, Pillow, PyMuPDF, LibreOffice Impress and fonts.
metadata:
  version: "1.2.0"
---

# PPTX authoring protocol

This feature is optional and independently removable. DeepAgents loads this SKILL.md; use the existing `execute` tool to run the Python CLI located at `/agent-skills/pptx-ooxml-author/scripts/office_pptx.py` (or `scripts/office_pptx.py` relative to this skill outside the platform). Run from the Thread user-file directory. Sandbox `python` includes the required dependencies. Each command is one operation and one normal approval boundary; repeat the absolute script path and working directory on each execute call. Never create another planner, Agent Loop, model worker or free-form SVG generator.

1. Inspect explicit uploaded PPTX templates first and reuse their masters, layouts, placeholders, theme and resources. Uploaded template rules outrank any built-in profile. Never flatten a template or a whole slide into an image.
2. Run `catalog` once per deck when the visual direction is unclear: it lists validated profiles, semantic layouts and reviewed components. Pick a profile from task nature (analysis, launch, technical, financial). If undecidable, use `corporate_blue`. A profile is configuration; it never contains page content.
3. Define a concise narrative and design direction; do not precompute the whole deck body. Run `init deck.pptx --presentation-id project-report [--profile consulting] [--template brand.pptx]`. Each following command is approved independently.
4. Build one slide at a time with `apply-one deck.pptx --request /path/operation.json`. Decide the page's semantic intent first, then call `plan --request intent.json` to rank layouts for the content, then `slide.add` with that layout and, when the variant matters, its `modules` count. Slots are the starting point, not a cage: use explicit `geometry` when the content or the user's request does not fit the declared slots, keeping elements inside the safe margins.
5. Prefer declared slots and profile style tokens, but adapt when they get in the way. The six original layouts (`cover`, `section`, `title_body`, `comparison`, `three_columns`, `data`) remain available; additional semantic layouts (`statement`, `hero`, `bento`, `evidence`, `kpi`, `timeline`, `architecture`, `matrix`) must be listed by `layouts` before use. When a slot cannot hold the content, split it, choose another layout or place the element with explicit geometry; only the profile's minimum font size is firm.
6. Native first. Prefer, in order: native text, native chart (bar/line/pie with a real `unit`), native table, native shapes, and `diagram.add` for any flow, process or architecture relation. Defaults are deliberately plain (no heavy borders, flat fills, light gridlines, labelled data points, thin table dividers); presets are starting points, and any style within the `element.set_style` vocabulary is fine when the page benefits or the user asks. Stable IDs persist in native OOXML metadata; never target the nth textbox.
7. Diagrams: declare nodes, edges and a `relation` per edge (`flows_to`, `calls`, `data_flow`, `depends_on`, `returns`, `bidirectional`, `association`); the engine chooses connection sites, arrowheads and routes and binds every connector to both nodes. The structure is yours: presets (`layered`, `flow`, `tree`) are defaults, and you may set `spacing`, `node_size`, `node_shape`, per-node `shape`, or give every node a `position` for any arrangement (hub, matrix, free layout) — connectors are still routed and bound for you. Do not draw loose `line`/`arrow` shapes between boxes to suggest a relation; the checker reports unbound connectors bridging two blocks as errors. If an edge cannot be routed, move nodes, re-rank or say it in a note; never change the real relationship to make the picture tidier.
8. Fine-tuning from natural language maps to bounded operations on the current geometry, never a page re-layout: "缩小 15%" → `element.adjust {scale:0.85}` (icons/images keep aspect and centre), "往右 0.2 英寸" → `element.adjust {dx_in:0.2}`, "旋转 5°" → `{rotate:5}`, "换成折线图" → `chart.change_type {type:"line"}` (data, series, position and ID kept; embedded workbook verified), "表头蓝底白字" → `element.set_style {style:{header_fill:"246BCE",header_text:"FFFFFF"}}`, or a preset via `element.apply_preset` (`style_presets` in `catalog`). Explicit user styles win over presets and survive profile changes. Shapes may use a soft shadow (`shadow:"soft"`) or a two-colour linear gradient (`gradient:{start,end,angle}`) when they help; keep them sparing. Glow, bevel, reflection and 3-D are not available.
9. SVG components are a fallback for what native objects cannot express, and only from the reviewed registry (`components`): component id, version, allowed variant, bounded params and declared text slots. A component is inserted as a PowerPoint **SVG picture** with a deterministic PNG fallback; it is never converted into native shapes, and its text slots stay native editable text boxes. Never hand-write, patch or download SVG at runtime.
10. Every successful mutation validates the candidate package, stores an immutable full PPTX revision and atomically publishes it. Failed validation never publishes a partial file. Recovery uses the shared artifact transaction store; restoring creates a NEW revision.
11. Complete each slide with `check-slide deck.pptx --slide-id slide-001 --notes "specific content checks" [--intent "page purpose"] [--constraint "user-required layout"]`. Actually inspect its structured text/chart values and supply concrete evidence. The engine renders the exact PPTX with Impress and records three results: `technical_review` (bounds, overflow, contrast, font sizes, margins, capacity, chart metadata, profile), `visual_integrity` (render, rendered text, collisions, connector binding) and `aesthetic_review`. Deterministic errors block the next slide; warnings are recorded.
12. `aesthetic_review` is advice from the platform host and never blocks building or delivering the deck. After each successful check the host re-renders the exact revision and asks a vision-capable model for findings in the background; you cannot record a pass yourself (`review-slide` is only an advisory note). Do not wait slide by slide — keep building, and read results with `review-status deck.pptx [--slide-id …]` when convenient, following `next_action`:
    - `critical` findings (unreadable, wrong or misleading content) are the only ones you fix without asking: one small local repair, then `check-slide` again. If the same critical finding survives two rounds (`auto_repair: false`), stop and tell the user.
    - every other finding (`major`, `minor`, deck consistency) is a suggestion for the user. Do not edit for it on your own; list the notable ones briefly when you deliver. The user can send any of them back to you from the preview ("交给 Agent 修复") or by message, and then you act on it.
    - `pending`, `uncertain` or `unavailable` never delay you; just do not claim visual acceptance you did not get.
    See `references/aesthetic-review.md`.
13. At every atomic boundary, prefer steering for the identified slide/viewed revision. Re-read status and repair the intended slide, including historical slides. Layout, profile and component changes invalidate affected reviews; recheck before appending another slide. `follow_up` is a later task, not steering.
14. Optionally run `request-deck-review deck.pptx --base-revision N` once all slides are built for a cross-slide consistency read (design system, repetition, card overuse, density rhythm, cover/summary); its findings are suggestions for the user. Run `finalize deck.pptx --base-revision N` when every slide has passed the automatic and content checks — visual review state does not gate it. The finalize result lists outstanding critical findings and suggestion counts; report them to the user in one short paragraph and let them decide. Report which objects are native editable PowerPoint objects and which are reviewed SVG picture components, plus any preserved unsupported animations, SmartArt or objects.
15. Never trade numeric correctness, text readability or native editability for decoration. Do not fabricate data in charts or illustrative components; the reference artwork shipped for design study is not a template enum and must not be copied page-for-page.

## Commands and operations

No PPTX-specific tool is registered in the Agent. Examples (one command per execute call, from the file directory):

```bash
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py catalog
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py init deck.pptx --presentation-id project-report --profile consulting
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py plan deck.pptx --request ../tmp/plan.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py apply-one deck.pptx --request ../tmp/operation.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py check-slide deck.pptx --slide-id slide-001 --notes "Checked title, labels and values" --intent "开篇点题"
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py review-status deck.pptx --slide-id slide-001
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py request-deck-review deck.pptx --base-revision 12
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

A native architecture diagram (the engine computes every coordinate of every connector):

```json
{"presentation_id":"project-report","operation_id":"op-012","base_revision":11,"slide_id":"slide-004","operation":"diagram.add","diagram":{"id":"arch","geometry":{"x":0.06,"y":0.22,"w":0.6,"h":0.68},"preset":"layered","nodes":[{"id":"web","rank":0,"label":"Web 交互层","detail":"任务下达、过程可视"},{"id":"agent","rank":1,"label":"Agent 执行层"},{"id":"tool","rank":2,"label":"Tool 能力层"},{"id":"sandbox","rank":2,"label":"Sandbox"}],"edges":[{"source":"web","target":"agent","relation":"calls"},{"source":"agent","target":"tool","relation":"calls"},{"source":"tool","target":"sandbox","relation":"flows_to","label":"执行"}]}}
```

Operations: `slide.add`, `slide.set_layout`, `slide.reorder`, `presentation.set_theme`, `presentation.set_profile`, `element.add_text|shape|image|table|chart|component`, `element.update`, `element.remove`, `element.adjust`, `element.set_style`, `element.apply_preset`, `chart.change_type`, `diagram.add`, `diagram.update`. Details, capacities and component semantics are in `references/operations.md`, `references/layout-rules.md`, `references/design-system.md` and `references/aesthetic-review.md`; the component registry is the authority for ids, versions and parameters.

The UI is read-only for content. It shows thumbnails, per-slide technical/integrity/aesthetic status, model findings with severity, region and element IDs, suggestions, re-review, explicit human accept/reject/dismiss, and the deck consistency summary; the formal screenshot is the visual evidence. All final content that can remain native does remain editable; component rasters are declared as such. Removing the feature removes its routes/skill/UI; it does not alter Agent execution or delete generated artifacts.
