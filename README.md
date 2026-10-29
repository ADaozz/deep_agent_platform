# Deep Agent Platform

基于 Vue 3 + Vite 的工作区 Agent 界面，视觉与交互参考用户提供的 `cursor-agents-replica.zip`。FastAPI 托管构建产物和 API，所有新会话由 [ADaozz/deep_agent_cli](https://github.com/ADaozz/deep_agent_cli) 的 `AgentSpec` / `build_agent` / `AgentRunner` 执行，固定提交 `f190baa2e08a160f1cf8e2c1575cf3261e0bd826`，没有复制推理循环。

这是本机单用户服务，监听 `127.0.0.1:8080`，尚无账号认证和多人租户隔离。

## 启动

需要 Linux / WSL2、Python 3.12+、Node.js 20.19+、Bubblewrap，以及允许运行 Bubblewrap 的 user namespace 配置。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
npm --prefix frontend install
npm --prefix frontend run build
.venv/bin/uvicorn platform_app.main:app --host 127.0.0.1 --port 8080
```

打开 `http://127.0.0.1:8080`。点击左下角 Settings 或输入框模型菜单的 Manage models，添加模型来源、模型 ID、Base URL、API Key、API 协议和支持的思考强度。未配置模型时可编辑工作区，发送任务会明确提示缺少配置。

前端开发：先启动后端，再运行 `npm --prefix frontend run dev`。Vite 将 `/api` 代理到 8080 端口。修改前端后重新 build；构建目录 `platform_app/web/` 不提交。服务启动时加载静态资源挂载，首次构建后需要启动或重启后端。

## 网页功能

右上角提供浅色／夜间模式与中文／English 切换。首次打开跟随系统配色和浏览器语言，手动选择保存在本地浏览器，刷新后继续生效。界面标签、菜单、配置弹窗、状态提示和日期随语言更新；用户的会话内容与配置保持原文。侧栏文件入口为 Filebase（文件库）。

文件库展示工作区文件并提供下载；聊天附件和输出文件块可点击直接下载。DOCX 和 PPTX 是两个独立、可卸载的扩展，可在侧栏「扩展功能」中分别启用、停用或卸载，不与主 Agent 执行逻辑耦合；卸载不删除产物与版本。部署方式见 [可选 Office 功能](docs/optional-office-features.md)。

DOCX 逐块提交，右侧提供真实分页、页码与整页/页宽缩放。PPTX 逐元素构建，提供缩略图、即时原生元素预览及完成页的正式渲染。两者只读、支持运行中纠偏，最终文件保持可编辑。未完成实际视觉审查时标记草稿。详见 [DOCX 说明](docs/docx-streaming.md) 和 [PPTX Skill](pptx-ooxml-skill/README.md)。

- 参考页面的浅色布局、可折叠侧栏、工作区菜单、运行环境菜单和文件上传；适配手机屏幕。`Ctrl+Alt+B` 切换侧栏，Enter 发送，Shift+Enter 换行。
- 新建/切换工作区，编辑系统提示词、规则、原生工具开关、额外 Office 工具和 Skill。
- 切换模型和思考强度，支持 Chat Completions 与 Responses。运行中的修改交给内核在安全边界应用。会话创建后新增的模型需新建会话使用。
- 持久化多轮会话、SSE 流式回答与思考、独立聚合的思考区域与工作过程时间线（可分别折叠，回答完成后默认收起并保留；同类工具合并，显示 success / failure 计数，隐藏命令与工具输出详情）、刷新重连和历史记录；已完成会话一次性加载压缩后的历史事件（省略逐 token 增量），长会话打开不再逐段回放。
- 输入框底部显示最近一次模型请求的输入 Token 和缓存命中率（缓存读取的输入 Token / 总输入 Token），支持 DeepSeek 和兼容接口的真实统计。未返回统计时显示 `—`，明确返回零命中时显示 `0%`；悬停可查看命中与输入 Token。历史会话可从原生 checkpoint 补齐缓存字段，无需重新调用模型。
- 原生文件读写、搜索、命令执行、子 Agent、提问、工具审批、暂停/继续、取消、运行中追加指令、后续任务排队、上下文压缩。网页会话与兼容任务接口均启用原生 `task` / `read_subagent_log`，按 CLI 默认策略委派子 Agent，并保留并发控制、取消传播与结果返回。
- 输入框加号直接上传文件，每条消息最多 10 个文件、每个 20MB。文件存入当前工作区，随消息传入可供 Agent 读取的路径；若模型支持图片输入，则最多 4 张符合条件的图片同时通过内核图片附件接口发送。
- 默认使用 `ask` 审批模式。模型旁的 Permission 菜单可切换 `ask / allow`；新会话发送前也可选择。切换到 `allow` 需输入 `ALLOW` 确认，与 CLI 一致。运行中的切换显示待生效状态，并由内核在安全边界应用。`allow` 自动批准工具并开放沙箱网络，MCP 工具仍在宿主运行。
- 工作区文件浏览/下载及真实 DOCX / PPTX / XLSX 文件下载；每轮结束后，本轮新增或修改的工作区文件以文件块展示，点击直接下载；原版任务历史保留。

## 工具、Skill 和 MCP

工作区配置统一位于侧栏的「工作区」区域：点击工作区名称或区域标题，集中编辑该工作区的提示词、规则、工具、Skill 和 MCP。管理器中可切换配置目标，不会切换当前会话或清空草稿。聊天框的加号仅用于文件上传。

**Everything** 为默认工作区，自动绑定全部内核工具、已注册的 Office 工具、其他工作区的 Skill、CLI 已安装的 Skill，以及已启用的 MCP 服务。工作区 Skill 在新会话创建时聚合并固定快照；原生 CLI Skill 由 `/skills/` 提供。修改来源工作区后，新会话自动获得最新绑定。关闭 Everything 的「自动绑定全部工具与 Skill」可改为手动配置。

**Core tools** 按工作区控制原生工具是否对模型可见。`write_todos` 已从平台移除，主 Agent、子 Agent 与兼容接口均不向模型暴露该工具。未启用的工具通过内核 `hidden_tools` 排除。联网工具直接使用 DeepSeek [Search / Fetch API](https://api-docs.deepseek.com/guides/search/)：`web_search` → `POST https://api.deepseek.com/v1/tools/search`，`web_fetch` → `POST https://api.deepseek.com/v1/tools/fetch`。复用模型配置中 DeepSeek 官方接口的 API Key；不注册 Tavily，不读取其环境变量，也没有回退。两项工具按工作区开关控制，ask 模式均需审批。沙箱默认使用 `full-access`，可以访问虚拟机真实文件系统；平台配置、密钥及内核安装仍按原生机制保护。`full-access` 与工具审批独立，默认 Permission 仍为 `ask`。沙箱不可用时明确失败，不降级成宿主 shell 或假回答。

**Workspace tools** 保留 Office 空间的已注册 Python 处理器。可编辑启用状态、description 和 JSON Schema；参数名与必填字段必须兼容处理器，调用时验证两层 schema，不能通过 JSON 注入任意处理器。

**Skills** 支持新增、编辑、启用和删除。每个会话生成独立的标准 `SKILL.md` 快照，以只读 `/workspace-skills/` 目录挂载，由原生 Skills 中间件按需加载。Skill 不会作为全文重复注入系统提示词。

**MCP servers** 在工具设置中使用原生 MCP JSON 格式，配置由 `MCPManager` 解析与连接。例如：

```json
{
  "enabled": true,
  "servers": {
    "docs": {
      "transport": "streamable_http",
      "url": "https://example.com/mcp",
      "auth": {"type": "bearer", "token_env": "DOCS_MCP_TOKEN"}
    }
  }
}
```

支持 `stdio` 和 `streamable_http`，凭据使用 `token_env` / `env_passthrough` 引用。MCP 的 OAuth 浏览器登录、全局 CLI 快捷键及终端专属显示未移植到网页。

工作区配置变更用于新会话；已有会话保留创建时的提示词、规则、工具、Skill 和 MCP 快照。每个工作区共用自己的文件目录，因此同一工作区多个 Agent 会看到彼此的文件修改。工具过滤与工作区目录不是多租户安全边界。

## 模型配置

当前部署从本机 `~/.deep-agent/config.yaml` 复用 DeepSeek 凭据，仅保留真实配置的 DeepSeek Flash。平台默认模型为 `deepseek/deepseek-flash`，默认思考强度为 `high`。CLI 全局配置保持不变；凭据保存在平台加密配置中。

新部署可参考 `config.example.yaml`，将 CLI 同格式的 YAML 放到 `.platform/config.yaml`，或用 `PLATFORM_CONFIG=/path/to/config.yaml` 指定文件。读取其中的 `llm` 部分，使用原生 `Settings.from_mapping` 校验。已有网页保存的加密配置优先于文件；文件优先于兼容的 `PLATFORM_*` 模型环境变量。可直接指定 CLI 配置文件作为初始来源，随后在网页调整平台自己的默认值。

与 CLI 一致，`reasoning_efforts` 的第一项是模型默认强度；DeepSeek Flash 配置为 `[high, low, max]`。网页初始选择、新建对话和 API 省略强度参数时都会使用这一默认值。显式选择 Default 或 API 传入 `null` 仍表示不发送强度参数。

复用内核的 `Settings.from_mapping` 与 `build_chat_model`。高级 JSON 编辑器可管理完整 `llm` 配置，删除模型、修改默认模型、超时和模型参数：

```json
{
  "default": "provider/model-name",
  "timeout": 60,
  "models": {
    "provider": {
      "base_url": "https://provider.example/v1",
      "api_key": "${PROVIDER_API_KEY}",
      "api": "responses",
      "reasoning_efforts": ["low", "medium", "high"],
      "models": {"model-name": {"input": ["text", "image"], "context_window": 128000}}
    }
  }
}
```

Default 思考强度表示不向模型端点发送强度参数；其余选项来自模型显式配置。直接输入的密钥通过 Fernet 加密保存，读取时替换为 `__UNCHANGED_SECRET__`；保留占位符可继续使用已有密钥。环境变量引用需在服务进程中注入。未保存网页配置时兼容 `PLATFORM_MODEL`、`PLATFORM_BASE_URL`、`PLATFORM_API_KEY`。

## 状态与架构

聊天中的思考过程、工具调用和中间说明合并为每轮一个可折叠区域；执行中展开，结束后收起。工具使用易读名称和执行状态，不展示原始返回值、参数 JSON、调用 ID 或内部模型来源 ID。最终回答独立显示。审批卡片保留必要的业务操作信息，使用结构化字段展示命令、文件和修改内容，不直接转储元数据。

- `frontend/src/App.vue`：Vue 界面、会话事件展示、模型/工作区配置。
- `platform_app/runtime.py`：内核的 HTTP 展示适配器；原生 Runner 持久化 checkpoint、审批与执行，适配层不实现 Agent 推理循环。
- `platform_app/domain.py`：SQLite 空间配置、历史记录及 Office 工具。
- `platform_app/models.py`：CLI 兼容的模型配置、密钥加密与脱敏。
- `platform_app/main.py`：FastAPI 入口，保留原版任务 API 与下载接口。

`.platform/` 保存数据库、加密密钥、会话 checkpoint、事件日志、Skill 快照、工作区文件和 Office 产物。可用 `PLATFORM_DATA_DIR` 指定外部持久目录。请将 `.platform/model-config.key` 与数据库一起备份；它不替代生产密钥服务。服务重启不会自动重试未完成的写操作，原生 checkpoint 支持后续恢复。

参考压缩包包含捕获账户的页面内容，已加入 `.gitignore`；页面使用真实平台数据，没有导入参考页面中的账户历史。

## 验证

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest -q
npm --prefix frontend run build
```

测试使用确定性的测试模型调用真实内核与真实 Bubblewrap，覆盖文件写入前审批、批准后落盘、命令执行、真实子 Agent 委派执行与结果返回、Skill 挂载、原生 checkpoint 恢复、模型/强度切换、工作区快照及 Office 生成。测试机器需要可用的 Bubblewrap。外部模型端点与实际 API Key 需由用户配置后验证。

工作区的 Skills 页用于配置已安装技能的启用状态，不再手动新增或编辑 Markdown。平台从 `~/.deep-agent/skills/*/SKILL.md` 发现技能；Everything 自动绑定全部，其他工作区通过 `skill_bindings` 按技能目录标识启用。新会话将启用技能的完整目录（含脚本与参考资料）复制到只读快照，供原生技能加载器发现。已有工作区技能只提供启用开关。
