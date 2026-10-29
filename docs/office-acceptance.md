# DOCX / PPTX 验收记录

基线 `main@2f49d4f`，2026-10-29。本轮保留主 Agent 原有执行循环和 steering，两个格式均为独立可选扩展。

## 自动回归与浏览器

- Python 全量回归：91 项通过（包含真实 LibreOffice Writer / Impress 渲染）。
- Node 会话与版本单调性测试：10 项通过。
- Vite 生产构建通过。
- DOCX：明确 A4、边距、分级标题、短正文、表格、图与图注要求，自然排成 12 页；112 个提交版本；中途第二节转业务表格；分页、缩放、焦点、关闭、滚动及刷新恢复通过。
- PPTX：企业模板、12 页、88 个提交版本；第 2 页换蓝白主题、第 5 页列表转原生架构图、第 9 页返回修改第 3 页描述均通过；只有初始化需要一次受限审批；文字、形状、表格和图表保留原生对象。
- 卸载：DOCX / PPTX 可分别停用、卸载、重新启用；物理移除目录可被跳过；两个都卸载后，真实 AgentRunner 普通对话仍通过，格式工具均不存在。

浏览器使用确定性测试模型驱动真实内核和工具，不假装对外部生产模型做过质量评估。自动验收时全部视觉状态保持 pending；随后开发助手实际查看了 DOCX 12 张页面 PNG 和 PPTX 12 张正式截图，再逐页录入视觉证据并完成样例发布。此步骤不会让平台默认的文本模型获得视觉审查权限。

## 可复现入口

```sh
pip install -r requirements-features.lock
pip install pytest httpx
PATH=/path/to/libreoffice/bin:$PATH python -m pytest -q tests docx-ooxml-skill/tests pptx-ooxml-skill/tests
node --test tests/*.test.mjs
npm --prefix frontend run build

# 分别在新的数据目录/进程运行两套浏览器验收：
python tests/docx_e2e_server.py
npm --prefix frontend run test:e2e:docx
python tests/pptx_e2e_server.py
npm --prefix frontend run test:e2e:pptx
```

环境需 LibreOffice Writer / Impress、fontconfig、Noto CJK / Liberation 字体以及 Chromium。独立安装/卸载见 [说明](optional-office-features.md)。

## 限制

真实 DOCX 分页需要排版引擎。双功能并行验收下，提交到 SSE P95 约 139 ms，完整分页预览更新 P95 约 9.2 秒；1 秒预览目标尚未达成。单页 PNG 解码和替换 P95 约 327 ms。

正式排版由 LibreOffice 提供，未在 Microsoft Word / PowerPoint 桌面版验证逐像素一致性或“无修复提示”。复杂模板对象保持原样并用正式截图预览；高级动画和 SmartArt 不承诺可编辑。对应限制已写入 Skill，不能被自动标为已验证。
