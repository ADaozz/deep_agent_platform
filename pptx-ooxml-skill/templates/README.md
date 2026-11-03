# 风格设计资料库

共 60 组 design.md＋reference.jpg，来自 44 个不同的原始参考图路径。这里只提供风格 MD 和图片，不提供可填充 PPTX 模板。

优先使用用户明确指定的风格；未指定时按受众与主题筛选，选择一套 MD 并查看对应参考图。整稿沿用同一视觉体系，不混用多套。

## 查询与浏览

```sh
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py list-styles
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py list-styles --category consulting
python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py list-styles --style-id consulting/red-black-growth
```

[本地图库](index.html) · [机器目录](catalog.json) · [来源信息](SOURCE.json) · [上游许可证](LICENSE.source)

## 分类

| 分类 | 配对数量 |
| --- | --- |
| strategy | 6 |
| business | 6 |
| work-supplement | 6 |
| promotion-supplement | 6 |
| academic-supplement | 6 |
| academic | 6 |
| consulting | 6 |
| finance | 6 |
| promotion | 6 |
| work | 6 |

## 文件结构和使用规则

每个 `<category>/<theme_id>/` 包含 `design.md`、`reference.jpg`、`source.json`。同名风格在不同分类的 MD 是不同源文件，分别保留；style_id 使用完整分类/名称。

MD 保留原始字节。未超限 JPG 保留原始字节；超长图仅删除尾部完整页面，不等比缩小，边长均不超过 8192。51 组配对发生裁尾，涉及 37 个不同的源 JPG；边界经检查修正，原脚本裁切与修正记录均在 每组 source.json 与导入工具 import-records 中的 audit.json。下载失败数为 0，没有生成替代参考图。

统计图优先使用 PPT 原生 chart 和嵌入式 Excel。只有 PowerPoint 或生成引擎没有对应图型时才允许自绘，必须在 chart_fallbacks 声明类型、原因和实际对象 ID；已支持图型不能因为风格、数据错误或 LibreOffice 预览不支持而降级。风格 MD 中的实现建议服从此规则。自绘图不能宣称为原生数据图表。

原始整理工具及清单位于 `../../scripts/import_pptx_styles/`，详细审计归档在该目录的 `import-records/`；更新时先整理到暂存目录，验证完整性后再安装。资料来源为公开仓库 https://github.com/acnlie/open-kimi-ppt-skill ，保留 LICENSE.source 与每份 source.json 的来源、哈希和裁切记录。

分类目录已移除数字前缀。与原分类重名的补充组使用 `work-supplement`、`promotion-supplement`、`academic-supplement`，以保留不同 MD 版本；每份 source.json 保留 original_category，原始仓库路径不变。
