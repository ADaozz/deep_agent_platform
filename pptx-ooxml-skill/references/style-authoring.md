# 风格 MD 与参考图：生成脚本对照

新建稿不导入 PPT 模板。先从风格 MD/参考图提炼统一的页尺寸、字体、标题/正文/标注字号、色板、边距、网格、线宽、页脚和图表编码，写入 `tmp/deck-plan.json`。参考图不替代文字和可编辑对象；统计图优先是真正的 PPT chart，仅缺少对应图型时允许申报自绘。

先将环境变量 PPTX_SKILL_DIR 设置为本 SKILL.md 的实际所在目录。以下是最小可运行脚本的结构；STYLE、标题、数据和布局在真实任务中来自用户材料。通过 `sys.path.insert` 导入技能脚本后，可在原生 PowerPoint 图表中继续编辑数据。

```python
import json, os, sys
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches
from pptx.dml.color import RGBColor
sys.path.insert(0, str(Path(os.environ['PPTX_SKILL_DIR']) / 'scripts'))
import deck_io
import native_charts
import style_library

STYLE = {
    'font_family': 'Arial',
    'theme': {'background':'FFFFFF','text':'111111','accent':'E53935',
              'muted':'6B7280','border':'D1D5DB'},
    'margins': {'unit':'fraction','left':.05,'right':.05,'top':.05,'bottom':.05}
}
style_id = 'consulting/red-black-growth'
plan = {'style_id':style_id,'pptx':'files/deck.pptx',**STYLE,'slides':[{'page':1,'statistical_charts':1}]}
Path('tmp').mkdir(exist_ok=True)
Path('files').mkdir(exist_ok=True)
Path('tmp/deck-plan.json').write_text(json.dumps(plan))
prs = Presentation()
style_library.apply_reference(prs, style_id, design=STYLE)
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
slide = prs.slides.add_slide(prs.slide_layouts[6])
slide.background.fill.solid()
slide.background.fill.fore_color.rgb = RGBColor.from_string(STYLE['theme']['background'])
chart_data = {'type':'column_clustered','categories':['Q1','Q2','Q3'],
              'series':[{'name':'收入','values':[12,18,25]}],'unit':'亿元'}
shape = native_charts.create(slide, chart_data,
                            (Inches(1), Inches(1.5), Inches(11), Inches(5)))
native_charts.style_xml(shape, {'kind':'chart','chart':chart_data}, STYLE)
native_charts.verify_workbook(shape, chart_data)
audit = native_charts.audit(prs, plan)
if audit['status'] != 'passed':
    raise ValueError(audit)
deck_io.save_deck(prs, 'files/deck.pptx')  # 原子写入交付路径，平台据此登记版本并预览
```

再执行：

```sh
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" audit-charts files/deck.pptx --request tmp/deck-plan.json
```

完整脚本在上述基础上生成原生标题、正文、表格与注释，使用同一个 STYLE，不为每页另选视觉规范。已支持的图型不能再定义 `draw_bar` / `draw_line_chart` 等形状替代实现。布局边框、流程/架构图可以使用形状，它们不是统计图。

脚本一次成功保存会被平台接管为新文件版本，后台生成版本化预览并通知前端。连续保存合并渲染最新版本；仅保留当前和前一文件快照/预览。损坏的中间文件不会替换上一个有效快照。不要删除状态目录、重复 init、还原旧文件或复制到新名字来绕过同步。

## 不支持的图型：显式申报自绘

先查询 `office_pptx.py charts`。只有没有对应图型时才使用自绘，例如当前无原生支持的 Sankey。LibreOffice 无法预览 waterfall 等已支持图型时仍保留原生 chart。

```json
{"slides":[{"page":1,"statistical_charts":2,"chart_fallbacks":[{"type":"sankey","reason":"PowerPoint 和当前 native_charts 无 Sankey 图型，使用原生形状与连接线实现流量关系","element_ids":["sankey-flow"]}]}]}
```

该页应有一个原生统计图和一个自绘 Sankey。自绘对象可命名 `da:sankey-flow`，或列出多个实际对象 ID；优先使用可编辑的形状/连接线，确需图片时说明限制。审计会拒绝不存在/重复使用的对象、用原生 chart 冒充降级对象，以及 line、waterfall 等已支持类型的降级申报。自绘图不能称为原生数据图表；结构审计不代替其数据语义和实际视觉检查。

资料库由 `templates/catalog.json` 与本地 `index.html` 索引。先按受众/主题筛选，再只读选定的 design.md 并查看 reference.jpg。参考图片仅作视觉依据，不直接铺成整页截图。MD 中的示例数据和绘图建议不覆盖用户的数据要求与技能的原生优先规则。

设计来源使用完整 style_id 并写入 PPT 元数据。前端显示其真实目录名；多个输出文件共用工作区时，每份计划必须指定 pptx 输出路径，以免将某份风格误标到其它 PPT。

审查会读取随 PPT 保存的参数和选中风格的 design.md。嵌入参数优先于旁边的计划文件；修改全局设计时同时更新嵌入参数。user_requirements 可记录用户要求的文字或列表；user_overrides 可记录配色、字体、边距等显式调整，优先于原风格。没有明确单位的旧 margins 仅传给视觉模型参考，不生成强制安全边距规则。
