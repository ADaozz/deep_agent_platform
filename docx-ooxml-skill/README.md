# DOCX OOXML Author Skill (v0.1)

A runnable Agent Skills-compatible foundation for **template-first** DOCX generation via WordprocessingML XML fragments, versioned patches, render-review loops and finalization gate.

This is a **bounded prototype**, not a comprehensive Microsoft Word editing engine. Advanced chart/SmartArt/field/section/object fidelity is listed in `references/extensions.md`.

Prerequisites: Python 3.10+, `pip install -r requirements.txt`, LibreOffice headless, fonts for your target language.

Usage: follow `SKILL.md`. Run from the task workspace root (the directory containing `files/` and `tmp/`), one atomic operation per command; the document lives in `files/` from `init` onward:

```bash
S=scripts/office_docx.py
python "$S" init --document-id report --output files/report.docx
python "$S" apply-one files/report.docx tmp/operation.json --asset-root tmp
python "$S" checkpoint files/report.docx
python "$S" finalize files/report.docx --base-revision N
```

The older candidate/plan batch commands (`apply`, `render`, `review-template`, `finalize --render-dir`) are kept for compatibility only and are described in `references/legacy-operations.md`; do not use them for new work. The input DOCX is never overwritten. Outputs are editable Office files, not page screenshots.
