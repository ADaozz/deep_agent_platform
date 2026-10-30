# 独立、可卸载的 DOCX / PPTX 功能

两项能力都是可选扩展，不是主 Agent 的内置文档逻辑：

| 功能 | 后端与 Skill | 前端 |
| --- | --- | --- |
| DOCX | `docx-ooxml-skill/` | `frontend/src/features/docx/` |
| PPTX | `pptx-ooxml-skill/` | `frontend/src/features/pptx/` |

`feature.json` 声明功能身份、适配器、依赖和 Skill。通用注册器 `platform_app/format_plugins.py` 负责发现和接入；`artifact_runtime.py` 仅提供快照、事务、事件、幂等和恢复，不负责规划或 Agent 执行。`runtime.py` / `agent_factory.py` 不导入 Word 或 PowerPoint 的引擎、组件、提示词或业务实现；只接入技能快照、版本事件和预览。DOCX/PPTX 的制作流程由 `SKILL.md` 说明，脚本经原生 execute 运行，不注册文档专属模型工具，也不将制作流程注入系统提示词。

前端 `FeaturePanels.vue` 使用可选组件目录发现，不直接导入两种格式的组件。移除某个目录后，其组件不参与构建。PPTX / DOCX 的接口也放在各自功能目录，停用或卸载后不可调用，另一功能和普通 Agent 不受影响。

## 安装、停用和卸载

只运行基础 Agent：

```sh
pip install -r requirements.lock
```

按需安装依赖：

```sh
pip install -r docx-ooxml-skill/requirements.txt
pip install -r pptx-ooxml-skill/requirements.txt
```

此外各格式的正式预览/检查需要 LibreOffice Writer 或 Impress、fontconfig 和文档字体。完整开发/验收环境可安装 `requirements-features.lock`，测试模板还使用 python-docx。

网页侧栏「扩展功能」允许分别启用、停用或卸载。这里的卸载会撤销注册和界面入口，保留安装包以便重新安装，**不会删除用户文件、历史快照或审查记录**。运行/等待中的任务需要先结束或取消，避免中途撤销正在使用的工具。

部署时需要真正移除代码和前端组件：

```sh
python scripts/manage_features.py list
python scripts/manage_features.py uninstall docx --purge
python scripts/manage_features.py uninstall pptx --purge
npm --prefix frontend run build
# 重启服务
```

`--purge` 只删除指定功能目录及其前端目录；不会删除 `.platform/` 中的产物/版本。依赖包可能被另一功能共享，不自动执行 pip uninstall。重新安装已清除的功能时，从部署包恢复对应目录、安装其 requirements、构建前端，然后启用。目录缺失或依赖不可用时，注册器跳过该功能，不阻止核心启动。

## 授权与主 Agent

Agent 设置的「技能」页显示 Word / PowerPoint 内置技能，可分别启用或停用；保存后用于新会话。DeepAgents 原生 Skills 中间件解析只读 `/agent-skills/<skill-name>/SKILL.md`。其中说明本技能 `scripts/` 的命令行入口、操作请求和校验流程。每次 execute 只执行一个有界修改，在 ask 模式下逐条审批；联网仍需申请 network=true。没有 `docx_*` / `pptx_*` 模型工具或额外的文档授权模型。

Python 依赖环境以只读方式提供给沙箱；用户级便携 LibreOffice 可同样以只读方式使用。渲染缓存通过 `OFFICE_RENDER_TMP` 写入当前 Thread 的临时目录。宿主扩展同步脚本产生的版本事件，并通过专用接口提供只读预览和人工审查。

## 验证

回归包含：分别卸载、重新启用、物理移除功能目录、两项都卸载后通过真实 AgentRunner 完成普通对话，以及真实沙箱中的 DOCX/PPTX CLI 制作、技能开关和无专用模型工具的 AgentRunner 执行。

```sh
python -m pytest -q tests/test_features.py tests/test_runtime.py
```
