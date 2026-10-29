# Template handling

Templates remain editable packages. Masters, layouts, theme parts, relationships, placeholders and existing resources are retained by python-pptx; complex objects are not reconstructed. Native numeric slide/shape IDs provide stable fallback identifiers for untouched template objects. New objects receive explicit stable names and metadata.

Template accent/font tokens are read from its theme; explicit layout_index selects a real template layout. Inspect actual placeholder geometry and use it as the first choice. Six fallback layouts serve blank decks or missing anchors. Template complex objects use formal screenshots, including during later edits; the UI must not guess their quick appearance.

Some advanced objects may not be safely editable in v1 (SmartArt, embedded OLE, advanced animations/transitions). Preserve untouched XML/relationships; verify package parts in template regression tests. If python-pptx changes unsupported material, stop and report the limitation rather than replacing it with a raster image. Microsoft PowerPoint compatibility requires opening in PowerPoint on the target environment; LibreOffice alone cannot prove absence of a Microsoft repair prompt.
