# PPTX Skill v3.4

按风格设计 MD＋参考图创建原生可编辑 PPT，或局部修改已有文件。入口与工作流以 [SKILL.md](SKILL.md) 为准。

无需运行本平台也能使用：完整复制此目录、安装 requirements.txt，并让目标 Agent 读取 SKILL.md。安装、Agent 提示词、命令行操作、本地预览、检查及功能边界见 **[独立使用指南](references/standalone.md)**；可运行 [两页示例](examples/standalone_deck.py) 生成原生图表和公式。

安装 Python 依赖：`pip install -r <技能目录>/requirements.txt`。正式渲染另需 LibreOffice Impress、fontconfig 和相应字体。CLI 使用随包 `scripts/artifact_runtime.py`，不从目录外加载平台代码；平台创建快照时保留随包实现。

## 使用边界

- 新建稿读取 `templates/<category>/<theme_id>/design.md` 和 reference.jpg；`list-styles` 查询 60 套资料。目录不是 PPTX 模板库。
- 统计图表使用原生 chart 和嵌入式工作簿，仅缺少图型时申报自绘；公式使用原生 Office Math。见 [charts.md](references/charts.md)、[equations.md](references/equations.md)。
- 导入、克隆、slot.fill、局部换图和 chart.replace_data 用于编辑已有文件，保留未涉及页面。
- 完成全部页面后执行 audit-charts、check-deck。Agent 根据报告局部修复真实遮挡并复检，直到检查通过；视觉审查所有等级都只给建议，不自动修改。
- 独立脚本、原子 CLI 与平台集成共用生成和检查逻辑。预览成功不代表 PowerPoint 实机验收。

## 审查依据

`style_library.apply_reference(prs, style_id, design=parameters)` 保存真实风格标识和设计参数。嵌入参数优先；匹配当前文件的 tmp/deck-plan.json 是旧文件的回退来源。审查接收选定的 design.md、配色、字体、字号、边距及用户要求；用户明确调整优先于风格 MD。没有明确数值或单位时不强套 corporate_blue 配色、字号或 6% 边距。既有文件没有设计声明时，以保留实际页面视觉体系为准。

`design/profiles` 和 `design/layouts` 支持显式原子布局及旧文件编辑兼容；它们不是风格资料库的替代品。SDK、计划格式与参数单位见 [style-authoring.md](references/style-authoring.md)。

## 平台集成是可选项

平台负责检测脚本覆盖、自动登记版本、后台生成预览、通知前端及只保留两版快照；平台后台通过独立模型 API 请求执行视觉审查。没有可用视觉 API 不阻止生成和交付，也不能声称视觉通过。独立 CLI 不自动启动这些后台服务。详见 [platform-integration.md](references/platform-integration.md)。

## 开发验证

```sh
python -m pytest -q pptx-ooxml-skill/tests tests/test_features.py
python pptx-ooxml-skill/tests/helpers/render_baseline.py
```

LibreOffice 测试需要可执行进程及字体环境。tests/helpers 内的模板生成器和基准渲染器只用于编辑回归，不是推荐的新建稿入口；平台技能快照不包含 tests。失效的 local-catalog 模板注册器已经移除，编辑用户 PPT 使用 import-template 或 init --template。
