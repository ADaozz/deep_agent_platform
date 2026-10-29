# Advanced DOCX feature roadmap

## Production-readiness checklist

The following are needed before claiming **full complex-DOCX** support:

1. Relationship-aware generic package part API with strict MIME and target registration (images, hyperlinks, charts, OLE, diagrams).
2. Style-system management: copied styles, themes, inherited formatting, latent styles, linked character styles, theme fonts, custom numbering and multilevel lists.
3. Section management: first/odd/even headers and footers, page sizes and orientation, columns, cover pages, page-number restarts and section breaks.
4. Table model: merge/split cells (`gridSpan`, `vMerge`), repeated headings, conditional table styles, keep-with-next, row heights, nested tables, captions.
5. Drawing model: anchored images, charts with workbook relationships, shape wraps, image cropping, positioning and alt text; do not rasterize editable Office charts.
6. Fields and navigation: TOC, PAGEREF, REF, SEQ, page count, hyperlinks, footnotes/endnotes, bibliography, comments, bookmarks, tracked changes.
7. OpenXML schema validation (Microsoft SDK) and differential Microsoft Word rendering for tricky layouts.
8. Template anchor strategy: stable structural IDs, content controls (SDT) and tags; source hashes, guarded operations, semantic match scoring with no ambiguous auto-selection.
9. Template page-region comparison using image alignment and masking of intentionally modified regions; use structured differencing rather than a single pixel-similarity threshold.
10. Accessibility: proper headings, table headers, image alt text, language tags, hyperlink description.
11. Media safety: antivirus/quarantine of untrusted document assets and restrictions on external relationships, ActiveX, embeddings and macros.
12. Multimodal content validation: source-to-output factual accuracy, diagram text fidelity, and prompt-injection resistance from template contents.
13. Runtime: bounded file size/page count, per-step timeout and resource limits, deterministic font versions, isolated LibreOffice profiles, rollback and audit trail.

## High-priority next implementation slices

- v0.2: SDT content-control filling, safe XML field helpers, style cloning, page-break and table split controls.
- v0.3: multi-section layout + headers/footers + TOC/reference update + embedded editable charts.
- v0.4: MS Word compatibility worker, visual region masking, golden-template regression suite and CI.

Avoid claiming that the user-facing model can intrinsically know final pagination: only the renderer can determine it under a particular font/engine environment.
