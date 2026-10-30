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

## 设计系统补全（2026-10，PPTX Skill v1.1）

本轮不改变主 Agent 执行循环，仅把 PPTX 的视觉能力升级为数据驱动：

- **Design Profile**：`design/profiles/` 下 6 个经 Schema 校验的配置（`corporate_blue`、`slate_dark`、`minimal_keynote`、`consulting`、`tech_dark`、`financial`）。旧主题名继续可用；`extends` 支持 token 级继承；未知字段、非法颜色、循环继承均明确报错。新增 Profile 不需要修改 Agent 或引擎代码。上传模板的强调色若与所选 Profile 的背景或卡片对比不足，会记录 `template_accent_contrast` 冲突并回退到 Profile 强调色，避免产生不可读的混色。
- **语义布局**：保留原六种布局，新增 `statement`、`hero`、`bento`、`evidence`、`kpi`、`timeline`、`architecture`、`matrix`，包含槽位容量与模块变体。`plan` 按意图、模块数、密度、容量贴合度确定性地排序；越界内容被拒绝而不是无限缩小字号；`slide.set_layout` 在缺少槽位或容量不足时拒绝迁移，不删除重建整页。
- **组件库**：25 个经审核的 SVG 组件（process / architecture / bento / infographic / illustration / icons），固定画布、参数 Schema、原生文字槽位、已提交预览。运行时只允许声明参数；脚本、外部引用、`foreignObject`、内嵌文字、滤镜均被拒绝；相同输入产生字节一致的结果。组件以 **SVG 图片** 插入：审核过的矢量源嵌入为图片的 `asvg:svgBlip` 矢量源，并附同一实例的确定性 PNG 回退（LibreOffice/旧版查看器使用回退，PowerPoint 2016+ 使用矢量）。不做 SVG→原生形状转换。可变文字作为原生文本框插入并记录为组件子元素，组件与文字共享事务边界与稳定 ID。
- **质量检查**：在原有包完整性、边界、实测文本溢出、碰撞、字体、渲染文字缺失之外，新增 WCAG 对比度、字号下限、安全边距、槽位/组件容量、组件注册与版本、图表系列标签与单位、Profile 一致性等分级检查。`error` 阻止验收，`warning` 记录，`advisory` 不阻塞；视觉重心与叙事节奏仍由真实截图审查决定。
- **前端**：快速预览从 manifest 的 Profile token 取色与排版，组件显示稳定 ID 与来源，页面标题显示版式与设计风格，审查列表按严重度展示可定位问题；正式 Impress 截图仍是最终视觉依据。

参考用的四套 SVG 仅作设计研究，未作为整页组件入库。组件与 Profile 均为本项目原创几何与规则。

本轮本地验证：`pptx-ooxml-skill/tests` 27 项通过（含真实 Impress 渲染）、`tests` 86 项通过（另有 1 项环境相关失败，见下）、Vite 生产构建通过；并人工查看了一组 8 页示例（封面、论断、结论+图表、Bento、时间线、分层架构、KPI、数据页）的正式渲染 PNG。`tests/test_isolation.py::test_full_access_and_return_to_restricted_use_native_rebuild` 在本机因宿主存在 `/skills` 目录而失败，与本轮改动无关，需在无该目录的环境复跑。

限制：组件的图形部分以 SVG 图片及 PNG 回退呈现，不是 PowerPoint 原生形状，不能在 PowerPoint 内直接编辑路径；文字、指标、表格与图表保持原生可编辑。未实现 SVG→原生形状转换（明确非目标）。SVG 图片采用 Office 2016+ 的 `asvg:svgBlip` 机制，本轮已校验包结构、内容类型、矢量/回退摘要与 LibreOffice 回退渲染，但仍需在目标 PowerPoint 环境实际打开确认无修复提示，未以 LibreOffice 结果代替。
