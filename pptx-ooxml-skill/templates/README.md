# PPTX templates

This directory is the read-only template library. Each subdirectory is one template:

```
templates/<template-id>/
  template.pptx
  manifest.json
  previews/
```

Only templates with a recorded redistribution license belong here. `templates/consulting-brief/` is original project content for technical verification. Source files are never modified; `slide.import` copies one page into a working PPTX.

`import-template` analyzes a PPTX the user already has and can write a manifest next to that file. It does not download templates and it does not copy an unlicensed file here. User-uploaded templates stay in the authorized workspace.

See `references/template-rules.md`.
