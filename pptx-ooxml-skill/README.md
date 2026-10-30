# PPTX Skill v1.2 — optional extension

Install dependencies with `pip install -r pptx-ooxml-skill/requirements.txt`, plus LibreOffice Impress, fontconfig and Noto CJK fonts. The platform discovers `feature.json`; the main Agent has no PPTX implementation imports. The extension can be stopped/uninstalled independently from DOCX through Features, or its directory can be removed from a deployment. Files/history remain available on disk.

The engine keeps the element-atomic protocol: one approved operation, stable IDs, immutable revisions, real Impress checks and per-slide visual acceptance. Visual planning is data-driven:

- `design/profiles/` — validated design tokens (colours, type scale, spacing, density, chart/table rules, layout and component policy).
- `design/layouts/` — semantic layouts with slots, capacities and module-count variants; `plan` ranks them deterministically for a content summary.
- `design/components/` — reviewed SVG components with a fixed canvas, parameter schema, native text slots and committed previews. Instances are inserted as SVG pictures with a deterministic PNG fallback (no shape conversion); important text stays native and editable.
- `design/design_quality.py` — deterministic error/warning/advisory checks for contrast, type size, margins, capacity, registration, chart metadata and connector semantics (unbound, dangling, relation-less or text-crossing lines).
- `scripts/native_style.py` — bounded style vocabulary, reviewed presets, relative move/scale/rotate and in-transaction chart type replacement with workbook verification.
- `scripts/native_diagram.py` + `design/diagram_presets.py` — native flow/architecture diagrams: the caller declares nodes, edges and relations; the engine places nodes and draws bound connectors with relation-derived arrows, rejecting edges it cannot route clearly.
- `vision_review_service.py` (host only, never copied into the sandbox) — multimodal aesthetic review of real host renders: prompt, JSON schema, evidence-based status rules, deck contact sheet and the private review ledger. `platform_adapter.py` schedules it after each check, applies only current results and verifies finalization. See `references/aesthetic-review.md`.

See `SKILL.md` and `references/` for the element protocol, layout rules, component semantics and template precedence. Fast preview uses the manifest's profile tokens and native element data; formal checks use real Impress renders. Never certify unseen images or PowerPoint repair-prompt compatibility without actually checking them.

完整验证：

```sh
python -m pytest -q pptx-ooxml-skill/tests tests/test_features.py
python -m pytest -q pptx-ooxml-skill/tests/test_design.py
python -m pytest -q pptx-ooxml-skill/tests/test_native_style.py pptx-ooxml-skill/tests/test_native_diagram.py pptx-ooxml-skill/tests/test_regression_sample.py
python -m pytest -q pptx-ooxml-skill/tests/test_vision_review.py pptx-ooxml-skill/tests/test_vision_eval.py   # scripted vision model, real Impress renders
python pptx-ooxml-skill/tests/vision_eval.py run /tmp/vision-eval --model <source/model>   # real model vs. hand-labelled defects
python pptx-ooxml-skill/scripts/component_renderer.py previews   # only after an intentional component change
python tests/pptx_e2e_server.py
# 另一个终端，测试模型仅用于驱动真实内核/工具/审批/渲染：
CHROMIUM_PATH=/usr/bin/chromium npm --prefix frontend run test:e2e:pptx
```

12 页模板验收依次在第 2 / 5 / 9 页修改整体蓝白配色、将列表替换为原生架构图、返回修改第 3 页的描述。未实际检查的页面继续显示视觉 pending，不会因为测试驱动而自动通过。

设计系统测试覆盖：Profile 扩展与非法配置拒绝、布局解析确定性与容量拒绝、布局变体迁移、SVG 安全校验与参数拒绝、组件渲染确定性、组件实例的原生文字与来源记录、Profile 切换后的重渲染与审阅失效、模板主题优先级、质量问题分级。
