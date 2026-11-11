# Agent Platform

基于 FastAPI 与 Vue 3 的 Agent 平台。执行引擎位于仓库内的 `platform_app/agent_core`，基于 LangGraph 与 deepagents，提供多轮会话、流式执行时间线、工具审批、Bubblewrap 沙箱隔离、子 Agent 委派，以及 DOCX / PPTX 文档生成与实时预览。所有运行所需源码都在本仓库内，不依赖外部 CLI 或其他代码仓库。

平台有两种运行方式：

| 模式 | 启用条件 | 说明 |
| --- | --- | --- |
| 单用户 | 默认 | 本机服务，自带网页界面，无账号认证 |
| 多用户（ITAM 接入） | 设置 `ITAM_RUNTIME_SHARED_SECRET` | 作为业务系统后端的 Agent 运行时，由后端签名转发用户身份，会话与文件按用户隔离 |

## 环境要求

- Linux 或 WSL2，Python 3.12+，Node.js 20.19+
- Bubblewrap，以及允许其运行的 user namespace 配置（沙箱不可用时任务会明确失败，不会降级为宿主 shell）
- 可选：LibreOffice、fontconfig 与中文字体，用于 DOCX / PPTX 的正式分页渲染，见 [可选 Office 功能](docs/optional-office-features.md)

## 快速开始（单用户）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install -r docx-ooxml-skill/requirements.txt -r pptx-ooxml-skill/requirements.txt
npm --prefix frontend install
npm --prefix frontend run build
PLATFORM_DATA_DIR=.platform .venv/bin/uvicorn platform_app.main:app --host 127.0.0.1 --port 8080
```

打开 `http://127.0.0.1:8080`，在设置中添加模型来源、模型 ID、Base URL、API Key、API 协议（Chat Completions 或 Responses）和思考强度后即可对话。

前端开发：先启动后端，再运行 `npm --prefix frontend run dev`，Vite 会把 `/api` 代理到 8080 端口。构建产物输出到 `platform_app/web/`，不提交到仓库。

## 多用户模式（ITAM 接入）

设置共享密钥（至少 32 字节）后，平台进入多用户模式。服务只应监听本机或内网地址，由业务后端代理访问：

```bash
export ITAM_RUNTIME_SHARED_SECRET='<与业务后端一致的随机密钥>'
PLATFORM_DATA_DIR=.platform-itam .venv/bin/uvicorn platform_app.main:app --host 127.0.0.1 --port 8090
```

每个 `/api/` 请求都必须携带业务后端生成的签名身份：

| 请求头 | 内容 |
| --- | --- |
| `X-Agent-User-Id` | 用户 ID（正整数） |
| `X-Agent-Role` | `admin` 或 `user` |
| `X-Agent-Timestamp` | Unix 秒级时间戳，与服务器时间相差不超过 30 秒 |
| `X-Agent-Signature` | HMAC-SHA256 十六进制签名 |

签名原文为以下字段按换行连接：`timestamp`、`user`、`role`、HTTP 方法、原始路径、原始查询串。签名无效时返回 401。

多用户模式下的访问规则：

- 会话归属创建者，访问他人会话一律返回 404；未标记归属的旧会话不可访问。
- 全局模型配置只有管理员可以修改；普通用户只能使用全局模型，并在设置中维护自己的私人模型。
- Agent 工具、Skill 与扩展功能由服务端统一管理，任何角色都不能通过接口修改。
- 禁止切换到 `full-access` 沙箱，所有会话保持 Thread 级文件隔离。

## 模型配置

模型配置保存在平台数据库中，API Key 使用 Fernet 加密，接口返回时替换为 `__UNCHANGED_SECRET__`，保留该占位符即可沿用已有密钥。首次部署也可以参考 `config.example.yaml`，放到 `$PLATFORM_DATA_DIR/config.yaml` 或通过 `PLATFORM_CONFIG` 指定；网页保存的配置优先于文件。

```json
{
  "default": "provider/model-name",
  "timeout": 60,
  "models": {
    "provider": {
      "base_url": "https://provider.example/v1",
      "api_key": "${PROVIDER_API_KEY}",
      "api": "chat_completions",
      "reasoning_efforts": ["high", "low"],
      "models": {"model-name": {"input": ["text", "image"], "context_window": 128000}}
    }
  }
}
```

`reasoning_efforts` 的第一项是默认思考强度。全局配置中的 `api_key` 可以写成 `${ENV_NAME}` 引用服务进程的环境变量。

私人模型（仅多用户模式）：每个用户独立保存、单独加密，在模型列表中以 `my-` 前缀显示，不会覆盖全局来源。私人模型不能引用服务器环境变量，`base_url` 必须是 `http://` 或 `https://` 地址。

## 主要功能

- **会话与执行时间线**：SSE 流式输出，思考、工具调用和结果按发生顺序展示，步骤结束后自动折叠；刷新或重连后可恢复。支持暂停、继续、取消、运行中追加指令和任务排队。
- **工具审批**：默认 `ask` 模式，写文件、执行命令等操作需要确认；可切换为 `allow`。`ask` 模式下命令默认不联网，申请联网需单独审批。
- **沙箱隔离**：每个会话独享文件目录、HOME/TMP 与只读 Skill 快照，命令在 Bubblewrap 中执行，详见 [Thread 文件与沙箱隔离](docs/thread-isolation.md)。
- **文件**：输入框可上传附件（每条消息最多 10 个、每个 20 MB）；会话产出的文件可下载和预览，并提供跨会话的全局文件列表。预览支持 Markdown、文本类格式、PDF、图片、DOCX 与 PPTX。
- **子 Agent**：原生 `task` 工具委派子任务，保留并发控制、取消传播与结果返回。
- **联网工具**：`web_search` / `web_fetch` 调用 DeepSeek 的 Search / Fetch 接口，复用模型配置中 DeepSeek 官方地址的 API Key；受全局开关与审批控制。

## 内置 Skill

Skill 以标准 `SKILL.md` 目录形式提供，新会话创建时复制为只读快照挂载到 `/agent-skills/`，由 deepagents 的 Skills 中间件按需加载。修改 Skill 只影响之后新建的会话。

| Skill | 能力 |
| --- | --- |
| `docx-ooxml-skill/` | 逐块构建 Word 文档，每步一个版本，可回滚；支持标题、段落格式、字符格式、行内与编号公式（原生 Office Math）、原生图表、题注与交叉引用、目录、合并单元格的复杂表格、图文环绕与多图并排、页眉页脚与页码 |
| `pptx-ooxml-skill/` | 基于 60 套风格资料生成演示文稿，支持原生图表与公式、模板导入与填槽、遮挡检查与视觉审查；也可脱离平台单独使用，见 [独立使用指南](pptx-ooxml-skill/references/standalone.md) |

两份 Skill 的操作说明分别见各自目录下的 `SKILL.md` 与 `references/`。DOCX / PPTX 预览以扩展形式提供，可通过 `scripts/manage_features.py` 启用或停用，见 [DOCX 说明](docs/docx-streaming.md)。

## 目录结构

| 路径 | 用途 |
| --- | --- |
| `platform_app/main.py` | FastAPI 入口、模型与设置接口 |
| `platform_app/runtime.py` | 会话、事件流、文件与审批接口 |
| `platform_app/itam_bridge.py` | 多用户模式的签名校验与访问控制 |
| `platform_app/models.py` | 全局与私人模型配置、密钥加密 |
| `platform_app/domain.py` | SQLite 存储与全局配置 |
| `platform_app/agent_core/` | 内置 Agent 执行引擎（MIT 许可，见目录内 LICENSE） |
| `frontend/` | 单用户模式的网页界面 |
| `docx-ooxml-skill/`、`pptx-ooxml-skill/` | 文档生成 Skill、脚本与测试 |
| `scripts/` | 扩展管理、PPT 风格资料导入与性能测试脚本 |
| `tests/`、`docs/` | 回归测试与部署、隔离、验收文档 |

## 数据目录

`PLATFORM_DATA_DIR`（默认 `.platform/`）保存数据库、模型配置加密密钥 `model-config.key`、会话 checkpoint、事件日志、Skill 快照、上传文件与文档产物。该目录不提交到仓库，备份时请将数据库与 `model-config.key` 一起保存。

## 测试

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest -q
PYTHONPATH=. .venv/bin/python -m pytest -q docx-ooxml-skill/tests pptx-ooxml-skill/tests
node --test tests/*.test.mjs
npm --prefix frontend run build
```

测试使用确定性的测试模型驱动真实执行引擎与真实 Bubblewrap，覆盖审批、命令执行、子 Agent、Skill 挂载、checkpoint 恢复、多用户隔离、私人模型与 Office 生成；测试机器需要可用的 Bubblewrap。浏览器端到端测试需要 Chromium，见 [Office 验收](docs/office-acceptance.md)。

## 许可

`platform_app/agent_core/` 按 MIT 许可纳入，原始版权与许可文本保存在该目录的 `LICENSE` 中。PPT 风格资料的来源与许可记录在 `pptx-ooxml-skill/templates/` 目录内。
