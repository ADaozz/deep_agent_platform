# Element protocol

Every mutation requires presentation_id, operation_id, base_revision and one operation. Slide-specific operations require slide_id. An element has id plus geometry or a layout anchor. Style tokens: cover_title (40 pt), slide_title (30 pt), body (20 pt), card_title (24 pt), caption (12 pt).

- Text: text ≤1200 characters, style token. Updates preserve native text objects.
- Shape: shape = rectangle, rounded_rectangle, arrow or ellipse; fill_token = accent/card/background/text/muted. Declare layer = content/background/decoration. An overlap_group explicitly permits known stacking. Do not use it to hide unintended overlap.
- Image: workspace-relative PNG/JPEG path ≤12 MB; geometry must preserve aspect ratio.
- Table: uniform rows ≤10×8, each cell ≤160 characters; saved as a native table.
- Chart: chart = {type:bar|line|pie,categories:[...],series:[{name,values}]}; ≤12 categories and ≤4 finite series. Saved as chart XML plus an editable embedded workbook.
- Update: target element.id; change text/style/geometry/fill or layer metadata. Remove targets one stable ID.
- slide.add: layout intent and optional actual template layout_index. Existing slide acceptance gates further additions.
- slide.set_layout: changes the slide intent and repositions known anchored elements. It does not erase content or flatten objects. Remove/recreate only unwanted elements.
- presentation.set_theme: corporate_blue or slate_dark, applies tokens to generated native objects and invalidates all slide acceptance.
- slide.reorder: moves one stable slide to index; rechecks required.

A duplicate ID/payload returns the original result; reused ID with different payload is rejected. Stale bases are rejected. Scope grants never authorize a different path or presentation.
