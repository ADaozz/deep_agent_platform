# DOCX OOXML Author Skill (v0.1)

A runnable Agent Skills-compatible foundation for **template-first** DOCX generation via WordprocessingML XML fragments, versioned patches, render-review loops and finalization gate.

This is a **bounded prototype**, not a comprehensive Microsoft Word editing engine. Advanced chart/SmartArt/field/section/object fidelity is listed in `references/extensions.md`.

Prerequisites: Python 3.10+, `pip install -r requirements.txt`, LibreOffice headless, fonts for your target language.

Usage:

```bash
S=scripts/office_docx.py
mkdir -p work
python "$S" init --output work/candidate-000.docx
# or: python "$S" init --template existing.docx --output work/candidate-000.docx
python "$S" inspect work/candidate-000.docx --output work/inspect.json
# Put the SHA-256 from inspect into examples/plan.example.json and save as work/plan-001.json
python "$S" apply work/candidate-000.docx work/plan-001.json --output work/candidate-001.docx
python "$S" validate work/candidate-001.docx
python "$S" render work/candidate-001.docx --output-dir work/render-001
python "$S" review-template --render-dir work/render-001 --output work/review-001.json
# After actual page inspection, fill review-001.json and check all pages.
python "$S" finalize work/candidate-001.docx --render-dir work/render-001 --review work/review-001.json --output work/final.docx
```

For template comparison, render the original and `compare --before work/baseline --after work/render-001 --output-dir work/compare-001`.

The agent should read `SKILL.md` first and open `references/operations.md` only when preparing a plan. The input DOCX is never overwritten. Outputs are editable Office files, not page screenshots.
