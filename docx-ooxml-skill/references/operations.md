# Operations contract v0.1

Every plan is JSON:

```json
{"source_sha256":"64-character sha256 from inspect", "operations":[{"op":"..."}]}
```

All indices are **zero based**, interpreted at the time the operation runs. For edits to template, include `expected_paragraph` or `expected_text` safeguards. If earlier operations insert paragraphs, indices of later operations can shift. Prefer one batch of stable replacements followed by append/insert operations. Re-inspect after each version for major edits.

## `replace_text`

```json
{"op":"replace_text", "paragraph_index":0,
 "expected_paragraph":"项目名称：{{NAME}}", "old":"{{NAME}}", "new":"AI Agent"}
```

Matches exactly one occurrence of `old` in visible `w:t` text of that paragraph, even across multiple `w:r`/`w:t` nodes. Preserves formatting of unchanged text; replacement inherits formatting of its first matched text node. Does not support field codes, drawings in paragraph.

## `insert_blocks`

```json
{"op":"insert_blocks", "after_body_index":-1, "blocks":[
 "<w:p xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr><w:r><w:t>项目背景</w:t></w:r></w:p>"
]}
```

`after_body_index` refers to `body_blocks` from `inspect`; `-1` inserts at start. Each fragment must be a complete `w:p` or `w:tbl` using the canonical WordprocessingML namespace. Referenced styles must exist in the template or in the bundled blank style pack; missing `w:pStyle` inside fragments is not yet checked, so be careful to use the `styles` list from inspect. `w:sectPr`, `w:drawing`, `w:object`, field codes, external relationship attributes, altChunk are disallowed in generic fragments.

### Example table fragment

```xml
<w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
  <w:tblGrid><w:gridCol w:w="4000"/><w:gridCol w:w="4000"/></w:tblGrid>
  <w:tr>
    <w:tc><w:tcPr><w:tcW w:w="4000" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc>
    <w:tc><w:tcPr><w:tcW w:w="4000" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>进度</w:t></w:r></w:p></w:tc>
  </w:tr>
</w:tbl>
```

For complex tables, explicitly set table width, grid columns, cell widths, borders, row split policy, and column spans. Never assume all rows are fixed height or all merged cells fit.

## `replace_sdt_text`

```json
{"op":"replace_sdt_text","tag":"project_name","new":"下一代 Agent 工作台"}
```

For **plain-text** content controls (`w:sdt`) with a unique `w:tag`. This preserves the first text Run's properties; does not handle rich-text/table/empty SDTs or all SDT variants. Preferred to fragile positional replacements when preparing templates yourself.

## `set_paragraph_format`

```json
{"op":"set_paragraph_format","paragraph_index":0,"expected_text":"项目概述",
 "alignment":"center","space_before_twips":240,"space_after_twips":180,
 "line_twips":360,"keep_next":true,"page_break_before":false}
```

All keys optional except `op` and `paragraph_index`. `line_twips` creates **exact** line spacing; adjust with care to avoid glyph clipping. This operation is designed for the iterative render-adjust cycle.

## `set_paragraph_style`

```json
{"op":"set_paragraph_style","paragraph_index":0,"expected_text":"Overview","style":"Heading1"}
```

Style must be declared in `word/styles.xml`.

## `set_page`

```json
{"op":"set_page","width_twips":11906,"height_twips":16838,
 "top_twips":1440,"bottom_twips":1440,"left_twips":1500,"right_twips":1500}
```

Applies to final section only. 1 inch = 1440 twips. Does not reconfigure intermediate sections or page-numbering fields.

## `insert_image`

```json
{"op":"insert_image","after_body_index":0,"path":"architecture.png","width_cm":13.5,"alt":"系统架构"}
```

`path` must remain inside `--asset-root`; supports PNG/JPEG with engine-created image part, relationship and inline drawing. SVG, GIF, chart OLE, floating wraps and precise text wrapping are intentionally not supported yet.

## Security and package limits

Rejects relative traversal, duplicate ZIP entries, XML DTD/ENTITY, oversized archive entries, arbitrary relationship attributes in generated fragments and source overwrites. An untrusted template may still contain external links or macros/ActiveX not understood by this subset: quarantine such files and do not invoke Office macros.
