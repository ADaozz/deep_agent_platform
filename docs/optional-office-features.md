# 独立、可卸载的 DOCX / PPTX 功能

两项能力都是可选扩展，不是主 Agent 的内置文档逻辑：

| 功能 | 后端与 Skill | 前端 |
| --- | --- | --- |
| DOCX | `docx-ooxml-skill/` | `frontend/src/features/docx/` |
| PPTX | `pptx-ooxml-skill/` | `frontend/src/features/pptx/` |

`feature.json` 声明功能身份、适配器、依赖和 Skill。通用注册器 `platform_app/format_plugins.py` 负责发现和接入；`artifact_runtime.py` 仅提供快照、事务、事件、幂等和恢复，不负责规划或 Agent 执行。`runtime.py` / `agent_factory.py` 不导入 Word 或 PowerPoint 的引擎、组件、提示词或业务实现；只接受启用扩展提供的工具、说明和事件。

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

PPTX 的 `pptx_init` 使用内核原有工具审批：ask 模式下一次授权当前演示文稿，后续受限元素修改只在这个会话/文件范围内执行。其他演示文稿、任意文件、命令和网络没有因此获得授权。全局 allow 仍由现有 Permission 菜单管理，扩展不会切换它。

原 `create_docx` / `create_pptx` 批量处理器已移除，旧配置会迁移；XLSX 不变。兼容任务 API 也不能绕过 PPTX 初始化审批；需要交互审批的 PPTX 工作流应使用会话界面。

## 验证

回归包含：分别卸载、重新启用、物理移除功能目录、两项都卸载后通过真实 AgentRunner 完成普通对话，以及另一功能的工具仍可独立注册。

```sh
python -m pytest -q tests/test_features.py tests/test_runtime.py
```
