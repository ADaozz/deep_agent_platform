# PPT 风格资料导入工具

此目录保存上游 Kimi Slides 风格资料的导入工具、60 份设计与 44 张原图的映射，以及首次导入的裁切审计。运行时使用的资料位于 [templates](../../pptx-ooxml-skill/templates/README.md)，Agent 的 Skill 快照不需要包含本工具。

来源仓库、已观察的提交和许可证见 [SOURCE.json](../../pptx-ooxml-skill/templates/SOURCE.json) 与 [LICENSE.source](../../pptx-ooxml-skill/templates/LICENSE.source)。`manifest.json` / `manifest.csv` 保留上游原始分类，`gallery.html` 是使用远程图片的来源索引；最终资料库的本地图库位于 `templates/index.html`。

## 整理到暂存目录

在项目根目录运行，需 Pillow；没有本地源仓库时会从公开仓库下载：

```sh
python3 scripts/import_pptx_styles/organize.py --out /tmp/pptx-styles-import
# 使用已下载的源仓库，无需联网
python3 scripts/import_pptx_styles/organize.py --repo-root /path/to/open-kimi-ppt-skill --out /tmp/pptx-styles-import
```

不要直接覆盖正式 `templates`。工具输出保留上游的数字分类；安装前需按现有目录映射去掉序号、区分同名补充组、更新 catalog 和本地图库，并核对每份 source.json。当前映射为 `01_strategy → strategy`、`02_business → business`、`03_work → work-supplement`、`04_promotion → promotion-supplement`、`05_academic → academic-supplement`，其余分类保留原名。

## 保留规则

- design.md 保持上游原始字节，每个设计对应自己的 reference.jpg 与 source.json。
- 未超限图片保持原始字节；超限长图仅裁去尾部完整页面，边长不超过 8192，不能缩放替代裁切。多列拼图必须人工检查页面边界。
- 下载失败不会以合成图片替代原图。工具支持为映射中明确没有原图的条目生成标记参考图，正式资料库目前没有此类条目。
- `import-records/audit.json` 和 audit.csv 保留首次整理记录；正式安装的人工裁切修正和新分类以各风格 source.json 为准。不要将新运行的审计覆盖历史记录。
- 上游分支可能变化，更新前比对来源提交、映射、哈希与裁切结果。

本地合成数据测试不访问网络，也不验证真实原图的裁切美观度：

```sh
python3 scripts/import_pptx_styles/test_organize.py
```
