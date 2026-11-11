---
name: pptx-ooxml-author
description: Create editable PowerPoint presentations from a style-design Markdown document and reference images, with real native charts and tables. Edit existing PPTX files while preserving unaffected content. Supports standalone CLI use and optional platform versioning, previews and API visual review.
metadata:
  version: "3.4.0"
  compatibility: "python-pptx, lxml, Pillow, PyMuPDF, openpyxl, latex2mathml, LibreOffice Impress and fonts"
---

# PPTX：按风格 MD 与参考图制作，原生对象交付

从当前任务工作目录执行。约定 PPTX 放 `files/`，计划、脚本和临时图片放 `tmp/`。按本 SKILL.md 的实际目录定位 `scripts/office_pptx.py`，下文简称 `office_pptx.py`，不要假设安装在固定沙箱路径。示例使用环境变量 `PPTX_SKILL_DIR` 表示这个实际目录；运行命令前自行设置。先安装技能 requirements.txt；本地渲染另需 LibreOffice、fontconfig 和字体。不启动本平台、交给其他 Agent 或本机脚本使用时，按 [独立使用指南](references/standalone.md) 安装、提示和检查。

## 交付文件（硬性规则）

- `files/<名称>.pptx` 是**唯一的工作稿和交付稿**，从第一次保存起就写在这里。平台只对 `files/` 中的 PPT 登记版本、生成预览；放在别处的整稿用户看不到。
- Python 脚本每次保存都调用 `deck_io.save_deck(prs, 'files/<名称>.pptx')`，它在目标目录内原子写入，平台不会读到半个 ZIP。不要自行 `prs.save` 到 `tmp/` 再复制或替换；`save_deck` 拒绝写入 `tmp/`。原子操作直接作用于 `files/` 中的文件。
- 不要在 `tmp/` 或其他目录维护整稿副本，也不要“先在别处做完、检查通过再输出”。检查与修复都直接针对 `files/` 中的文件。
- 不要自行用 LibreOffice 渲染整稿、读截图来检查效果；预览由平台生成，质量检查用下文的 `audit-charts` 与 `check-deck`。

## 新建 PPT

新建稿使用用户的**风格设计 MD＋参考图**确定视觉系统，不搜索或复用 PPT 模板库；PPTX 模板库保持为空；`templates/` 现在保存配对的风格 MD 与参考图。用户未指定风格时，先用 `office_pptx.py list-styles` 按分类筛选至多三套候选，再读取一套 `design.md`、查看其 `reference.jpg`；不要一次读取全部风格或混用多套视觉体系。用户明确提供的风格要求优先。参考图用于理解版式、字体层级、配色、留白与图表表达，不直接铺成整页截图。缺少其中一种输入时，先用已有要求提炼风格；确有关键歧义再询问，不寻找旧 PPT 模板兜底。

先在 `tmp/deck-plan.json` 记录选定目录的完整 `style_id`（如 `consulting/red-black-growth`）及输出 `pptx` 路径、受众、叙事、页尺寸、统一字体/回退字体、色板、标题层级、边距、页脚、每页重点与统计图表数量。MD 与参考图冲突时以用户明确指令和 MD 为准，记录必要取舍。整稿沿用同一组设计参数，不能逐页重新配色或缩字。所有数字、单位、时间范围与来源须有依据。

可使用原子接口创建页面与对象，也可写一个可重复运行的 Python 生成脚本。脚本调用 `style_library.apply_reference(prs, style_id, design=design_parameters)` 将风格来源及实际设计参数写入 PPT，原子初始化使用 `init ... --style-id <category/theme_id>`。界面据此显示真实风格文件夹名称，不将内部 profile 名称当成设计来源。脚本统一读取上述设计参数，用 `deck_io.save_deck` 写入 `files/` 中的目标 PPTX；可以分批生成并多次保存，每次保存平台都会登记版本并更新预览。平台仍会验证完整性，不会因无效文件覆盖已登记版本。

审查依据依次为用户明确要求/调整、本稿实际参数、选中风格 MD。计划顶层记录 fonts、palette（或 theme）、type_scale、margins、user_requirements/user_overrides；传入 apply_reference 的 design 使用同样的参数。margins 必须写明 unit（fraction/in/pt）及 left/right/top/bottom。没有明确数值或单位的部分只做视觉建议，不套默认 profile 的配色、字号或 6% 安全边距。选定 style_id 只记录来源，不会自动把 MD 转成排版；生成脚本需实际应用参数。

## 统计图表优先用 PPT 原生 chart，仅缺少图型时自绘

**统计图表优先生成真正的 PPT chart，并包含可编辑的嵌入式 Excel 数据。只有 PowerPoint 或当前生成引擎缺少对应图型时，才允许自绘。** 已支持的柱/条、折线、面积、饼/环、散点、气泡、雷达、瀑布、漏斗、箱线等，不得用线条方块或图片替代。坐标轴、柱、点、图例和系列由原生图表生成，可额外添加原生文本注释。风格 MD 中关于绘图方式的建议也服从本规则。

对应脚本与操作：

- 原子操作：`element.add_chart`；更新数据用 `chart.replace_data` 或生成对象的 `element.update`；换型用 `chart.change_type`。
- Python 生成脚本：从技能 `scripts/` 导入 `native_charts`，调用 `native_charts.create(slide, chart_payload, position)`；继承整稿色板/字体用 `native_charts.style_xml`；使用 `native_charts.verify_workbook` 验证数据。已支持的图型不要再实现一套形状绘制函数；样式偏好、数据错误、字体或路径错误、LibreOffice 预览不足，都不是“不支持”的理由。完整脚本对照见 [style-authoring.md](references/style-authoring.md)。
- `office_pptx.py charts` 查询全部类型和每种三个场景；数据格式、示例与兼容性见 [charts.md](references/charts.md)。散点用真实 `[x,y]`，气泡用 `[x,y,size]`，层级图用类别路径，股票数据必须满足高低开收口径，不能猜造缺失维度。
- 生成完成后必须运行 `office_pptx.py audit-charts files/deck.pptx --request tmp/deck-plan.json`。计划的每一页用 `{"page":1,"statistical_charts":2}` 声明统计图总数；不支持的图型另在该页 `chart_fallbacks` 中逐项声明 type、reason、element_ids。审计校验原生图＋有效自绘图数量，已支持图型不能申报降级。分别报告 native_charts、fallback_charts 与 all_native，不能把自绘图称为原生图。失败先修正生成脚本。无计划时仅返回 inventory，不能宣称审计通过。

架构图、流程图、时间线、战略矩阵可使用原生形状和连接线；表格使用原生 table。这些不是统计图表，不计入 statistical_charts。已有文件中的图片/形状图不能被描述为可编辑数据图表；本次新建或重绘的统计图均按上述优先级制作；自绘图明确说明原因和可编辑性，另做数据与视觉核对。

ChartEx 高级图表、曲面和量价股票图需要 PowerPoint 核对，LibreOffice 可能省略或近似显示。保持原生结构并报告预览未验证，不自行改成形状拼图。地图边界解析依赖 Office 地图服务，不承诺离线完整显示。

## 原生可编辑公式

分式、矩阵、积分、上下标、根式、求和/连乘和分段函数使用 **Office Math（OMML）原生公式**，不用普通文字、图片或线条拼装替代。原子操作为 `element.add_equation`（latex、geometry/anchor、可选 font_size/align）；修改内容用 `element.update` 的 latex，字号/颜色/对齐用 `element.set_style`。脚本用 `native_math.create` / `native_math.replace`，与原子操作共用转换器；所有公式局部更新，保留其它对象。数学字体使用 Cambria Math，颜色继承整稿色板。用法及支持范围见 [equations.md](references/equations.md)，`catalog` 的 equations 提供示例。不支持的 LaTeX 结构会报错，应改用已支持的等价写法并核对语义，不能静默栅格化。公式也参与整稿遮挡检查；生成结束后按 check-deck 流程执行，视觉建议不自动触发公式修改。

## 修改已有 PPT

仅在用户要求编辑已有 PPT 时使用导入、页面复制、`slot.fill`、局部换图与数据替换。保留未涉及页面和原有背景/母版/字体体系，按修改范围操作。`slot.fill` 仅更新已有文字；新文字使用 `element.add_text`。导入源文件与路径规则见 [template-rules.md](references/template-rules.md)，参数见 [operations.md](references/operations.md)。这些兼容能力不是新建稿的默认流程。

## 整稿完成后检查，局部修复

先完成全部页面和原生图表审计，再统一运行 `office_pptx.py check-deck files/deck.pptx --request tmp/deck-check.json`。请求为 `{"content_notes":{"<slide_id>":"该页具体内容核对记录"}}`；slide_id 从 status 读取。生成过程中只更新预览，不逐页启动检查或视觉修复。

`check-deck` 返回 `repair_targets`（遮挡的 slide_id、elements）和 `other_errors`。**Agent 自动修复遮挡：只修改报告中的页面和相关元素，再运行 check-deck，直到 overlap_passed 为 true 且检查通过。** 不受旧的三次修复上限限制。真实的有意叠放才可标记 overlap_group，不能靠忽略错误、删除内容或整体缩字伪造通过。渲染/字体等环境错误先处理原因；无法验证时明确报告，不能宣称通过。

**修复读取现有 PPT 并局部改动，禁止重新生成整稿。** 优先 `element.update`；脚本用 `Presentation('files/deck.pptx')`，通过稳定 slide_id/element_id 定位修改，再用 `deck_io.save_deck(prs, 'files/deck.pptx')` 写回原路径。保留其它页及未涉及对象的内容、背景、字体、布局和图表数据。不得重跑从空白 Presentation 重建全部页面的脚本。仅用户要求全局修改时才改全局参数。

全部自动检查通过后，check-deck 记录单页和整稿视觉审查请求；接入平台时由后台另外调用模型 API 审查截图，不是 Agent 自身的看图能力。独立 CLI 不会自动获得这个后台服务。**所有视觉结果（包括 critical）只给建议，禁止据此自动修改；用户明确要求修复后才局部修改。** 用 review-status 查询并汇总建议；模型或渲染不可用须说明。手动 check-slide 仅用于指定页面诊断，不是默认生成流程。见 [aesthetic-review.md](references/aesthetic-review.md)。

## 文件保存与可选平台集成

- 独立 CLI 的原子操作由随包 artifact_runtime 保存版本；直接 Python 脚本写文件后，可再次调用 CLI 完成检查。
- **以下自动化仅在接入平台时提供**：平台在工具执行结束与文件列表刷新时检测完整的 PPTX 覆盖，校验并登记新版本；相同内容不重复登记；脚本覆盖按页及引用资源比较，仅使实际变化页的检查失效，未改页保留审查结果。后台生成该版本预览并通过事件更新前端，连续快速保存合并为最新版本渲染。
- **平台只保留当前版和前一版的 PPT 快照及渲染缓存。** 旧快照不能恢复；操作 ID、审查摘要等审计元数据保留，不等于保留旧 PPT 文件。不要删除 `.pptx-state` 或复制旧文件绕过版本系统。
- 脚本覆盖后无需手工删除历史或重新 init。原子操作仍带 presentation_id、operation_id、当前 base_revision；重试使用同一 operation_id 和同一请求。
- 原生图表审计通过后按上述整稿检查流程执行，遮挡修复与视觉建议分开处理。预览成功不等于内容正确或 PowerPoint 实机验收通过；未完成的检查明确说明。
- 正式渲染调用 PATH 中的 `libreoffice`；按实际系统配置可执行入口，不假设固定路径或 `soffice` 别名。临时渲染文件不放 `files/`，不把 Agent 自己生成的 PDF 图片当作平台已经登记的预览。

独立使用步骤见 [standalone.md](references/standalone.md)；平台快照与后台审查的边界见 [platform-integration.md](references/platform-integration.md)。未配置可用视觉 API 时，该审查未执行或不可用，不阻塞生成和交付，也不能标记为视觉通过。
