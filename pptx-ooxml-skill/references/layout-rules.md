# Layouts, design quality and review

Default 16:9 canvas. Layouts are declarative JSON in `design/layouts/`; profiles adapt their normalised slots to page margins. Profiles, slots and capacities are listed by `catalog`/`layouts`.

## Layout intents

Legacy, always retained: `cover`, `section`, `title_body`, `comparison`, `three_columns`, `data`.

Semantic additions: `statement` (single decisive sentence), `hero` (cover with visual column), `bento` (3/4/6 module tiles), `evidence` (takeaway + exhibit + reading), `kpi` (3/4 metrics and labels), `timeline` (3/4/5 phases), `architecture` (3/4 stacked layers), `matrix` (2×2 quadrants).

A layout declares stable id/version, intent, density, slot rectangles, accepted content types, role/style token, per-slot character and line capacity, and optional module-count variants. `layout_resolver.plan()` normalises a content summary, rejects layouts that cannot hold it, scores the rest deterministically (intent, exact module count, density, tight fit, profile preference) and returns profile-adjusted slots plus alternatives and rejection reasons.

## Rules that are enforced

- A slot only accepts declared content types; text capacity is characters **and** lines.
- Overflow is never solved by shrinking type: split the content, choose a roomier layout or reduce it.
- `slide.set_layout` refuses to migrate when an anchored element has no target slot or its text exceeds the new slot capacity. It never deletes and re-creates content to hide a migration problem.
- Profile margins change slot geometry; anchored elements move with their slot on profile or layout changes.
- `modules` selects a layout variant; a layout without that variant is rejected with the available counts.

## Quality severities

- `error` — unreadable, lost or wrong content: package/render failure, out of bounds, proven text overflow, unexpected content overlap, missing font, unregistered style/layout/component, slot or component capacity exceeded, contrast below the error threshold, profile size drift, unlabelled chart series, missing rendered text. Blocks acceptance.
- `warning` — profile deviation that must be fixed or explicitly accepted: small type, contrast below the normal-text threshold, safe-margin intrusion, component version/source drift, component below its reviewed minimum size.
- `advisory` — optimisation only: alignment drift, uneven spacing, missing chart unit, dense table cell.

Background and decoration layers never trigger collision errors, and intentional same-`overlap_group` stacking is excluded. Composition, visual weight and narrative rhythm are not scored numerically; they stay with the actual rendered screenshot review. A successful ZIP or a passing numeric score is never visual certification.
