# Deep Agent Platform

工作空间驱动的 Agent 平台第一版。共用 `ADaozz/deep_agent_cli` 的 Python 内核（固定提交 `f190baa2`），不复制推理循环。当前为**本机单用户开发版本**，不要直接暴露到公网。

## 启动

Linux，Python 3.12+：

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
# 在服务器环境安全配置这三个变量，不要提交密钥
export PLATFORM_BASE_URL=https://your-provider.example/v1
export PLATFORM_MODEL=your-model
# PLATFORM_API_KEY 通过环境设置注入
.venv/bin/uvicorn platform_app.main:app --host 127.0.0.1 --port 8080
```

推荐启动后点击网页右上角「模型配置」，新增来源、模型、API 协议、API Key 和支持的思考强度。保存后在任务输入框选择模型与强度。未配置模型仍可使用空间切换、配置编辑；提交任务会明确提示缺少配置，不产生模拟回答。模型需支持工具调用，可使用 OpenAI Responses 或 Chat Completions。当前云环境额外需要允许模型端点的网络访问；现有代理和 CA 信任应保留。

开发检查：`.venv/bin/pip install pytest httpx && .venv/bin/python -m pytest -q`。

## 架构与边界

- `platform_app/domain.py`：SQLite 空间配置、任务记录和 WorkspaceAdapter。每个任务固定配置快照，独立创建工具实例；规则、技能、提示词以及工具描述/schema 不共享可变对象。运行中的任务不受后续配置修改影响。并发编辑使用 revision 检查。
- `platform_app/main.py`：API、任务执行和唯一内核集成入口。所有空间共用同一套 `create_agent` / `AgentRunner` 实现，每个任务独立上下文。没有第二套自写 agent 循环。
- 内核原生编程提示词仍会保留；空间提示词作为用户指令传入，这是上游 API 的行为。工具通过内核 hidden_tools 过滤；不连接全局 MCP、不读取全局 Skills，不启用 shell / 文件系统 / 子 Agent。状态后端为 StateBackend，无宿主目录权限。当前技能作为空间指令注入，而非按需读取的技能目录。
- 工具 schema 及 docstring（description）可编辑；工具执行只能绑定服务器注册的处理器。schema 需保留处理器参数和必填字段，运行时再做两层参数验证。不能通过编辑 JSON 执行任意 Python。
- Office 提供真实 DOCX / PPTX / XLSX 生成及下载，输出按空间和任务分目录，不使用模型输入构造文件路径。表格字符串不作为公式执行。Research / Writing 为无外部工具的文本空间。
- `.platform/` 保存本地状态，可用 `PLATFORM_DATA_DIR` 指向外部持久卷。服务重启会把未完成任务标记失败，不伪装为完成。

## 当前范围

已实现：深色响应式平台界面、空间切换与新建、配置持久化与版本检查、异步任务与轮询、历史记录、文件下载、真实内核接入及 Office 工具。

尚未实现：账号与组织权限、多人租户隔离、生产容器沙箱和配额、MCP 配置、流式事件、会话续聊、取消与审批交互。当前每个任务是一轮独立会话。工具配置隔离不是操作系统或多租户安全隔离。

参考目标为 cursor.com/agents，但本次环境无法访问该页面（403），因此界面为原创的同类布局，未声称像素级复刻。后续需要以可访问页面或用户提供的截图校准。

## 网页模型配置

复用 CLI 的 `Settings.from_mapping` 和 `build_chat_model`，配置结构为：

```yaml
llm:
  default: provider/model-name
  timeout: 60
  models:
    provider:
      base_url: https://provider.example/v1
      api_key: ${PROVIDER_API_KEY}
      api: responses
      reasoning_efforts: [low, medium, high]
      models:
        model-name:
          input: [text]
          context_window: 128000
```

网页提供新增表单，支持向已有来源增加模型。高级 JSON 编辑器直接编辑上述 `llm` 内容，可删除模型、修改端点、模型级覆盖、重试和超时等 CLI 参数。`默认强度` 表示不向服务端传 reasoning 参数；其他强度只允许选择当前模型声明的值。协议由配置决定，不做自动猜测。

可输入 API Key 或 `${ENV_VAR}` 引用。直接输入的密钥使用 Fernet 加密保存，读取接口以 `__UNCHANGED_SECRET__` 脱敏；编辑时保留该占位符可沿用已存密钥。加密密钥位于 `.platform/model-config.key`（0600），与数据库一起持久化、备份，不要提交到 Git。密钥与数据库共用本机存储，这不替代生产级密钥服务和身份认证。环境变量引用需在服务端注入；支持先保存未就绪引用，但任务提交会检查。

每个任务固定模型配置、协议、模型 ID 和思考强度快照。已提交任务不受后续网页配置编辑影响。环境变量引用在运行时解析；轮换服务器凭据不会固定到旧值。任务和模型读取接口不会返回明文密钥。旧版 `PLATFORM_*` 变量作为未保存网页配置时的兼容来源保留。

验证覆盖两种协议及 reasoning 参数、密钥加密/脱敏/保留、并发编辑冲突、无效配置拒绝和任务模型快照。未调用真实外部模型验证端点凭据。
