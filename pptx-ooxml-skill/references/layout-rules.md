# Layout and review

Default 16:9 canvas, theme font Noto Sans CJK SC, 6–8% margins, shared text tokens, fixed named slots. Layout intents: cover, section, title_body, comparison, three_columns, data. Native template layout and theme take priority.

Checks include package/relationship integrity, canvas bounds, measured text height, font substitution, unexpected overlap, rendered text presence, explicit content evidence and actual visual review. Backgrounds, decorations and intentional same-group overlays are excluded from generic collision failures. Geometry checks are conservative heuristics, not visual certification.

Automatic/content checks gate the next slide. Global changes invalidate all affected slide checks. Normal mode can retain visual-pending slides; strict mode cannot advance until they are visually accepted. Both require actual visual acceptance for finalization. Three failed check attempts block further mutations until restore/intervention.
