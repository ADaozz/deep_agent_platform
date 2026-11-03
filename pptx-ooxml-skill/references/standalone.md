# 独立使用 PPTX Skill

无需启动本项目的 FastAPI、Vue 页面或 Agent 服务。完整复制 `pptx-ooxml-skill/` 即可交给能读取文件、执行 Python 并查看图片的 Agent，也可以自己运行脚本。支持 Linux / WSL；版本运行时使用 POSIX 文件锁，Windows 原生 Python 尚未适配。

## 1. 安装与目录

保留 SKILL.md、scripts、references、design、templates 和 examples 的相对位置，尤其不能只复制 office_pptx.py。随包的 scripts/artifact_runtime.py 提供保存、文件锁和版本状态；platform_adapter.py 与 vision_review_service.py 是可选平台适配，不是独立生成的依赖。

```sh
# 设置为复制后的实际目录；路径可以包含空格
export PPTX_SKILL_DIR="/absolute/path/pptx-ooxml-skill"
python3 -m venv /tmp/pptx-venv
source /tmp/pptx-venv/bin/activate
python -m pip install -r "$PPTX_SKILL_DIR/requirements.txt"

# 每个任务使用单独工作目录，避免覆盖其它演示文稿
mkdir -p ~/pptx-task/files ~/pptx-task/tmp
cd ~/pptx-task
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" --help
```

生成 PPTX 本身不需要 API Key，也不需要 LibreOffice。需要正式渲染和自动检查时，还需把 LibreOffice Impress 的 `libreoffice` 命令加入 PATH，并安装 fontconfig 和实际使用的字体。Ubuntu / WSL 可安装：

```sh
sudo apt-get install libreoffice-impress fontconfig fonts-noto-cjk fonts-liberation
fc-cache -f
libreoffice --version
fc-match 'Noto Sans CJK SC'
```

若需要在 PowerPoint 中使用 Cambria Math，可在具备使用授权的环境中安装该字体；本机字体替换或 LibreOffice 预览不能作为 PowerPoint 公式编辑验证。

## 2. 让其它 Agent 使用

将整个目录安装到目标 Agent 的 Skill 目录，或直接把 SKILL.md 的绝对路径放进任务提示。不同 Agent 的技能安装机制不同，不要求固定使用 `/agent-skills` 路径。若 Agent 支持图片输入，让它查看所选风格的 reference.jpg；纯文本 Agent 可以读取 design.md，但应说明未直接核对参考图。

可复制下面的提示词，将路径、主题、页数和材料换成实际内容：

```text
请读取 /absolute/path/pptx-ooxml-skill/SKILL.md，按其中流程制作 8 页中文季度业务汇报。
工作目录：/absolute/path/my-task；成品保存为 files/quarterly.pptx，计划和脚本保存到 tmp/。
材料：[提供实际内容、数字、单位、时间范围与来源]。
风格：简洁、商务、蓝白配色。先查询风格库，读取一套 design.md 并查看 reference.jpg，
根据我的颜色要求统一调整整稿；style_id 只记录来源，不能代替实际应用字体、配色和布局。
统计图表优先用原生 chart 和嵌入式 Excel；公式用原生 Office Math。
全部页面完成后做图表审计和 check-deck，只局部修复真实遮挡并复检。
视觉审查只给建议，不自动修改。独立环境没有平台后台审查服务，不得把 pending 当作通过。
交付 PPTX、检查结果，以及仍需 Microsoft PowerPoint 实机验证的项目。
```

## 3. 查询风格与图表

```sh
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" list-styles --category consulting
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" list-styles --style-id consulting/red-black-growth
cat "$PPTX_SKILL_DIR/templates/consulting/red-black-growth/design.md"
# 用图片查看器或 Agent 的看图工具打开同目录 reference.jpg
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" charts
```

风格库包含设计说明与参考图，不能当成可填槽 PPTX 模板。用户颜色等要求优先于风格原有参数；生成脚本必须实际使用统一参数。`style_library.apply_reference` 保存来源和参数，不会自动排版。

## 4. 运行完整示例

示例生成两页：原生柱状图、原生积分与分式公式。数字明确标为演示数据；这只是接口示例，真实任务需按选定风格和材料重做内容与布局。

```sh
python "$PPTX_SKILL_DIR/examples/standalone_deck.py" --output files/demo.pptx
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" audit-charts files/demo.pptx --request tmp/deck-plan.json
```

脚本是可重复执行的完整生成示例。**只在首次生成时运行；修正已有文件时不能重跑它重建整稿。** 局部修正应读取已有 Presentation，通过稳定的 slide_id / element_id 找到目标对象，保留其余页面。Python API 细节见 [生成脚本](style-authoring.md)、[图表](charts.md) 和 [公式](equations.md)。

## 5. 登记、检查与查看渲染

独立 Python 脚本完成保存后，需手动登记外部文件；平台环境会自动完成这一步。示例中的 presentation_id 为 demo，后续继续使用相同 ID：

```sh
python - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ['PPTX_SKILL_DIR']) / 'scripts'))
from office_pptx import PresentationStore
PresentationStore('files/demo.pptx').track_external('demo')
PY
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" status files/demo.pptx > tmp/status.json
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" check-deck files/demo.pptx --request tmp/deck-check.json > tmp/check-result.json
python "$PPTX_SKILL_DIR/scripts/office_pptx.py" review-status files/demo.pptx
```

示例已为 results、formula 两页生成具体 content_notes。真实文稿需先从 status 的 manifest.slides 读取 slide_id，逐页核对内容后填写记录，不能用空白或统一的“通过”代替核对。按 check-deck 的 repair_targets 与 other_errors 局部修复，脚本保存后再次 track_external、check-deck，直到检查通过。

默认状态保存在 `files/.pptx-state/demo.pptx/`；渲染结果位于其中 `tmp/render-*`，可打开 PDF / PNG 查看。设置 OFFICE_RENDER_TMP 后渲染位置会变化，可从 Python 的 `store.render_directory(store.status()['revision'])` 获取实际目录。不要删除状态目录来绕过检查。

可在自动检查通过后用当前 revision 执行 finalize。它表示满足脚本交付条件，不代表 API 视觉审查通过，更不代表已在 Microsoft PowerPoint 中测试编辑性。

## 6. 修改已有 PPT 与独立模式边界

编辑用户提供的文件时，使用 `init --template` 或 import-template、slide.import、slot.fill；合法路径与完整参数见 [模板编辑规则](template-rules.md) 和 [操作接口](operations.md)。slot.fill 只能修改已有文本，不用于插入新文本或替换原生公式。已有文件的背景、母版和未涉及页面应保留。

| 能力 | 独立使用 |
| --- | --- |
| 风格选择、原生图表、Office Math、编辑既有 PPT | 支持，无需平台 |
| 原子操作、版本数据库、本地正式渲染和自动检查 | 随包提供；渲染需 LibreOffice 与字体 |
| Python 覆盖保存后自动登记 | 手动调用 track_external；平台才自动检测 |
| 右侧预览、SSE 通知、两版快照自动保留 | 平台功能，独立 CLI 不自动提供 |
| 截图的模型 API 视觉审查 | 平台另外调用 API；独立 CLI 不自动启动审查服务 |

独立 check-deck 记录的 visual pending 只表示请求已登记。没有后台 API 服务时可以交付，注明未进行 API 视觉审查；Agent 自己看图应单独说明检查方式，不冒充平台 API 的结果。结构中存在原生公式标记只能证明存储结构，实际可编辑性需在 PowerPoint 中打开验证。更多边界见 [平台集成说明](platform-integration.md)。
