---
name: pptx-ooxml-author
description: Build and revise editable PowerPoint decks from a template when one fits, otherwise from the design system. Clone slides, fill slots, recolor through a persistent palette, and check real Impress renders. One approved atomic operation at a time.
compatibility: Optional platform PPTX extension; python-pptx, lxml, Pillow, PyMuPDF, LibreOffice Impress and fonts.
metadata:
  version: "1.5.1"
---

# PPTX authoring

This skill is optional. DeepAgents loads this file and calls the existing `execute` tool. The CLI is `/agent-skills/pptx-ooxml-author/scripts/office_pptx.py` in the platform, or `scripts/office_pptx.py` beside this file. Run it from the Thread user-file directory. Each command is one operation and one approval. Do not start another planner, Agent Loop, or free-form SVG generator.

## 1. Template selection

Before creating a deck, look for a user-uploaded PPTX and run `list-templates`. A template that can hold the content is the default. `import-template` only writes a manifest; it does not download a deck or copy an unlicensed file into this skill.

- New deck from a template: `init deck.pptx --presentation-id project-report --template brand.pptx --use template_reference`. Sample slides are prototypes. Clone what you need, then `presentation.discard_prototypes` so the delivery contains only generated slides.
- Edit a file the user already has: `init --template brand.pptx --use edit_existing` (the default). Keep their slides.
- No suitable template: `init` a blank deck and follow the fallback below.

## 2. Template workflow

`list-templates` / `import-template` → `init --use template_reference` → `match-template --role …` → for each page `slide.clone` → `slot.fill` / local edit → `check-slide` → next page → `presentation.discard_prototypes` → `check-slide` every remaining page of that revision → `request-deck-review` → `finalize`.

Decide the slide's purpose, then match an existing template slide. Clone it, fill it, then `check-slide` that generated page before cloning the next. Content slides must already have passed `check-slide`; otherwise `slide.clone` and `slide.add` raise `Check all affected slides before creating the next slide`. Prototype slides are layout sources and are not in that gate. Call `plan` and `slide.add` only when no template slide can hold the content. Do not claim the deck is finished while prototypes remain.

Keep masters, theme, layouts and native objects. Do not flatten a slide into a picture. Clone, relationship and recolor rules are in `references/operations.md` and `references/template-rules.md`.

`slot.fill` replaces slot text and keeps the first run. If the text does not fit, the operation raises `capacity conflict` and changes nothing. Shorten the copy, pick another template slide, or enlarge that one box with `element.adjust`.

## 3. Editing and recolor

Prefer editing the native object that is already on the slide. `element.adjust` moves, scales or rotates that object and does not restyle the page. Imported shapes accept `element.set_style` only for fill, text color, bold, italic, size and alignment. Picture, pattern and gradient-to-artwork fills are rejected rather than discarded. Charts and tables that came from the template use `chart.replace_data` and table-safe updates, not a rebuilt page.

`presentation.recolor` writes `accent`, `background`, `text` and optional `muted` into the theme and into direct colors that belong to that theme. It also stores `effective_palette`. New shapes, charts, tables and backgrounds read that palette before the profile palette. `template_tokens` keeps the original theme and is not overwritten. `presentation.set_profile` changes type scale and layout policy; it does not replace an effective palette. Restoring a revision restores the palette from that revision.

Logo and brand objects are protected by the id recorded at import, including after the shape is renamed to `da:…`. Clones inherit that protection. Pictures are not repainted. A complex object that might be a brand asset is returned as `needs_confirmation` and left unchanged. `recolor_complete: false` means say what was left unchanged.

Explicit styles on one object win over the palette. Do not rebuild a template page to change one color.

## 4. Fallback

Use this only when no template slide fits. Run `catalog` if the visual direction is unclear and pick a profile (`corporate_blue` when nothing else fits). A profile has no page content. Then `plan` ranks layouts and `slide.add` creates a blank slide of that layout. `check-slide` that page before adding another. Add native text, charts, tables, shapes, or `diagram.add` for a real relationship. SVG components are a last resort for marks native shapes cannot express; use only the reviewed registry. Layout ids, diagram fields, style keys and component parameters are in `references/`.

## 5. Check and deliver

Every successful mutation validates the package, stores a full PPTX revision and publishes it atomically. A failed operation does not change the file or the revision. Repeat the same `operation_id` after uncertain delivery; do not mint a new id for the same request.

`check-slide` after each generated page is part of the loop, not a batch at the end. Deterministic errors block the next content slide. Prototype slides in `template_reference` are not content slides. `presentation.discard_prototypes` changes the page set and clears those checks, so run `check-slide` again on every remaining generated slide of that exact revision before `request-deck-review` and `finalize`. `aesthetic_review` is advice and does not block delivery. A deck review for an older revision is stale history; it is not a pass for the current file. Fix a critical finding once, locally, then check again. Leave other findings for the user. Read `review-status` when convenient; do not wait on it slide by slide.

After prototypes are gone, run `request-deck-review` once for cross-slide consistency. Its findings are suggestions. `finalize` requires every remaining slide to have passed the automatic and content checks, and it refuses `template_reference` decks that still contain prototypes. Report outstanding critical findings, native objects versus SVG pictures, and anything the clone omitted or refused.

## Commands

One command per execute call:

```bash
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py list-templates
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py import-template --template brand.pptx --template-id brand --license-note "user file, not redistributed"
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py init deck.pptx --presentation-id project-report --template brand.pptx --use template_reference
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py match-template deck.pptx --role data
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py apply-one deck.pptx --request ../tmp/clone-page-1.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py apply-one deck.pptx --request ../tmp/fill-page-1.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py check-slide deck.pptx --slide-id page-1 --notes "Checked title and values" --intent "开篇"
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py apply-one deck.pptx --request ../tmp/clone-page-2.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py apply-one deck.pptx --request ../tmp/discard.json
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py check-slide deck.pptx --slide-id page-1 --notes "Rechecked after prototypes were discarded"
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py request-deck-review deck.pptx --base-revision 12
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py finalize deck.pptx --base-revision 12
```

```json
{"presentation_id":"project-report","operation_id":"op-004","base_revision":3,"operation":"slide.clone","source_slide_id":"tpl-004","slide_id":"page-4","insert_after":"page-3"}
```

```json
{"presentation_id":"project-report","operation_id":"op-005","base_revision":4,"slide_id":"page-4","operation":"slot.fill","slots":{"title":"执行架构","subtitle":"基于现有模板"}}
```

```json
{"presentation_id":"project-report","operation_id":"discard-001","base_revision":32,"operation":"presentation.discard_prototypes","redirects":{"tpl-001":"page-001","tpl-002":"page-004","tpl-003":"page-007"}}
```

Operation names and payloads: `references/operations.md`. Template behavior: `references/template-rules.md`. Layouts, diagrams, components and aesthetic review: `references/layout-rules.md`, `references/design-system.md`, `references/aesthetic-review.md`.
