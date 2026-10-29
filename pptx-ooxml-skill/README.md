# PPTX Skill v1.0 — optional extension

Install dependencies with `pip install -r pptx-ooxml-skill/requirements.txt`, plus LibreOffice Impress, fontconfig and Noto CJK fonts. The platform discovers `feature.json`; the main Agent has no PPTX implementation imports. The extension can be stopped/uninstalled independently from DOCX through Features, or its directory can be removed from a deployment. Files/history remain available on disk.

See SKILL.md and references for the element protocol, six layouts, one-deck authorization, slide checks and fidelity limits. Fast preview uses native element data; formal checks use real Impress PNGs. Never certify unseen images or PowerPoint repair-prompt compatibility without actually checking them.

完整验证：

```sh
python -m pytest -q pptx-ooxml-skill/tests tests/test_features.py
python tests/pptx_e2e_server.py
# 另一个终端，测试模型仅用于驱动真实内核/工具/审批/渲染：
CHROMIUM_PATH=/usr/bin/chromium npm --prefix frontend run test:e2e:pptx
```

12 页模板验收依次在第 2 / 5 / 9 页修改整体蓝白配色、将列表替换为原生架构图、返回修改第 3 页的描述。未实际检查的页面继续显示视觉 pending，不会因为测试驱动而自动通过。
