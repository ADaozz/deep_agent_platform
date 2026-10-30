# 平台内置执行引擎

`platform_app/agent_core` 是平台仓库内的 Python 源码，不是外部 CLI 包的包装器，也不是 Git 子模块。平台直接维护并发布这些文件。依赖文件只列出 LangChain、LangGraph、deepagents、HTTP 等第三方库，不安装 `deep-agent-cli`，没有 CLI 项目的网络下载、导入回退或自动更新逻辑。

## 来源与许可

运行时源码复用自 [ADaozz/deep_agent_cli](https://github.com/ADaozz/deep_agent_cli) 的 `f190baa2e08a160f1cf8e2c1575cf3261e0bd826` 基线，按 MIT 许可纳入本仓库。原作者版权与完整许可保存在 [LICENSE](../platform_app/agent_core/LICENSE)。该提交只用于来源追溯，不参与安装和运行。

仅纳入执行、模型、会话、沙箱、工具、中间件、子 Agent 与 MCP 处理模块；未纳入 CLI 终端界面、命令入口、终端配置初始化器或 CLI 包版本查询。源码的内部导入统一使用 `platform_app.agent_core`，不提供 `agent` 名称兼容层。

## 平台维护范围

- 保留原有主 Agent Loop、steering、安全检查点、checkpoint、审批和原生 Bubblewrap 隔离机制。
- 在平台源码中增加 Skills 挂载控制和按沙箱模式限制命令联网，防止全局可写 Skills 与宿主 HTTP 绕过 Thread 隔离。
- 默认配置、Skills、会话路径属于 `PLATFORM_DATA_DIR`，不读取 `~/.deep-agent`。子 Agent 日志按 Thread 保存。
- DOCX/PPTX 工具由独立可卸载扩展注册，内置执行引擎没有格式专属依赖。

CLI 项目可以独立演进；平台不要求其修改、发版或合并 PR。今后需要复用上游修复时，在平台内审查和修改源码，按平台测试与版本发布。

## 独立安装验证

2026-10-30，仅用 `requirements-features.lock` 创建全新虚拟环境，未安装 `deep-agent-cli`，`find_spec("agent")` 为 `None`。阻止任何外部 `agent` 导入后，平台仍能启动；完整平台回归 103 项通过，包含真实 Bubblewrap、审批、子 Agent、steering、checkpoint 恢复和两种 Office 扩展。前端状态测试 12 项及生产构建通过。
