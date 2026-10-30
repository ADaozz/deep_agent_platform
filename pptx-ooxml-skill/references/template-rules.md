# Template handling

Templates remain editable packages. Masters, layouts, theme parts, relationships, placeholders and existing resources are retained by python-pptx; complex objects are not reconstructed. Native numeric slide/shape IDs provide stable fallback identifiers for untouched template objects. New objects receive explicit stable names and metadata.

Template accent/font tokens are read from its theme and take precedence over the selected design profile; the rest of the profile (type scale, spacing, density, layout policy, component policy) still applies. Explicit layout_index selects a real template layout. Inspect actual placeholder geometry and use it as the first choice; declared layouts serve blank decks or missing anchors. Template complex objects use formal screenshots, including during later edits; the UI must not guess their quick appearance.

Design profiles are configuration. Switching a profile never rewrites the template theme part, never replaces masters/layouts/media, and never rasterises template content. A profile change restyles generated objects only; untouched complex template objects stay untouched and are marked complex instead of being silently approximated.

Some advanced objects may not be safely editable in v1 (SmartArt, embedded OLE, advanced animations/transitions). Preserve untouched XML/relationships; verify package parts in template regression tests. If python-pptx changes unsupported material, stop and report the limitation rather than replacing it with a raster image. Microsoft PowerPoint compatibility requires opening in PowerPoint on the target environment; LibreOffice alone cannot prove absence of a Microsoft repair prompt.
