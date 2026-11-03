# Deep Agent Platform

基于 Vue 3 + Vite 与 FastAPI 的独立 Agent 平台。执行引擎位于仓库内的 `platform_app/agent_core`，复用 [ADaozz/deep_agent_cli](https://github.com/ADaozz/deep_agent_cli) 的 MIT 许可运行时源码，由平台独立维护。安装、运行和部署不需要安装 CLI、克隆其仓库或依赖其提交。源码来源与本地调整见 [内置运行时](docs/agent-core.md)。

这是本机单用户服务，监听 `127.0.0.1:8080`，尚无账号认证和多人租户隔离。

## 启动

需要 Linux / WSL2、Python 3.12+、Node.js 20.19+、Bubblewrap，以及允许运行 Bubblewrap 的 user namespace 配置。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
npm --prefix frontend install
node --test tests/*.test.mjs
npm --prefix frontend run build
PLATFORM_DATA_DIR=.platform-thread .venv/bin/uvicorn platform_app.main:app --host 127.0.0.1 --port 8080
```

打开 `http://127.0.0.1:8080`。点击左下角 Settings 或输入框模型菜单的 Manage models，添加模型来源、模型 ID、Base URL、API Key、API 协议和支持的思考强度。未配置模型时可编辑 Agent 设置，发送任务会明确提示缺少配置。

前端开发：先启动后端，再运行 `npm --prefix frontend run dev`。Vite 将 `/api` 代理到 8080 端口。修改前端后重新 build；构建目录 `platform_app/web/` 不提交。服务启动时加载静态资源挂载，首次构建后需要启动或重启后端。

## 网页功能

右上角提供浅色／夜间模式与中文／English 切换。首次打开跟随系统配色和浏览器语言，手动选择保存在本地浏览器，刷新后继续生效。界面标签、菜单、配置弹窗、状态提示和日期随语言更新；用户的会话内容与配置保持原文。侧栏文件入口为 Filebase（文件库）。

会话文件页展示当前 Thread 的私有文件并提供下载；聊天输出文件块点击打开支持格式的预览，下载按钮保留原文件下载。DOCX 和 PPTX 是两个独立、可卸载的扩展，可在侧栏「扩展功能」中分别启用、停用或卸载；卸载不删除产物与版本。通用文本、PDF 和图片预览不依赖 Office 扩展。部署方式见 [可选 Office 功能](docs/optional-office-features.md)。

DOCX 逐块提交，右侧提供真实分页、页码与整页/页宽缩放。PPTX 提供缩略图、即时原生元素预览及后台正式渲染；脚本覆盖文件后自动登记新版本并更新预览，保留当前及前一版文件快照。两者预览只读、支持运行中纠偏，最终文件保持可编辑。详见 [DOCX 说明](docs/docx-streaming.md) 和 [PPTX Skill](pptx-ooxml-skill/README.md)。

点击聊天文件块可在右侧预览 Markdown、TXT、YAML/YML、JSON、CSV、LOG、TOML、INI、XML、PDF 和 PNG/JPEG/GIF/WebP/AVIF/BMP/SVG 图片，并通过顶部文件列表切换。Markdown 支持排版与源码切换、本地图片及文件链接；图片支持缩放，PDF 使用浏览器阅读器。文本预览上限为 2 MB，超限文件仍可下载。

PPT 新建从 [60 套风格资料](pptx-ooxml-skill/templates/README.md) 中选择设计 Markdown 与参考图，结合用户指定的颜色、受众和主题统一整稿风格；导入、克隆与填槽用于修改已有 PPT。统计图表优先使用 PowerPoint 原生 chart 和嵌入式工作簿，仅缺少图型时才允许说明原因后自绘。分式、矩阵、积分等公式使用原生 Office Math，预览渲染与 PowerPoint 实机编辑验证分别记录。

全部幻灯片完成后统一进行遮挡检查与视觉审查。Agent 局部修复真实遮挡并复检，视觉审查由平台单独调用支持图片输入的模型 API，只提供建议，不自动修改文件。缺少可用视觉 API 时不阻塞生成或交付，也不标为视觉通过。审查默认折叠并显示问题数及等级，展开直接列出当前问题；保留驳回问题、交给 Agent 修复，不提供人工通过、不通过或单页重新审查入口。真实风格及用户调整会进入审查上下文，详见 [PPTX 主说明](pptx-ooxml-skill/SKILL.md)。

- 参考页面的浅色布局、可折叠侧栏、运行环境菜单和文件上传；适配手机屏幕。`Ctrl+Alt+B` 切换侧栏，Enter 发送，Shift+Enter 换行。
- 统一 Agent 设置，可编辑系统提示词、规则、原生工具开关、额外 Office 工具和 Skill。
- 切换模型和思考强度，支持 Chat Completions 与 Responses。运行中的修改交给内核在安全边界应用。会话创建后新增的模型需新建会话使用。
- 持久化多轮会话与 SSE 流式执行时间线：思考摘要、工具调用和执行结果按实际发生顺序纵向展示，各个调用独立保留。运行内容最多显示五行，内部滚动并自动跟随最新内容；向上浏览时暂停跟随。步骤结束自动收成一行，Agent 本轮真正结束后再收成一行任务概要。点击可展开完整输入、输出与历史，刷新重连后仍可恢复。
- 输入框底部显示最近一次模型请求的输入 Token 和缓存命中率（缓存读取的输入 Token / 总输入 Token），支持 DeepSeek 和兼容接口的真实统计。未返回统计时显示 `—`，明确返回零命中时显示 `0%`；悬停可查看命中与输入 Token。使用当前布局持久化记录中的真实统计值。
- 原生文件读写、搜索、命令执行、子 Agent、提问、工具审批、暂停/继续、取消、运行中追加指令、后续任务排队、上下文压缩。网页会话启用原生 `task` / `read_subagent_log`，按内置运行时策略委派子 Agent，并保留并发控制、取消传播与结果返回。
- 输入框加号直接上传文件，每条消息最多 10 个文件、每个 20MB。文件存入当前 Thread 的私有文件库，随消息传入可供 Agent 读取的路径；若模型支持图片输入，则最多 4 张符合条件的图片同时通过内核图片附件接口发送。
- 默认使用 `ask` 审批模式。模型旁的 Permission 菜单可切换 `ask / allow`；新会话发送前也可选择。切换到 `allow` 需输入 `ALLOW` 确认。运行中的切换显示待生效状态，并由内核在安全边界应用。`allow` 自动批准工具，不改变文件系统隔离模式；ask 模式下 execute 默认无网，申请 `network=true` 并获批后可联网，restricted 同样生效；allow 模式沿用命令默认联网。未提供 Thread 作用域适配的宿主 MCP 服务不会启动。
- 每轮结束后，本轮新增或修改的文件以紧凑文件块展示；支持的格式点击预览，其余格式点击下载。文件名和独立下载按钮始终可用。

## 工具、Skill 和 MCP

只想使用 PPT 技能，无需启动平台：完整复制 `pptx-ooxml-skill/`、安装其 requirements.txt，再让支持读文件和执行 Python 的 Agent 读取 SKILL.md。详见 [独立使用 PPT Skill 的完整指南](pptx-ooxml-skill/references/standalone.md)，包含可运行的两页示例、风格选择、原生图表/公式、检查与本地预览，以及独立模式和平台功能的边界。

侧栏「Agent 设置」统一配置提示词、规则、工具、Skill 和 MCP。所有新会话使用这份配置，已有会话保留各自的配置快照。平台不再提供工作区创建、切换、资源聚合、按工作区筛选或独立文件目录。

**Core tools** 通过全局设置控制原生工具是否对模型可见。`write_todos` 已从平台移除，主 Agent 与子 Agent 均不向模型暴露该工具。未启用的工具通过内核 `hidden_tools` 排除。联网工具直接使用 DeepSeek [Search / Fetch API](https://api-docs.deepseek.com/guides/search/)：`web_search` → `POST https://api.deepseek.com/v1/tools/search`，`web_fetch` → `POST https://api.deepseek.com/v1/tools/fetch`。复用模型配置中 DeepSeek 官方接口的 API Key；不注册 Tavily，不读取其环境变量，也没有回退。两项工具按全局开关控制，ask 模式均需审批。沙箱默认使用 `restricted`，每个 Thread 独享文件、HOME/TMP 及只读 Skill 快照；切换 `full-access` 需单独输入 `FULL_ACCESS`。工具审批独立，默认 Permission 仍为 `ask`。沙箱不可用时明确失败，不降级成宿主 shell 或假回答。

**Additional tools** 保留已注册的 XLSX Python 处理器，DOCX/PPTX 由内置技能提供，通过 `execute` 调用技能目录中的 Python 脚本，不固定注册文档专属 Agent 工具。可编辑启用状态、description 和 JSON Schema；参数名与必填字段必须兼容处理器，调用时验证两层 schema，不能通过 JSON 注入任意处理器。

**Skills** 展示 DOCX/PPTX 内置技能、平台数据目录 `skills/` 下已安装技能及配置中的技能，可全局启用或停用。每个会话生成独立的标准 `SKILL.md` 快照，以只读 `/agent-skills/` 目录挂载，由原生 Skills 中间件按需加载。Skill 不会作为全文重复注入系统提示词。

**MCP servers** 可在工具设置中保留原生 MCP JSON 配置。尚未提供 Thread 作用域适配的服务不会启动；原生 `MCPManager` 不会被当作文件隔离边界。例如：

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

支持 `stdio` 和 `streamable_http`，凭据使用 `token_env` / `env_passthrough` 引用。MCP 的 OAuth 浏览器登录、终端快捷键及终端专属显示未移植到网页。

配置变更用于新会话；已有会话保留提示词、规则、工具及 Skill 快照。文件上传、下载、Office 服务与 checkpoint 均按 Thread 解析。界面不恢复旧版工作区管理功能，内部通过持久化关联为 Thread 自动分配 Workspace。

隔离策略、目录布局、独立内核 PR 与 MCP 边界见 [Thread 文件与沙箱隔离](docs/thread-isolation.md)。不兼容旧共享目录、旧 checkpoint 或旧任务 API，不提供迁移；旧布局启动会明确报错。请使用新的 `PLATFORM_DATA_DIR`，重新配置模型和 Agent 设置。旧数据不删除、不导入。

## 模型配置

平台独立管理模型配置和加密凭据，不自动读取 CLI 的配置、Skills 或会话目录。首次部署请在网页配置模型，或显式提供 `PLATFORM_CONFIG`。

新部署可参考 `config.example.yaml`，将 平台模型 YAML 放到 `.platform/config.yaml`，或用 `PLATFORM_CONFIG=/path/to/config.yaml` 指定文件。读取其中的 `llm` 部分，使用原生 `Settings.from_mapping` 校验。已有网页保存的加密配置优先于文件；文件优先于兼容的 `PLATFORM_*` 模型环境变量。文件只在显式指定时作为配置来源。

`reasoning_efforts` 的第一项是模型默认强度；DeepSeek Flash 配置为 `[high, low, max]`。网页初始选择、新建对话和 API 省略强度参数时都会使用这一默认值。显式选择 Default 或 API 传入 `null` 仍表示不发送强度参数。

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

每轮执行只有一条纵向时间线，思考摘要、中间说明和工具调用依次追加；同名工具的不同调用各有独立状态和滚动位置。运行中的内容窗口最多五行，用户向上滚动时暂停跟随，返回底部后恢复；窗口限高不会截断日志。成功、失败或取消的步骤自动折叠，摘要展示名称、状态、耗时和简短结果，点击后可查看完整输入、流式输出和最终返回。仅当 Agent 本轮真正结束时，整条时间线才折叠成包含状态、步骤数和总耗时的一行；重新展开时，已完成步骤默认保持折叠。事件时间戳和全部流式记录持久化保留，旧会话缺失的时间不会被虚构。最终回答独立显示。审批卡片保留必要的业务操作信息，使用结构化字段展示命令、文件和修改内容。

- `frontend/src/App.vue`：Vue 界面、会话事件展示、模型/Agent 配置。
- `platform_app/runtime.py`：内核的 HTTP 展示适配器；原生 Runner 持久化 checkpoint、审批与执行，适配层不实现 Agent 推理循环。
- `platform_app/domain.py`：SQLite 全局配置、历史记录及 Office 工具。
- `platform_app/models.py`：平台模型配置、密钥加密与脱敏。
- `platform_app/main.py`：FastAPI 入口、全局配置与扩展路由。

主要目录：

| 目录 | 用途 |
| --- | --- |
| `frontend/src/` | 聊天页面、通用文件预览和可选 Office 面板 |
| `platform_app/` | HTTP 服务、内置 Agent 运行时和扩展注册 |
| `docx-ooxml-skill/` | DOCX 技能、脚本与测试 |
| `pptx-ooxml-skill/` | 可独立使用的 PPTX 技能、原生图表/公式与风格资料 |
| `scripts/import_pptx_styles/` | 风格资料导入工具、来源映射与裁切审计 |
| `tests/`、`docs/` | 平台回归测试与部署、隔离、验收说明 |

数据目录保存数据库、加密密钥、会话 checkpoint、事件日志、Skill 快照、文件库文件和 Office 产物，不提交到 Git。默认目录为 `.platform/`，上面的启动示例显式使用 `.platform-thread/`；可用 `PLATFORM_DATA_DIR` 指定其他持久目录。请将该目录中的 `model-config.key` 与数据库一起备份。服务重启不会自动重试未完成的写操作，原生 checkpoint 支持后续恢复。新会话采用最新 Skill 快照，已有会话保留原快照。

## 验证

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q pptx-ooxml-skill/tests
node --test tests/*.test.mjs
npm --prefix frontend run build
```

测试使用确定性的测试模型调用真实内核与真实 Bubblewrap，覆盖文件写入前审批、批准后落盘、命令执行、真实子 Agent 委派执行与结果返回、Skill 挂载、原生 checkpoint 恢复、模型/强度切换、配置快照及 Office 生成。测试机器需要可用的 Bubblewrap。外部模型端点与实际 API Key 需由用户配置后验证。

执行时间线的浏览器验收使用真实 SSE 与持久化 journal，由测试专用事件驱动器提供确定性事件（不调用外部模型，也不向生产应用注册测试路由）。先运行 `.venv/bin/python tests/process_e2e_server.py`，再运行 `npm --prefix frontend run test:e2e:process`；需要 Chromium，可通过 `CHROMIUM_PATH` 指定路径。覆盖五行限高、独立并行滚动、自动折叠、完整日志重新展开、刷新恢复、失败与取消，截图保存在 `frontend/test-results/process-*.png`。

Agent 设置中的 Skills 页用于配置已安装技能的启用状态，不再手动新增或编辑 Markdown。平台从 `$PLATFORM_DATA_DIR/skills/*/SKILL.md` 发现技能；通过 `skill_bindings` 按技能目录标识全局启用或停用。新会话将启用技能的完整目录（含脚本与参考资料）复制到只读快照，供原生技能加载器发现。已有配置技能只提供启用开关。

完整 Office 浏览器验收建议串行执行，避免 LibreOffice 同时渲染争用资源；慢速测试机器可用 `OFFICE_E2E_TIMEOUT_SCALE=3` 放大轮询等待预算。此设置只影响测试，不改变生产质量门禁或性能指标。正式渲染需要 LibreOffice、fontconfig 和文档字体；Office 依赖安装见 [可选 Office 功能](docs/optional-office-features.md)。
