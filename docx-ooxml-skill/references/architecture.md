# Architecture and evidence

## Why a temporary DOCX is required

WordprocessingML XML encodes document structure and formatting constraints. Word/LibreOffice computes actual line breaks and page composition. A renderer needs a package (DOCX or alternative office document object model), so draft assembly comes **before** visual QA. Only *publication* comes after QA.

## Operational layers

1. Source acquisition: explicit user template first; otherwise blank seed with predefined styles.
2. Package introspection: parse main body, styles, media, relations, page settings, sections, headers/footers; record original checksum.
3. Model-owned plan: request text to create/change and bounded WordprocessingML fragments. Never rewrite the entire OOXML package.
4. Deterministic engine: stage copy, apply edits, resolve image IDs and part relationships, validate, checkpoint.
5. Layout engine: LibreOffice headless to PDF, PDF page PNGs. For final Microsoft Word compatibility use an Office rendering check if available; the two engines differ.
6. Template compare: side-by-side pages; inspect static branding zones for drift while allowing expected content changes.
7. Quality gate: objective checks + explicit inspected visual review with hash binding.

## Validation levels

**Level 1 — XML**: well-formed XML, no DTD, basic document/body root and block positioning.

**Level 2 — Package**: relevant relationship targets and references exist, media content types declared, no dangling targets. This CLI implements a conservative subset, not an XSD or ECMA-376 schema validation.

**Level 3 — Office compatibility**: LibreOffice opens/render; for critical files, Microsoft Word opens without repair. Optional Open XML SDK `OpenXmlValidator` for deep schema checks (external .NET process), and relationship graph inspection for charts/SmartArt/fields.

**Level 4 — Visual**: page PNG inspection and comparison with baseline; agent/human verifies all pages. Never equate `render successful` with `layout successful`.

**Level 5 — Semantic**: required content coverage, no fabricated facts, heading logic, tables and references, image alt text, font availability.

## Template preservation

The engine preserves original package part bytes except parts explicitly modified. XML serialization of the main part may change whitespace/prefixes; compare normalized semantic elements rather than raw document.xml byte equality. Most edits should touch only the main part, and optionally its relationship part, media and content types for images.

## Limits of the provided implementation

Version 0.1 is an executable, bounded **foundation**. It does not magically support every valid Word feature: multi-section headers, Word chart objects, dynamic TOC, tracked changes, numbering remaps, comments, floating objects and dynamic page count fields need additional handlers. Without those handlers, do not claim complete compatibility or high fidelity for arbitrary complex DOCX.

Recommended external validation: Microsoft Open XML SDK / `OpenXmlValidator`, and rendering in Microsoft Word on a licensed Windows worker. LibreOffice is a fallback layout oracle, not an identity-equivalent Word engine.

## Standards

- OOXML package structure: https://learn.microsoft.com/en-us/office/open-xml/general/how-to-create-a-package
- WordprocessingML parts: https://learn.microsoft.com/en-us/office/open-xml/word/structure-of-a-wordprocessingml-document
- Agent Skills structure: https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx
